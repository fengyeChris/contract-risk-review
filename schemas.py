"""
合同风险条款审查 —— 输入 / 输出数据契约（Pydantic schema）

本文件是 mvp_scope.md 的代码化落地，对应关系如下：
- §0.2 铁律②：一条证据可以命中多个类别 → Evidence.matched_categories 是 list
- §0.3：存在性（exists）与风险等级（risk_level）是两个独立维度；
        条款不存在时 risk_level 必须是 "na"，不能是 "high"
- §0.3：缺失必备条款清单只纳入 01 争议解决 / 08 责任上限 / 09 赔偿 / 10 知识产权归属
- §0.4：风险判读立场（reader_stance）MVP 固定为 "customer"（客户 / 买方视角）

环境：Python 3.13 + pydantic 2.12.5（已在 conda 环境 langchain1.2 中验证）
"""

from enum import Enum

from pydantic import BaseModel, Field, model_validator

# ============================================================
# 一、枚举（Enum）：把"允许的取值"写死成代码
# 目的：LLM 只能从固定集合里选，避免输出 "High" / "高风险" / "5" 这类乱编的值
# ============================================================


class ClauseId(str, Enum):
    """10 类条款的稳定 ID（编号固定，便于评测集与报告对齐）"""

    GOVERNING_LAW = "01_governing_law"
    CHANGE_OF_CONTROL = "02_change_of_control"
    ANTI_ASSIGNMENT = "03_anti_assignment"
    NON_COMPETE = "04_non_compete"
    EXCLUSIVITY = "05_exclusivity"
    TERMINATION_NOTICE = "06_termination_notice"
    RENEWAL = "07_renewal"
    CAP_ON_LIABILITY = "08_cap_on_liability"
    LIQUIDATED_DAMAGES = "09_liquidated_damages"
    IP_OWNERSHIP = "10_ip_ownership"


class RiskLevel(str, Enum):
    """风险等级。na = 条款不存在，此时不评估风险（见 mvp_scope.md §0.3）"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NA = "na"


class ReaderStance(str, Enum):
    """风险判读立场。MVP 固定 customer；vendor 留到 Step 5 之后"""

    CUSTOMER = "customer"
    VENDOR = "vendor"


# ============================================================
# 二、输入契约
# ============================================================


class ContractInput(BaseModel):
    """一份待审查的合同"""

    contract_id: str = Field(..., min_length=1, description="合同唯一标识，评测集与报告用它对齐")
    file_name: str = Field(..., min_length=1, description="原始文件名，便于人工复核时定位")
    text: str = Field(..., min_length=1, description="CUAD 纯文本全文")
    reader_stance: ReaderStance = Field(
        default=ReaderStance.CUSTOMER,
        description="风险判读立场，MVP 固定为客户/买方视角",
    )


# ============================================================
# 三、输出契约
# ============================================================


class Evidence(BaseModel):
    """证据片段：模型判定"该条款存在"所依据的原文位置"""

    text: str = Field(..., min_length=1, description="原文片段（不得改写、不得翻译）")
    char_start: int = Field(..., ge=0, description="片段在全文中的起始字符下标")
    char_end: int = Field(..., ge=0, description="片段在全文中的结束字符下标（不含）")
    section: str | None = Field(default=None, description="章节号，如 'Section 12.4'，未知则留空")
    matched_categories: list[ClauseId] = Field(
        default_factory=list,
        description="该片段命中的类别，可为多个（见 mvp_scope.md §0.2 铁律②）",
    )

    @model_validator(mode="after")
    def _span_must_be_valid(self):
        if self.char_end <= self.char_start:
            raise ValueError("char_end 必须大于 char_start")
        if self.char_end - self.char_start != len(self.text):
            raise ValueError("char_end - char_start 必须等于 text 的字符长度")
        return self


class ClauseFinding(BaseModel):
    """一类条款的审查结论"""

    clause_id: ClauseId
    exists: bool = Field(..., description="该条款是否存在")
    risk_level: RiskLevel = Field(..., description="风险等级；exists=False 时必须是 na")
    confidence: float = Field(..., ge=0.0, le=1.0, description="模型对自己结论的置信度")
    evidence: list[Evidence] = Field(default_factory=list, description="证据片段，可为空")
    reason_zh: str = Field(..., min_length=1, description="判定理由（中文），供人工复核")
    gaps: list[str] = Field(default_factory=list, description="缺口与建议补充项，如'未约定违约金总额上限'")

    @model_validator(mode="after")
    def _exists_and_risk_must_be_consistent(self):
        # 规则①：条款不存在 → 风险必须是 na（见 §0.3）
        if not self.exists and self.risk_level != RiskLevel.NA:
            raise ValueError("exists=False 时，risk_level 必须是 'na'（见 mvp_scope.md §0.3）")

        
        # 规则②：条款存在 → 风险不允许是 na
        if self.exists and self.risk_level == RiskLevel.NA:
            raise ValueError("exists=True 时，risk_level 不允许是 'na'（见 mvp_scope.md §0.3）")

        return self

    @model_validator(mode="after")
    def _no_evidence_means_low_confidence(self):
        # 命中却没有证据片段 → 不允许给高置信度（防幻觉；Step 4 会专项统计这类 case）
        if self.exists and not self.evidence and self.confidence > 0.5:
            raise ValueError("exists=True 但没有 evidence 时，confidence 必须 ≤ 0.5")
        return self


class ReviewReport(BaseModel):
    """一份合同的完整审查报告（顶层输出）"""

    contract_id: str = Field(..., min_length=1)
    file_name: str = Field(..., min_length=1)
    reader_stance: ReaderStance = Field(default=ReaderStance.CUSTOMER)
    findings: list[ClauseFinding] = Field(..., description="必须覆盖全部 10 类条款")
    missing_required_clauses: list[ClauseId] = Field(
        default_factory=list, description="真正缺失的必备条款（见 §0.3）"
    )
    summary_zh: str = Field(..., min_length=1, description="中文摘要，给非技术读者看")

    @model_validator(mode="after")
    def _findings_must_cover_all_ten(self):
        found = {f.clause_id for f in self.findings}
        if len(found) != len(self.findings):
            raise ValueError("findings 中不得出现重复的 clause_id")
        missing = set(ClauseId) - found
        if missing:
            names = ", ".join(sorted(m.value for m in missing))
            raise ValueError(f"findings 必须覆盖全部 10 类条款，当前缺少：{names}")
        return self


# ============================================================
# 四、必备条款清单与缺失计算
# ============================================================


# 依据 mvp_scope.md §0.3 / §0.6：「缺失必备条款清单」只纳入真正必备的类，
# 其余类别缺失只报告、不报警。
# 必备清单（3 类）：01 争议解决 / 08 责任上限 / 10 知识产权归属
# 口径变更：原第 9 类「赔偿条款」已改为「违约金（Liquidated Damages）」，
#          违约金缺失不属于必备问题 → 从清单中移除（见 mvp_scope.md §0.6）
REQUIRED_CLAUSES: frozenset[ClauseId] = frozenset(
    {
        ClauseId.GOVERNING_LAW,
        ClauseId.CAP_ON_LIABILITY,
        ClauseId.IP_OWNERSHIP,
    }
)


def compute_missing_required(report: ReviewReport) -> list[ClauseId]:
    """算出"缺失且必备"的条款，按 ClauseId 定义顺序返回（顺序固定，便于报告对比）"""
    return [
        cid
        for cid in ClauseId
        if cid in REQUIRED_CLAUSES
        and not any(f.clause_id == cid and f.exists for f in report.findings)
    ]
