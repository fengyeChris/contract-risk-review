"""
Step 3.3：建向量索引（Chroma 持久化 + 增量构建 + 自检）

输入：data/processed/chunks_<split>.jsonl
输出：data/chroma_bge_m3/（Chroma 持久化目录，已在 .gitignore 中）

流程
1. 读 chunk 文件
2. **增量**：先取 Chroma 里已有的 id，差集算出本次真正需要嵌入的 chunk
3. 用 bge-m3（CLS pooling + 归一化，device 自动）批量嵌入
4. 分批写入 Chroma，metadata 带 contract_id / split / chunk_index / char_start / char_end
5. 自检：① 向量数 == chunk 数；② 抽样查询必须只返回同一份合同的 chunk（验证 metadata 过滤）

已实测的接口（chromadb 1.5.9，本机验证通过）：
    collection.get(include=[])["ids"]                  → 取全部已有 id
    collection.count()                                 → 向量总数
    collection.query(query_embeddings=..., where={...}) → 带 metadata 过滤的检索

Windows 小坑：Chroma 的持久化目录在 client 存活期间被占用，删不掉（WinError 32）；
             所以不要一边跑脚本一边去删 data/chroma_bge_m3。

用法（在 hetong/ 目录下运行）：
    python -X utf8 scripts\\build_index.py main_eval
    python -X utf8 scripts\\build_index.py robustness
"""

import sys
import time
from pathlib import Path

from torch import chunk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from text_utils import read_jsonl  # noqa: E402

COLLECTION = "contract_chunks"
BATCH_SIZE = 8  # 由 scripts/bench_embedding.py 实测得出（GPU 上最快）


def load_env(path: Path) -> dict[str, str]:
    """极简 .env 解析（避免额外依赖）"""
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


from embedding import load_embedder, resolve_device  # noqa: E402


def load_model(model_dir: Path, device: str):
    """加载 bge-m3（统一走 embedding.load_embedder，保证与检索端完全一致）"""
    return load_embedder(model_dir, device=device)


def main() -> None:
    split = sys.argv[1] if len(sys.argv) > 1 else "main_eval"
    env = load_env(ROOT / ".env")
    device = resolve_device(env.get("EMBED_DEVICE", "auto"))

    chunks_path = ROOT / "data" / "processed" / f"chunks_{split}.jsonl"
    if not chunks_path.exists():
        raise SystemExit(f"❌ 找不到 chunk 文件：{chunks_path}")
    chunks = read_jsonl(chunks_path)

    chroma_dir = ROOT / env.get("CHROMA_DIR", "data/chroma_bge_m3")
    chroma_dir.mkdir(parents=True, exist_ok=True)

    import chromadb

    client = chromadb.PersistentClient(path=str(chroma_dir))
    collection = client.get_or_create_collection(COLLECTION)
    print(f"split={split} | device={device} | Chroma={chroma_dir}")
    print(f"chunk 文件共 {len(chunks)} 条")

    existing_ids = set(collection.get(include=[])["ids"])
    # 注意：main_eval 与 robustness **共用同一个 collection**（靠 metadata 区分），所以：
    #   - 全量已有 id → 用于"增量差集"，判断哪些 chunk 不用重嵌
    #   - 本 split 已有多少 → 用于进度显示
    # 这两者不是一回事，混用会让进度显示错乱（早期版本就踩了这个坑）。
    existing_in_split = len(collection.get(where={"split": split}, include=[])["ids"])
    print(f"collection 内已有向量 {len(existing_ids)} 条；其中 split={split} 的 {existing_in_split} 条")

    todo = [chunk for chunk in chunks if chunk["chunk_id"] not in existing_ids]

    # ⬜⬜⬜ 待你补（第 1 处）：算出本次真正要嵌入的 chunk ⬜⬜⬜
    #   要求：结果是 list[dict]，元素来自 chunks，且**保持 chunks 的原始顺序**
    #   输入：chunks（list[dict]，每项有 "chunk_id"）、existing_ids（set[str]）
    #   最小例子（把变量名换成我们的即可）：
    #       wanted   = ['a2', 'a3', 'a4', 'a5']
    #       existing = {'a2', 'a3'}
    #       todo     = [x for x in wanted if x not in existing]     # -> ['a4', 'a5']

    if todo is None:
        raise SystemExit("❌ 请先补上第 1 处的实现：算出 todo（本次要嵌入的 chunk 列表）")

    print(f"本次需要嵌入 {len(todo)} 条；已存在跳过 {len(chunks) - len(todo)} 条")
    if not todo:
        print("✅ 索引已是最新，无需嵌入")
        return

    model = load_model(Path(env["EMBED_MODEL_PATH"]), device)

    started = time.perf_counter()
    done = 0
    for offset in range(0, len(todo), BATCH_SIZE):
        batch = todo[offset:offset + BATCH_SIZE]
        vectors = model.encode(
            [c["text"] for c in batch],
            batch_size=BATCH_SIZE,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        collection.add(
            ids=[c["chunk_id"] for c in batch],
            embeddings=[v.tolist() for v in vectors],
            metadatas=[
                {
                    "contract_id": c["contract_id"],
                    "split": c["split"],
                    "chunk_index": c["chunk_index"],
                    "char_start": c["char_start"],
                    "char_end": c["char_end"],
                }
                for c in batch
            ],
            documents=[c["text"] for c in batch],
        )
        done += len(batch)

        # 进度分两个维度：
        #   ① 本次任务：以"本次要做的量"为分母 → 进度条与 ETA 才有意义（续跑时不会被误导）
        #   ② 本数据集索引完整度：以本 split 的 chunk 总数为分母
        elapsed = time.perf_counter() - started
        rate = done / elapsed if elapsed else 0
        eta = (len(todo) - done) / rate if rate else 0
        print(
            f"  本次 {done}/{len(todo)} ({done / len(todo) * 100:5.1f}%)"
            f" | 本数据集索引 {existing_in_split + done}/{len(chunks)}"
            f" | {rate:5.1f} 条/秒 | 预计还需 {eta / 60:4.1f} 分钟",
            flush=True,
        )

    # ---------------- 自检 ----------------
    print("\n---- 自检 ----")
    # 注意：必须按 split 统计，不能直接用 collection.count()——
    # 主评测集与鲁棒性集共用同一个 collection，全量数会比本数据集总数大。
    total_in_split = len(collection.get(where={"split": split}, include=[])["ids"])
    ok = total_in_split == len(chunks)
    print(f"① split={split} 向量数 {total_in_split} / 预期 {len(chunks)}：{'✅' if ok else '❌'}")
    print(f"   （collection 全量 {collection.count()} 条 —— 多个数据集共用同一 collection）")

    sample = chunks[0]
    query_vector = model.encode([sample["text"]], normalize_embeddings=True)[0].tolist()
    result = collection.query(
        query_embeddings=[query_vector],
        n_results=5,
        where={"contract_id": sample["contract_id"]},
    )
    hit_ids = result["ids"][0]
    same_contract = all(cid.startswith(sample["contract_id"]) for cid in hit_ids)
    print(f"② metadata 过滤：返回 {len(hit_ids)} 条，全部属于同一份合同：{'✅' if same_contract else '❌'}")
    for rank, (cid, distance) in enumerate(zip(hit_ids, result["distances"][0]), start=1):
        flag = "  ← 就是它自己" if cid == sample["chunk_id"] else ""
        print(f"     Top{rank} {cid.split('::')[-1]}  distance={distance:.4f}{flag}")


if __name__ == "__main__":
    main()
