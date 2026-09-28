"""
Web 演示服务（只读）

设计原则
--------
1. **零 LLM 调用、零模型加载**：所有内容都从磁盘上的真实产物读取 ——
   `examples/reports/*.json`（审查报告）、`examples/contracts/*.txt`（合同原文）、
   `eval/*.json`（评测指标）、`badcase_attribution.md`（判错归因）。
   所以这个演示**不花钱、不需要 API key、不需要 GPU**，打开就能看。
2. **不做任何"演示专用数据"**：接口直通文件内容，页面上看到的数字与 README/日志里的完全一致。
   唯一做的加工是把散在几个文件里的信息拼成前端好用的形状（并且不改变任何数值）。
3. 与主体代码共用 `config.py` 的路径常量与 `clauses.py` 的条款登记表，避免出现第二份定义。

启动
----
    python -X utf8 webapp/server.py                # 默认 http://127.0.0.1:8077
    python -X utf8 webapp/server.py --port 8080
"""

import argparse
import json
import re
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clauses import CLAUSES  # noqa: E402
from config import EVAL_DIR, ROOT as PROJECT_ROOT  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"
EXAMPLES_DIR = PROJECT_ROOT / "examples"
REPORTS_DIR = EXAMPLES_DIR / "reports"
CONTRACTS_DIR = EXAMPLES_DIR / "contracts"
EVAL_DIR_PATH = EVAL_DIR
ATTRIBUTION_MD = PROJECT_ROOT / "badcase_attribution.md"

# 评测看板要展示的三组对比（标签 → 指标文件名）。数字全部来自文件，不写死。
AB_RUNS = [
    ("基线（纯向量检索）", "metrics_part30.json"),
    ("+ 重排", "metrics_rerank30.json"),
    ("+ 口径对齐", "metrics_rerank30_v3.json"),
]
RETRIEVAL_RUNS = [
    ("基线", "retrieval_baseline.json"),
    ("+ 重排", "retrieval_rerank_full.json"),
]

app = FastAPI(title="合同风险审查 · 演示", docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def revalidate_html_and_static(request, call_next):
    """
    HTML 与静态资源一律要求"先校验再使用"（no-cache = 必须回服务器校验，不是不缓存）。

    为什么必须在演示服务里做这件事：**改了 JS/CSS 后浏览器还在用旧的**，
    而这类问题看起来像"代码没生效"，非常费时间（本项目就因它白查了一轮：
    `page.goto('...#eval')` 只是哈希变更、根本不重新加载页面）。
    StaticFiles 自带 ETag，未改动时仍是 304，不会多传流量。
    """
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response

# 启动时读一次的只读缓存（文件都在本地，很小）
_TITLES = {
    spec.clause_id.value: {"zh": spec.name_zh, "en": spec.name_en} for spec in CLAUSES
}
_GOLD: dict[str, dict] = {}


def _load_gold() -> dict[str, dict]:
    """金标准（用于"这份样例与金标准差在哪"—— 演示也要如实显示不一致）"""
    global _GOLD
    if _GOLD:
        return _GOLD
    path = EVAL_DIR_PATH / "golden_eval.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").split("\n"):
            line = line.strip()
            if line:
                record = json.loads(line)
                _GOLD[record["contract_id"]] = record.get("labels", {})
    return _GOLD


def _read_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sample_summary(report: dict, path: Path) -> dict:
    findings = report.get("findings", [])
    return {
        "id": path.stem,
        "contract_id": report.get("contract_id", ""),
        "file_name": report.get("file_name", ""),
        "summary_zh": report.get("summary_zh", ""),
        "n_exists": sum(1 for f in findings if f.get("exists")),
        "n_missing_required": len(report.get("missing_required_clauses") or []),
        "missing_required": [c for c in (report.get("missing_required_clauses") or [])],
        "n_evidence": sum(len(f.get("evidence") or []) for f in findings),
        "n_high": sum(1 for f in findings if f.get("risk_level") == "high"),
    }


@app.get("/api/samples")
def api_samples() -> dict:
    samples = [_sample_summary(_read_report(p), p) for p in sorted(REPORTS_DIR.glob("*.json"))]
    if not samples:
        raise HTTPException(500, "examples/reports/ 为空：先跑 scripts/build_examples.py")
    return {"samples": samples}


