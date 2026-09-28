"""
Step 4.1：批量评测（存在性 F1 / 证据定位准确率 / 必备条款缺失识别率）

以 `eval/golden_eval.jsonl` 的金标准为基准：逐份合同跑完整管线，再逐类条款打分。

用法（在 hetong/ 目录下运行）：
    python -X utf8 run_eval.py --limit 10        # 小批量：前 10 份（约 3 分钟）
    python -X utf8 run_eval.py                   # 全量 198 份（约 56 分钟，串行）

**推荐：分两段跑（把长跑拆开，中间能看到中间结果）**
    python -X utf8 run_eval.py --skip 0   --limit 99 --tag part1    # 第一段（约 28 分钟）
    python -X utf8 run_eval.py --skip 99  --limit 99 --tag part2    # 第二段（约 28 分钟）
    python -X utf8 run_eval.py --tag baseline --resume              # 汇总打分（**零 API 调用**）

说明：`--resume` 会读取磁盘上已有的报告，所以最后这次汇总只花几秒 ——
      **LLM 调用（贵、慢）和打分（便宜、快）可以分开跑**，这是长跑任务的通用做法。
    python -X utf8 run_eval.py --tag baseline    # 结果存 eval/metrics_baseline.json

指标口径（**写死在这里**，避免"每次算得不一样"）：
1. **存在性**：逐类累计 TP/FP/FN/TN
   精确率 = TP/(TP+FP)，召回率 = TP/(TP+FN)，F1 = 2PR/(P+R)
2. **证据定位准确率** = 在"存在性判对的阳性 case"中，预测证据与金标准证据**重叠**的比例
   （重叠口径见 `evidence_hit()`；模型没给证据也算不命中）
3. **必备条款缺失识别率** = 在"金标准为不存在"的必备条款（01/08/10）里，系统也判不存在的比例
4. `[PIPELINE_ERROR]` 的记录**一律剔除**，不计入任何指标，单独打印计数
"""

import argparse
import json
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config import (  # noqa: E402
    ENV,
    EVAL_DIR,
    REPORTS_DIR,
    TXT_DIR,
    chroma_dir,
    embed_device,
    embed_model_dir,
    llm_model,
    rerank_candidates,
    rerank_enabled,
    rerank_model_dir,
    safe_filename,
)
from pipeline import default_client, review_contract  # noqa: E402
from retriever import Retriever  # noqa: E402
from schemas import REQUIRED_CLAUSES, ClauseId, ReaderStance, ReviewReport  # noqa: E402
from text_utils import load_txt_index, lookup, read_jsonl  # noqa: E402

PIPELINE_ERROR_PREFIX = "[PIPELINE_ERROR]"


