"""
Step 2.5：切块（chunking）+ 金标准证据 → chunk 的覆盖映射

策略（方案 B：段落为主 + 超长二级切）
1. 按空行切段落
2. 贪心合并相邻段落，直到再加一段就超过 CHUNK_MAX_CHARS
   （段落 p50 只有 137 字符，若一段一 chunk，检索粒度会碎到没有上下文）
3. 单段自身就超过 CHUNK_MAX_CHARS → 二级切：按窗口滑动 + overlap，并**尽量在句子边界断开**
4. 金标准证据 → 记录它被哪些 chunk **完整包含**（Step 4 算 context recall 用）

参数来源：scripts/analyze_chunking.py 的实测分布
    - 证据 p95 = 849、p99 = 1388 → chunk 上限取 1200 可装下 98.1% 的证据
    - 句子 p50 = 118、p90 = 365 → overlap 至少要 ≈ 句子 p50

用法（在 hetong/ 目录下运行）：
    python -X utf8 scripts\\build_chunks.py            # 用默认参数
    python -X utf8 scripts\\build_chunks.py 1200 60     # 覆盖参数做对比实验
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from text_utils import load_txt_index, lookup, read_text  # noqa: E402

DATA = ROOT / "data" / "cuad"
TXT_DIR = DATA / "full_contract_txt"
PROCESSED = ROOT / "data" / "processed"

CHUNK_MAX_CHARS = int(sys.argv[1]) if len(sys.argv) > 2 else 1200
OVERLAP = int(sys.argv[2]) if len(sys.argv) > 2 else 200

SENTENCE_TAIL_RATIO = 0.6  # 二级切分时，只在窗口后 40% 范围内找句子边界


# ---------------------------------------------------------------- 段落与切分


def para_spans(text: str) -> list[tuple[int, int]]:
    """按空行切段落，返回每段的 (start, end)"""
    spans: list[tuple[int, int]] = []
    cursor = 0
    for m in re.finditer(r"\n\s*\n+", text):
        if text[cursor:m.start()].strip():
            spans.append((cursor, m.start()))
        cursor = m.end()
    if text[cursor:].strip():
        spans.append((cursor, len(text)))
    return spans


def sentence_cut(text: str, start: int, end: int) -> int | None:
    """在 [start, end] 窗口的后段找最后一个句末标点，返回切点（找不到返回 None）"""
    low = start + int((end - start) * SENTENCE_TAIL_RATIO)
    last = None
    for m in re.finditer(r"[.;:]\s", text[low:end]):
        last = m
    return None if last is None else low + last.end()


def split_oversized(start: int, end: int, text: str) -> list[tuple[int, int]]:
    """超长段落的二级切分：窗口滑动 + overlap，尽量切在句子边界"""
    windows: list[tuple[int, int]] = []
    pos = start
    while pos < end:
        stop = min(pos + CHUNK_MAX_CHARS, end)
        if stop < end:
            cut = sentence_cut(text, pos, stop)
            if cut is not None:
                stop = cut
        windows.append((pos, stop))
        if stop >= end:
            break
        pos = max(stop - OVERLAP, pos + 1)
    return windows


def build_chunk_spans(text: str) -> list[tuple[int, int, int, bool]]:
    """
    返回 [(char_start, char_end, n_paragraphs, is_oversized_split), ...]

    规则：先合并相邻短段落，超长段落单独二级切分。
    """
    result: list[tuple[int, int, int, bool]] = []
    buffer_start: int | None = None
    buffer_end: int | None = None
    buffer_paras = 0

    def flush() -> None:
        nonlocal buffer_start, buffer_end, buffer_paras
        if buffer_start is not None and buffer_end is not None:
            result.append((buffer_start, buffer_end, buffer_paras, False))
        buffer_start = buffer_end = None
        buffer_paras = 0

    for start, end in para_spans(text):
        length = end - start
        if length > CHUNK_MAX_CHARS:
            flush()
            for w_start, w_end in split_oversized(start, end, text):
                result.append((w_start, w_end, 1, True))
            continue

        if buffer_start is None:
            buffer_start, buffer_end, buffer_paras = start, end, 1
        elif end - buffer_start <= CHUNK_MAX_CHARS:
            buffer_end = end
            buffer_paras += 1
        else:
            flush()
            buffer_start, buffer_end, buffer_paras = start, end, 1

    flush()
    return result


# ---------------------------------------------------------------- 覆盖映射


def covering_chunks(span: tuple[int, int], chunks: list[dict]) -> list[str]:
    """返回**完整包含**该证据区间的 chunk_id 列表（部分重叠不算）"""
    start, end = span
    return [
        c["chunk_id"]
        for c in chunks
        if c["char_start"] <= start and end <= c["char_end"]
    ]


def best_coverage(span: tuple[int, int], chunks: list[dict]) -> float:
    """
    该证据被"单个 chunk 覆盖得最好的比例"（0~1）。

    为什么不用二元判断：一段证据如果被切成两半、分别落在相邻两个 chunk 里，
    「完整包含」= 0，但实际上检索到两个 chunk 时信息是齐的。
    所以要看"最好的单块覆盖率"，它更能反映切块粒度是否合适。
    """
    start, end = span
    length = max(end - start, 1)
    best = 0.0
    for c in chunks:
        overlap = min(end, c["char_end"]) - max(start, c["char_start"])
        if overlap > 0:
            best = max(best, overlap / length)
    return best


# ---------------------------------------------------------------- 主流程


def process(golden_path: Path, out_chunks: Path, out_map: Path, tag: str) -> dict:
    idx = load_txt_index(TXT_DIR)
    records = [json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    all_chunks: list[dict] = []
    gold_rows: list[dict] = []
    stats = {
        "contracts": len(records),
        "chunks": 0,
        "oversized_chunks": 0,
        "evidence_total": 0,
        "evidence_single_chunk": 0,
        "evidence_multi_chunk": 0,
        "evidence_no_chunk": 0,
        "evidence_in_oversized_paragraph": 0,
        "cov_full": 0,
        "cov_ge80": 0,
        "cov_ge50": 0,
        "cov_lt50": 0,
        "chunk_chars": [],
    }

    for record in records:
        path = lookup(idx, record["contract_id"])
        text = read_text(path)
        spans = build_chunk_spans(text)
        chunks = []
        for i, (start, end, n_paras, oversized) in enumerate(spans):
            chunks.append(
                {
                    "chunk_id": f"{record['contract_id']}::{i:04d}",
                    "contract_id": record["contract_id"],
                    "split": record["split"],
                    "chunk_index": i,
                    "text": text[start:end],
                    "char_start": start,
                    "char_end": end,
                    "n_chars": end - start,
                    "n_paragraphs": n_paras,
                    "is_oversized_split": oversized,
                }
            )
        all_chunks.extend(chunks)
        stats["chunks"] += len(chunks)
        stats["oversized_chunks"] += sum(1 for c in chunks if c["is_oversized_split"])
        stats["chunk_chars"].extend(c["n_chars"] for c in chunks)

        for clause_id, label in record["labels"].items():
            for ev_index, ev in enumerate(label["evidence"]):
                if ev["char_start"] is None:
                    continue
                stats["evidence_total"] += 1
                span = (ev["char_start"], ev["char_end"])
                ids = covering_chunks(span, chunks)
                if len(ids) == 1:
                    stats["evidence_single_chunk"] += 1
                elif len(ids) > 1:
                    stats["evidence_multi_chunk"] += 1
                else:
                    stats["evidence_no_chunk"] += 1

                ratio = best_coverage(span, chunks)
                if ratio >= 0.999:
                    stats["cov_full"] += 1
                elif ratio >= 0.8:
                    stats["cov_ge80"] += 1
                elif ratio >= 0.5:
                    stats["cov_ge50"] += 1
                else:
                    stats["cov_lt50"] += 1
                gold_rows.append(
                    {
                        "contract_id": record["contract_id"],
                        "clause_id": clause_id,
                        "evidence_index": ev_index,
                        "char_start": ev["char_start"],
                        "char_end": ev["char_end"],
                        "n_chars": ev["char_end"] - ev["char_start"],
                        "n_covering_chunks": len(ids),
                        "covering_chunk_ids": ids,
                    }
                )

    out_chunks.parent.mkdir(parents=True, exist_ok=True)
    with out_chunks.open("w", encoding="utf-8") as fh:
        for chunk in all_chunks:
            fh.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    with out_map.open("w", encoding="utf-8") as fh:
        for row in gold_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\n========== {tag}（chunk 上限 {CHUNK_MAX_CHARS} / overlap {OVERLAP}）==========")
    print(f"合同数            : {stats['contracts']}")
    print(f"chunk 总数        : {stats['chunks']}  （平均每份 {stats['chunks'] / stats['contracts']:.1f} 个）")
    print(f"  其中超长二级切分 : {stats['oversized_chunks']}")
    print(f"金标准证据数      : {stats['evidence_total']}")
    total = max(stats["evidence_total"], 1)
    print(f"  ✅ 被单个 chunk 完整包含 : {stats['evidence_single_chunk']:5d} ({stats['evidence_single_chunk'] / total * 100:5.1f}%)")
    print(f"  ⚠️ 跨多个 chunk（含重叠）: {stats['evidence_multi_chunk']:5d} ({stats['evidence_multi_chunk'] / total * 100:5.1f}%)")
    print(f"  ❌ 完全未被包含          : {stats['evidence_no_chunk']:5d} ({stats['evidence_no_chunk'] / total * 100:5.1f}%)")
    print("  最好单块覆盖率分布：")
    print(f"      =100%   : {stats['cov_full']:5d} ({stats['cov_full'] / total * 100:5.1f}%)")
    print(f"      ≥80%    : {stats['cov_ge80']:5d} ({stats['cov_ge80'] / total * 100:5.1f}%)")
    print(f"      ≥50%    : {stats['cov_ge50']:5d} ({stats['cov_ge50'] / total * 100:5.1f}%)")
    print(f"      <50%    : {stats['cov_lt50']:5d} ({stats['cov_lt50'] / total * 100:5.1f}%)")
    chars = sorted(stats["chunk_chars"])
    print(f"  chunk 长度        : p50 {chars[len(chars) // 2]} | p90 {chars[int(len(chars) * 0.9)]} | max {chars[-1]}")
    stats["chunk_chars"] = "见上"
    return stats


def main() -> None:
    main_stats = process(
        ROOT / "eval" / "golden_eval.jsonl",
        PROCESSED / "chunks_main_eval.jsonl",
        PROCESSED / "chunk_gold_map_main_eval.jsonl",
        "主评测集",
    )
    robustness_stats = process(
        PROCESSED / "robustness_set.jsonl",
        PROCESSED / "chunks_robustness.jsonl",
        PROCESSED / "chunk_gold_map_robustness.jsonl",
        "鲁棒性集",
    )

    (PROCESSED / "chunking_summary.json").write_text(
        json.dumps(
            {
                "params": {"chunk_max_chars": CHUNK_MAX_CHARS, "overlap": OVERLAP},
                "main_eval": main_stats,
                "robustness": robustness_stats,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
