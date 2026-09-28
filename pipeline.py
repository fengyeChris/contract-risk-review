"""
Step 3.4：LLM 审查管线（检索 → 组装 prompt → 调 LLM → 校验 → 证据定位）

四个环节（对应 3.4 原理与 mvp_scope.md §0.9）

① **组装 prompt**：固定立场 + 该条款的判定标准 / 边界 / 风险三档（来自 clauses.py）
   + 「证据必须原文照抄」+ JSON 输出要求 + 置信度刻度
② **调 LLM**：`response_format={"type": "json_object"}`、`temperature=0`
   （JSON 模式已用 scripts/smoke_llm.py 实测确认支持）
③ **解析 + 校验**：先用 `validate_payload` 校验**模型该负责的部分**（字段齐全 / 取值合法），
   不合格就把校验错误**回灌**给模型重试（最多 2 次）；仍不合格 → 标记 `[PIPELINE_ERROR]`，
   **绝不编造结论**（Step 4 按该前缀过滤）。
   校验通过后才走 `to_finding` —— 那之后的异常是我们自己的 bug，直接抛出、不重试
④ **证据定位（两段式）**：模型只负责"原文引用"，**字符位置由程序算**
   + 防幻觉检查：引用必须来自喂给它的 chunk，否则视为幻觉候选

一句话原则：**模型干它擅长的（理解/生成文本），程序干它擅长的（精确计算/查找）。**
"""

import json
import time
from dataclasses import dataclass

from clauses import CLAUSES, ClauseSpec
from config import (
    ENV,
    build_llm_client,
    llm_extra_body,
    llm_temperature,
    rerank_candidates,
    rerank_device,
    rerank_enabled,
    rerank_max_length,
    rerank_model_dir,
)
from schemas import (
    ClauseFinding,
    ClauseId,
    Evidence,
    ReaderStance,
    ReviewReport,
    RiskLevel,
    compute_missing_required,
)
from text_utils import find_span_loose  # noqa: F401  （locate_quote 的填空会用到它）

STANCE_DESC = {
    ReaderStance.CUSTOMER: "客户 / 买方 / 服务接收方（我方是付钱并接收服务的一方）",
    ReaderStance.VENDOR: "供应方 / 卖方（我方是提供服务并收款的一方）",
}

OUTPUT_FORMAT = """{
  "exists": <true 或 false>,
  "risk_level": <"high" | "medium" | "low" | "na">,
  "confidence": <0.0 ~ 1.0 之间的小数>,
  "reason_zh": "<中文判定理由，2~4 句，说明依据了文本里的哪些表述>",
  "gaps": ["<中文：建议补充或值得关注的缺口；没有就空数组>"],
  "evidence_quotes": ["<从下面文本中逐字照抄的英文原文；没有就空数组>"]
}"""

SYSTEM_PROMPT = """你是专业的商业合同风险条款审查助手。
你的工作是：判断给定合同文本中是否存在指定的条款，并站在固定立场上给出风险判读与原文证据。

必须遵守的规则：
1. 立场固定为：{stance}。不要切换立场，也不要在输出里讨论立场。
2. 只依据下方提供的合同文本判断，不得引入外部知识、行业惯例或想象的内容。
3. `evidence_quotes` 里的每一段都必须是**从提供文本中逐字照抄的英文原文**：
   不得改写、不得翻译、不得缩写、不得添加省略号。
4. 只输出一个 JSON 对象；不要输出解释文字，不要用 Markdown 代码块包裹。"""

UNLOCATABLE = "unlocatable"


# ================================================================
# ⓿ 两段式检索（Step 5.3 增强①：粗召 → rerank 精排）
# ================================================================
_RERANKER = None


