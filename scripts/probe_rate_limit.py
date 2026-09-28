"""
限流探针：换模型 / 准备长跑前，先探明"能承受多快的请求节奏"

背景（实测踩到的两个 429，都来自智谱免费模型）：
    · code 1305「该模型当前访问量过大」—— 模型侧拥塞
    · code 1302「您的账户已达到速率限制」—— **账户侧限流**，SDK 重试 6 次也扛不住
评测要跑 990~1980 次调用。不先探明节奏就开跑 = 大面积失败 + 浪费时间。

做法：按固定间隔连发 N 次**最小请求**（省 token），统计成功率与平均延迟。

用法（在 hetong/ 目录下运行）：
    python -X utf8 scripts\\probe_rate_limit.py --calls 8 --interval 4
    python -X utf8 scripts\\probe_rate_limit.py --calls 20 --interval 8   # 更保守的节奏
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import build_llm_client, llm_model, llm_temperature  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="探明模型的可用请求节奏")
    parser.add_argument("--calls", type=int, default=8, help="总共发多少次请求")
    parser.add_argument("--interval", type=float, default=4.0, help="每次请求之间的间隔（秒）")
    args = parser.parse_args()

    model = llm_model()
    print(f"模型 {model} | 计划 {args.calls} 次，间隔 {args.interval} 秒\n")
    client = build_llm_client()

    ok = 0
    latencies: list[float] = []
    for i in range(1, args.calls + 1):
        started = time.perf_counter()
        try:
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "回答一个字：好"}],
                temperature=llm_temperature(),
                max_tokens=64,
            )
            elapsed = time.perf_counter() - started
            latencies.append(elapsed)
            ok += 1
            print(f"  第 {i:2d} 次 ✅ {elapsed:5.2f}s")
        except Exception as exc:  # noqa: BLE001
            elapsed = time.perf_counter() - started
            code = getattr(getattr(exc, "response", None), "json", lambda: {})()
            print(f"  第 {i:2d} 次 ❌ {elapsed:5.2f}s | {type(exc).__name__} | {str(exc)[:90]}")
        if i < args.calls:
            time.sleep(args.interval)

    print("\n---- 结论 ----")
    print(f"成功率: {ok}/{args.calls} = {ok / args.calls * 100:.0f}%")
    if latencies:
        avg = sum(latencies) / len(latencies)
        print(f"平均延迟: {avg:.2f} 秒")
        per_call = avg + args.interval
        for n in (500, 990, 1980):
            print(f"  按此节奏，{n} 次调用 ≈ {n * per_call / 60:.0f} 分钟")
    else:
        print("全部失败 —— 说明账户此刻已被限流，需要等待或降速重试")


if __name__ == "__main__":
    main()
