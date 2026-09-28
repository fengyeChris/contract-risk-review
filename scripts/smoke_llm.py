"""
LLM 连通性冒烟测试（**供应商无关**：provider / model / base_url / temperature 全部从 .env 读）

换模型或换供应商后，先确认三件事（**不猜 API，直接问 API**）：
1. API key 能否通过 OpenAI 兼容模式鉴权
2. `.env` 里的模型名是否可用
3. 是否支持 JSON 模式（我们的管线依赖 `response_format={"type":"json_object"}`）+ 单次调用延迟

已用官方文档核实的 base_url（写在 config.py 的 PROVIDERS 里）：
    智谱 BigModel   : https://open.bigmodel.cn/api/paas/v4/
    阿里云 DashScope : https://dashscope.aliyuncs.com/compatible-mode/v1

⚠️ 两个已知的兼容性差异（踩过坑，记在这里）：
    · **智谱的 `temperature=0` 不适用**（官方文档：取值区间 (0,1)）→ 用 .env 的 LLM_TEMPERATURE=0.01
    · GLM 是**混合思考模型**，思考内容会占 completion token → pipeline 的 max_tokens 已调到 1600

用法（在 hetong/ 目录下运行）：
    C:\\Users\\Admin\\.conda\\envs\\langchain1.2\\python.exe -X utf8 scripts\\smoke_llm.py
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import (  # noqa: E402
    build_llm_client,
    llm_api_key,
    llm_base_url,
    llm_model,
    llm_provider,
    llm_temperature,
)


def mask(key: str) -> str:
    """只显示长度与首尾片段，避免把密钥写进日志或对话"""
    return f"已读取（长度 {len(key)}，前缀 {key[:6]}…，末 4 位 …{key[-4:]}）" if key else "缺失"


def main() -> None:
    model = llm_model()
    base_url = llm_base_url()
    temperature = llm_temperature()

    print(f"provider   : {llm_provider()}")
    print(f"模型        : {model}")
    print(f"base_url    : {base_url}")
    print(f"temperature : {temperature}")
    print(f"API key     : {mask(llm_api_key())}")

    client = build_llm_client()

    # ---------------- 测试 1：最小对话 + 延迟 ----------------
    print("\n[测试 1] 基本对话 …")
    latency = 0.0
    try:
        started = time.perf_counter()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "只回答两个字：收到"}],
            temperature=temperature,
            max_tokens=512,
        )
        latency = time.perf_counter() - started
        print(f"  ✅ 成功 | 延迟 {latency:.2f} 秒")
        print(f"  回复: {resp.choices[0].message.content.strip()}")
        print(f"  token 用量: prompt={resp.usage.prompt_tokens} completion={resp.usage.completion_tokens}")
        # 混合思考模型可能把思考内容放在单独字段里 → 检查一下，避免以后踩坑
        extra = getattr(resp.choices[0].message, "reasoning_content", None)
        if extra:
            print(f"  ⓘ 该模型返回了独立的 reasoning_content（{len(str(extra))} 字符）→ 思考内容不占用 content")
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ 失败 | {type(exc).__name__}: {exc}")
        return

    # ---------------- 测试 2：JSON 模式 ----------------
    print("\n[测试 2] JSON 模式（response_format=json_object）…")
    json_ok = False
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": "输出一个 JSON，包含字段 ok（值为 true）与字段 note（值为 hello）"}
            ],
            temperature=temperature,
            response_format={"type": "json_object"},
            max_tokens=1024,
        )
        raw = resp.choices[0].message.content
        print("  ✅ 支持 JSON 模式")
        print(f"  原始返回: {raw.strip()[:240]}")
        parsed = json.loads(raw)
        print(f"  解析结果: {parsed}  →  类型 {type(parsed).__name__}")
        json_ok = True
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ 不支持或调用失败 | {type(exc).__name__}: {exc}")

    # ---------------- 成本与时间估算 ----------------
    total_calls = 198 * 10
    if latency:
        print(f"\n[估算] 主评测集 198 份 × 10 类 = {total_calls} 次调用")
        print(f"        按单次 {latency:.2f} 秒串行 ≈ {total_calls * latency / 60:.0f} 分钟（后续可用并发压缩）")

    print("\n---- 结论 ----")
    print("鉴权与模型可用 : ✅")
    print(f"JSON 模式      : {'✅ 可用（可直接做结构化输出）' if json_ok else '❌ 不可用（需 prompt 约束 + 手动解析 + Pydantic 校验）'}")


if __name__ == "__main__":
    main()
