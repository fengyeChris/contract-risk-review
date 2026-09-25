"""
schemas.py 的自测用例

这是本项目的"及格线"：
- 跑一遍就知道输出契约有没有被破坏
- 每一条测试都对应 mvp_scope.md 的一条规则（注释里标了出处）

运行方式（在 hetong/ 目录下）：
    C:\\Users\\Admin\\.conda\\envs\\langchain1.2\\python.exe -m pytest -q
"""

import pytest
from pydantic import ValidationError

from schemas import (
    REQUIRED_CLAUSES,
    ClauseFinding,
    ClauseId,
    Evidence,
    ReaderStance,
    ReviewReport,
    RiskLevel,
    compute_missing_required,
)


# ---------- 构造测试数据的辅助函数 ----------


def _finding(
    clause_id: ClauseId,
    exists: bool = True,
    risk: RiskLevel = RiskLevel.LOW,
    evidence: list | None = None,
    confidence: float = 0.5,  # 默认不带证据，所以置信度默认压在 0.5（见 schemas.py 防幻觉校验）
) -> ClauseFinding:
    return ClauseFinding(
        clause_id=clause_id,
        exists=exists,
        risk_level=risk,
        confidence=confidence,
        evidence=evidence or [],
        reason_zh="测试用理由",
        gaps=[],
    )


def _all_findings() -> list[ClauseFinding]:
    return [_finding(cid) for cid in ClauseId]


def _report(findings: list[ClauseFinding]) -> ReviewReport:
    return ReviewReport(
        contract_id="cuad-test-001",
        file_name="test_contract.txt",
        reader_stance=ReaderStance.CUSTOMER,
        findings=findings,
        missing_required_clauses=[],
        summary_zh="测试报告",
    )


def _report_with(overrides: list[ClauseFinding]) -> ReviewReport:
    """在"10 类齐全"的基础上，替换掉指定类别的结论"""
    by_id = {f.clause_id: f for f in _all_findings()}
    by_id.update({f.clause_id: f for f in overrides})
    return _report([by_id[cid] for cid in ClauseId])


# ---------- 测试：合法输入应当通过 ----------


def test_valid_report_passes():
    report = _report(_all_findings())
    assert report.contract_id == "cuad-test-001"
    assert report.reader_stance == ReaderStance.CUSTOMER  # §0.4：默认客户视角
    assert len(report.findings) == 10


# ---------- 测试：存在性 与 风险等级 的一致性（§0.3）----------


def test_missing_clause_must_be_na():
    """条款不存在时，风险必须是 na，不能填 high"""
    with pytest.raises(ValidationError):
        _finding(ClauseId.RENEWAL, exists=False, risk=RiskLevel.HIGH)


def test_existing_clause_must_not_be_na():
    """条款存在时，风险不允许是 na  ← 第 1 处空白补完后，这条应当通过"""
    with pytest.raises(ValidationError):
        _finding(ClauseId.RENEWAL, exists=True, risk=RiskLevel.NA)


# ---------- 测试：报告结构完整性 ----------


def test_findings_must_cover_all_ten():
    """findings 少一类就该报错"""
    with pytest.raises(ValidationError):
        _report(_all_findings()[:-1])


# ---------- 测试：证据片段 ----------


def test_evidence_span_must_be_valid():
    """char_end 必须大于 char_start，且长度与 text 一致"""
    with pytest.raises(ValidationError):
        Evidence(text="x", char_start=10, char_end=10)
    with pytest.raises(ValidationError):
        Evidence(text="xxxxx", char_start=0, char_end=3)

    ok = Evidence(text="abc", char_start=5, char_end=8)
    assert ok.matched_categories == []


def test_multiple_categories_allowed_on_one_evidence():
    """§0.2 铁律②：一条证据可以命中多个类别"""
    ev = Evidence(
        text="abc",
        char_start=0,
        char_end=3,
        matched_categories=[ClauseId.ANTI_ASSIGNMENT, ClauseId.CHANGE_OF_CONTROL],
    )
    assert len(ev.matched_categories) == 2


# ---------- 测试：防幻觉 ----------


def test_no_evidence_limits_confidence():
    """命中但没有证据片段时，置信度不得超过 0.5"""
    with pytest.raises(ValidationError):
        _finding(ClauseId.GOVERNING_LAW, confidence=0.9)

    ok = _finding(ClauseId.GOVERNING_LAW, confidence=0.5)
    assert ok.confidence == 0.5


# ---------- 测试：必备条款清单（§0.3）----------


def test_required_clauses_are_the_three_from_scope():
    """必备清单只含 01 争议解决、08 责任上限、10 知识产权归属（§0.3 / §0.6）"""
    expected = {
        ClauseId.GOVERNING_LAW,
        ClauseId.CAP_ON_LIABILITY,
        ClauseId.IP_OWNERSHIP,
    }
    assert set(REQUIRED_CLAUSES) == expected


def test_compute_missing_required_ignores_non_required_clauses():
    """08 责任上限缺失 → 必须报警；04 竞业限制缺失 → 只报告、不报警"""
    report = _report_with(
        [
            _finding(ClauseId.CAP_ON_LIABILITY, exists=False, risk=RiskLevel.NA),
            _finding(ClauseId.NON_COMPETE, exists=False, risk=RiskLevel.NA),
        ]
    )
    assert compute_missing_required(report) == [ClauseId.CAP_ON_LIABILITY]
