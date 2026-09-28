"""临时脚本：变异检验 —— 故意把实现改坏，确认测试真的会失败（用完即删）"""

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "tests")
import pipeline  # noqa: E402
import test_concurrency as tc  # noqa: E402
from clauses import CLAUSES  # noqa: E402
from schemas import ReaderStance  # noqa: E402


class _Noop:
    """假的锁 / 信号量：`with` 它什么都锁不住"""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def order_ok(runs) -> bool:
    return [r.finding.clause_id.value for r in runs] == [s.clause_id.value for s in CLAUSES]


print("=" * 72)
print("变异检验：把实现故意改坏，看测试会不会失败（会失败 = 测试有牙齿）")
print("=" * 72)

# ---------- 变异 1：拆掉 GPU 锁 ----------
orig_gpu = pipeline._GPU_LOCK
pipeline._GPU_LOCK = _Noop()
_, _, _, ret, _ = tc._run(jobs=5, llm_latency=0.10, retrieval_latency=0.03)
pipeline._GPU_LOCK = orig_gpu
print(f"\n[变异1] 拆掉 GPU 锁 → 检索并发峰值 = {ret.max_active}")
print(f"        测试③ 断言 ==1 → {'✅ 测试会失败（有牙齿）' if ret.max_active > 1 else '❌ 测试发现不了'}")

# ---------- 变异 2：拆掉 LLM 总闸 ----------
CAP = 6  # 与 .env 默认的 LLM_MAX_CONCURRENCY 一致
orig_sem = pipeline._LLM_SEM
pipeline._LLM_SEM = _Noop()
_, _, _, _, cli = tc._run(jobs=len(CLAUSES), llm_latency=0.10)
pipeline._LLM_SEM = orig_sem
print(f"\n[变异2] 拆掉 LLM 信号量 → LLM 并发峰值 = {cli.max_active}（总闸应为 {CAP}）")
print(f"        测试④ 断言 <={CAP} → {'✅ 测试会失败（有牙齿）' if cli.max_active > CAP else '❌ 测试发现不了'}")


# ---------- 变异 3：让线程池按"完成顺序"返回结果 ----------
class _CompletionOrderPool(ThreadPoolExecutor):
    def map(self, fn, *iterables, **kwargs):
        futures = [self.submit(fn, x) for x in iterables[0]]
        return (f.result() for f in as_completed(futures))


orig_pool = pipeline.ThreadPoolExecutor
pipeline.ThreadPoolExecutor = _CompletionOrderPool
_, runs_bad = pipeline.review_contract(
    contract_id="STUB",
    file_name="stub.txt",
    retriever=tc.StubRetriever(),
    client=tc.StubLLM(latency=0.12, reverse=True),  # 完成顺序与提交顺序相反
    model="stub-model",
    stance=ReaderStance.CUSTOMER,
    k=3,
    verbose=False,
    use_rerank=False,
    jobs=5,
)
pipeline.ThreadPoolExecutor = orig_pool
print("\n[变异3] 让线程池按完成顺序返回结果 → 顺序 = " + ("✅ 仍是登记顺序" if order_ok(runs_bad) else "❌ 已被带偏"))
print(f"        测试①/⑤ 断言 ==登记顺序 → {'✅ 测试会失败（有牙齿）' if not order_ok(runs_bad) else '❌ 测试发现不了'}")
if not order_ok(runs_bad):
    print(f"        （实际返回的前 4 个条款：{[r.finding.clause_id.value for r in runs_bad][:4]}）")
