"""守卫：`scripts/*.py` 里凡是**打印非 ASCII** 的，都必须显式把 stdout 设成 UTF-8。

**为什么需要这条守卫**（实测踩过，2026-09-15）：

`scripts/verify_event_panel.py` 真的跑完了等价比对（49,095 个单元零差异），却在最后一行
打印「判定：等价 ✅」时抛 `UnicodeEncodeError` —— 因为脚本被重定向/管道时，Python 的
stdout 用的是系统默认码页（本机 GBK），而 `✅` 不在 GBK 里。后果：**退出码 1**，
读者看到的是编码 traceback，"比对通过"这个结论根本没打出来。

这个坑的性质：**结论正确、报告失败**，而且只在"重定向到文件"时出现（终端里可能反而是好的），
而本项目的工作流恰恰就是重定向到文件再读（见 `20` 号文档 §7）。靠人记住"新脚本要加
reconfigure"不可靠，所以做成静态检查。

判据（AST，不是文本匹配，避免把注释/文档字符串里的中文误报成"要打印的中文"）：
找出 `print(...)` / `sys.stdout.write(...)` 这类调用中**字面量里含非 ASCII 字符**的位置；
若存在，则要求源码里出现 `reconfigure(` 且同一处声明 `encoding="utf-8"`。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"

#: 视为"会输出到 stdout"的调用名（属性访问只看最后一段）。
_WRITE_CALLS = {"print", "write"}


def _offending_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """调用参数里含非 ASCII **字面量**的位置（f-string 的常量部分也算）。"""
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = (target.id if isinstance(target, ast.Name)
                else target.attr if isinstance(target, ast.Attribute) else "")
        if name not in _WRITE_CALLS:
            continue
        for child in ast.walk(node):
            if not (isinstance(child, ast.Constant) and isinstance(child.value, str)):
                continue
            if any(ord(character) > 127 for character in child.value):
                offenders.append((node.lineno, child.value.strip().splitlines()[0][:48]))
    return offenders


def _reconfigures_utf8(source: str) -> bool:
    """是否把某个流显式设成 UTF-8。"""
    return ("reconfigure(" in source
            and ('encoding="utf-8"' in source or "encoding='utf-8'" in source))


def test_the_detector_itself_works():
    """**守卫的自检**：确保上面那个扫描器不是"永远通过"的空壳。

    实测教训（AGENTS 全局规则第 7 条）：静默失效的守卫比没有守卫更糟——它会让人以为
    有防线。所以这里先钉住它能抓到一个真的违规样例，并要求它在真实脚本集上确实命中
    非空集合（否则说明 AST 判据写错了，主测试会变成恒真）。
    """
    bad = ast.parse('print("等价 ✅")\n')
    assert _offending_calls(bad), "扫描器抓不到含非 ASCII 的 print —— 守卫失效"
    assert not _reconfigures_utf8('print("等价 ✅")\n')
    assert _reconfigures_utf8(
        'sys.stdout.reconfigure(encoding="utf-8")\nprint("等价 ✅")\n')

    detected = [path.name for path in SCRIPTS.glob("*.py")
                if _offending_calls(ast.parse(path.read_text(encoding="utf-8")))]
    assert len(detected) >= 5, (
        f"只检测到 {len(detected)} 个脚本会打印非 ASCII（{detected}）——"
        "与脚本目录的实际情况不符，判据大概写错了")


def test_every_script_that_prints_non_ascii_sets_utf8_stdout():
    assert SCRIPTS.is_dir(), f"找不到脚本目录：{SCRIPTS}"
    failures: list[str] = []
    for path in sorted(SCRIPTS.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        offenders = _offending_calls(ast.parse(source))
        if not offenders or _reconfigures_utf8(source):
            continue
        sample = "、".join(f"L{line}『{text}』" for line, text in offenders[:3])
        failures.append(
            f"{path.name}: 打印了非 ASCII（{sample}… 共 {len(offenders)} 处）"
            "但没有 `stream.reconfigure(encoding=\"utf-8\")`——重定向到文件时会抛 "
            "UnicodeEncodeError，让人看不到真正的结论")

    assert not failures, "以下脚本会在重定向时因编码崩溃：\n  " + "\n  ".join(failures)
