"""
从已有产物里导出 examples/（离线演示数据）

**为什么需要 examples/**：
  · `data/` 不进版本库，而"输出长什么样"恰恰是别人判断项目可信度的第一入口 ——
    招聘方不会为了看输出先装 3GB 模型、再充 API 钱；
  · 样例必须**可追溯**（哪份合同、哪个报告、什么参数跑的），所以不手抄，
    而是按 contract_id 从 `data/` 里复制，随时可重新生成。

选样例的标准 —— 三类合同、三种看点（不是随便挑的）：
  1. `LIMEENERGYCO … DISTRIBUTOR AGREEMENT`：已**逐条核对过金标准**（10/10 一致），
     带"必备条款缺失"报警 → 展示"正常路径 + 缺失识别"；
  2. `ADAMSGOLFINC … ENDORSEMENT AGREEMENT`：高风险集中，且是**已知 badcase**
     （04 竞业限制漏报，模型把"我方"认成代言人 —— 见 badcase_log.md 的归因表）
     → 展示"我们知道自己哪里错"；
  3. `PfHospitalityGroupInc … Franchise Agreement`：条款存在多、证据条数多（21 条）
     → 压一压前端渲染（长报告是最容易露馅的地方）。

用法（在 hetong/ 目录下）：
    python -X utf8 scripts/build_examples.py
    python -X utf8 scripts/build_examples.py --reports-dir data/processed/reports_final
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import REPORTS_DIR, ROOT, TXT_DIR, safe_filename  # noqa: E402
from text_utils import load_txt_index, lookup  # noqa: E402

EXAMPLES = [
    "LIMEENERGYCO_09_09_1999-EX-10-DISTRIBUTOR AGREEMENT",
    "ADAMSGOLFINC_03_21_2005-EX-10.17-ENDORSEMENT AGREEMENT",
    "PfHospitalityGroupInc_20150923_10-12G_EX-10.1_9266710_EX-10.1_Franchise Agreement1",
]

OUT = ROOT / "examples"


def resolve_reports_dir(explicit: str | None) -> Path:
    """报告目录：优先用命令行指定的；否则优先全量跑的那份，最后退回默认目录"""
    if explicit:
        return Path(explicit)
    final = ROOT / "data" / "processed" / "reports_final"
    return final if final.exists() else REPORTS_DIR


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 examples/（离线演示数据）")
    parser.add_argument("--reports-dir", default=None, help="报告目录，默认自动选择（优先 reports_final）")
    args = parser.parse_args()
    reports_src = resolve_reports_dir(args.reports_dir)

    txt_index = load_txt_index(TXT_DIR)

    # ---- 第一步：全部校验通过后再复制 ----
    # 先校验后复制是必须的：否则中途失败会留下"复制了一半"的 examples/，
    # 而它看起来是完整的（这类"半成品状态"比直接报错更难发现）。
    plan: list[tuple[str, Path, Path]] = []
    problems: list[str] = []
    for contract_id in EXAMPLES:
        src_txt = lookup(txt_index, contract_id)
        src_report = reports_src / f"{safe_filename(contract_id)}.json"
        if src_txt is None:
            problems.append(f"找不到合同原文：{contract_id}（先跑 scripts/build_dataset.py）")
        elif not src_report.exists():
            problems.append(f"找不到报告：{src_report}（先跑 run_eval.py 生成报告）")
        else:
            plan.append((contract_id, src_txt, src_report))
    if problems:
        raise SystemExit("❌ 导出中止（未复制任何文件）：\n  - " + "\n  - ".join(problems))

    # ---- 第二步：复制 ----
    contracts_dir = OUT / "contracts"
    reports_dir = OUT / "reports"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    print(f"报告来源：{reports_src}\n导出到：{OUT}\n")
    print(f"{'合同':<46} {'原文':>8} {'报告':>8} {'证据':>5}")
    print("-" * 72)
    for contract_id, src_txt, src_report in plan:
        shutil.copy2(src_txt, contracts_dir / src_txt.name)
        shutil.copy2(src_report, reports_dir / src_report.name)
        data = json.loads(src_report.read_text(encoding="utf-8"))
        n_ev = sum(len(f.get("evidence", [])) for f in data.get("findings", []))
        print(
            f"{contract_id[:46]:<46} {src_txt.stat().st_size // 1024:>6}KB "
            f"{src_report.stat().st_size // 1024:>6}KB {n_ev:>5}"
        )
    print(f"\n✅ 完成：{len(plan)} 份合同 + 报告（用 python -X utf8 examples/show_report.py 查看）")


if __name__ == "__main__":
    main()