@app.get("/api/report/{sample_id}")
def api_report(sample_id: str) -> dict:
    path = REPORTS_DIR / f"{sample_id}.json"
    if not path.exists():
        raise HTTPException(404, f"没有这份样例：{sample_id}")
    report = _read_report(path)

    contract_path = CONTRACTS_DIR / report.get("file_name", "")
    text = contract_path.read_text(encoding="utf-8") if contract_path.exists() else ""

    # 与金标准逐类对照（负数 = 金标准判不存在、模型判存在 → 误报）
    gold = _load_gold().get(report.get("contract_id", ""), {})
    diff = []
    for finding in report.get("findings", []):
        clause_id = finding.get("clause_id")
        label = gold.get(clause_id)
        if label is None:
            continue
        gold_exists = bool(label.get("exists", False))
        pred_exists = bool(finding.get("exists"))
        diff.append(
            {
                "clause_id": clause_id,
                "gold_exists": gold_exists,
                "pred_exists": pred_exists,
                "match": gold_exists == pred_exists,
                "kind": "" if gold_exists == pred_exists else ("误报" if pred_exists else "漏报"),
            }
        )

    return {
        "sample": _sample_summary(report, path),
        "report": report,
        "clause_titles": _TITLES,
        "text": text,
        "eval_diff": diff,
    }


@app.get("/api/eval")
def api_eval() -> dict:
    def load(name: str) -> dict | None:
        path = EVAL_DIR_PATH / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    final = load("metrics_final.json") or {}
    ab = []
    for label, name in AB_RUNS:
        data = load(name)
        if data:
            ab.append(
                {
                    "label": label,
                    "file": name,
                    "config": data.get("config", {}),
                    "overall": data.get("overall", {}),
                }
            )
    retrieval = []
    for label, name in RETRIEVAL_RUNS:
        data = load(name)
        if data:
            retrieval.append(
                {
                    "label": label,
                    "file": name,
                    "config": data.get("config", {}),
                    "overall": data.get("overall", {}),
                }
            )
    return {
        "final": {
            "file": "metrics_final.json",
            "config": final.get("config", {}),
            "overall": final.get("overall", {}),
            "per_clause": final.get("per_clause", {}),
            "fn_cases": final.get("fn_cases", []),
            "fp_cases": final.get("fp_cases", []),
        },
        "clause_titles": _TITLES,
        "ab": ab,
        "retrieval": retrieval,
    }


@app.get("/api/attribution")
def api_attribution() -> dict:
    """
    判错归因（来自 `badcase_attribution.md`，由 scripts/attribute_badcases.py 自动生成）。
    这里只做机械解析；解析不到就返回空结构，前端自行隐藏该区块（不猜、不编）。
    """
    if not ATTRIBUTION_MD.exists():
        return {"available": False}
    lines = ATTRIBUTION_MD.read_text(encoding="utf-8").split("\n")

    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    def group(label: str) -> int:
        for line in lines:
            if line.startswith("|") and cells(line)[:1] == [label]:
                try:
                    return int(cells(line)[1])
                except (IndexError, ValueError):
                    return 0
        return 0

    def case_rows() -> list[dict]:
        """
        抓两张明细表（漏报 5 列 / 误报 4 列）。
        判据用**表头**（同时含 `合同` 与 `归因层`），而不是列数 ——
        列数在两张表里不一样（第一次实现就因此漏掉了整张误报表）。
        """
        rows, started = [], False
        for line in lines:
            if not line.startswith("|"):
                continue
            head = [c.strip().strip("*") for c in cells(line)]  # 去掉 markdown 加粗
            if set(head[0]) <= set("-: "):  # 分隔行
                continue
            if head[:1] == ["合同"] and "归因层" in head:
                started = True
                continue
            if started and len(head) >= 4:
                rows.append(
                    {"contract": head[0], "clause": head[1], "layer": head[2], "note": head[3]}
                )
        return rows

    scanned = 0
    wrong = 0
    match = re.search(r"扫描 (\d+) 条判定，判错 \*\*(\d+)\*\*", "\n".join(lines))
    if match:
        scanned, wrong = int(match.group(1)), int(match.group(2))

    known_gaps = []
    for line in lines:
        head = cells(line) if line.startswith("|") else []
        if len(head) == 2 and head[0].endswith(("law", "control", "assignment", "compete",
                                               "exclusivity", "notice", "renewal",
                                               "liability", "damages", "ownership")):
            known_gaps.append({"clause": head[0], "desc": head[1]})

    all_cases = case_rows()
    return {
        "available": True,
        "scanned": scanned,
        "wrong": wrong,
        "fn": {
            "检索层": group("检索层"),
            "模型层": group("模型层"),
            "cases": [r for r in all_cases if r["layer"] in ("检索层", "模型层")],
        },
        "fp": {
            "口径落差": group("口径落差"),
            "待人工判定": group("待人工判定"),
            "cases": [r for r in all_cases if r["layer"] in ("口径落差", "待人工判定")],
        },
        "known_gaps": known_gaps,
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    parser = argparse.ArgumentParser(description="合同审查演示服务（只读，不调用 LLM）")
    parser.add_argument("--port", type=int, default=8077)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    import uvicorn

    print(f"演示服务：http://{args.host}:{args.port}   （只读本地报告，不调用 LLM）")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