def get_reranker(use_rerank: bool | None = None):
    """
    惰性加载重排模型（进程内只加载一次）。返回 `Reranker` 或 `None`。

    开关来自 `.env` 的 `RERANK_ENABLED`；传 `use_rerank=True/False` 可显式覆盖 ——
    Step 5 做"加/不加该增强"的前后对比时用这个参数，而不是去改 .env（改了容易忘，对比就脏了）。
    """
    global _RERANKER
    enabled = rerank_enabled() if use_rerank is None else use_rerank
    if not enabled:
        return None
    if _RERANKER is None:
        from reranker import Reranker

        _RERANKER = Reranker(rerank_model_dir(), device=rerank_device(), max_length=rerank_max_length())
        print(f"[重排已启用] {rerank_model_dir().name} @ {_RERANKER.device} | "
              f"粗召 {rerank_candidates()} → 精排取 {ENV.get('TOP_K', '5')} 段喂 LLM")
    return _RERANKER


def retrieve_for_clause(retriever, reranker, contract_id: str, query: str, k: int) -> list[dict]:
    """
    两段式检索。依据（`eval/retrieval_baseline.json` vs `eval/retrieval_rerank_full.json` 全量 198 份实测）：
        hit@5 0.766 → **0.847**（+8.1 pp），MRR 0.607 → **0.737**（+13.0 pp），hit@20 不变（同集合）。

    · 未启用重排 → 直接向量检索 top-k；
    · 启用重排   → 向量**粗召** candidates 个 → cross-encoder **精排** → 取前 k 段喂给 LLM。
      粗召保证"证据在池子里"，精排保证"证据排在前面"，而喂给 LLM 的段数不变 —— **prompt 不膨胀**。
    """
    if reranker is None:
        return retriever.search(contract_id, query, k=k)
    hits = retriever.search(contract_id, query, k=max(rerank_candidates(), k))
    return reranker.rerank(query, hits, top_n=k)


