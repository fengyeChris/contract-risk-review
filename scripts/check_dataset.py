"""
数据体检脚本（Step 2.2）：在写任何 pipeline 之前，先确认数据本身可信。

它回答四个问题：
1. 510 份合同的纯文本，能不能和 510 条标注一一对上？
2. 标注里的 answer_start（字符下标）在纯文本里是否准确？
   → 这决定"证据定位准确率"这个指标是否可信
3. 对不上的位置，能不能用"忽略空白差异"的搜索重新定位？
   → 这决定 PDF 转换出来的文本还能不能用
4. 我们关心的类别各有多少正样本？→ 决定样本量够不够撑起评测集

用法（在 hetong/ 目录下运行）：
    C:\\Users\\Admin\\.conda\\envs\\langchain1.2\\python.exe -X utf8 scripts\\check_dataset.py
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from label_mapping import DIRECT_MAP  # noqa: E402
from text_utils import find_span_loose, load_txt_index, lookup  # noqa: E402

DATA = ROOT / "data" / "cuad"
JSON_PATH = DATA / "CUAD_v1.json"
TXT_DIR = DATA / "full_contract_txt"


def main() -> None:
    contracts = json.loads(JSON_PATH.read_text(encoding="utf-8"))["data"]
    idx = load_txt_index(TXT_DIR)

    print("=" * 64)
    print("1) 标注 与 纯文本 的对齐")
    print("=" * 64)
    matched = [c for c in contracts if lookup(idx, c["title"])]
    unmatched = [c["title"] for c in contracts if not lookup(idx, c["title"])]
    print(f"标注合同数   : {len(contracts)}")
    print(f"纯文本文件数 : {len(set(idx.values()))}")
    print(f"能对上       : {len(matched)}")
    print(f"对不上       : {len(unmatched)}")
    for t in unmatched:
        print(f"   缺失文本 → {t}")

    print()
    print("=" * 64)
    print("2) 字符位置（answer_start）准确性 + 重新定位能力")
    print("=" * 64)
    total_qas = strict_ok = loose_only = lost = no_answer = 0
    strict_by_part = defaultdict(lambda: [0, 0])

    for c in matched:
        path = lookup(idx, c["title"])
        text = path.read_text(encoding="utf-8", errors="ignore")
        part = path.parent.name
        for qa in c["paragraphs"][0]["qas"]:
            total_qas += 1
            if qa.get("is_impossible") or not qa["answers"]:
                no_answer += 1
                continue
            start, a_text = qa["answers"][0]["answer_start"], qa["answers"][0]["text"]
            strict_by_part[part][1] += 1
            if text[start:start + len(a_text)] == a_text:
                strict_ok += 1
                strict_by_part[part][0] += 1
            elif find_span_loose(text, a_text):
                loose_only += 1
            else:
                lost += 1

    positives = strict_ok + loose_only + lost
    print(f"标注总条数            : {total_qas}")
    print(f"  负样本 / 无答案      : {no_answer}")
    print(f"  正样本（有原文答案） : {positives}")
    print()
    print(f"位置精确对齐  OK      : {strict_ok:5d}  ({strict_ok / positives * 100:5.1f}%)")
    print(f"精确对不上、可用忽略空白重新定位 : {loose_only:5d}  ({loose_only / positives * 100:5.1f}%)")
    print(f"彻底找不到（不可用）  : {lost:5d}  ({lost / positives * 100:5.1f}%)")
    print()
    print("按目录的精确对齐率：")
    for part in sorted(strict_by_part):
        ok, tot = strict_by_part[part]
        print(f"  {part:9s}: {ok:5d}/{tot:5d} = {ok / tot * 100 if tot else 0:5.1f}%")

    print()
    print("=" * 64)
    print("3) 逐份合同分类：官方文本 vs PDF 转换文本")
    print("=" * 64)
    buckets = Counter()
    official, converted, ambiguous = [], [], []
    for c in matched:
        path = lookup(idx, c["title"])
        text = path.read_text(encoding="utf-8", errors="ignore")
        ok = tot = 0
        for qa in c["paragraphs"][0]["qas"]:
            if qa.get("is_impossible") or not qa["answers"]:
                continue
            a = qa["answers"][0]
            tot += 1
            if text[a["answer_start"]:a["answer_start"] + len(a["text"])] == a["text"]:
                ok += 1
        if tot < 3:
            buckets["正样本太少，无法分类"] += 1
            continue
        rate = ok / tot
        if rate >= 0.9:
            buckets["≥90% → 官方原始文本"] += 1
            official.append(c["title"])
        elif rate <= 0.1:
            buckets["≤10% → PDF 转换文本"] += 1
            converted.append(c["title"])
        else:
            buckets["10%~90% → 混合/存疑"] += 1
            ambiguous.append((c["title"], ok, tot))
    for k, v in buckets.items():
        print(f"  {k:28s}: {v:4d}")
    for title, ok, tot in ambiguous[:5]:
        print(f"    存疑: {title[:50]} ({ok}/{tot})")

    print()
    print("=" * 64)
    print("4) 类别正样本数（合同数，与文本质量无关）")
    print("=" * 64)
    pos_all = Counter()
    pos_by_part = defaultdict(Counter)
    for c in matched:
        part = lookup(idx, c["title"]).parent.name
        for qa in c["paragraphs"][0]["qas"]:
            if not qa.get("is_impossible") and qa["answers"]:
                label = qa["id"].split("__")[-1]
                pos_all[label] += 1
                pos_by_part[part][label] += 1

    official_set = set(official)
    pos_official = Counter()
    for c in matched:
        if c["title"] not in official_set:
            continue
        for qa in c["paragraphs"][0]["qas"]:
            if not qa.get("is_impossible") and qa["answers"]:
                pos_official[qa["id"].split("__")[-1]] += 1

    print(f"  {'clause':24s} {'CUAD label':34s} {'全部':>5s} {'仅官方文本':>10s}")
    for label, cid in sorted(DIRECT_MAP.items(), key=lambda kv: kv[1].value):
        print(f"  {cid.value:24s} {label:34s} {pos_all[label]:5d} {pos_official[label]:10d}")
    for label in ["Uncapped Liability", "Most Favored Nation"]:
        print(f"  {'(候选)':24s} {label:34s} {pos_all[label]:5d} {pos_official[label]:10d}")

    print()
    print("全部 41 类（按正样本数降序）：")
    for label, n in pos_all.most_common():
        print(f"  {n:4d}  {label}")


if __name__ == "__main__":
    main()
