"""
检索层：把「查询文本」变成「top-k chunk」，且**限定在同一份合同内**

三个设计要点（对应 3.4 原理）
1. **查询用「定义 + 信号词」**（来自 `clauses.py`），不用条款名 —— 条款名是标注者的语言
2. **检索范围限定同一份合同**：Chroma metadata 过滤 `where={"contract_id": ...}`，
   否则会召回别的合同，判定直接错
3. **查询向量的编码方式必须与建索引时完全一致**（同一个 `embedding.load_embedder`），
   否则相似度不可比、检索质量静默下降
"""

from pathlib import Path

from embedding import load_embedder


class Retriever:
    def __init__(self, chroma_dir: Path | str, model_dir: Path | str, device: str = "auto",
                 collection_name: str = "contract_chunks") -> None:
        import chromadb

        self.client = chromadb.PersistentClient(path=str(chroma_dir))
        self.collection = self.client.get_or_create_collection(collection_name)
        self.embedder = load_embedder(model_dir, device=device)

    @property
    def device(self) -> str:
        return str(self.embedder.device)

    def search(self, contract_id: str, query: str, k: int = 5) -> list[dict]:
        """在指定合同内检索 top-k chunk，按相似度升序（distance 越小越相似）"""
        vector = self.embedder.encode([query], normalize_embeddings=True, show_progress_bar=False)[0].tolist()
        result = self.collection.query(
            query_embeddings=[vector],
            n_results=k,
            where={"contract_id": contract_id},
        )
        hits: list[dict] = []
        for chunk_id, text, meta, distance in zip(
            result["ids"][0], result["documents"][0], result["metadatas"][0], result["distances"][0]
        ):
            hits.append(
                {
                    "chunk_id": chunk_id,
                    "text": text,
                    "char_start": meta["char_start"],
                    "char_end": meta["char_end"],
                    "chunk_index": meta["chunk_index"],
                    "distance": distance,
                }
            )
        return hits

    def count_in_split(self, split: str) -> int:
        return len(self.collection.get(where={"split": split}, include=[])["ids"])
