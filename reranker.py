"""
Step 5.3 增强①：重排（rerank）—— 粗召多一点，精排取 top-n

**为什么需要它**（依据 eval/retrieval_baseline.json 的实测）：
    · 当前 hit@5 = 0.766，但 hit@20 = 0.913 → **有 14.7 个百分点的正确证据落在第 6~20 名之间**；
    · 单纯把 top-k 从 5 提到 20 会让 prompt 膨胀 4 倍（20 × 1200 字符 ≈ 6000 tokens）；
    · 正解是 **两段式**：先用向量粗召 20 个 → 再用 cross-encoder 精排 → 只把最优的 5 个喂给 LLM。

**和向量检索的区别**（这是"为什么值得多花一次计算"的答案）：
    · 向量检索是**双塔**：query 和 chunk 各自编码成向量再算距离 —— 快，但**看不到两者的交互**；
    · cross-encoder 把 query 和 chunk **拼在一起**送进模型 —— 慢，但能判断"这段文字是否真的回答了这个问题"，
      所以对 `exclusive`（授权排他）与 `exclusive`（交易排他）这种**同词不同义**的陷阱特别有效。

本模块只负责"打分 + 重排"，不改变检索召回集合（集合由 retriever 决定）。
"""

from pathlib import Path


class Reranker:
    def __init__(self, model_dir: Path | str, device: str = "auto", max_length: int = 512):
        from sentence_transformers import CrossEncoder

        from embedding import resolve_device  # 复用 embedding 那边的设备解析（"auto" → 有 CUDA 就用 CUDA）

        self.device = resolve_device(device)
        self.model = CrossEncoder(str(model_dir), device=self.device, max_length=max_length)
        self.model_dir = str(model_dir)
        self.max_length = max_length

    def rerank(self, query: str, hits: list[dict], top_n: int | None = None,
               batch_size: int = 8, extra_pairs_per_hit: int = 1) -> list[dict]:
        """
        对候选片段重排。

        - 打分用 cross-encoder（query 与 chunk 拼接后过模型），分数越高越相关；
        - 返回**新的列表**（不修改入参），并给每个片段补上：
            `rerank_score`  精排分数
            `retrieve_rank` 粗召时的排名（1-based，便于分析"精排提升了多少"）
        - `top_n=None` 表示全部返回（评测时需要看 6~20 名的情况）
        """
        if not hits:
            return []

        pairs = [(query, hit["text"]) for hit in hits]
        scores = self.model.predict(pairs, batch_size=batch_size, show_progress_bar=False)

        ranked = []
        for hit, score in zip(hits, scores):
            item = dict(hit)
            item["rerank_score"] = float(score)
            ranked.append(item)
        ranked.sort(key=lambda x: x["rerank_score"], reverse=True)
        for new_rank, item in enumerate(ranked, 1):
            item.setdefault("retrieve_rank", None)
        for original_rank, hit in enumerate(hits, 1):
            for item in ranked:
                if item["chunk_id"] == hit["chunk_id"]:
                    item["retrieve_rank"] = original_rank
                    break
        return ranked[:top_n] if top_n else ranked
