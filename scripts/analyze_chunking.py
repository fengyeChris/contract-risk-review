"""
Step 2.5 前置分析：切块参数（chunk 上限 / overlap）的数据依据

回答三个问题：
1. 合同的段落有多长？→ 决定"超长段落"的兜底上限设多少
2. 金标准证据片段有多长？→ chunk 上限必须能容下证据，否则「context recall」无从谈起
3. 金标准证据会跨段落吗？→ 如果会，纯段落切块会把证据拦腰切断

用法（在 hetong/ 目录下运行）：
    C:\\Users\\Admin\\.conda\\envs\\langchain1.2\\python.exe -X utf8 scripts\\analyze_chunking.py
"""

import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from text_utils import load_txt_index, lookup, read_text  # noqa: E402

DATA = ROOT / "data" / "cuad"
TXT_DIR = DATA / "full_contract_txt"
GOLDEN = ROOT / "eval" / "golden_eval.jsonl"


# ---------------------------------------------------------------- 基础工具


def para_spans(text: str) -> list[tuple[int, int]]:
    """按空行切段落，返回每段的 (start, end) 字符区间"""
    spans: list[tuple[int, int]] = []
    cursor = 0
    for m in re.finditer(r"\n\s*\n+", text):
        if text[cursor:m.start()].strip():
            spans.append((cursor, m.start()))
        cursor = m.end()
    if text[cursor:].strip():
        spans.append((cursor, len(text)))
    return spans


def sentences(text: str) -> list[str]:
    """粗切句子：按 . ; : 后跟空白切分（够用即可，不需要完美）"""
    return [s for s in re.split(r"(?<=[.;:])\s+", text) if s.strip()]


def describe(name: str, values: list[int]) -> dict:
    """打印一组长度的分布统计"""
    if not values:
        print(f"{name}: 无数据")
        return {}
    values = sorted(values)

    def q(p: float) -> int:
        return values[min(int(len(values) * p), len(values) - 1)]

    stats = {
        "count": len(values),
        "mean": int(statistics.mean(values)),
        "p50": q(0.50),
        "p90": q(0.90),
        "p95": q(0.95),
        "p99": q(0.99),
        "max": values[-1],
    }
    print(
        f"{name}: 数量 {stats['count']:6d} | 均值 {stats['mean']:6d} | "
        f"p50 {stats['p50']:6d} | p90 {stats['p90']:6d} | p95 {stats['p95']:6d} | "
        f"p99 {stats['p99']:6d} | max {stats['max']:7d}"
    )
    return stats


def bucket_report(name: str, values: list[int], edges: list[int]) -> None:
    """按阈值统计"超过某长度的比例"——用来选 chunk 上限"""
    print(f"\n{name} 超过阈值的比例：")
    for edge in edges:
        n = sum(1 for v in values if v > edge)
        print(f"  > {edge:5d} 字符: {n:6d} 条 ({n / len(values) * 100:5.1f}%)")


# ---------------------------------------------------------------- 主流程


def main() -> None:
    idx = load_txt_index(TXT_DIR)
    records = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"主评测集合同数: {len(records)}\n")

    all_paras: list[int] = []
    all_sents: list[int] = []
    long_para_examples: list[tuple[int, str]] = []

    for record in records:
        path = lookup(idx, record["contract_id"])
        text = read_text(path)
        for start, end in para_spans(text):
            length = end - start
            all_paras.append(length)
            if length > 1500:
                long_para_examples.append((length, text[start:start + 60].replace("\n", " ")))
        for sentence in sentences(text):
            all_sents.append(len(sentence))

    print("=" * 78)
    print("1) 段落长度分布（198 份主评测集合同，按空行切分）")
    print("=" * 78)
    para_stats = describe("段落", all_paras)
    bucket_report("段落", all_paras, [400, 600, 800, 1000, 1200, 1500, 2000, 3000])

    print("\n超长段落示例（前 5 个 >1500 字符）：")
    for length, head in sorted(long_para_examples, reverse=True)[:5]:
        print(f"  {length:6d} 字符 | {head}...")

    print()
    print("=" * 78)
    print("2) 句子长度分布（用于判断 overlap 该多大）")
    print("=" * 78)
    sent_stats = describe("句子", all_sents)

    # ---------------- 金标准证据 ----------------
    ev_lengths: list[int] = []
    span_para_counts: list[int] = []          # 每条证据覆盖了几个段落
    ev_by_clause: dict[str, list[int]] = {}

    for record in records:
        path = lookup(idx, record["contract_id"])
        text = read_text(path)
        spans = para_spans(text)
        for clause_id, label in record["labels"].items():
            for ev in label["evidence"]:
                if ev["locator"] == "unlocatable" or ev["char_start"] is None:
                    continue
                length = len(ev["text"])
                ev_lengths.append(length)
                ev_by_clause.setdefault(clause_id, []).append(length)
                s, e = ev["char_start"], ev["char_end"]
                span_para_counts.append(sum(1 for a, b in spans if a < e and b > s))

    print()
    print("=" * 78)
    print("3) 金标准证据片段长度分布")
    print("=" * 78)
    ev_stats = describe("证据", ev_lengths)
    bucket_report("证据", ev_lengths, [200, 400, 600, 800, 1000, 1200, 1500, 2000])

    print("\n按条款拆开（p50 / p95 / max）：")
    for clause_id in sorted(ev_by_clause):
        values = sorted(ev_by_clause[clause_id])
        p50 = values[len(values) // 2]
        p95 = values[min(int(len(values) * 0.95), len(values) - 1)]
        print(f"  {clause_id:24s} n={len(values):4d} | p50 {p50:5d} | p95 {p95:5d} | max {values[-1]:5d}")

    print()
    print("=" * 78)
    print("4) 金标准证据跨段落情况（决定「纯段落切」是否可行）")
    print("=" * 78)
    from collections import Counter

    counter = Counter(span_para_counts)
    total = len(span_para_counts)
    for k in sorted(counter):
        label = f"跨越 {k} 个段落" if k > 1 else "恰好落在 1 个段落内"
        print(f"  {label:18s}: {counter[k]:5d} 条 ({counter[k] / total * 100:5.1f}%)")

    print()
    print("=" * 78)
    print("5) 数据给出的建议值（供决策，不是自动结论）")
    print("=" * 78)
    max_single = max((l for l, c in zip(ev_lengths, span_para_counts) if c == 1), default=0)
    print(f"  句子 p50 = {sent_stats['p50']} 字符；句子 p90 = {sent_stats['p90']} 字符")
    print(f"  段落 p50 = {para_stats['p50']} 字符；段落 p95 = {para_stats['p95']} 字符")
    print(f"  证据 p50 = {ev_stats['p50']} 字符；证据 p95 = {ev_stats['p95']} 字符；证据 max = {ev_stats['max']} 字符")
    print(f"  恰好落在单个段落内的证据，最长 = {max_single} 字符")
    print()
    print("  含义：")
    print(f"   - chunk 上限至少要 > {ev_stats['p95']}（证据 p95），否则 p95 分位的证据装不进一个 chunk")
    print(f"   - overlap 至少要 ≈ {sent_stats['p50']}（句子 p50），否则边界处会被切断半句话")
    print(f"   - overlap 达到 {sent_stats['p90']}（句子 p90）时，边界处的整句基本都能保住")


if __name__ == "__main__":
    main()
