"""
Step 5.3 前置：**检索层评测（hit@k / MRR）** —— 零 LLM 调用

为什么先做这一步（依据 Step 4 的归因结论）：
    · 31 个判错里，**14 个漏报是"检索层"**（金标准证据压根没被召回），模型错只有 3 个；
    · ⇒ **修检索的收益远大于换模型**，而检索层好坏可以**脱离 LLM 独立度量**（本地 embedding，秒级、零 token）。

度量口径（写死在这里，避免每次算得不一样）：
- **hit@k**：金标准证据段落是否落在检索返回的**前 k 个片段**之内
  （判定标准：存在某个片段的字符区间**完整覆盖**某个金标准区间 —— 与 evidence_hit 同源的口径）
- **MRR**：第一个覆盖金标准的片段的排名倒数之平均（1.0 = 每次都排第一）
- 分母只统计**金标准确实标注了证据**的 case（没有正样本的类无法评估检索）

用法（在 hetong/ 目录下运行）：
    python -X utf8 scripts\\eval_retrieval.py --limit 30      # 30 份（约 10 秒）
    python -X utf8 scripts\\eval_retrieval.py --tag baseline  # 全量 198 份（约 1~2 分钟）

Step 5.3 做增强（提高 top-k / rerank / BM25 混合 / 查询改造）时，一律与本 baseline 对比。
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clauses import BY_ID  # noqa: E402
from config import (  # noqa: E402
    ENV,
    EVAL_DIR,
    chroma_dir,
    embed_device,
    embed_model_dir,
    rerank_candidates,
    rerank_device,
    rerank_max_length,
    rerank_model_dir,
)
from retriever import Retriever  # noqa: E402
from schemas import ClauseId  # noqa: E402
from text_utils import read_jsonl  # noqa: E402

K_LIST = (1, 3, 5, 10, 15, 20)
MAX_K = max(K_LIST)


@dataclass
class Stat:
    n: int = 0
    hit: dict[int, int] = field(default_factory=lambda: {k: 0 for k in K_LIST})
    rr_sum: float = 0.0  # 排名倒数之和（用于 MRR）

    def hit_at(self, k: int) -> float:
        return self.hit[k] / self.n if self.n else float("nan")

    @property
    def mrr(self) -> float:
        return self.rr_sum / self.n if self.n else float("nan")

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            **{f"hit@{k}": (self.hit[k] / self.n if self.n else None) for k in K_LIST},
            "mrr": (self.rr_sum / self.n if self.n else None),
        }


def first_covering_rank(hits: list[dict], gold_spans: list[tuple[int, int]]) -> int | None:
    """返回第一个"完整覆盖某个金标准区间"的片段排名（1-based）；没有则 None"""
    for rank, hit in enumerate(hits, 1):
        for start, end in gold_spans:
            if hit["char_start"] <= start and end <= hit["char_end"]:
                return rank
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="检索层评测：hit@k / MRR（零 LLM 调用）")
    parser.add_argument("--limit", type=int, default=None, help="只评前 N 份合同")
    parser.add_argument("--tag", default="baseline", help="结果文件名标签（Step 5 前后对比用）")
    parser.add_argument("--rerank", action="store_true",
                        help="启用重排：先粗召 RERANK_CANDIDATES 个，再用 cross-encoder 精排（零 LLM）")
    args = parser.parse_args()

    records = read_jsonl(EVAL_DIR / "golden_eval.jsonl")
    if args.limit:
        records = records[: args.limit]

    print("=" * 92)
    print(f"检索层评测 | 合同数 {len(records)} | max_k {MAX_K} | "
          f"chunk={ENV.get('CHUNK_MAX_CHARS')}/{ENV.get('CHUNK_OVERLAP')}")
    print("=" * 92)

    retriever = Retriever(chroma_dir=chroma_dir(), model_dir=embed_model_dir(), device=embed_device())

    reranker = None
    fetch_k = MAX_K
    if args.rerank:
        from reranker import Reranker

        reranker = Reranker(rerank_model_dir(), device=rerank_device(), max_length=rerank_max_length())
        fetch_k = max(rerank_candidates(), MAX_K)
        print(f"重排：粗召 {fetch_k} → bge-reranker-v2-m3 精排（max_length={rerank_max_length()}, "
              f"device={reranker.device}）→ 按精排顺序评估")
        print("      hit@20 应与 baseline 持平（同一集合），关键看 hit@1/3/5 与 MRR 的变化\n")

    overall = Stat()
    per_clause = {cid: Stat() for cid in ClauseId}
    started = time.perf_counter()

    for record in records:
        for cid in ClauseId:
            gold = record.get("labels", {}).get(cid.value, {})
            gold_spans = [(e["char_start"], e["char_end"]) for e in gold.get("evidence", [])]
            if not gold_spans:  # 没有正样本 → 无法评估检索
                continue
            query = BY_ID[cid].retrieval_query
            hits = retriever.search(record["contract_id"], query, k=fetch_k)
            if reranker is not None:
                hits = reranker.rerank(query, hits)
            rank = first_covering_rank(hits, gold_spans)
            for stat in (overall, per_clause[cid]):
                stat.n += 1
                if rank is not None:
                    for k in K_LIST:
                        if rank <= k:
                            stat.hit[k] += 1
                    stat.rr_sum += 1.0 / rank

    elapsed = time.perf_counter() - started
    header = f"{'条款':24s} {'n':>4s}" + "".join(f" {'hit@' + str(k):>8s}" for k in K_LIST) + f" {'MRR':>7s}"
    print(header)
    print("-" * len(header))
    for cid in ClauseId:
        st = per_clause[cid]
        row = f"{cid.value:24s} {st.n:4d}"
        row += "".join(f" {st.hit_at(k):8.3f}" for k in K_LIST)
        row += f" {st.mrr:7.3f}"
        print(row)
    print("-" * len(header))
    row = f"{'总计（宏）':24s} {overall.n:4d}"
    row += "".join(f" {overall.hit_at(k):8.3f}" for k in K_LIST)
    row += f" {overall.mrr:7.3f}"
    print(row)
    print(f"\n用时 {elapsed:.1f} 秒（{overall.n} 次检索，平均 {elapsed / max(overall.n, 1):.2f} 秒/次，零 LLM 调用）")

    summary = {
        "tag": args.tag,
        "config": {
            "contracts": len(records),
            "max_k": MAX_K,
            "chunk_size": ENV.get("CHUNK_MAX_CHARS"),
            "chunk_overlap": ENV.get("CHUNK_OVERLAP"),
            "query_type": "retrieval_query（英文条款名 + 信号词）",
            "rerank": bool(args.rerank),
            "rerank_model": (str(rerank_model_dir()) if args.rerank else None),
            "rerank_candidates": (fetch_k if args.rerank else None),
            "rerank_max_length": (rerank_max_length() if args.rerank else None),
        },
        "overall": overall.as_dict(),
        "per_clause": {cid.value: per_clause[cid].as_dict() for cid in ClauseId},
    }
    out_path = EVAL_DIR / f"retrieval_{args.tag}.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"检索层指标已保存：{out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
