"""
Step 3.4 CLI：审查一份合同（先 CLI 跑通，Step 3.5 再包一层 FastAPI）

用法（在 hetong/ 目录下运行）：
    python -X utf8 cli.py --list                              # 列出主评测集里的合同
    python -X utf8 cli.py "<contract_id>"                     # 完整审查 10 类条款
    python -X utf8 cli.py "<contract_id>" --clauses 08,09     # 只跑指定条款（调试用）
    python -X utf8 cli.py "<contract_id>" --k 5 --show-evidence
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config import (  # noqa: E402
    EVAL_DIR,
    REPORTS_DIR,
    TXT_DIR,
    chroma_dir,
    embed_device,
    embed_model_dir,
    llm_model,
    safe_filename,
)
from pipeline import review_contract  # noqa: E402
from retriever import Retriever  # noqa: E402
from schemas import ClauseId, ReaderStance, RiskLevel  # noqa: E402
from text_utils import load_txt_index, lookup, read_jsonl, read_text  # noqa: E402

RISK_MARK = {
    RiskLevel.HIGH: "🔴 高",
    RiskLevel.MEDIUM: "🟡 中",
    RiskLevel.LOW: "🟢 低",
    RiskLevel.NA: "—  na",
}


def parse_clause_filter(raw: str | None) -> set[ClauseId] | None:
    if not raw:
        return None
    wanted: set[ClauseId] = set()
    for token in raw.split(","):
        token = token.strip()
        for cid in ClauseId:
            if cid.value.startswith(token) or cid.value == token:
                wanted.add(cid)
    if not wanted:
        raise SystemExit(f"❌ --clauses 没匹配到任何条款：{raw}")
    return wanted


def print_report(report, show_evidence: bool) -> None:
    line = "=" * 78
    print(f"\n{line}\n审查报告\n{line}")
    print(f"合同 ID : {report.contract_id}")
    print(f"文件名  : {report.file_name}")
    print(f"立场    : {report.reader_stance.value}（MVP 固定客户/买方视角）")
    print(f"摘要    : {report.summary_zh}\n")

    print(f"{'条款':26s} {'存在':4s} {'风险':6s} {'置信度':7s} {'证据':4s} 缺口")
    print("-" * 78)
    for finding in report.findings:
        gaps = "；".join(finding.gaps[:1])
        print(
            f"{finding.clause_id.value:26s} {'✅' if finding.exists else '❌':4s} "
            f"{RISK_MARK[finding.risk_level]:6s} {finding.confidence:7.2f} {len(finding.evidence):4d} {gaps[:28]}"
        )

    if report.missing_required_clauses:
        print("\n⚠️ 缺失的必备条款：" + "、".join(c.value for c in report.missing_required_clauses))
    else:
        print("\n✅ 必备条款（01 / 08 / 10）均已检出")

    errors = [f for f in report.findings if f.reason_zh.startswith("[PIPELINE_ERROR]")]
    if errors:
        print(f"\n❗ {len(errors)} 类条款因模型输出未通过校验被标记为 PIPELINE_ERROR（评测时应剔除）：")
        for f in errors:
            print(f"   {f.clause_id.value}: {f.reason_zh[:100]}")

    if show_evidence:
        print(f"\n{line}\n证据片段\n{line}")
        for finding in report.findings:
            for ev in finding.evidence:
                print(f"[{finding.clause_id.value}] 位置 {ev.char_start}-{ev.char_end}")
                print(f"    {ev.text[:220]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="合同风险条款审查（CLI）")
    parser.add_argument("contract_id", nargs="?", help="合同 ID（见 --list）")
    parser.add_argument("--list", action="store_true", help="列出主评测集里的合同")
    parser.add_argument("--clauses", help="只跑指定条款编号，如 08,09（调试用，不生成完整报告）")
    parser.add_argument("--k", type=int, default=None, help="检索 top-k，默认取 .env 的 TOP_K")
    parser.add_argument("--show-evidence", action="store_true", help="打印证据片段原文")
    parser.add_argument("--no-rerank", action="store_true", help="强制关闭重排（纯向量检索），便于对比调试")
    args = parser.parse_args()

    records = {r["contract_id"]: r for r in read_jsonl(EVAL_DIR / "golden_eval.jsonl")}

    if args.list or not args.contract_id:
        print(f"主评测集共 {len(records)} 份合同（前 20 份）：")
        for cid in list(records)[:20]:
            print(f"  {cid}")
        print("\n用法示例：python -X utf8 cli.py \"%s\" --show-evidence" % next(iter(records)))
        return

    record = records.get(args.contract_id)
    if record is None:
        raise SystemExit(f"❌ 主评测集里没有这份合同：{args.contract_id}\n（用 --list 查看可用 ID）")

    txt_index = load_txt_index(TXT_DIR)
    path = lookup(txt_index, args.contract_id)
    if path is None:
        raise SystemExit(f"❌ 找不到纯文本文件：{args.contract_id}")
    text = read_text(path)

    only = parse_clause_filter(args.clauses)
    model = llm_model()
    print(f"合同    : {args.contract_id}")
    print(f"文本长度: {len(text):,} 字符")
    print(f"模型    : {model} | top-k: {args.k or '默认'} | 检索范围: 本合同内\n")

    retriever = Retriever(chroma_dir=chroma_dir(), model_dir=embed_model_dir(), device=embed_device())
    from pipeline import default_client

    client = default_client()

    started = time.perf_counter()
    try:
        report, runs = review_contract(
            contract_id=args.contract_id,
            file_name=path.name,
            retriever=retriever,
            client=client,
            model=model,
            stance=ReaderStance.CUSTOMER,
            k=args.k,
            only=only,
            use_rerank=False if args.no_rerank else None,
        )
    except NotImplementedError as exc:
        print(f"\n{'!' * 78}")
        print("⏸ 管线跑到「证据定位」这一步停下了 —— 因为那里还留着一处填空没补。")
        print(f"   具体位置：pipeline.py 的 locate_quote()")
        print(f"   提示内容都写在那个函数的 docstring 里（三步：精确匹配 → 忽略空白定位 → 返回失败值）。")
        print(f"   原始报错：{exc}")
        print(f"{'!' * 78}")
        return

    total = time.perf_counter() - started
    if report is None:
        print(f"\n（只跑了 {len(runs)} 类条款，未生成完整报告）  总用时 {total:.1f} 秒")
        for run in runs:
            f = run.finding
            print(f"\n[{f.clause_id.value}] exists={f.exists} risk={f.risk_level.value} conf={f.confidence}")
            print(f"  理由: {f.reason_zh}")
            for ev in f.evidence:
                print(f"  证据 {ev.char_start}-{ev.char_end}: {ev.text[:120]}")
        return

    print_report(report, args.show_evidence)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"{safe_filename(args.contract_id)}.json"
    out_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    calls = len(runs)
    print(f"\n总用时 {total:.1f} 秒（{calls} 次 LLM 调用，平均 {total / max(calls, 1):.1f} 秒/次）")
    print(f"报告已保存：{out_path.relative_to(ROOT)}")
    print(f"自测口径：198 份 × 10 类 = 1,980 次调用，按本次平均耗时估计全量评测约 {total / max(calls, 1) * 1980 / 60:.0f} 分钟（串行）")


if __name__ == "__main__":
    main()
