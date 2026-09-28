"""
`config.load_env` 的自测

重点**不在**"解析对不对"，而在**环境缺失时的行为**：
新克隆的仓库只有 `.env.example`、没有 `.env` —— 如果 import config 就崩，
那么所有"不需要任何 key"的功能（`--help`、离线看报告、`pytest`）都会跟着用不了。
这个测试就是钉住这个行为，防止以后有人把 `if not target.exists()` 那行顺手删掉。
"""

from pathlib import Path

from config import load_env


def test_missing_env_file_returns_empty_dict() -> None:
    """新克隆的仓库没有 .env → 必须返回 {}，而不是 FileNotFoundError"""
    assert load_env(Path("__definitely_not_exists__.env")) == {}


def test_parse_basic_pairs(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_MODEL=qwen3.8-flash\nTOP_K=5\n", encoding="utf-8")
    assert load_env(env_file) == {"LLM_MODEL": "qwen3.8-flash", "TOP_K": "5"}


def test_skip_blank_and_comment_lines(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("# 注释\n\nLLM_PROVIDER=dashscope\n   \n# 中文注释\n", encoding="utf-8")
    assert load_env(env_file) == {"LLM_PROVIDER": "dashscope"}


def test_value_may_contain_equal_sign(tmp_path: Path) -> None:
    """JSON 类参数（如 LLM_EXTRA_BODY={"enable_thinking": false}）里含 `=`，必须只切第一个"""
    env_file = tmp_path / ".env"
    env_file.write_text('LLM_EXTRA_BODY={"enable_thinking": false}\n', encoding="utf-8")
    assert load_env(env_file)["LLM_EXTRA_BODY"] == '{"enable_thinking": false}'


def test_key_and_value_are_stripped(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("  TOP_K = 5  \n", encoding="utf-8")
    assert load_env(env_file) == {"TOP_K": "5"}
