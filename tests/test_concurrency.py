"""
并发改造的自测（工程增强 E1）

**为什么这个测试必须存在**：并发最大的风险不是"跑不动"，而是**结果悄悄变了** ——
多线程一开，条款顺序、检索重叠、指标累加都可能出错，而这些错误**不会抛异常**，
只会让分数变得不可复现。而"不可复现"恰恰是本项目最不能接受的事。

**怎么在零成本下验证**：把两端"贵且不可控"的东西换成**桩**：
  · 桩 LLM 客户端：不联网、返回固定 JSON、用 sleep 模拟接口耗时
      → 顺带验证：并发数**真的发生了**，且**没超过** LLM_MAX_CONCURRENCY 这个总闸
  · 桩 Retriever：不碰 Chroma / 不占 GPU、返回固定片段，并记录"检索是否被并发"
      → 顺带验证：GPU 锁真的把检索串行化了
于是：零 API 花费、零显存占用、秒级跑完、完全确定性（可以进 CI）。

运行：python -m pytest -q
"""

import threading
import time

from clauses import CLAUSES
from config import ENV
from pipeline import review_contract
from schemas import ReaderStance

STUB_TEXT = "The parties agree as follows. STUB EVIDENCE shall govern this Agreement. " * 8
STUB_PAYLOAD = (
    '{"exists": true, "risk_level": "low", "confidence": 0.9, "reason_zh": "桩模型输出", '
    '"gaps": [], "evidence_quotes": ["STUB EVIDENCE"]}'
)


# ---------- 桩：假 LLM（不联网）----------
class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)


class _Response:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


class StubLLM:
    """假 LLM：`client.chat.completions.create(...)` 这条路走得通，但不联网"""

    def __init__(self, latency: float = 0.0, reverse: bool = False) -> None:
        self.latency = latency
        # reverse=True：**先来的睡得久** → 完成顺序与进入顺序相反，
        # 用来把"结果会不会被完成顺序带偏"这个风险暴露出来（见 test_order_...）
        self.reverse = reverse
        self.calls = 0
        self.active = 0          # 当前在飞的请求数
        self.max_active = 0      # 历史峰值（用来验证并发上限）
        self.entry_order: list[int] = []   # 进入顺序
        self.exit_order: list[int] = []    # 完成顺序
        self._lock = threading.Lock()
        self.chat = self         # 让 client.chat.completions.create 能一路点下去
        self.completions = self

    def create(self, **_kwargs):
        with self._lock:
            self.calls += 1
            n = self.calls
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.entry_order.append(n)
        try:
            time.sleep(max(0.0, self.latency - 0.01 * n) if self.reverse else self.latency)
            return _Response(STUB_PAYLOAD)
        finally:
            with self._lock:
                self.active -= 1
                self.exit_order.append(n)


# ---------- 桩：假检索器（不碰 GPU / Chroma）----------
class StubRetriever:
    """假检索器：返回固定片段，并记录"检索区间是否发生过重叠" """

    def __init__(self, latency: float = 0.0) -> None:
        self.latency = latency
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def search(self, contract_id: str, query: str, k: int = 5, **_kwargs) -> list[dict]:
        with self._lock:
            self.calls += 1
            n = self.calls
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.latency)  # 模拟检索耗时（真机上是向量化 + 重排）
            # 片段内必须**真的包含**模型引用的那句，否则会被判成"定位不到"（防幻觉路径）
            return [
                {
                    "chunk_id": f"stub-{n}",
                    "text": STUB_TEXT,
                    "char_start": 1000,
                    "char_end": 1000 + len(STUB_TEXT),
                    "distance": 0.1,
                }
            ]
        finally:
            with self._lock:
                self.active -= 1


def _run(jobs: int, llm_latency: float = 0.0, retrieval_latency: float = 0.0):
    """跑一整份合同的 10 类条款（全用桩），返回 (耗时, report, runs, 桩检索器, 桩LLM)"""
    retriever = StubRetriever(latency=retrieval_latency)
    client = StubLLM(latency=llm_latency)
    t0 = time.perf_counter()
    report, runs = review_contract(
        contract_id="STUB_CONTRACT",
        file_name="stub.txt",
        retriever=retriever,
        client=client,
        model="stub-model",
        stance=ReaderStance.CUSTOMER,
        k=3,
        verbose=False,
        use_rerank=False,
        jobs=jobs,
    )
    return time.perf_counter() - t0, report, runs, retriever, client


