"""
Embedding 测速（Step 3.2）：先跑一小批，再外推全量时间 —— 不猜数字

为什么必须先测速：
    bge-m3 约 5.7 亿参数（2.19 GB），而 MVP 原定的 bge-small-en 只有 3300 万参数（差约 17 倍）。
    你的 torch 是 CPU 版，所以"全量 13,257 个 chunk 要多久"必须实测，不能凭感觉。

用法（在 hetong/ 目录下运行）：
    python -X utf8 scripts\\bench_embedding.py          # 默认测 100 条
    python -X utf8 scripts\\bench_embedding.py 300      # 测 300 条
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from text_utils import read_jsonl  # noqa: E402


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


def main() -> None:
    sample_size = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    env = load_env(ROOT / ".env")

    import torch

    device_setting = env.get("EMBED_DEVICE", "auto").lower()
    if device_setting == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    else:
        device = device_setting
    gpu_name = torch.cuda.get_device_name(0) if device == "cuda" else "未检测到可用 CUDA"
    print(f"推理设备: {device}（{gpu_name}）")
    print(f"torch  : {torch.__version__}")

    model_path = env.get("EMBED_MODEL_PATH")
    if not model_path:
        print("❌ .env 里缺少 EMBED_MODEL_PATH")
        return
    model_dir = Path(model_path)
    print(f"模型目录: {model_dir}")
    print(f"目录存在: {model_dir.exists()}   大小: "
          f"{sum(f.stat().st_size for f in model_dir.rglob('*') if f.is_file()) / 1024 / 1024:.0f} MB")

    chunks_path = ROOT / "data" / "processed" / "chunks_main_eval.jsonl"
    chunks = read_jsonl(chunks_path)
    total_chunks = len(chunks)
    texts = [c["text"] for c in chunks[:sample_size]]
    print(f"待嵌入总数: {total_chunks} 个 chunk；本次测速取样 {len(texts)} 个")

    from sentence_transformers import SentenceTransformer

    try:  # sentence-transformers 6.x 的新路径
        from sentence_transformers.sentence_transformer.modules import Pooling, Transformer
    except ImportError:  # 兼容旧版本
        from sentence_transformers.models import Pooling, Transformer

    # ⚠️ 关键细节：BGE 系列必须用 CLS pooling（取 [CLS] 位置的向量）+ 归一化。
    # 本地模型目录里既没有 1_Pooling/config.json，也没有 sentence_bert_config.json，
    # 若直接 SentenceTransformer(目录)，它会默认用 mean pooling（对所有 token 求平均）——
    # 不报任何错，但检索质量会悄悄下降。所以这里显式指定 CLS pooling。
    t0 = time.perf_counter()
    word = Transformer(str(model_dir))
    pool = Pooling(word.get_embedding_dimension(), pooling_mode="cls")
    model = SentenceTransformer(modules=[word, pool], device=device)
    load_secs = time.perf_counter() - t0
    print(f"\n模型加载耗时   : {load_secs:.1f} 秒")
    print(f"向量维度       : {model.get_embedding_dimension()}")
    print(f"最大序列长度   : {model.max_seq_length} token（我们的 chunk 约 250 token，远未触顶）")
    print(f"Pooling 模式   : {model[1].pooling_mode}（必须是 cls，否则 bge-m3 的效果会明显变差）")

    avg_chars = sum(len(t) for t in texts) / len(texts)
    print(f"本批平均长度   : {avg_chars:.0f} 字符/chunk")

    # 预热（第一次调用含初始化开销，不计入测速）
    model.encode(texts[:2], normalize_embeddings=True, show_progress_bar=False)

    probe = texts[:60]
    print("\n---- batch_size 调优（同一批 60 条，CPU 上批大小对吞吐影响很大）----")
    best: tuple[int, float] | None = None
    for batch_size in (8, 16, 32, 64):
        t0 = time.perf_counter()
        model.encode(probe, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)
        secs = time.perf_counter() - t0
        rate = len(probe) / secs
        print(f"  batch={batch_size:3d}: {secs:5.1f} 秒 → {rate:5.1f} 条/秒 → 全量约 {total_chunks / rate / 60:5.1f} 分钟")
        if best is None or rate > best[1]:
            best = (batch_size, rate)

    assert best is not None
    print(f"\n最快配置       : batch_size={best[0]}（{best[1]:.1f} 条/秒）")
    print(f"全量外推       : {total_chunks} 条 ≈ {total_chunks / best[1] / 60:.1f} 分钟")

    vectors = model.encode(probe, batch_size=best[0], normalize_embeddings=True, show_progress_bar=False)
    print(f"向量矩阵形状   : {vectors.shape}")

    t0 = time.perf_counter()
    model.encode(["What is the governing law of this agreement?"], normalize_embeddings=True)
    print(f"单条 query 编码: {time.perf_counter() - t0:.3f} 秒（这是每次检索的固定开销）")


if __name__ == "__main__":
    main()
