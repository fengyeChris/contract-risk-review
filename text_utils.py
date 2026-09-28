"""
文本与文件名工具（被 scripts/check_dataset.py 与 scripts/build_dataset.py 共用）

为什么要单独抽出来：同一套"文件名归一化 + 忽略空白定位"的逻辑，在两个脚本里都要用。
复制两份代码迟早会改歪其中一份 —— 这是最典型的工程坏味道（DRY 原则）。
"""

import json
import re
from pathlib import Path


def norm(s: str) -> str:
    """归一化：只保留字母数字，其余字符统一视作分隔符（解决 A/R、MACY'S 之类的写差异）"""
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").upper()


def squeeze(s: str) -> str:
    """更激进：删掉所有非字母数字（应对文件名里标点被直接删掉的情况）"""
    return re.sub(r"[^A-Za-z0-9]+", "", s).upper()


def load_txt_index(txt_dir: Path) -> dict[str, Path]:
    """归一化文件名 → 文件路径；每个文件登记两种 key，提高匹配率"""
    idx: dict[str, Path] = {}
    for p in txt_dir.rglob("*.txt"):
        idx.setdefault(norm(p.stem), p)
        idx.setdefault(squeeze(p.stem), p)
    return idx


def lookup(idx: dict[str, Path], title: str) -> Path | None:
    """先按常规归一化找，找不到再按激进归一化找"""
    return idx.get(norm(title)) or idx.get(squeeze(title))


def read_text(path: Path) -> str:
    """读取合同文本（容错：遇到编码问题不抛异常）"""
    return path.read_text(encoding="utf-8", errors="ignore")


def find_span_loose(text: str, needle: str) -> tuple[int, int] | None:
    """
    忽略空白差异定位 needle，返回映射回**原始文本**的下标区间。

    PDF 提取常把原文档的换行、多余空格带进文本，精确匹配会失败；
    这里把两边都去掉所有空白字符再搜，再用下标映射还原真实位置。
    """
    chars: list[str] = []
    idxmap: list[int] = []
    for i, ch in enumerate(text):
        if not ch.isspace():
            chars.append(ch)
            idxmap.append(i)

    target = re.sub(r"\s+", "", needle)
    if not target:
        return None
    pos = "".join(chars).find(target)
    if pos < 0:
        return None
    return idxmap[pos], idxmap[pos + len(target) - 1] + 1


def read_jsonl(path: Path) -> list[dict]:
    """
    读取 JSONL 文件。

    ⚠️ 为什么不用 str.splitlines()：
        splitlines() 会按 10 种字符断行（\\n \\r \\v \\f \\x1c \\x1d \\x1e \\x85 \\u2028 \\u2029），
        而 JSONL 的"行"只应由 `\\n` 定义。PDF 转换出的文本里常含 U+2028（行分隔符），
        用 splitlines() 会把一条记录拦腰切断 → json 报 "Unterminated string"。
        （实测：chunks_robustness.jsonl 里有 17 个 U+2028，导致多切出 16 行。）

    这里用文件对象迭代：Python 的文件迭代只按 `\\n` 断行（\\r\\n 会被统一成 \\n），
    U+2028 之类的字符会**作为普通字符留在字符串里** ——
    而 JSON 规范（RFC 8259）允许字符串里出现未转义的 U+2028。
    """
    with path.open("r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_jsonl(path: Path, records: list[dict]) -> None:
    """
    写 JSONL，并在写完后**自检**：文件行数必须等于记录数。

    为什么要有这道自检：把"记录被换行字符切断"这类问题挡在**写入端**，
    而不是等读取时才发现（U+2028 这个坑就是靠它能在写入时被立刻抓住）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    expected = len(records)
    with path.open("r", encoding="utf-8") as fh:
        actual = sum(1 for _ in fh)
    if actual != expected:
        raise RuntimeError(
            f"写入自检失败：{path} 应有 {expected} 行，实际 {actual} 行"
            f"（可能有记录被换行字符切断，检查文本里是否含 U+2028 / U+2029 / \\x85）"
        )
