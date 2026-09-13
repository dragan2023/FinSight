"""`doc_lint` 的路径解析守卫（2026-09-14 补：中间通配被误报成"路径不存在"）。

起因：写 `20` 号接力文档时引用 `scripts/governance_record_*_change.py`（指代一族脚本），
旧的 `_resolve` 只认"开头的 `*`"，于是把它判成 missing、lint 直接退出码 1。
改成真正的 glob 之后，这里把几种写法钉死——**并且要保证 `*` 不跨目录**，
否则 `api/*.py` 会连 `api/sub/x.py` 也算匹配，反而放过写错的路径。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_lint():
    spec = importlib.util.spec_from_file_location("doc_lint_under_test",
                                                  ROOT / "scripts" / "doc_lint.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LINT = _load_lint()
FILES = [
    "backend/app/api/screener.py",
    "backend/app/api/sub/deep.py",
    "scripts/governance_record_change.py",
    "scripts/governance_record_rights_change.py",
    "docs/荐股治理方案/18_事件研究加速_预计算面板.md",
]


def test_exact_repo_relative_path_resolves():
    assert LINT._resolve("scripts/governance_record_change.py", FILES) == "ok"


def test_unique_suffix_shorthand_resolves():
    assert LINT._resolve("api/screener.py", FILES) == "ok_shorthand"


def test_mid_string_wildcard_resolves():
    """**这条就是本次修的**：中间通配指代一族脚本，必须算匹配。"""
    assert LINT._resolve("scripts/governance_record_*_change.py", FILES) == "ok"


def test_leading_wildcard_still_resolves():
    assert LINT._resolve("backend/app/api/*.py", FILES) == "ok"
    assert LINT._resolve("api/*.py", FILES) == "ok_shorthand"


def test_wildcard_does_not_cross_directories():
    """`*` 不跨 `/`：`api/*.py` 不应匹配到 `api/sub/deep.py`。"""
    module = LINT
    matcher = module._glob_pattern_regex("backend/app/api/*.py")
    assert matcher.match("backend/app/api/screener.py")
    assert not matcher.match("backend/app/api/sub/deep.py")


def test_nonexistent_wildcard_is_still_reported():
    assert LINT._resolve("scripts/nope_*.py", FILES) == "missing"
    assert LINT._resolve("backend/app/api/*.rs", FILES) == "missing"


def test_real_repo_glob_references_in_docs_resolve():
    """真仓库自检：文档里现在实际用到的两种通配写法都能解析。"""
    real_files = [str(item.relative_to(ROOT)).replace("\\", "/")
                  for item in ROOT.rglob("*") if item.is_file()]
    assert LINT._resolve("scripts/governance_record_*_change.py", real_files) == "ok"
    assert LINT._resolve("backend/app/api/*.py", real_files) == "ok"