# ================================================================
# ① 组装 prompt
# ================================================================
def build_messages(spec: ClauseSpec, hits: list[dict], stance: ReaderStance) -> list[dict]:
    context = "\n\n".join(
        f"[片段 {i}｜原文位置 {h['char_start']}-{h['char_end']}]\n{h['text']}" for i, h in enumerate(hits, 1)
    )
    user = f"""请判断下面这份合同中，**是否存在**「{spec.name_zh}（{spec.name_en}）」条款。

【命中标准】
{spec.criteria}

【边界：像但不算命中】
{spec.boundaries}

【风险三档（判读风险的依据）】
{spec.risk_tiers}

【第一步：先确定"我方"是谁（必须做）】
本合同没有标注甲乙方身份。请先判断"我方"对应哪一方，并把这个结论写在 reason_zh 的**开头**，
格式如：`我方=Distributor（经销商，支付货款并接收产品的一方）。……`
口径：我方 = 支付价款并接收产品或服务的一方（买方 / 客户 / 经销商 / 被许可方）。
风险判读一律站在"我方"立场：**限制我方、剥夺我方权利 = 风险高；限制对方 = 风险低**。
若从文本无法判断我方是谁，按最保守口径（风险更高）判读，并在 reason_zh 里注明"无法确定我方身份"。

【补充规则】
- exists=false 时 risk_level 必须是 "na"；exists=true 时 risk_level 不得为 "na"。
- confidence 是**你对本次结论的把握**（无论结论是"存在"还是"不存在"），刻度：
  0.90 以上 = 文本有直接、明确的表述（或明确完全没有相关约定）；0.70~0.90 = 需少量推断；0.70 以下 = 依据薄弱。
- ⚠️ 判定"不存在"且你有把握时，confidence 也应该高（例如 0.9）；**不要因为"没找到证据"就给 0**。
- 若条款存在但只是"引用式"表述（如 see Section 14.2），可把该引用句本身当作证据。

【输出格式（只输出这个 JSON 对象）】
{OUTPUT_FORMAT}

【合同文本片段】
<<<
{context}
>>>"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(stance=STANCE_DESC[stance])},
        {"role": "user", "content": user},
    ]


# ================================================================
# ④ 证据定位（本轮唯一填空）
# ================================================================
def locate_quote(quote: str, hits: list[dict]) -> tuple[int | None, int | None, str | None, str]:
    """
    ✅ 已实现（学习者补写，复核后修正 2 处：`hit["id"]` → `hit["chunk_id"]`；
       兜底分支必须是 `return 失败值`，不能 `raise` —— 否则"疑似幻觉"这个信号会变成崩溃）

    为什么必须由程序来做：LLM 不"数数"，它给的 char_start / char_end 会看着合理但实际错位，
    而且这种错误**很难被发现**（报告看起来很完整）。所以让它只负责"照抄原文"，位置由程序算。

    输入：
        quote : 模型输出的英文原文引用
        hits  : 检索返回的 chunk 列表，每项含 `text`（该 chunk 的原文）与 `char_start`（该 chunk 在全文中的起点）

    输出（四元组）：
        (char_start, char_end, chunk_id, located_text)
        · char_start / char_end 必须是**全文绝对坐标** = 该 chunk 的 char_start + 片段在 chunk 内的局部偏移
        · located_text 取该区间的原文（用它填 Evidence.text，才能满足 `len(text) == char_end - char_start`）
        · 在所有 chunk 里都找不到 → 返回 (None, None, None, "")
          这同时就是**防幻觉检查**：模型引用了我们没给它的内容

    按这个顺序写（提示）：
        1) 精确匹配：      local = hit["text"].find(quote)
        2) 忽略空白定位：  span = find_span_loose(hit["text"], quote)   # 返回 (局部起点, 局部终点)
        3) 两者都不中 → 换下一个 chunk 继续；全部失败 → 返回失败值
    """
    for hit in hits:
        local = hit["text"].find(quote)
        if local >= 0:
            end = local + len(quote)
            return (
                hit["char_start"] + local,
                hit["char_start"] + end,
                hit["chunk_id"],
                hit["text"][local:end],
            )
        span = find_span_loose(hit["text"], quote)
        if span is not None:
            return (
                hit["char_start"] + span[0],
                hit["char_start"] + span[1],
                hit["chunk_id"],
                hit["text"][span[0]:span[1]],
            )
    # 所有 chunk 都找不到 → 这是"防幻觉"信号，不是崩溃：交给上层记入 gaps 并压低置信度
    return None, None, None, ""


# ================================================================
# ③ 解析 + 校验（模型输出 → ClauseFinding）
# ================================================================
def to_finding(clause_id: ClauseId, payload: dict, hits: list[dict]) -> ClauseFinding:
    """把模型返回的 JSON 转成 ClauseFinding；证据走两段式定位 + 防幻觉检查"""
    quotes = [q.strip() for q in (payload.get("evidence_quotes") or []) if isinstance(q, str) and q.strip()]

    evidence: list[Evidence] = []
    unlocatable: list[str] = []
    for quote in quotes:
        start, end, _chunk_id, text = locate_quote(quote, hits)
        if start is None or end is None:
            unlocatable.append(quote[:60])
            continue
        evidence.append(Evidence(text=text, char_start=start, char_end=end, matched_categories=[clause_id]))

    exists = bool(payload["exists"])
    confidence = float(payload["confidence"])
    gaps = list(payload.get("gaps") or [])

    if unlocatable:
        gaps.append(f"模型引用但在提供的文本中定位不到（疑似幻觉）：{unlocatable[0]}…")
    if exists and not evidence:
        # 与 schemas.py 的防幻觉规则一致：命中但拿不出可定位证据 → 压低置信度（不熔断）
        confidence = min(confidence, 0.5)

    return ClauseFinding(
        clause_id=clause_id,
        exists=exists,
        risk_level=RiskLevel(payload["risk_level"]),
        confidence=confidence,
        evidence=evidence,
        reason_zh=payload.get("reason_zh") or "(模型未给出理由)",
        gaps=gaps,
    )


def parse_json(raw: str) -> dict:
    """容错解析：模型偶尔会用 ```json 包裹或前后多说话"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def validate_payload(payload: dict) -> None:
    """
    只校验「模型该负责」的部分：字段齐全 + 取值合法。

    设计原则：**只有"对方的错"才值得重试。**
    · 这里的异常由模型造成 → 回灌错误让它自己改，重试有意义；
    · `to_finding` / `locate_quote` 里的异常是我们自己的 bug → 重试三次只会白烧 API 额度，
      还会把真正的 bug 伪装成 `[PIPELINE_ERROR]`（本次实测：一个 `hit["id"]` 键名写错，
      烧掉 12 次调用，并让报告显示"10 类条款全部缺失"这种完全错误的结论）。
    """
    if not isinstance(payload, dict):
        raise ValueError("输出必须是 JSON 对象")
    for key in ("exists", "risk_level", "confidence", "reason_zh"):
        if key not in payload:
            raise ValueError(f"缺少必填字段 {key!r}")
    if not isinstance(payload["exists"], bool):
        raise ValueError("exists 必须是 true 或 false")
    RiskLevel(payload["risk_level"])  # 非法取值由枚举白名单拦下
    # 跨字段一致性：**这属于"模型该负责的部分"** —— 它给出了自相矛盾的 payload，应该重试而不是判我们自己的错。
    # 实测教训：全量 198 份里有 1 份模型返回了 exists=true + risk_level="na"，
    # 因为这里没校验、而 ClauseFinding 的校验器在重试环之外，结果整份合同失败（其余 9 类结论一起丢）。
    exists = payload["exists"]
    if exists and payload["risk_level"] == RiskLevel.NA.value:
        raise ValueError('exists=true 时 risk_level 不允许是 "na"，请给出 high / medium / low 之一')
    if not exists and payload["risk_level"] != RiskLevel.NA.value:
        raise ValueError('exists=false 时 risk_level 必须是 "na"')
    confidence = float(payload["confidence"])
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence 必须在 0~1 之间")
    quotes = payload.get("evidence_quotes", [])
    if not isinstance(quotes, list) or any(not isinstance(q, str) for q in quotes):
        raise ValueError("evidence_quotes 必须是字符串数组")


# ================================================================
# ② 调 LLM + 重试
# ================================================================
@dataclass
class ClauseRun:
    finding: ClauseFinding
    latency: float
    attempts: int


def call_llm(client, model: str, messages: list[dict]) -> tuple[str, float]:
    started = time.perf_counter()
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=llm_temperature(),  # 来自 .env（智谱要求 > 0，故配 0.01）
        response_format={"type": "json_object"},
        max_tokens=1600,  # 留足空间：某些模型（如 GLM）的思考 token 也会占用额度
        extra_body=llm_extra_body(),  # 供应商特有参数，如智谱关闭思考模式
    )
    return resp.choices[0].message.content, time.perf_counter() - started


def review_clause(client, model: str, spec: ClauseSpec, hits: list[dict], stance: ReaderStance,
                  max_retries: int = 2) -> ClauseRun:
    """跑一类条款：调 LLM → 校验模型输出；不合格把错误回灌重试；仍不合格则标记 [PIPELINE_ERROR]"""
    messages = build_messages(spec, hits, stance)
    started = time.perf_counter()
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 2):
        raw, _ = call_llm(client, model, messages)
        try:
            payload = parse_json(raw)
            validate_payload(payload)  # ← 只校验「模型该负责」的部分
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": f"你的输出不符合要求：{exc}\n请只输出修正后的 JSON 对象，不要任何解释。"},
            ]
            continue

        # 模型输出已合格。再往下若有异常，一定是我们自己的代码 bug → 直接抛出，不重试
        return ClauseRun(
            finding=to_finding(spec.clause_id, payload, hits),
            latency=time.perf_counter() - started,
            attempts=attempt,
        )

    # 连续失败：不编造结论，留可过滤的标记（Step 4 用 [PIPELINE_ERROR] 前缀剔除）
    return ClauseRun(
        finding=ClauseFinding(
            clause_id=spec.clause_id,
            exists=False,
            risk_level=RiskLevel.NA,
            confidence=0.0,
            evidence=[],
            reason_zh=f"[PIPELINE_ERROR] 模型输出连续 {max_retries + 1} 次未通过校验：{last_error}",
            gaps=["该条结论不可信，需人工复核；评测时应剔除"],
        ),
        latency=time.perf_counter() - started,
        attempts=max_retries + 1,
    )


# ================================================================
# 顶层：审查一份合同
# ================================================================
def build_summary_zh(findings: list[ClauseFinding], missing: list[ClauseId]) -> str:
    """生成中文摘要（纯程序拼装，不再调一次 LLM —— 摘要不该是另一个幻觉来源）"""
    exists = [f for f in findings if f.exists]
    high = [f for f in findings if f.risk_level == RiskLevel.HIGH]
    low_conf = [f for f in findings if f.confidence < 0.6]
    parts = [
        f"共检查 {len(findings)} 类条款：存在 {len(exists)} 类，缺失 {len(findings) - len(exists)} 类。",
    ]
    if missing:
        parts.append(f"其中必备条款缺失 {len(missing)} 类：" + "、".join(m.value for m in missing) + "。")
    else:
        parts.append("必备条款（01 争议解决 / 08 责任上限 / 10 知识产权归属）均已检出。")
    if high:
        parts.append(f"高风险 {len(high)} 类：" + "、".join(f.clause_id.value for f in high) + "。")
    if low_conf:
        parts.append(f"置信度低于 0.6 的有 {len(low_conf)} 类，建议人工复核。")
    return "".join(parts)


def review_contract(contract_id: str, file_name: str, retriever, client, model: str,
                    stance: ReaderStance = ReaderStance.CUSTOMER, k: int | None = None,
                    only: set[ClauseId] | None = None, verbose: bool = True,
                    use_rerank: bool | None = None):
    """跑完整份合同。返回 (report | None, runs)；只跑部分条款时不生成完整报告"""
    k = k or int(ENV.get("TOP_K", "5"))
    reranker = get_reranker(use_rerank)
    specs = [s for s in CLAUSES if not only or s.clause_id in only]
    runs: list[ClauseRun] = []

    for spec in specs:
        hits = retrieve_for_clause(retriever, reranker, contract_id, spec.retrieval_query, k)
        if verbose:
            if not hits:
                detail = "无命中"
            elif reranker is not None:
                detail = f"精排 {len(hits)} 段（最高分 {hits[0].get('rerank_score', float('nan')):.3f}）"
            else:
                detail = f"向量 {len(hits)} 段（最近距离 {hits[0]['distance']:.4f}）"
            print(f"  {spec.clause_id.value:24s} ① {detail} → ② 调用 LLM…")
        run = review_clause(client, model, spec, hits, stance)
        runs.append(run)
        if verbose:
            f = run.finding
            print(
                f"  {spec.clause_id.value:24s} ③ 校验通过 exists={str(f.exists):5s} risk={f.risk_level.value:6s} "
                f"conf={f.confidence:4.2f} 证据={len(f.evidence)} 用时={run.latency:5.1f}s 重试={run.attempts - 1}"
            )

    findings = [r.finding for r in runs]
    if len(findings) != len(ClauseId):
        if verbose:
            print(f"\n（只跑了 {len(findings)}/{len(ClauseId)} 类条款，跳过完整报告的校验）")
        return None, runs

    report = ReviewReport(
        contract_id=contract_id,
        file_name=file_name,
        reader_stance=stance,
        findings=findings,
        missing_required_clauses=[],
        summary_zh="(待填充)",
    )
    report.missing_required_clauses = compute_missing_required(report)
    report.summary_zh = build_summary_zh(findings, report.missing_required_clauses)
    return report, runs


def default_client():
    return build_llm_client()
