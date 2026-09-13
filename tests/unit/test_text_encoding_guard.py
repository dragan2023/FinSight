"""编码守卫：源码与文档必须是干净的 UTF-8，且**不含乱码特征字符**。

## 为什么需要这个测试

2026-09-14 我用 PowerShell 的 `Get-Content ... | Set-Content -Encoding UTF8` 改
`backend/app/market_warehouse/repository.py`，结果**中文全变乱码**：
Windows PowerShell 5.1 读文件默认按**当前 ANSI 代码页（本机是 GBK）**解码，
于是 UTF-8 的中文字节被当成 GBK 解出错误字符，再以 UTF-8 写回，文件就被永久改坏
（实测**不可逆**：解码产生了私用区字符，回编码为 GBK 时直接抛错）。
只能 `git checkout` 恢复后重新施加改动。

## 这个测试能抓住什么

乱码的产物是**私用区字符**（U+E000–U+F8FF，GBK 未定义位到 PUA 的映射）与替换字符
U+FFFD。这两类字符在源码、文档、测试里**没有任何正当用途**，所以用它们当判据几乎不会误报，
但能在破坏发生后的第一次测试里就报警——而不是等到某天读代码时才发现。

## 真正防止它再发生的做法（写在这里，连同上面的教训）

1. **不要用 PowerShell 改写文本文件**——`Get-Content` / `Set-Content` / `Out-File` /
   `>` 重定向在 5.1 上都不保证 UTF-8。改文件用编辑器（`edit` / `write` 工具）。
2. 必须做文本变换时，用 **Python** 并显式 `encoding="utf-8"`、`newline="\n"`；
   或 PowerShell 7（`pwsh`，默认 UTF-8）。PS 5.1 下读文件至少要 `-Encoding UTF8`。
3. `> file 2>&1` 只用于**捕获命令输出到日志**（字节透传），不要用作文件的写入手段。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: 扫描范围：仓库里**由开发者手工维护**的文本。
SCAN_DIRS = ("backend", "scripts", "tests", "docs")
SCAN_SUFFIXES = (".py", ".md", ".ts", ".tsx", ".json", ".toml", ".yml", ".yaml", ".sql")
#: 私用区（GBK 乱码的典型产物）与替换字符：源码/文档里都不该出现。
BAD_RANGES = ((0xE000, 0xF8FF),)
BAD_CHARS = ("\ufffd",)
#: 目录白名单（构建产物、依赖）。
SKIP_DIRS = {"node_modules", ".venv", ".git", "__pycache__", "dist", "build", ".cache"}


def _text_files() -> list[Path]:
    files: list[Path] = []
    for name in SCAN_DIRS:
        base = ROOT / name
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            files.append(path)
    return files


def _bad_characters(text: str) -> list[str]:
    found = [char for char in BAD_CHARS if char in text]
    for char in text:
        if any(low <= ord(char) <= high for low, high in BAD_RANGES):
            found.append(char)
    # 去重但保持可读
    return sorted(set(found))


def test_all_maintained_text_files_are_valid_utf8():
    """UTF-8 可解码（带 BOM 也算合格——但下面单独查 BOM）。"""
    broken = []
    for path in _text_files():
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            broken.append(f"{path.relative_to(ROOT)}: {exc}")
    assert not broken, "以下文件不是合法 UTF-8（很可能是被非 UTF-8 工具改写过）：\n" + \
                       "\n".join(broken)


def test_no_mojibake_markers_in_maintained_text_files():
    """**这条是抓 PowerShell 乱码的关键**：私用区字符与 U+FFFD 不该出现在源码/文档里。"""
    offenders = []
    for path in _text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue                      # 上一条测试已经报了
        bad = _bad_characters(text)
        if bad:
            codepoints = " ".join(f"U+{ord(char):04X}" for char in bad[:8])
            offenders.append(f"{path.relative_to(ROOT)}: {codepoints}")
    assert not offenders, (
        "检测到乱码特征字符（私用区 U+E000–U+F8FF / U+FFFD）——文件很可能被 "
        "PowerShell 的 Get-Content|Set-Content 按 GBK 解码毁过，需要从 git 恢复后重做改动：\n"
        + "\n".join(offenders))


@pytest.mark.parametrize("path", ["backend/app/market_warehouse/repository.py",
                                  "backend/app/screener/events.py"])
def test_key_modules_carry_readable_chinese(path):
    """抽查关键模块：中文注释必须仍然是人能读的（防止"能解码但是乱码"逃过上面两条）。"""
    text = (ROOT / path).read_text(encoding="utf-8")
    chinese = [line for line in text.splitlines()
               if any("\u4e00" <= char <= "\u9fff" for char in line)]
    assert chinese, f"{path} 里应当有中文注释"
    # 常见乱码产物：连续的非 CJK 生僻字符夹杂在标点里。这里用一个朴素但有效的判据：
    # 中文行里不应该出现私用区字符（上面已查），且应当能匹配到常见技术词。
    joined = " ".join(chinese)
    assert "面板" in joined or "等价" in joined or "涨停" in joined or "事件" in joined
