"""
离线查看审查报告（**零模型、零 API、零向量库**）

**为什么需要它**：别人 clone 下来最想看的是"输出长什么样"，而不是先装 3GB 本地模型、
再充 API 钱。这个脚本只读 `examples/` 里预先生成好的报告 JSON 与合同原文，纯本地、秒级。

它顺便演示了本项目最核心的一条设计：**证据的字符位置是程序算出来的**，不是模型写的 ——
所以 `--evidence` 能把原文按位置精确切出来，并且当场校验"切出来的 = 报告里存的"。
模型不参与算位置，也就没有"看起来合理但实际错位"的可能。

用法（在 hetong/ 目录下）：
    python -X utf8 examples/show_report.py                    # 列出样例，渲染第一份
    python -X utf8 examples/show_report.py limeenergy         # 按名称片段选一份
    python -X utf8 examples/show_report.py --all --evidence   # 全部渲染 + 打印证据原文
"""

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from clauses import BY_ID  # noqa: E402
from schemas import ReviewReport  # noqa: E402

RISK_ZH = {"high": "高", "medium": "中", "low": "低", "na": "—"}
LINE = "─" * 88


def load_reports() -> list[tuple[Path, ReviewReport]]:
    reports = []
    for path in sorted((HERE / "reports").glob("*.json")):
        reports.append((path, ReviewReport.model_validate_json(path.read_text(encoding="utf-8"))))
    if not reports:
        raise SystemExit("❌ examples/reports/ 下没有报告。先跑：python -X utf8 scripts/build_examples.py")
    return reports


def render(path: Path, report: ReviewReport, show_evidence: bool) -> None:
    contract_path = HERE / "contracts" / report.file_name
    text = contract_path.read_text(encoding="utf-8") if contract_path.exists() else None

    print("=" * 88)
    print(f"报告文件 : {path.name}")
    print(f"合同原文 : {report.file_name}" + (f"（{len(text):,} 字符）" if text else "（原文不在 examples/ 里）"))
    print(f"读者立场 : {report.reader_stance.value}（客户/买方视角）")
    print(f"摘要     : {report.summary_zh}")
    if report.missing_required_clauses:
        print(f"⚠️ 必备条款缺失 : {'、'.join(c.value for c in report.missing_required_clauses)}")
    print("=" * 88)

    # ---- 总览表 ----
    print(f"{'条款':<28}{'存在':<6}{'风险':<6}{'置信度':<9}{'证据':<5}理由摘要")
    print(LINE)
    for f in report.findings:
        reason = f.reason_zh.replace("\n", " ")[:38]
        print(
            f"{f.clause_id.value:<28}{'✅' if f.exists else '❌':<6}"
            f"{RISK_ZH.get(f.risk_level.value, f.risk_level.value):<6}{f.confidence:<9.2f}"
            f"{len(f.evidence):<5}{reason}…"
        )

    # ---- 逐条明细 ----
    for f in report.findings:
        spec = BY_ID.get(f.clause_id)
        title = spec.name_zh if spec else f.clause_id.value
        print(f"\n{LINE}")
        print(
            f"[{f.clause_id.value}] {title}    存在 {'是' if f.exists else '否'} ｜ "
            f"风险 {RISK_ZH.get(f.risk_level.value, f.risk_level.value)} ｜ 置信度 {f.confidence:.2f}"
        )
        print(f"  理由：{f.reason_zh}")
        if f.gaps:
            for gap in f.gaps:
                print(f"  缺口：{gap}")
        if not f.evidence:
            continue
        for i, e in enumerate(f.evidence, 1):
            print(f"  证据 {i}｜原文位置 {e.char_start}-{e.char_end}", end="")
            if text is not None:
                # 当场校验：按位置从原文切出来的那一段，必须与报告里存的原文完全一致
                ok = text[e.char_start:e.char_end] == e.text
                print(f"　按位置切原文 = {'一致 ✅' if ok else '不一致 ❌'}")
            else:
                print()
            if show_evidence:
                snippet = e.text if len(e.text) <= 400 else e.text[:400] + " …"
                for line in snippet.split("\n"):
                    print(f"      │ {line}")


def main() -> None:
    parser = argparse.ArgumentParser(description="离线查看审查报告（不联网、不加载模型）")
    parser.add_argument("name", nargs="?", default=None, help="按样例名称片段筛选，如 limeenergy")
    parser.add_argument("--all", action="store_true", help="渲染全部样例")
    parser.add_argument("--evidence", action="store_true", help="打印证据原文（默认只打印位置）")
    args = parser.parse_args()

    reports = load_reports()
    print("可用样例：")
    for i, (path, report) in enumerate(reports, 1):
        n_exists = sum(1 for f in report.findings if f.exists)
        n_missing = len(report.missing_required_clauses)
        print(f"  {i}. {report.contract_id[:64]}")
        print(f"     存在 {n_exists}/10 类 ｜ 必备条款缺失 {n_missing} 类 ｜ 证据 {sum(len(f.evidence) for f in report.findings)} 条")
    print()

    selected = reports
    if args.name:
        key = args.name.lower().replace(" ", "")
        selected = [(p, r) for p, r in reports if key in r.contract_id.lower().replace(" ", "")]
        if not selected:
            raise SystemExit(f"❌ 没有匹配「{args.name}」的样例")
    if not args.all:
        selected = selected[:1]

    for path, report in selected:
        render(path, report, show_evidence=args.evidence)


if __name__ == "__main__":
    main()
