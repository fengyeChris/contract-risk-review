"""
Step 2.3 / 2.4：把 CUAD 原始标注整理成本项目的评测数据

输入
----
data/cuad/CUAD_v1.json            510 份合同 × 41 类标注
data/cuad/full_contract_txt/**    510 份纯文本（198 份官方 + 303 份 PDF 转换）
label_mapping.py                  41 类 → 本项目 10 类的映射表

输出
----
eval/golden_eval.jsonl               主评测集（198 份官方文本）—— 进版本库
data/processed/robustness_set.jsonl  鲁棒性集（303 份转换文本）—— 不进版本库
eval/golden_eval_summary.json        各类正/负样本数统计 —— 供 Step 4 报告引用

核心处理
--------
1. 只保留映射表里的 10 类条款，其余类别走 CROSS_REF / BOUNDARY_ONLY / DROPPED
2. is_impossible=True → exists=False（负样本）；否则为 True 并附带证据片段
3. 证据定位分三级：精确 → 忽略空白重定位 → unlocatable
   （unlocatable 的记录**保留**，但 char_start/char_end 为 null，排除出「证据定位准确率」指标）
4. 按单份合同的精确对齐率 ≥90% / ≤10% 区分官方文本与 PDF 转换文本

用法（在 hetong/ 目录下运行）：
    C:\\Users\\Admin\\.conda\\envs\\langchain1.2\\python.exe -X utf8 scripts\\build_dataset.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from label_mapping import BOUNDARY_ONLY, CROSS_REF, DIRECT_MAP  # noqa: E402
from text_utils import find_span_loose, load_txt_index, lookup, read_text, write_jsonl  # noqa: E402

DATA = ROOT / "data" / "cuad"
JSON_PATH = DATA / "CUAD_v1.json"
TXT_DIR = DATA / "full_contract_txt"
EVAL_DIR = ROOT / "eval"
PROCESSED_DIR = ROOT / "data" / "processed"

OFFICIAL_MIN_RATE = 0.9  # 单份合同精确对齐率 ≥ 此值 → 判定为官方原始文本
CONVERTED_MAX_RATE = 0.1  # ≤ 此值 → 判定为 PDF 转换文本

EXACT, LOOSE, UNLOCATABLE = "exact", "loose", "unlocatable"

# §0.6 里记录的主评测集各类正样本数（实测值），用于本次运行的自检
EXPECTED_MAIN_EVAL_POSITIVES = {
    "01_governing_law": 173,
    "02_change_of_control": 49,
    "03_anti_assignment": 147,
    "04_non_compete": 46,
    "05_exclusivity": 68,
    "06_termination_notice": 79,
    "07_renewal": 70,
    "08_cap_on_liability": 106,
    "09_liquidated_damages": 21,
    "10_ip_ownership": 50,
}


# ================================================================
# 证据定位
# ================================================================
def locate_evidence(text: str, answer_text: str, char_start: int) -> tuple[int | None, int | None, str]:
    """
    三级定位，返回 (char_start, char_end, locator)

    1. 精确：直接用标注里的 answer_start
    2. 忽略空白重定位：PDF 转换文本的换行/空格会打乱下标，用 find_span_loose 找回
    3. 都失败 → (None, None, "unlocatable")：记录保留，但不参与定位指标
    """
    if char_start >= 0 and text[char_start:char_start + len(answer_text)] == answer_text:
        return char_start, char_start + len(answer_text), EXACT

    span = find_span_loose(text, answer_text)
    if span is not None:
        return span[0], span[1], LOOSE

    return None, None, UNLOCATABLE


# ================================================================
# 文本类型判定
# ================================================================
def classify_text(text: str, qas: list[dict]) -> tuple[str, float]:
    """返回 (文本类型, 精确对齐率)。类型 ∈ {official_txt, pdf_converted, mixed, too_few_positives}"""
    ok = total = 0
    for qa in qas:
        if qa.get("is_impossible") or not qa["answers"]:
            continue
        answer = qa["answers"][0]
        total += 1
        if text[answer["answer_start"]:answer["answer_start"] + len(answer["text"])] == answer["text"]:
            ok += 1

    if total < 3:
        return "too_few_positives", -1.0
    rate = ok / total
    if rate >= OFFICIAL_MIN_RATE:
        return "official_txt", rate
    if rate <= CONVERTED_MAX_RATE:
        return "pdf_converted", rate
    return "mixed", rate


# ================================================================
# 单条标注 → 证据片段
# ================================================================
def collect_evidence(text: str, qa: dict) -> list[dict]:
    evidence: list[dict] = []
    for answer in qa["answers"]:
        s, e, locator = locate_evidence(text, answer["text"], answer["answer_start"])
        evidence.append({"text": answer["text"], "char_start": s, "char_end": e, "locator": locator})
    return evidence


def build_labels(text: str, qas_by_label: dict[str, dict]) -> dict[str, dict]:
    """按映射表生成 10 类条款的金标准"""
    labels: dict[str, dict] = {}
    for label, clause_id in DIRECT_MAP.items():
        qa = qas_by_label.get(label)
        if qa is None or qa.get("is_impossible") or not qa["answers"]:
            labels[clause_id.value] = {"exists": False, "evidence": []}
        else:
            labels[clause_id.value] = {"exists": True, "evidence": collect_evidence(text, qa)}
    return labels


def build_side_labels(text: str, qas_by_label: dict[str, dict], labels: list[str]) -> dict[str, dict]:
    """生成交叉引用 / 边界样本的辅助标注（不参与 F1，供归因分析）"""
    out: dict[str, dict] = {}
    for label in labels:
        qa = qas_by_label.get(label)
        if qa is None:
            continue
        if qa.get("is_impossible") or not qa["answers"]:
            out[label] = {"exists": False, "evidence": []}
        else:
            out[label] = {"exists": True, "evidence": collect_evidence(text, qa)}
    return out


def build_record(title: str, path: Path, text: str, contract: dict, text_type: str, split: str) -> dict:
    qas = contract["paragraphs"][0]["qas"]
    qas_by_label = {q["id"].split("__")[-1]: q for q in qas}

    record = {
        "contract_id": title,
        "file_path": str(path.relative_to(DATA)).replace("\\", "/"),
        "split": split,
        "text_type": text_type,
        "n_chars": len(text),
        "labels": build_labels(text, qas_by_label),
        "cross_ref": build_side_labels(text, qas_by_label, list(CROSS_REF)),
        "boundary": build_side_labels(text, qas_by_label, list(BOUNDARY_ONLY)),
    }
    return record


# ================================================================
# 统计
# ================================================================
def summarize(records: list[dict]) -> dict:
    summary: dict[str, dict] = {}
    for record in records:
        for clause_id, label in record["labels"].items():
            bucket = summary.setdefault(clause_id, {"positives": 0, "negatives": 0, "unlocatable": 0})
            if label["exists"]:
                bucket["positives"] += 1
                if any(e["locator"] == UNLOCATABLE for e in label["evidence"]):
                    bucket["unlocatable"] += 1
            else:
                bucket["negatives"] += 1
    return dict(sorted(summary.items()))


def print_summary(name: str, summary: dict) -> None:
    print(f"\n---- {name} ----")
    print(f"  {'clause':24s} {'正样本':>7s} {'负样本':>7s} {'定位失败':>9s}")
    for clause_id, s in summary.items():
        print(f"  {clause_id:24s} {s['positives']:7d} {s['negatives']:7d} {s['unlocatable']:9d}")


# ================================================================
# 主流程
# ================================================================
def main() -> None:
    contracts = json.loads(JSON_PATH.read_text(encoding="utf-8"))["data"]
    idx = load_txt_index(TXT_DIR)

    main_eval: list[dict] = []
    robustness: list[dict] = []
    excluded: list[tuple[str, str]] = []

    for contract in contracts:
        title = contract["title"]
        path = lookup(idx, title)
        if path is None:
            excluded.append((title, "缺少纯文本"))
            continue

        text = read_text(path)
        qas = contract["paragraphs"][0]["qas"]
        text_type, rate = classify_text(text, qas)

        if text_type == "official_txt":
            main_eval.append(build_record(title, path, text, contract, text_type, "main_eval"))
        elif text_type == "pdf_converted":
            robustness.append(build_record(title, path, text, contract, text_type, "robustness"))
        else:
            excluded.append((title, f"{text_type}（对齐率 {rate:.2f}）"))

    write_jsonl(EVAL_DIR / "golden_eval.jsonl", main_eval)
    write_jsonl(PROCESSED_DIR / "robustness_set.jsonl", robustness)

    main_summary = summarize(main_eval)
    robustness_summary = summarize(robustness)

    (EVAL_DIR / "golden_eval_summary.json").write_text(
        json.dumps(
            {
                "main_eval": {"n_contracts": len(main_eval), "by_clause": main_summary},
                "robustness": {"n_contracts": len(robustness), "by_clause": robustness_summary},
                "excluded": [{"contract_id": t, "reason": r} for t, r in excluded],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("=" * 64)
    print("构建完成")
    print("=" * 64)
    print(f"主评测集（官方文本）: {len(main_eval)} 份  →  eval/golden_eval.jsonl")
    print(f"鲁棒性集（转换文本）: {len(robustness)} 份  →  data/processed/robustness_set.jsonl")
    print(f"未纳入              : {len(excluded)} 份")
    for title, reason in excluded:
        print(f"    {reason} → {title[:50]}")

    print_summary("主评测集", main_summary)
    print_summary("鲁棒性集", robustness_summary)

    print("\n---- 自检：主评测集正样本数 vs mvp_scope.md §0.6 记录值 ----")
    mismatch = 0
    for clause_id, expected in EXPECTED_MAIN_EVAL_POSITIVES.items():
        actual = main_summary.get(clause_id, {}).get("positives", -1)
        flag = "OK " if actual == expected else "!! "
        if actual != expected:
            mismatch += 1
        print(f"  {flag}{clause_id:24s} 实测 {actual:4d} / 记录 {expected:4d}")
    print(f"\n不一致项: {mismatch}（0 = 与标准文件记录完全一致）")


if __name__ == "__main__":
    main()