# ================================================================
# ① 结果必须与串行**逐字段一致**，顺序必须等于登记表顺序
# ================================================================
def test_parallel_results_identical_to_serial() -> None:
    _, report_s, runs_s, _, _ = _run(jobs=1)
    _, report_p, runs_p, _, _ = _run(jobs=5)

    # 顺序：并发用的是 ex.map（按提交顺序返回），必须与 clauses.py 的登记顺序一致。
    # 顺序一错，报告与金标准就错位 —— 这类 bug 不崩溃，只会让分数不可信。
    assert [r.finding.clause_id.value for r in runs_p] == [s.clause_id.value for s in CLAUSES]

    # 逐字段：两种模式的 finding 与 report 必须完全一样
    assert [r.finding.model_dump() for r in runs_p] == [r.finding.model_dump() for r in runs_s]
    assert report_p is not None and report_s is not None
    assert report_p.model_dump() == report_s.model_dump()


# ================================================================
# ② 并发要真的更快（否则这套改造就是自欺欺人）
# ================================================================
def test_parallel_is_actually_faster() -> None:
    t_serial, *_ = _run(jobs=1, llm_latency=0.12, retrieval_latency=0.02)
    t_parallel, *_ = _run(jobs=5, llm_latency=0.12, retrieval_latency=0.02)

    assert t_parallel < t_serial * 0.6, (
        f"并发没有生效：串行 {t_serial:.2f}s vs 并发 {t_parallel:.2f}s"
        f"（若 .env 的 LLM_MAX_CONCURRENCY 被设成 1，这里必然失败）"
    )


# ================================================================
# ③ GPU 锁：检索永远串行（一块卡上同时跑两个前向 = 抢显存 + 结果不稳）
# ================================================================
def test_retrieval_never_overlaps() -> None:
    _, _, _, retriever, _ = _run(jobs=5, llm_latency=0.10, retrieval_latency=0.03)

    assert retriever.calls == len(CLAUSES), "每类条款应各检索一次"
    assert retriever.max_active == 1, (
        f"检索出现了并发（峰值 {retriever.max_active}）—— _GPU_LOCK 没生效"
    )


# ================================================================
# ④ LLM 总闸：并发要发生，但不能突破 LLM_MAX_CONCURRENCY
# ================================================================
def test_llm_concurrency_respects_cap() -> None:
    """
    注意这里用 `jobs=len(CLAUSES)`（10 个条款全放开），而**不是** 5：
    总闸是 6，如果只放开 5 个并发，那"不超限"是**必然成立**的 —— 测了等于没测。
    只有放开数 > 上限，这个断言才真正在测信号量。
    """
    cap = int(ENV.get("LLM_MAX_CONCURRENCY", "6"))
    _, _, _, _, client = _run(jobs=len(CLAUSES), llm_latency=0.10, retrieval_latency=0.0)

    assert client.calls == len(CLAUSES)
    assert client.max_active <= cap, f"突破了并发总闸：峰值 {client.max_active} > 上限 {cap}"
    if cap > 1:
        assert client.max_active > 1, "并发根本没发生（信号量或线程池没起作用）"


# ================================================================
# ⑤ 完成顺序被打乱时，结果顺序仍必须与串行一致
# ================================================================
def test_order_stable_when_completion_order_reversed() -> None:
    """
    这一条才是"顺序"风险的真正压力测试：
    test ① 用的是等长耗时，**提交顺序≈完成顺序**，所以即使实现按完成顺序返回，也可能侥幸通过。
    这里让"先来的睡得久" → 完成顺序与提交顺序**相反** → 任何按完成顺序返回的实现都会暴露。
    """
    _, report_s, runs_s, _, _ = _run(jobs=5)

    retriever = StubRetriever()
    client = StubLLM(latency=0.12, reverse=True)
    report_p, runs_p = review_contract(
        contract_id="STUB_CONTRACT",
        file_name="stub.txt",
        retriever=retriever,
        client=client,
        model="stub-model",
        stance=ReaderStance.CUSTOMER,
        k=3,
        verbose=False,
        use_rerank=False,
        jobs=5,
    )

    # 先证明"这个场景确实乱序了"，否则本测试是空转（套套逻辑）
    assert client.entry_order != client.exit_order, "耗时没有造成完成顺序错乱 → 本测试没有测到东西"

    assert [r.finding.clause_id.value for r in runs_p] == [s.clause_id.value for s in CLAUSES]
    assert [r.finding.model_dump() for r in runs_p] == [r.finding.model_dump() for r in runs_s]
    assert report_p is not None and report_s is not None
    assert report_p.model_dump() == report_s.model_dump()