# ================================================================
# ⬜ 待你补：证据匹配口径（对应 badcase_log.md BC-005）
# ================================================================
def evidence_hit(pred_spans: list[tuple[int, int]], gold_spans: list[tuple[int, int]]) -> bool:
    """
    ✅ 已实现（参考实现 v0.9.3）：判断"模型给出的证据位置"是否算命中金标准

    **建议口径：只要有一个预测区间与任何一个金标准区间「有重叠」就算命中。**

    为什么不用其他口径（这三个理由就是选它的依据）：

    | 备选口径 | 为什么不用 |
    |---|---|
    | 完全相等 | 01 实测：预测 `(52036,52151)`、金标准 `(52061,52151)` —— 模型多带了章节号前缀 `6.9 Governing Law.`，完全相等会**误判为错** |
    | 必须包含金标准整段 | 过严：金标准常标注一整个长段落，模型只引用其中最关键的一句反而被算错 |
    | 语义相似（人工判断） | 不可自动化、不可复现 —— 评测指标必须能一键重跑 |

    输入：
        pred_spans : 模型给出的证据区间列表，如 [(52036, 52151)]
        gold_spans : 金标准的证据区间列表，如 [(52061, 52151), (1854, 2004)]

    输出：True / False

    先想清楚一件事：把区间看成**半开区间** `[start, end)`（因为 char_end 是"不含"的下标）。
    然后用这 3 个小例子自测你的实现（**别跳过，这三个例子就是你的测试用例**）：
        (10, 20) vs (15, 25)  → 应该 True   （有公共字符）
        (10, 20) vs (20, 25)  → 应该 False  （首尾相接，但没有公共字符）
        (10, 20) vs (30, 40)  → 应该 False
    还要处理：pred_spans 为空（模型没给证据）→ 直接 False。
    """
    # ① 空列表保护：模型没给证据（或金标准没标注证据）时没有可比基准 → 不算命中
    #    `not []` 为 True，所以这一行同时处理了"pred 为空"和"gold 为空"两种情况
    if not pred_spans or not gold_spans:
        return False

    # ② 双层循环 = "任意一个预测区间 × 任意一个金标准区间"的全部组合
    #    外层遍历模型给的（通常 1~3 个），内层遍历金标准的（可能多点标注）→ 组合数很小，性能无忧
    for pred_start, pred_end in pred_spans:
        for gold_start, gold_end in gold_spans:
            # ③ 半开区间 [start, end) 的"有交集"判定：
            #    两个区间重叠 ⇔ 各自的起点都小于对方的终点
            #    等价写法：max(pred_start, gold_start) < min(pred_end, gold_end)
            #             （读作"交集的起点 < 交集的终点"，即交集中至少有一个字符）
            #    注意用的是 < 而不是 <= ：因为 end 这个下标本身不属于区间，
            #    所以 (10,20) 与 (20,25) 是"首尾相接"而非重叠，必须判 False
            if pred_start < gold_end and gold_start < pred_end:
                # ④ 找到一对重叠就立刻返回（短路）：不命中才算"错"，命中一次就够了
                return True

    # ⑤ 所有组合都比过了，没有交集 → 不命中
    return False


def self_check() -> None:
    """开工前先用 5 个已知用例校验 evidence_hit()（避免跑完 3 分钟才发现没补）"""
    try:
        cases = [
            ((10, 20), (15, 25), True, "有公共字符"),
            ((10, 20), (20, 25), False, "首尾相接，无公共字符"),
            ((10, 20), (30, 40), False, "完全无关"),
            ((10, 20), (0, 10), False, "首尾相接（反向）"),
        ]
        for pred, gold, expected, why in cases:
            got = evidence_hit([pred], [gold])
            if got is not expected:
                raise SystemExit(f"❌ evidence_hit 自检失败：{pred} vs {gold} 应得 {expected}（{why}），实际 {got}")
        if evidence_hit([], [(10, 20)]):
            raise SystemExit("❌ evidence_hit 自检失败：预测证据为空时应返回 False")
    except NotImplementedError as exc:
        raise SystemExit(f"⏸ run_eval.py 停下：证据匹配还没补。\n   {exc}") from exc
    print("✅ evidence_hit 自检通过（5/5）\n")


# ================================================================
# 指标容器
# ================================================================
def _nan_to_none(value: float) -> float | None:
    """JSON 里没有 NaN —— 落盘前统一转成 null，否则指标文件不是合法 JSON"""
    return None if value != value else value


@dataclass
class ClauseMetrics:
    clause_id: ClauseId
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    excluded: int = 0  # PIPELINE_ERROR，剔除不计
    ev_total: int = 0  # 存在性判对的阳性 case 数（= TP）
    ev_hit: int = 0  # 其中证据重叠命中的数量

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else float("nan")

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else float("nan")

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        if p != p or r != r or (p + r) == 0:  # nan 判断
            return float("nan")
        return 2 * p * r / (p + r)

    @property
    def evidence_accuracy(self) -> float:
        return self.ev_hit / self.ev_total if self.ev_total else float("nan")

    def as_dict(self) -> dict:
        return {
            "clause_id": self.clause_id.value,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "excluded": self.excluded,
            "precision": _nan_to_none(self.precision),
            "recall": _nan_to_none(self.recall),
            "f1": _nan_to_none(self.f1),
            "evidence_accuracy": _nan_to_none(self.evidence_accuracy),
            "ev_hit": self.ev_hit,
            "ev_total": self.ev_total,
        }


def _fmt(value: float) -> str:
    return "  —  " if value != value else f"{value:.3f}"  # nan → 占位符


def _micro(metrics: dict[ClauseId, ClauseMetrics]) -> tuple[int, int, int, float, float, float]:
    tp = sum(m.tp for m in metrics.values())
    fp = sum(m.fp for m in metrics.values())
    fn = sum(m.fn for m in metrics.values())
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else float("nan")
    return tp, fp, fn, precision, recall, f1


