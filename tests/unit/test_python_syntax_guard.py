"""守卫：仓库里**每个** Python 文件都必须能通过语法解析。

**为什么需要它**（本会话踩了三次，2026-09-14/15）：Python 字符串里**嵌套同类型的直角双引号**
（例如在双引号字符串的中文文本里再写 `"……"`）会直接造成 `SyntaxError`。这种错误在
`pytest` 里**不一定暴露**——因为 `scripts/` 下的大多数脚本根本不会被 import，只有真正去跑
那个脚本时才会炸（我在 `governance_register_financial_interaction.py` 上连踩两次，
修完又发现同一个文件里还有四处）。

`ast.parse` 是零依赖、毫秒级的检查，把"整个仓库都能解析"变成一条可自动执行的守卫，
比"写文档提醒自己用「」"可靠得多。

**不是替代测试**：它只保证语法正确，不保证逻辑正确——那是各模块自己的测试的事。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
#: 要检查的目录（仓库里 Python 的实际所在地）。
SCANNED = ("backend", "scripts", "tests")


def _python_files() -> list[Path]:
    files: list[Path] = []
    for name in SCANNED:
        base = ROOT / name
        if base.is_dir():
            files.extend(sorted(path for path in base.rglob("*.py")
                                if "__pycache__" not in path.parts))
    return files


def test_every_python_file_parses():
    files = _python_files()
    assert len(files) >= 50, f"只扫到 {len(files)} 个 Python 文件，扫描范围大概写错了"

    failures: list[str] = []
    for path in files:
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as error:
            relative = path.relative_to(ROOT)
            failures.append(f"{relative}:{error.lineno}: {error.msg}")

    assert not failures, (
        "以下 Python 文件语法错误（scripts/ 下的脚本不会被 import，所以 pytest 平时抓不到）：\n  "
        + "\n  ".join(failures))


def test_the_scan_can_actually_detect_a_broken_file(tmp_path):
    """**守卫的自检**：确认上面的扫描器真的会因为语法错误而失败，而不是恒真。"""
    broken = tmp_path / "broken.py"
    broken.write_text('VALUE = "中文里嵌套"直角引号"就会炸"\n', encoding="utf-8")
    try:
        ast.parse(broken.read_text(encoding="utf-8"))
        raise AssertionError("这个样例本该解析失败——守卫的自检失效了")
    except SyntaxError:
        pass
