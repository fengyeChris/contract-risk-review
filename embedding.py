"""
Embedding 加载器（全项目唯一入口）

为什么必须唯一：
    **查询向量与索引向量必须用完全相同的加载方式**（同样的 pooling、同样的归一化），
    否则相似度不可比 —— 检索结果会莫名其妙变差，而且不报错。
    （我们已经在 `1_Pooling/config.json` 缺失那个坑上吃过一次教训。）

用途：`scripts/build_index.py`（建索引）与 `retriever.py`（检索）共用。
"""

from pathlib import Path


def resolve_device(setting: str = "auto") -> str:
    """把 .env 里的 EMBED_DEVICE 解析成实际设备"""
    import torch

    if setting == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return setting


def load_embedder(model_dir: Path | str, device: str = "auto"):
    """
    加载 BGE 系列的 embedding 模型。

    ⚠️ 关键：BGE 必须用 **CLS pooling + 归一化**。
    本地模型目录缺少 `1_Pooling/config.json`，若直接 `SentenceTransformer(目录)`，
    它会**静默**退化为 mean pooling —— 不报错，但检索质量悄悄变差。
    """
    from sentence_transformers import SentenceTransformer

    try:  # sentence-transformers 6.x 的新路径
        from sentence_transformers.sentence_transformer.modules import Pooling, Transformer
    except ImportError:  # 兼容旧版本
        from sentence_transformers.models import Pooling, Transformer

    word = Transformer(str(model_dir))
    pool = Pooling(word.get_embedding_dimension(), pooling_mode="cls")
    return SentenceTransformer(modules=[word, pool], device=resolve_device(device))
