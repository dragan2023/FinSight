"""V2-03 知识库分块：标题层级感知 + 页码/行号回链的纯函数切分。

- 目标块长 ~800 字，重叠 ~15%（120 字），按整行打包（超长行才按字符硬切）。
- PDF 解析文本自带「[第 N 页]」标记行：据此为每个分块记录页码。
- Markdown ATX 标题维护层级栈：分块记录所属标题路径，正文不含注入标题。
- 所有分块都是原文的连续切片：content == text[char_start:char_end]。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

KB_CHUNK_TARGET_CHARS = 800
KB_CHUNK_OVERLAP_CHARS = 120

_PAGE_MARKER = re.compile(r"^\[第 (\d+) 页\]$")
_MD_HEADING = re.compile(r"^(#{1,6})\s+(\S.*)$")


@dataclass(frozen=True)
class ChunkSpec:
    content: str
    heading: str
    page: int | None
    line_start: int
    line_end: int
    char_start: int
    char_end: int


@dataclass(frozen=True)
class _Unit:
    """最小打包单元：一段连续原文行（含所属页码与标题路径）。"""

    text: str
    heading: str
    page: int | None
    line_start: int
    line_end: int
    char_start: int
    char_end: int


def _heading_path(stack: list[tuple[int, str]]) -> str:
    return " / ".join(title for _, title in stack)


def _scan_units(text: str) -> list[_Unit]:
    """把解析文本切成段落级单元，同时追踪页码与 Markdown 标题层级。"""
    units: list[_Unit] = []
    heading_stack: list[tuple[int, str]] = []
    page: int | None = None
    offset = 0
    lines = text.split("\n")
    pack: list[str] = []
    pack_heading = ""
    pack_page: int | None = None
    pack_first_line = 0
    pack_start_char = 0

    def flush(end_line_index: int, end_char: int) -> None:
        if not pack:
            return
        units.append(_Unit(
            text="\n".join(pack), heading=pack_heading, page=pack_page,
            line_start=pack_first_line + 1, line_end=end_line_index,
            char_start=pack_start_char, char_end=end_char))
        pack.clear()

    for index, line in enumerate(lines):
        line_start_char = offset
        offset += len(line) + 1
        marker = _PAGE_MARKER.match(line.strip())
        if marker:
            flush(index, line_start_char)
            page = int(marker.group(1))
            continue
        heading_match = _MD_HEADING.match(line.strip())
        if heading_match:
            flush(index, line_start_char)
            level = len(heading_match.group(1))
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, heading_match.group(2).strip()))
            pack_heading = _heading_path(heading_stack)
            pack_page = page
            pack_first_line = index
            pack_start_char = line_start_char
            pack.append(line)
            continue
        if not line.strip():
            flush(index, line_start_char)
            continue
        if not pack:
            pack_heading = _heading_path(heading_stack)
            pack_page = page
            pack_first_line = index
            pack_start_char = line_start_char
        pack.append(line)
    flush(len(lines), offset - 1)
    return units


def _hard_split_lines(unit: _Unit, target_chars: int) -> list[_Unit]:
    """超长段落按行打包；单行仍超长时按字符硬切，行号落在该行。"""
    pieces: list[_Unit] = []
    lines = unit.text.split("\n")
    line_starts = []
    cursor = unit.char_start
    for line in lines:
        line_starts.append(cursor)
        cursor += len(line) + 1
    pack: list[str] = []
    pack_first = 0
    base_line = unit.line_start

    def flush_pack(end_position: int) -> None:
        if not pack:
            return
        first, last = pack_first, end_position
        pieces.append(_Unit(
            text="\n".join(pack), heading=unit.heading, page=unit.page,
            line_start=base_line + first, line_end=base_line + last,
            char_start=line_starts[first],
            char_end=line_starts[last] + len(lines[last])))
        pack.clear()

    for position, line in enumerate(lines):
        projected = sum(len(item) + 1 for item in pack) + len(line)
        if pack and projected > target_chars:
            flush_pack(position - 1)
            pack_first = position
        if len(line) + 1 > target_chars:
            for slice_start in range(0, len(line), target_chars):
                piece = line[slice_start:slice_start + target_chars]
                pieces.append(_Unit(
                    text=piece, heading=unit.heading, page=unit.page,
                    line_start=base_line + position, line_end=base_line + position,
                    char_start=line_starts[position] + slice_start,
                    char_end=line_starts[position] + slice_start + len(piece)))
            continue
        pack.append(line)
    flush_pack(len(lines) - 1)
    return pieces


def split_chunks(text: str, *, target_chars: int = KB_CHUNK_TARGET_CHARS,
                 overlap_chars: int = KB_CHUNK_OVERLAP_CHARS) -> list[ChunkSpec]:
    if not text or not text.strip():
        return []
    units: list[_Unit] = []
    for unit in _scan_units(text):
        if len(unit.text) <= target_chars:
            units.append(unit)
        else:
            units.extend(_hard_split_lines(unit, target_chars))
    chunks: list[ChunkSpec] = []
    pack: list[_Unit] = []
    pack_chars = 0

    def emit() -> None:
        nonlocal pack, pack_chars
        if not pack:
            return
        # 直接取原文切片（可能含空行与页标记行）：content 与
        # text[char_start:char_end] 恒等，页码/行号回链不失真。
        content = text[pack[0].char_start:pack[-1].char_end]
        heading = next((unit.heading for unit in pack if unit.heading), "")
        # 页码取块首：引用定位指向分块起始位置的页。
        page = pack[0].page
        chunks.append(ChunkSpec(
            content=content, heading=heading, page=page,
            line_start=pack[0].line_start, line_end=pack[-1].line_end,
            char_start=pack[0].char_start,
            char_end=pack[-1].char_end,
        ))
        tail: list[_Unit] = []
        tail_chars = 0
        for unit in reversed(pack):
            # 先判预算再收进尾块：单块超预算时宁可不重叠，也不造出超长分块。
            if tail_chars + len(unit.text) + 1 > overlap_chars:
                break
            tail.insert(0, unit)
            tail_chars += len(unit.text) + 1
        pack = tail
        pack_chars = tail_chars

    for unit in units:
        if pack and pack_chars + len(unit.text) + 1 > target_chars:
            emit()
        pack.append(unit)
        pack_chars += len(unit.text) + 1
    emit()
    return chunks
