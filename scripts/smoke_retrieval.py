"""
Step 3.4 第二步：检索层小型实测（recall@k）

目的：在写 LLM 管线之前，先确认「检索到底能不能找到金标准证据」——
      因为**召回是本项目第一指标**，检索找不到，后面 LLM 再强也只能答"不存在"。

做法：
1. 从 `data/processed/chunk_gold_map_main_eval.jsonl` 取「有金标准 chunk」的样本（每个条款 3 条，共约 30 条）
2. 用 `clauses.py` 的 `query_text` 检索（**不用条款名**），限定同一合同内
3. 一次性取 top-10，然后同时算 recall@3 / @5 / @10（省掉三次检索）
4. 对"没命中"的样本打印 top-5 诊断信息

用法（在 hetong/ 目录下运行）：
    C:\\Users\\Admin\\.conda\\envs\\langchain1.2\\python.exe -X utf8 scripts\\smoke_retrieval.py
"""

import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clauses import BY_ID  # noqa: E402
from retriever import Retriever  # noqa: E402
from schemas import ClauseId  # noqa: E402
from text_utils import read_jsonl  # noqa: E402

TAKE_PER_CLAUSE = 3
TOP_K = 10  # 一次取 10，随后算 @3/@5/@10

# 查询构造的两种模式（用来验证"中文描述是否稀释了查询"这个假设）
#   full       : 中文定义 + 英文信号词（clauses.py 里的 query_text 原文）
#   signals_en : 只用英文条款名 + 英文信号词
MODE = sys.argv[1] if len(sys.argv) > 1 else "full"


def build_query(spec, mode: str) -> str:
    if mode == "signals_en":
        parts = spec.query_text.split("常见表述：")
        if len(parts) == 2:
            return f"{spec.name_en} clause: {parts[1]}"
    return spec.query_text


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def main() -> None:
    env = load_env(ROOT / ".env")
    gold_map = read_jsonl(ROOT / "data" / "processed" / "chunk_gold_map_main_eval.jsonl")

    # 分层取样：每个条款取前 3 条「有金标准 chunk」的样本
    by_clause: dict[str, list[dict]] = defaultdict(list)
    for row in gold_map:
        if row["covering_chunk_ids"]:
            by_clause[row["clause_id"]].append(row)
    sample = [row for cid in sorted(by_clause) for row in by_clause[cid][:TAKE_PER_CLAUSE]]

    retriever = Retriever(
        chroma_dir=ROOT / env.get("CHROMA_DIR", "data/chroma_bge_m3"),
        model_dir=Path(env["EMBED_MODEL_PATH"]),
        device=env.get("EMBED_DEVICE", "auto"),
    )
    print(f"检索设备: {retriever.device} | 样本数: {len(sample)}（每条款最多 {TAKE_PER_CLAUSE} 条）")
    print(f"查询模式: {MODE}（full=中文+英文信号词；signals_en=纯英文信号词）\n")

    hits_at = {3: 0, 5: 0, 10: 0}
    ranks: list[int] = []
    per_clause = defaultdict(lambda: {"n": 0, "hit5": 0})
    misses: list[tuple[dict, list[dict]]] = []

    for row in sample:
        spec = BY_ID[ClauseId(row["clause_id"])]
        hits = retriever.search(row["contract_id"], build_query(spec, MODE), k=TOP_K)
        gold_ids = set(row["covering_chunk_ids"])

        rank = next((i for i, h in enumerate(hits, 1) if h["chunk_id"] in gold_ids), None)
        if rank:
            ranks.append(rank)
        for k in hits_at:
            if rank and rank <= k:
                hits_at[k] += 1

        stat = per_clause[row["clause_id"]]
        stat["n"] += 1
        if rank and rank <= 5:
            stat["hit5"] += 1
        else:
            misses.append((row, hits))

    n = len(sample)
    print("=" * 70)
    print("检索召回（金标准证据的 chunk 是否进了 top-k）")
    print("=" * 70)
    for k in (3, 5, 10):
        print(f"  recall@{k:<2d}: {hits_at[k]:3d}/{n} = {hits_at[k] / n * 100:5.1f}%")
    if ranks:
        print(f"  命中位置分布: 平均 rank {sum(ranks) / len(ranks):.1f} | 中位 {sorted(ranks)[len(ranks) // 2]} | 最好 1 | 最差 {max(ranks)}")

    print("\n按条款（recall@5）：")
    for cid in sorted(per_clause):
        stat = per_clause[cid]
        print(f"  {cid:24s} {stat['hit5']}/{stat['n']}")

    if misses:
        print(f"\n---- 未进 top-5 的样本诊断（共 {len(misses)} 个，全部列出）----")
        for row, hits in misses:
            spec = BY_ID[ClauseId(row["clause_id"])]
            print(f"\n[{row['clause_id']}] {row['contract_id'][:52]}")
            print(f"  金标准 chunk: {row['covering_chunk_ids'][:1]} （共 {row['n_covering_chunks']} 个，证据字符位置 {row['char_start']}）")
            print(f"  查询前 70 字: {spec.query_text[:70]}…")
            print("  top-3 检索结果：")
            for i, h in enumerate(hits[:3], 1):
                snippet = h["text"][:62].replace(chr(10), " ")
                print(f"    {i}. idx={h['chunk_index']:>4} d={h['distance']:.4f} {snippet!r}")


if __name__ == "__main__":
    main()
