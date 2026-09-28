"""
项目配置与共享客户端（生产路径的唯一入口）

把 `.env` 解析、路径、向量库目录、LLM 客户端集中到这里。
背景：`scripts/` 下的几个诊断脚本各自写了一份 `load_env`（历史遗留），
生产路径（`pipeline.py` / `cli.py`）一律走这里，避免继续扩散副本。

⚠️ 读 .env 用 `.split("\n")`，**不要用 `splitlines()`** —— 见 docs/工程日志.md 坑 7。
"""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CUAD_DIR = DATA_DIR / "cuad"
TXT_DIR = CUAD_DIR / "full_contract_txt"
PROCESSED_DIR = DATA_DIR / "processed"
EVAL_DIR = ROOT / "eval"
REPORTS_DIR = PROCESSED_DIR / "reports"

# 各厂商的 OpenAI 兼容端点（均已用官方文档核实，禁止凭印象写）
PROVIDERS: dict[str, tuple[str, str]] = {
    # provider 名: (API key 在 .env 里的变量名, base_url)
    "ollama": ("LLM_API_KEY", "http://localhost:11434/v1"),  # 本地部署，不做鉴权，key 随意填
    "zhipu": ("GLM_API_KEY", "https://open.bigmodel.cn/api/paas/v4/"),
    "dashscope": ("DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
}


def load_env(path: Path | None = None) -> dict[str, str]:
    """
    极简 .env 解析（无第三方依赖）。

    ⚠️ **`.env` 不存在时返回空字典，而不是抛异常** —— 这条直接决定"能不能被 clone 后跑起来"：
    新克隆的仓库只有 `.env.example`。如果在 import 阶段就崩，那么连"不需要任何 key"的功能
    （`run_eval.py --help`、`examples/show_report.py` 离线看报告、`pytest`）都会一起用不了。
    需要 key 的地方（`build_llm_client()`）在**真正被调用时**才报错，那时提示才是有意义的。
    """
    target = path or (ROOT / ".env")
    if not target.exists():
        return {}
    env: dict[str, str] = {}
    for line in target.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


ENV = load_env()


def chroma_dir() -> Path:
    return ROOT / ENV.get("CHROMA_DIR", "data/chroma_bge_m3")


def embed_model_dir() -> Path:
    return Path(ENV["EMBED_MODEL_PATH"])


def embed_device() -> str:
    return ENV.get("EMBED_DEVICE", "auto")


def top_k() -> int:
    return int(ENV.get("TOP_K", "5"))


def rerank_enabled() -> bool:
    return ENV.get("RERANK_ENABLED", "0").strip().lower() in {"1", "true", "yes"}


def rerank_model_dir() -> Path:
    return Path(ENV["RERANK_MODEL_PATH"])


def rerank_candidates() -> int:
    """粗召候选数（rerank 前的池子大小）"""
    return int(ENV.get("RERANK_CANDIDATES", "20"))


def rerank_top_n() -> int:
    """精排后喂给 LLM 的片段数"""
    return int(ENV.get("RERANK_TOP_N", "5"))


def rerank_device() -> str:
    return ENV.get("RERANK_DEVICE", "cpu")


def rerank_max_length() -> int:
    """重排时每条候选截断的 token 上限（bge-reranker-v2-m3 支持很长，但截断能显著提速）"""
    return int(ENV.get("RERANK_MAX_LENGTH", "512"))


def safe_filename(text: str, max_len: int = 80) -> str:
    """
    把 contract_id 变成安全的文件名（合同名里常有空格、括号、逗号等）。

    ⚠️ **必须防碰撞**：CUAD 里有 `...Franchise Agreement1` 与 `...Franchise Agreement3`
    这类超长、只差最后一位的合同名。简单截断会让两份合同**写进同一个报告文件**；
    更危险的是 `--resume` 会把 A 的报告当成 B 的打分 —— **指标被悄悄污染**（本项目实测踩到过）。
    做法：只要发生过字符替换或截断，就在末尾附上原串的短哈希，保证一一对应。
    """
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in text)
    if cleaned != text or len(text) > max_len:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
        return f"{cleaned[: max_len - 9]}_{digest}"
    return cleaned[:max_len]


def llm_provider() -> str:
    return ENV.get("LLM_PROVIDER", "zhipu").lower()


def llm_model() -> str:
    return ENV.get("LLM_MODEL", "glm-4.7-flash")


def llm_api_key() -> str:
    key_name = PROVIDERS.get(llm_provider(), ("LLM_API_KEY", ""))[0]
    key = ENV.get(key_name) or ENV.get("LLM_API_KEY")
    if not key:
        raise SystemExit(f"❌ .env 里没有 {key_name}（provider={llm_provider()}）")
    return key


def llm_base_url() -> str:
    default_url = PROVIDERS.get(llm_provider(), ("", ""))[1]
    return ENV.get("LLM_BASE_URL") or default_url


def llm_temperature() -> float:
    """
    采样温度：越小越确定（评测要求可复现）。

    ⚠️ 不能硬编码 0：**智谱的 OpenAI 兼容接口里 `temperature=0` 不适用**
    （官方文档：取值区间为 (0,1)）→ 对 GLM 在 .env 里配 0.01。
    """
    return float(ENV.get("LLM_TEMPERATURE", "0"))


def llm_extra_body() -> dict | None:
    """
    供应商特有的可选参数（在 .env 里写 JSON 字符串）。

    实测（GLM-4.7-Flash）：默认开启思考模式时单次 **3.40 秒 / completion 146 tokens**；
    传 `{"thinking":{"type":"disabled"}}` 关掉后 **1.63 秒 / 10 tokens**，JSON 输出不变
    → 1980 次调用从约 112 分钟降到约 54 分钟，而且更确定（利于评测复现）。
    """
    raw = ENV.get("LLM_EXTRA_BODY", "").strip()
    return json.loads(raw) if raw else None


def build_llm_client():
    """
    按 LLM_PROVIDER 构造 OpenAI 兼容客户端（base_url 均已用官方文档核实）。

    ⚠️ 免费模型常有 429 限流（智谱实测遇到过 code 1305「该模型当前访问量过大」）→
    交给 SDK 自带的重试 + 指数退避（`max_retries`），否则单次失败就会让整份合同算失败。
    """
    from openai import OpenAI

    return OpenAI(
        api_key=llm_api_key(),
        base_url=llm_base_url(),
        max_retries=int(ENV.get("LLM_MAX_RETRIES", "6")),
        timeout=float(ENV.get("LLM_TIMEOUT", "60")),
    )