def _macro(metrics: dict[ClauseId, ClauseMetrics]) -> tuple[float, float, float]:
    values = [m for m in metrics.values() if m.precision == m.precision]
    recalls = [m for m in metrics.values() if m.recall == m.recall]
    f1s = [m for m in metrics.values() if m.f1 == m.f1]
    p = sum(m.precision for m in values) / len(values) if values else float("nan")
    r = sum(m.recall for m in recalls) / len(recalls) if recalls else float("nan")
    f = sum(m.f1 for m in f1s) / len(f1s) if f1s else float("nan")
    return p, r, f


# ================================================================
# 主流程
# ================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="批量评测：存在性 F1 / 证据定位 / 缺失识别")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 份（小批量验证用）")
    parser.add_argument("--skip", type=int, default=0, help="跳过前 N 份（配合 --limit 分段跑全量）")
    parser.add_argument("--tag", default="baseline", help="结果文件名标签（Step 5 前后对比用）")
    parser.add_argument("--resume", action="store_true", help="跳过已有报告的合同")
    parser.add_argument("--k", type=int, default=None, help="检索 top-k，默认取 .env 的 TOP_K")
    parser.add_argument("--no-rerank", action="store_true",
                        help="强制关闭重排（Step 5 做『加/不加该增强』前后对比时用）")
    parser.add_argument("--reports-dir", default=None,
                        help="报告目录，默认 data/processed/reports。做 A/B 时**必须分开**，"
                             "否则 --resume 会把对照组和增强组的报告混着读，指标直接失真")
    parser.add_argument("--jobs", type=int, default=1,
                        help="并发审查的合同数（默认 1＝串行）。LLM 的并发总闸由 .env 的 "
                             "LLM_MAX_CONCURRENCY 控制（默认 6），所以这里调大不会突破 API 限制")
    parser.add_argument("--clause-jobs", type=int, default=1,
                        help="单份合同内并发审查的条款数（默认 1）。推荐起步：--jobs 4 --clause-jobs 2")
    args = parser.parse_args()

    use_rerank = False if args.no_rerank else None  # None = 跟随 .env
    reports_dir = Path(args.reports_dir) if args.reports_dir else REPORTS_DIR  # 必须在头部打印之前算好
    self_check()

    records = read_jsonl(EVAL_DIR / "golden_eval.jsonl")
    if args.skip:
        records = records[args.skip :]
    if args.limit:
        records = records[: args.limit]
    model, k = llm_model(), args.k or int(ENV.get("TOP_K", "5"))

    rerank_on = rerank_enabled() and not args.no_rerank
    print("=" * 84)
    print(f"批量评测  |  合同数 {len(records)}  |  模型 {model}  |  top-k {k}  |  立场 customer")
    print(f"chunk={ENV.get('CHUNK_MAX_CHARS', '?')}/{ENV.get('CHUNK_OVERLAP', '?')}  |  检索范围＝单份合同内")
    print(f"重排 rerank = {'ON（粗召 ' + str(rerank_candidates()) + ' → 精排取 top-k）' if rerank_on else 'OFF（纯向量 top-k）'}")
    print(f"报告目录 = {reports_dir}")
    print("=" * 84)

    retriever = Retriever(chroma_dir=chroma_dir(), model_dir=embed_model_dir(), device=embed_device())
    client = default_client()
    txt_index = load_txt_index(TXT_DIR)
    reports_dir.mkdir(parents=True, exist_ok=True)

    metrics = {cid: ClauseMetrics(clause_id=cid) for cid in ClauseId}
    failures: list[dict] = []
    evidence_miss_cases: list[dict] = []
    fp_cases: list[dict] = []  # 误报：金标准没有、我们报了
    fn_cases: list[dict] = []  # 漏报：金标准有、我们没报
    required_neg_total = required_neg_caught = 0

    started_all = time.perf_counter()
    jobs = max(1, args.jobs)
    clause_jobs = max(1, args.clause_jobs)
    if jobs > 1 or clause_jobs > 1:
        print(f"并发 = 合同级 {jobs} × 条款级 {clause_jobs}"
              f"（LLM 并发总闸 {ENV.get('LLM_MAX_CONCURRENCY', '6')}；GPU 检索串行）\n")

    progress_lock = threading.Lock()
    progress = {"done": 0}

    def mark(contract_id: str, note: str) -> None:
        """
        并发模式下的**实时进度**。长跑任务必须能看见进度 —— 卡住时"没有输出"比报错更难查
        （本项目就吃过一次亏：输出被缓冲到结束，进程被当成超时杀掉）。
        串行模式不在这里打印，交给第二阶段按旧格式打印，保证输出与旧版完全一致。
        """
        if jobs <= 1:
            return
        with progress_lock:
            progress["done"] += 1
            print(f"[{progress['done']}/{len(records)}] {contract_id[:56]:56s} {note}")

    def produce(record: dict) -> dict:
        """
        产出**一份合同的报告**（可在线程里跑）。只做"贵且独立"的事：读缓存 / 调 LLM / 落盘。

        **打分不在这里做** —— 打分留在主线程按顺序做（见第二阶段）。
        原因：多个线程往同一个 metrics 里累加，结果是不可复现的；而打分本身几乎不耗时，
        放进线程池只会拿"结果确定性"换一点点速度，是一笔亏本买卖。
        """
        contract_id = record["contract_id"]
        out_path = reports_dir / f"{safe_filename(contract_id)}.json"
        note = ""
        if args.resume and out_path.exists():
            try:
                report = ReviewReport.model_validate_json(out_path.read_text(encoding="utf-8"))
                note = "读取已有报告"
                mark(contract_id, note)
                return {"contract_id": contract_id, "report": report, "note": note}
            except Exception as exc:  # noqa: BLE001
                # 上次跑到一半被杀掉 → 可能留下不完整的报告：重跑这一份，而不是让整个评测崩掉
                note = f"⚠️报告损坏({exc.__class__.__name__})重跑"

        path = lookup(txt_index, contract_id)
        if path is None:
            mark(contract_id, "✗ 找不到文本")
            return {"contract_id": contract_id, "report": None, "note": note, "error": "找不到纯文本文件"}

        t0 = time.perf_counter()
        try:
            report, _ = review_contract(
                contract_id=contract_id,
                file_name=path.name,
                retriever=retriever,
                client=client,
                model=model,
                stance=ReaderStance.CUSTOMER,
                k=k,
                verbose=False,
                use_rerank=use_rerank,
                jobs=clause_jobs,
            )
        except Exception as exc:  # noqa: BLE001
            mark(contract_id, f"✗ {exc!r}")
            return {"contract_id": contract_id, "report": None, "note": note, "error": repr(exc)}
        elapsed = time.perf_counter() - t0
        if report is None:
            mark(contract_id, "✗ 报告不完整")
            return {"contract_id": contract_id, "report": None, "note": note, "error": "报告不完整（未覆盖 10 类）"}
        out_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        note = f"{elapsed:5.1f}s"
        mark(contract_id, note)
        return {"contract_id": contract_id, "report": report, "note": note}

    # ---- 第一阶段：生成 / 读取报告（贵，可并发）----
    # 用 map 而不是 as_completed：map 的返回顺序 = 提交顺序，与 records 一一对应。
    # 用 as_completed 会按"完成顺序"返回，后面的 zip 打分就会张冠李戴 —— 而且不会崩，只会算错分。
    if jobs > 1:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            produced = list(pool.map(produce, records))
    else:
        produced = [produce(r) for r in records]

    # ---- 第二阶段：按顺序打分（便宜，串行 → 指标累加确定性）----
    for idx, record in enumerate(records, 1):
        contract_id = record["contract_id"]
        item = produced[idx - 1]
        if item.get("error"):
            failures.append({"contract_id": contract_id, "error": item["error"]})
            if jobs <= 1:  # 并发模式下上面 mark() 已经打过这行
                print(f"[{idx}/{len(records)}] {contract_id[:56]:56s} ✗ {item['error']}")
            continue
        report = item["report"]
        note = item["note"]

        # ---- 打分 ----
        labels = record.get("labels", {})
        for finding in report.findings:
            cid = finding.clause_id
            m = metrics[cid]
            if finding.reason_zh.startswith(PIPELINE_ERROR_PREFIX):
                m.excluded += 1
                continue
            gold = labels.get(cid.value, {})
            gold_exists = bool(gold.get("exists", False))
            pred_exists = finding.exists

            if gold_exists and not pred_exists:
                m.fn += 1
                # 归因要用：模型当时的理由 + 金标准证据原文（判断"是没召回到"还是"看到了没认出来"）
                fn_cases.append(
                    {
                        "contract_id": contract_id,
                        "clause_id": cid.value,
                        "pred_confidence": finding.confidence,
                        "reason_zh": finding.reason_zh[:200],
                        "gold_evidence_head": (gold.get("evidence") or [{}])[0].get("text", "")[:130],
                    }
                )
            elif not gold_exists and pred_exists:
                m.fp += 1
                fp_cases.append(
                    {
                        "contract_id": contract_id,
                        "clause_id": cid.value,
                        "risk_level": finding.risk_level.value,
                        "reason_zh": finding.reason_zh[:200],
                        "pred_evidence_head": finding.evidence[0].text[:130] if finding.evidence else "",
                    }
                )
            elif gold_exists and pred_exists:
                m.tp += 1
                m.ev_total += 1
                pred_spans = [(e.char_start, e.char_end) for e in finding.evidence]
                gold_spans = [(e["char_start"], e["char_end"]) for e in gold.get("evidence", [])]
                if evidence_hit(pred_spans, gold_spans):
                    m.ev_hit += 1
                else:
                    evidence_miss_cases.append(
                        {"contract_id": contract_id, "clause_id": cid.value, "pred_spans": pred_spans}
                    )
            else:
                m.tn += 1

            # 必备条款"缺失"识别（只统计金标准为不存在的必备条款）
            if cid in REQUIRED_CLAUSES and not gold_exists:
                required_neg_total += 1
                if not pred_exists:
                    required_neg_caught += 1

        _, _, _, _, _, running_f1 = _micro(metrics)
        # 串行模式每份都打（与旧版输出一致）；并发模式每 10 份一次 —— 进度已在第一阶段实时可见，这里打太多是刷屏
        if jobs <= 1 or idx % 10 == 0 or idx == len(records):
            print(f"[{idx}/{len(records)}] {contract_id[:56]:56s} {note:>16s}  累计 F1={_fmt(running_f1)}")

    # ---- 汇总 ----
    total_time = time.perf_counter() - started_all
    macro_p, macro_r, macro_f1 = _macro(metrics)
    mtp, mfp, mfn, micro_p, micro_r, micro_f1 = _micro(metrics)
    ev_total = sum(m.ev_total for m in metrics.values())
    ev_hit = sum(m.ev_hit for m in metrics.values())
    excluded = sum(m.excluded for m in metrics.values())

    print("\n" + "=" * 84)
    print("逐类条款结果（存在性）")
    print("=" * 84)
    print(f"{'条款':26s} {'TP':>4s} {'FP':>4s} {'FN':>4s} {'TN':>4s} {'精确率':>7s} {'召回率':>7s} {'F1':>7s} {'证据命中':>10s}")
    print("-" * 84)
    for cid in ClauseId:
        m = metrics[cid]
        print(
            f"{cid.value:26s} {m.tp:4d} {m.fp:4d} {m.fn:4d} {m.tn:4d} "
            f"{_fmt(m.precision):>7s} {_fmt(m.recall):>7s} {_fmt(m.f1):>7s} "
            f"{f'{m.ev_hit}/{m.ev_total}' if m.ev_total else '—':>10s}"
        )
    print("-" * 84)
    print(f"{'宏平均（10 类等权）':26s} {'':4s} {'':4s} {'':4s} {'':4s} {_fmt(macro_p):>7s} {_fmt(macro_r):>7s} {_fmt(macro_f1):>7s}")
    print(f"{'微平均（汇总计数）':26s} {mtp:4d} {mfp:4d} {mfn:4d} {'':4s} {_fmt(micro_p):>7s} {_fmt(micro_r):>7s} {_fmt(micro_f1):>7s}")

    print("\n" + "=" * 84)
    print("三个核心指标")
    print("=" * 84)
    print(f"1. 存在性 F1        : 宏平均 {_fmt(macro_f1)}  |  微平均 {_fmt(micro_f1)}")
    print(f"2. 证据定位准确率   : {ev_hit}/{ev_total} = {_fmt(ev_hit / ev_total if ev_total else float('nan'))}"
          f"   （分母 = 存在性判对的阳性 case）")
    print(f"3. 必备条款缺失识别率: {required_neg_caught}/{required_neg_total} = "
          f"{_fmt(required_neg_caught / required_neg_total if required_neg_total else float('nan'))}"
          f"   （分母 = 金标准为不存在的 01/08/10）")

    if fn_cases or fp_cases:
        print("\n" + "=" * 84)
        print(f"存在性判错的 case（漏报 FN {len(fn_cases)} / 误报 FP {len(fp_cases)}）—— 归因清单")
        print("=" * 84)
        for case in fn_cases[:10]:
            print(f"漏报 {case['contract_id'][:44]:44s} {case['clause_id']:22s} conf={case['pred_confidence']:.2f}")
            print(f"     模型理由: {case['reason_zh']}")
            if case["gold_evidence_head"]:
                print(f"     金标准证据: {case['gold_evidence_head']}")
        for case in fp_cases[:10]:
            print(f"误报 {case['contract_id'][:44]:44s} {case['clause_id']:22s} risk={case['risk_level']}")
            print(f"     模型理由: {case['reason_zh']}")
            if case["pred_evidence_head"]:
                print(f"     模型证据: {case['pred_evidence_head']}")

    if evidence_miss_cases:
        print(f"\n存在性判对、但证据与金标准不重叠的 case（{len(evidence_miss_cases)} 个，属检索召回问题，见 BC-005）：")
        for case in evidence_miss_cases[:20]:
            print(f"  - {case['contract_id'][:50]:50s} {case['clause_id']:24s} {case['pred_spans']}")
        if len(evidence_miss_cases) > 20:
            print(f"  …… 其余 {len(evidence_miss_cases) - 20} 个见 metrics JSON")

    if excluded:
        print(f"\n⚠️ PIPELINE_ERROR 记录 {excluded} 条，已剔除（需你人工复核这些合同）")
    if failures:
        print(f"\n⚠️ 失败 {len(failures)} 份：")
        for f in failures[:10]:
            print(f"  - {f['contract_id'][:50]:50s} {f['error'][:90]}")

    print(f"\n总用时 {total_time / 60:.1f} 分钟（{len(records)} 份合同，平均 {total_time / max(len(records), 1):.1f} 秒/份）")

    summary = {
        "tag": args.tag,
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": {
            "model": model,
            "top_k": k,
            "reader_stance": "customer",
            "chunk_size": ENV.get("CHUNK_MAX_CHARS") or ENV.get("CHUNK_SIZE"),
            "chunk_overlap": ENV.get("CHUNK_OVERLAP"),
            "contracts_total": len(records),
            "rerank": {
                "enabled": rerank_on,
                "candidates": (rerank_candidates() if rerank_on else None),
                "model": (str(rerank_model_dir()) if rerank_on else None),
            },
        },
        "per_clause": [metrics[cid].as_dict() for cid in ClauseId],
        "overall": {
            "macro": {
                "precision": _nan_to_none(macro_p),
                "recall": _nan_to_none(macro_r),
                "f1": _nan_to_none(macro_f1),
            },
            "micro": {
                "tp": mtp,
                "fp": mfp,
                "fn": mfn,
                "precision": _nan_to_none(micro_p),
                "recall": _nan_to_none(micro_r),
                "f1": _nan_to_none(micro_f1),
            },
            "evidence_accuracy": ev_hit / ev_total if ev_total else None,
            "evidence_hit": ev_hit,
            "evidence_total": ev_total,
            "required_missing_recall": required_neg_caught / required_neg_total if required_neg_total else None,
            "excluded_pipeline_errors": excluded,
        },
        "evidence_miss_cases": evidence_miss_cases,
        "fn_cases": fn_cases,
        "fp_cases": fp_cases,
        "failures": failures,
    }
    metrics_path = EVAL_DIR / f"metrics_{args.tag}.json"
    metrics_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"指标已保存：{metrics_path.relative_to(ROOT)}  （Step 5 做前后对比时直接读它）")


if __name__ == "__main__":
    main()
