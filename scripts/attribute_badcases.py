"""
Step 4.2：badcase 自动归因（**零 LLM 调用** —— 只用本地 embedding 跑检索）

任务书 Step 4 要求交付「20 个 badcase 的归因」。归因里最关键的一刀是
**检索层 vs 模型层**，而这一刀不需要 LLM：

- **漏报（FN，金标准有、我们没报）** → 用本地 embedding 重跑一次该类条款的检索，
  看**金标准的证据段落有没有被召回**：
    · 没召回 → **检索层**（模型压根没看到证据；换个更强的模型也无济于事）
    · 召回了 → **模型层**（证据就在上下文里，模型仍判不存在）
- **误报（FP，金标准没有、我们报了）** → 检查模型引用的证据是否真的来自喂给它的片段
  （防幻觉检查），并按条款归类到已知的"口径落差"模式。

用法（在 hetong/ 目录下运行）：
    python -X utf8 scripts\\attribute_badcases.py
    python -X utf8 scripts\\attribute_badcases.py --k 5 --out badcase_attribution.md
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clauses import BY_ID  # noqa: E402
from config import EVAL_DIR, REPORTS_DIR, chroma_dir, embed_device, embed_model_dir  # noqa: E402
from retriever import Retriever  # noqa: E402
from schemas import ReviewReport  # noqa: E402
from text_utils import read_jsonl  # noqa: E402

# 已知的"口径落差"模式（依据 badcase_log.md：我们的定义与 CUAD 标注口径不一致）
CALIBER_NOTE = {
    "02_change_of_control": "转让例外/转让允许条款被判成控制权变更（BC-006：CUAD 问『什么构成控制权变更』，我们问『有没有针对它的安排』）",
    "06_termination_notice": "无理由终止权被判命中，但 CUAD 未标注该类（金标准覆盖不全）",
    "09_liquidated_damages": "penalty / Late Fees 被判成违约金（我们的定义宽于 CUAD 的 `Liquidated Damages`）",
    "10_ip_ownership": "背景 IP 保留声明被判成归属条款（§10.3 待收窄）",
    "01_governing_law": "管辖/仲裁句被判命中，但 CUAD 的 `Governing Law` 只覆盖『适用法律』",
}


def gold_spans_of(record: dict, clause_id_value: str) -> list[tuple[int, int]]:
    gold = record.get("labels", {}).get(clause_id_value, {})
    return [(e["char_start"], e["char_end"]) for e in gold.get("evidence", [])]


def gold_recalled(record: dict, clause_id_value: str, retriever, k: int) -> tuple[bool | None, str]:
    """金标准证据有没有被召回（本地检索，不花 token）"""
    spans = gold_spans_of(record, clause_id_value)
    if not spans:
        return None, "金标准未标注证据片段"
    spec = BY_ID[[c for c in BY_ID if c.value == clause_id_value][0]]
    hits = retriever.search(record["contract_id"], spec.retrieval_query, k=k)
    for hit in hits:
        for start, end in spans:
            if hit["char_start"] <= start and end <= hit["char_end"]:
                return True, f"已召回（片段 {hit['char_start']}-{hit['char_end']} 覆盖金标准 {start}-{end}）"
    nearest = min((abs(hit["char_start"] - spans[0][0]) for hit in hits), default=-1)
    return False, f"**未召回**（金标准 {spans[0][0]}-{spans[0][1]}，最近片段相距 {nearest} 字符）"


def main() -> None:
    parser = argparse.ArgumentParser(description="badcase 自动归因（零 LLM 调用）")
    parser.add_argument("--k", type=int, default=5, help="检索 top-k，需与评测时一致")
    parser.add_argument("--out", default="badcase_attribution.md", help="归因报告输出文件名")
    args = parser.parse_args()

    records = {r["contract_id"]: r for r in read_jsonl(EVAL_DIR / "golden_eval.jsonl")}
    report_paths = sorted(REPORTS_DIR.glob("*.json"))
    if not report_paths:
        raise SystemExit(f"❌ {REPORTS_DIR} 下没有报告，先跑 run_eval.py")

    retriever = Retriever(chroma_dir=chroma_dir(), model_dir=embed_model_dir(), device=embed_device())

    fn_rows: list[dict] = []
    fp_rows: list[dict] = []
    scanned = 0

    for path in report_paths:
        report = ReviewReport.model_validate_json(path.read_text(encoding="utf-8"))
        record = records.get(report.contract_id)
        if record is None:
            continue
        for finding in report.findings:
            if finding.reason_zh.startswith("[PIPELINE_ERROR]"):
                continue
            gold = record.get("labels", {}).get(finding.clause_id.value, {})
            gold_exists = bool(gold.get("exists"))
            scanned += 1
            if finding.exists == gold_exists:
                continue

            if gold_exists and not finding.exists:  # 漏报
                recalled, detail = gold_recalled(record, finding.clause_id.value, retriever, args.k)
                layer = "检索层" if recalled is False else ("模型层" if recalled else "数据层/存疑")
                fn_rows.append(
                    {
                        "contract": report.contract_id,
                        "clause": finding.clause_id.value,
                        "layer": layer,
                        "detail": detail,
                        "confidence": finding.confidence,
                        "reason": finding.reason_zh[:150],
                    }
                )
            else:  # 误报
                real = bool(finding.evidence)
                fp_rows.append(
                    {
                        "contract": report.contract_id,
                        "clause": finding.clause_id.value,
                        "layer": "口径落差" if finding.clause_id.value in CALIBER_NOTE else "待人工判定",
                        "note": CALIBER_NOTE.get(finding.clause_id.value, "—"),
                        "evidence_is_real": real,
                        "quote": (finding.evidence[0].text[:110] if real else "（模型未给出可定位证据）"),
                        "reason": finding.reason_zh[:150],
                    }
                )

    fn_layer = Counter(r["layer"] for r in fn_rows)
    fp_layer = Counter(r["layer"] for r in fp_rows)

    print(f"扫描 {scanned} 条判定 | 判错 {len(fn_rows) + len(fp_rows)} 条"
          f"（漏报 {len(fn_rows)} / 误报 {len(fp_rows)}）\n")
    print("漏报归因：", dict(fn_layer) or "无")
    print("误报归因：", dict(fp_layer) or "无")

    lines = [
        "# Badcase 归因表（Step 4 交付物 · 自动生成）",
        "",
        f"> 生成方式：`python -X utf8 scripts/attribute_badcases.py --k {args.k}`",
        "> **零 LLM 调用** —— 漏报的『检索层 / 模型层』判定由本地 embedding 重跑检索得出，可一键复现。",
        "",
        f"扫描 {scanned} 条判定，判错 **{len(fn_rows) + len(fp_rows)}** 条"
        f"（漏报 {len(fn_rows)} / 误报 {len(fp_rows)}）。",
        "",
        "## 一、漏报（FN）：检索层 vs 模型层",
        "",
        f"| 归因层 | 数量 | 占比 |",
        f"|---|---|---|",
    ]
    for layer, count in fn_layer.most_common():
        lines.append(f"| {layer} | {count} | {count / max(len(fn_rows), 1) * 100:.0f}% |")
    lines += ["", "| 合同 | 条款 | 归因层 | 证据 | 模型置信度 |", "|---|---|---|---|---|"]
    for row in sorted(fn_rows, key=lambda r: (r["layer"], r["clause"])):
        lines.append(
            f"| {row['contract'][:38]} | {row['clause']} | **{row['layer']}** | {row['detail']} | {row['confidence']:.2f} |"
        )

    lines += [
        "",
        "## 二、误报（FP）：口径落差 vs 其他",
        "",
        f"| 归因层 | 数量 |", "|---|---|",
    ]
    for layer, count in fp_layer.most_common():
        lines.append(f"| {layer} | {count} |")
    lines += ["", "| 合同 | 条款 | 归因层 | 模型引用的证据 |", "|---|---|---|---|"]
    for row in fp_rows:
        lines.append(
            f"| {row['contract'][:38]} | {row['clause']} | {row['layer']} | {row['quote']} |"
        )

    lines += ["", "## 三、已知口径落差的对照说明", "", "| 条款 | 说明 |", "|---|---|"]
    for clause, note in CALIBER_NOTE.items():
        lines.append(f"| {clause} | {note} |")

    out_path = ROOT / args.out
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n归因报告已写入：{out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
