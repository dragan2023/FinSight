"""Local attachment text parsers (plan 04 section 2.5).

解析只在本机进行：不访问网络、不调用外部模型、不做 OCR。类型由「扩展名 +
MIME 双检」与「内容魔数嗅探」共同决定，绝不根据扩展名直接信任内容；可执行
文件、宏工作簿、HTML 与归档包一律拒绝。扫描件如实报告"无文本层"，不用 OCR
冒充文本。单个文件解析失败返回结构化错误（AttachmentParseError），由上层
转成 DataGap 候选，绝不影响其他文件或研究任务。

docx 没有已装解析库（python-docx 不在依赖清单），明确 unsupported 并提示
另存为受支持格式；不为此引入重型新依赖。
"""

from __future__ import annotations

import csv
import io
import time
import zipfile
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from ..web_research.public_reader import has_pdf_magic

PARSE_TIME_BUDGET_SECONDS = 60.0

# 附件扩展名/MIME 白名单（双检的第二把尺子）；解析器与存储共用同一份定义。
ALLOWED_EXTENSIONS = frozenset({"txt", "md", "markdown", "csv", "tsv", "xlsx", "pdf"})

MIME_BY_EXTENSION = {
    "txt": frozenset({"text/plain"}),
    "md": frozenset({"text/markdown", "text/plain"}),
    "markdown": frozenset({"text/markdown", "text/plain"}),
    "csv": frozenset({"text/csv", "text/plain", "application/vnd.ms-excel"}),
    "tsv": frozenset({"text/tab-separated-values", "text/plain"}),
    "xlsx": frozenset({"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       "application/octet-stream"}),
    "pdf": frozenset({"application/pdf"}),
}


class AttachmentParseError(Exception):
    """解析失败的结构化信号；message 是面向用户的中文文案，不含上游原文。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# 结构化错误码固定集合：API 响应与 DataGap 候选都从这里取值。
PARSE_ERROR_CODES = (
    "unsupported_type", "type_mismatch", "content_rejected", "decode_error",
    "limit_exceeded", "password_protected", "no_text_layer", "parse_failed",
    "time_budget_exceeded", "too_large", "total_too_large", "empty_file",
    "invalid_filename",
)

REASON_BY_CODE = {
    "time_budget_exceeded": "timeout",
}

REMEDIES_BY_CODE = {
    "unsupported_type": ["转换为 TXT/Markdown/CSV/TSV/XLSX/文本型 PDF 后重新上传。"],
    "type_mismatch": ["使用上传控件重新选择文件，确保扩展名与实际内容类型一致。"],
    "content_rejected": ["确认文件内容与类型一致且不是可执行文件/压缩包后重新上传。"],
    "decode_error": ["将文件另存为 UTF-8 编码后重新上传。"],
    "limit_exceeded": ["拆分文件、减少行数/单元格/页数后重新上传。"],
    "password_protected": ["解除密码保护或另存为无密码的文本型 PDF 后重新上传。"],
    "no_text_layer": ["对少量页面截图并使用已配置且支持视觉的模型分析。",
                      "手动摘录关键文字，以文本补充方式保存。"],
    "parse_failed": ["确认文件未损坏后重新上传。"],
    "time_budget_exceeded": ["拆分文件后重新上传。"],
    "too_large": ["压缩文件大小后重新上传。"],
    "total_too_large": ["减少单次上传的文件数量或大小。"],
    "empty_file": ["确认文件有内容后重新上传。"],
    "invalid_filename": ["使用不含路径分隔符的常规文件名后重新上传。"],
}


class AttachmentLimits(BaseModel):
    """计划 §2.5 的可见产品上限；全部可在测试中缩小，不因读取失败拖垮任务。"""

    model_config = ConfigDict(extra="forbid")

    max_file_bytes: int = Field(default=25 * 1024 * 1024, ge=1)
    max_files_per_request: int = Field(default=5, ge=1)
    max_total_bytes: int = Field(default=100 * 1024 * 1024, ge=1)
    pdf_max_pages: int = Field(default=200, ge=1)
    max_rows_per_table: int = Field(default=10000, ge=1)
    max_workbook_cells: int = Field(default=100000, ge=1)
    max_parse_chars: int = Field(default=200000, ge=1)
    excerpt_chars: int = Field(default=20000, ge=1)
    parse_time_budget_seconds: float = Field(default=PARSE_TIME_BUDGET_SECONDS, gt=0)


class ParsedAttachment(BaseModel):
    """单个文件的成功解析结果：文本、解析方式、统计与警告（含页码/表名）。"""

    model_config = ConfigDict(extra="forbid")

    kind: str
    parse_method: str
    text: str = ""
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)


def sniff_content(data: bytes) -> str:
    """按文件头字节嗅探内容族；扩展名与 MIME 永远不当成内容证明。"""
    if data.startswith(b"MZ"):
        return "executable"
    if has_pdf_magic(data):
        return "pdf"
    if data.startswith(b"PK\x03\x04") or data.startswith(b"PK\x05\x06"):
        return "zip"
    head = data[:8192].lower()
    if b"\x00" in data[:8192]:
        return "binary"
    if b"<!doctype html" in head or b"<html" in head:
        return "html"
    return "text"


def _decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise AttachmentParseError(
            "decode_error", "文件不是有效的 UTF-8 文本，请另存为 UTF-8 编码后重新上传。") from error


def _cap_text(text: str, limits: AttachmentLimits) -> tuple[str, bool]:
    if len(text) <= limits.max_parse_chars:
        return text, False
    warning = f"文本超过 {limits.max_parse_chars} 字符解析上限，已截断。"
    return text[:limits.max_parse_chars], True


def _parse_plain_text(data: bytes, kind: str, limits: AttachmentLimits) -> ParsedAttachment:
    text = _decode_text(data).strip()
    if not text:
        raise AttachmentParseError("empty_file", "文件内容为空，未解析到有效文本。")
    capped, truncated = _cap_text(text, limits)
    warnings = [f"文本超过 {limits.max_parse_chars} 字符解析上限，已截断。"] if truncated else []
    return ParsedAttachment(
        kind=kind, parse_method="plain_text", text=capped, truncated=truncated,
        warnings=warnings, stats={"chars": len(capped)})


def _parse_delimited(data: bytes, *, kind: str, delimiter: str,
                     limits: AttachmentLimits) -> ParsedAttachment:
    decoded = _decode_text(data)
    reader = csv.reader(io.StringIO(decoded, newline=""), delimiter=delimiter)
    rendered: list[str] = []
    rows = 0
    usable_rows = 0
    columns = 0
    ragged = False
    expected = None
    for row in reader:
        rows += 1
        if rows > limits.max_rows_per_table:
            raise AttachmentParseError(
                "limit_exceeded",
                f"表格超过每表 {limits.max_rows_per_table} 行上限，已拒绝该文件。")
        columns = max(columns, len(row))
        if any(cell is not None and cell.strip() for cell in row):
            usable_rows += 1
        if expected is None:
            expected = len(row)
        elif len(row) != expected:
            ragged = True
        rendered.append("\t".join(cell if cell is not None else "" for cell in row))
    if rows == 0 or usable_rows == 0:
        raise AttachmentParseError("empty_file", "文件内容为空，未解析到任何表格行。")
    text_body = "\n".join(rendered)
    capped, truncated = _cap_text(text_body, limits)
    warnings = []
    if ragged:
        warnings.append("表格各行列数不一致，已按原始行渲染。")
    if truncated:
        warnings.append(f"文本超过 {limits.max_parse_chars} 字符解析上限，已截断。")
    return ParsedAttachment(
        kind=kind, parse_method=f"{kind}_table", text=capped, truncated=truncated,
        warnings=warnings, stats={"rows": rows, "columns": columns})


def _parse_xlsx(data: bytes, limits: AttachmentLimits,
                deadline: float, clock: Callable[[], float]) -> ParsedAttachment:
    # 宏工作簿与外部链接先按 ZIP 目录名判定，绝不交给解析器执行任何部件。
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile as error:
        raise AttachmentParseError("parse_failed", "工作簿文件损坏，无法解析。") from error
    if any(name.endswith("vbaProject.bin") for name in names):
        raise AttachmentParseError(
            "content_rejected", "检测到宏工作簿（vbaProject），已拒绝；请另存为不含宏的 XLSX。")
    warnings = []
    if any(name.startswith("xl/externalLinks/") for name in names):
        warnings.append("工作簿包含外部链接，已忽略，未加载任何外部数据。")
    from openpyxl import load_workbook
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True,
                                 keep_vba=False)
    except Exception as error:  # noqa: BLE001 - 只报类型，不透传上游内容
        raise AttachmentParseError(
            "parse_failed", "工作簿解析失败：" + type(error).__name__) from error
    rendered: list[str] = []
    total_rows = 0
    total_cells = 0
    usable_cells = 0
    sheet_names: list[str] = []
    truncated = False
    truncated_warning = None
    try:
        for sheet in workbook.worksheets:
            if clock() > deadline:
                raise AttachmentParseError(
                    "time_budget_exceeded",
                    f"解析时间超过 {limits.parse_time_budget_seconds:g} 秒预算，已中止。")
            sheet_names.append(sheet.title)
            rendered.append(f"## {sheet.title}")
            rows = 0
            for row in sheet.iter_rows(values_only=True):
                if clock() > deadline:
                    raise AttachmentParseError(
                        "time_budget_exceeded",
                        f"解析时间超过 {limits.parse_time_budget_seconds:g} 秒预算，已中止。")
                rows += 1
                if rows > limits.max_rows_per_table:
                    raise AttachmentParseError(
                        "limit_exceeded",
                        f"工作表「{sheet.title}」超过每表 {limits.max_rows_per_table} 行上限，已拒绝该文件。")
                total_cells += len(row)
                if total_cells > limits.max_workbook_cells:
                    raise AttachmentParseError(
                        "limit_exceeded",
                        f"工作簿超过 {limits.max_workbook_cells} 个实际处理单元格上限，已拒绝该文件。")
                if any(value is not None and str(value).strip() for value in row):
                    usable_cells += 1
                if not truncated:
                    line = "\t".join("" if value is None else str(value) for value in row)
                    if len("\n".join(rendered)) + len(line) > limits.max_parse_chars:
                        truncated = True
                        truncated_warning = (
                            f"文本超过 {limits.max_parse_chars} 字符解析上限，已截断。")
                    else:
                        rendered.append(line)
            total_rows += rows
            rendered.append("")
    finally:
        workbook.close()
    if truncated:
        warnings.append(truncated_warning)
    # read_only + data_only：公式单元格只取文件内缓存值，本项目绝不重新计算。
    warnings.append("公式单元格仅读取文件内缓存值，未重新计算。")
    text = "\n".join(rendered).strip("\n")
    if usable_cells == 0:
        raise AttachmentParseError("empty_file", "工作簿为空，未解析到任何单元格数据。")
    return ParsedAttachment(
        kind="xlsx", parse_method="xlsx_readonly", text=text, truncated=truncated,
        warnings=warnings, stats={"sheets": sheet_names[:50], "rows": total_rows,
                                  "cells": total_cells})


def _parse_pdf(data: bytes, limits: AttachmentLimits,
               deadline: float, clock: Callable[[], float]) -> ParsedAttachment:
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
    except Exception as error:  # noqa: BLE001 - 只报类型，不透传上游内容
        raise AttachmentParseError("parse_failed", "PDF 文件损坏，无法解析。") from error
    if reader.is_encrypted:
        raise AttachmentParseError(
            "password_protected", "PDF 受密码保护，请解除密码或另存为无密码文本型 PDF 后重新上传。")
    pages = len(reader.pages)
    if pages > limits.pdf_max_pages:
        raise AttachmentParseError(
            "limit_exceeded", f"PDF 共 {pages} 页，超过 {limits.pdf_max_pages} 页上限。")
    parts: list[str] = []
    warnings: list[str] = []
    total_chars = 0
    parsed_pages = 0
    truncated = False
    for index, page in enumerate(reader.pages, start=1):
        if clock() > deadline:
            raise AttachmentParseError(
                "time_budget_exceeded",
                f"解析时间超过 {limits.parse_time_budget_seconds:g} 秒预算，已中止。")
        try:
            page_text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - 单页失败不拖垮整份文件
            warnings.append(f"第 {index} 页文本提取失败，已跳过。")
            continue
        if not page_text.strip():
            continue
        chunk = f"[第 {index} 页]\n{page_text}"
        if total_chars + len(chunk) > limits.max_parse_chars:
            remain = limits.max_parse_chars - total_chars
            if remain > 0:
                parts.append(chunk[:remain])
            truncated = True
            warnings.append(f"文本超过 {limits.max_parse_chars} 字符解析上限，已截断。")
            break
        parts.append(chunk)
        total_chars += len(chunk)
        parsed_pages += 1
    text = "\n\n".join(parts)
    if not text.strip():
        raise AttachmentParseError(
            "no_text_layer",
            "未检测到 PDF 文本层（可能为扫描件）；本应用不做 OCR，可上传页面截图配合已配置的视觉模型分析。")
    return ParsedAttachment(
        kind="pdf", parse_method="pdf_text_layer", text=text, truncated=truncated,
        warnings=warnings,
        stats={"pages": pages, "parsed_pages": parsed_pages, "chars": total_chars})


def parse_attachment(data: bytes, *, extension: str, mime_type: str,
                     limits: AttachmentLimits | None = None,
                     clock: Callable[[], float] = time.monotonic) -> ParsedAttachment:
    """双检（扩展名+MIME）→ 魔数嗅探 → 分类型解析；任何失败都是结构化错误。"""
    limits = limits or AttachmentLimits()
    if not isinstance(data, (bytes, bytearray)):
        raise AttachmentParseError("parse_failed", "文件内容不是有效字节，已拒绝。")
    ext = (extension or "").strip().lstrip(".").lower()
    if ext == "docx":
        raise AttachmentParseError(
            "unsupported_type",
            "当前版本支持 TXT/Markdown/CSV/TSV/XLSX/文本型 PDF；docx 请另存为上述格式后上传。")
    if ext not in ALLOWED_EXTENSIONS:
        raise AttachmentParseError(
            "unsupported_type",
            "仅支持 TXT/Markdown/CSV/TSV/XLSX/文本型 PDF 附件，其他类型已拒绝。")
    mime = (mime_type or "").split(";", 1)[0].strip().lower()
    if mime not in MIME_BY_EXTENSION[ext]:
        raise AttachmentParseError(
            "type_mismatch", f"Content-Type「{mime or '缺失'}」与扩展名 .{ext} 不匹配，已拒绝。")
    content = sniff_content(data)
    deadline = clock() + limits.parse_time_budget_seconds
    if content == "executable":
        raise AttachmentParseError("content_rejected", "检测到可执行文件内容，已拒绝。")
    if content == "html":
        raise AttachmentParseError("content_rejected", "HTML 内容被拒绝，请上传纯文本或文档格式。")
    if content == "binary":
        raise AttachmentParseError("content_rejected", "文件包含二进制内容，与声明的文本类型不符。")
    if content == "pdf":
        if ext != "pdf":
            raise AttachmentParseError("content_rejected", "文件内容与声明类型不符，已拒绝。")
        return _parse_pdf(data, limits, deadline, clock)
    if content == "zip":
        if ext != "xlsx":
            raise AttachmentParseError("content_rejected", "归档/压缩包内容被拒绝。")
        return _parse_xlsx(data, limits, deadline, clock)
    # text 族：内容是文本时按扩展名路由
    if ext == "csv":
        return _parse_delimited(data, kind="csv", delimiter=",", limits=limits)
    if ext == "tsv":
        return _parse_delimited(data, kind="tsv", delimiter="\t", limits=limits)
    kind = "markdown" if ext in {"md", "markdown"} else "text"
    return _parse_plain_text(data, kind, limits)
