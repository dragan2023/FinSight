"""Attachment parser unit tests (plan 04 sections 2.5 / 4).

全部离线：文件字节在测试内构造（手工最小 PDF、openpyxl 内存工作簿、zip 部件），
不访问网络。覆盖：各类型解析、魔数伪造/伪扩展、编码边界、可见上限、
时间预算（注入时钟）、扫描件如实报无文本层、结构化错误码。
"""

from __future__ import annotations

import io
import zipfile

import pytest
from openpyxl import Workbook
from pypdf import PdfReader, PdfWriter

from backend.app.research.attachment_parsers import (
    ALLOWED_EXTENSIONS,
    AttachmentLimits,
    AttachmentParseError,
    PARSE_ERROR_CODES,
    parse_attachment,
    sniff_content,
)

# ---------------------------------------------------------------- helpers ---
def build_minimal_pdf(*texts: str) -> bytes:
    """构造文本型 PDF：每段文本一页（BT/Tj 文本对象，pypdf strict=False 可提取）。"""
    n_pages = max(len(texts), 1)
    kids = " ".join(f"{3 + i} 0 R" for i in range(n_pages))
    font_id = 3 + 2 * n_pages
    page_objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode(),
    ]
    for i in range(n_pages):
        page_objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {3 + n_pages + i} 0 R"
            f" /Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode())
    for text in (texts if texts else [""]):
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        page_objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    page_objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(page_objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(page_objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(page_objects) + 1} /Root 1 0 R >>\n"
              f"startxref\n{xref_pos}\n%%EOF\n".encode())
    return out.getvalue()


def build_xlsx(sheets: dict[str, list[list]]) -> bytes:
    workbook = Workbook()
    first = True
    for title, rows in sheets.items():
        sheet = workbook.active if first else workbook.create_sheet(title)
        if first:
            sheet.title = title
            first = False
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def rewrite_zip(data: bytes, extra: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item.filename))
        for name, payload in extra.items():
            dst.writestr(name, payload)
    return out.getvalue()


def make_pdf_blank_pages(count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(count):
        writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class FakeClock:
    def __init__(self, *values: float) -> None:
        self.values = list(values)
        self.index = 0

    def __call__(self) -> float:
        value = self.values[min(self.index, len(self.values) - 1)]
        self.index += 1
        return value


def parse(data: bytes, ext: str, mime: str, limits: AttachmentLimits | None = None,
          clock=None):
    kwargs = {} if clock is None else {"clock": clock}
    return parse_attachment(data, extension=ext, mime_type=mime, limits=limits, **kwargs)


# ------------------------------------------------------------ text family ---
def test_plain_text_and_markdown_parse():
    result = parse("营收增长 12%，毛利率稳定。".encode("utf-8"), "txt", "text/plain")
    assert result.kind == "text" and result.parse_method == "plain_text"
    assert "营收增长" in result.text and result.stats["chars"] > 0
    md = parse("# 标题\n\n- 要点一".encode("utf-8"), "md", "text/markdown")
    assert md.kind == "markdown" and "# 标题" in md.text


def test_utf8_bom_accepted():
    result = parse(b"\xef\xbb\xbfperiod,revenue\n2024Q1,100\n", "csv", "text/csv")
    assert result.stats["rows"] == 2
    assert "2024Q1\t100" in result.text


def test_gbk_bytes_rejected_with_decode_error():
    with pytest.raises(AttachmentParseError) as info:
        parse("营收，利润\n100，80\n".encode("gbk"), "csv", "text/csv")
    assert info.value.code == "decode_error"
    assert "UTF-8" in info.value.message


def test_binary_and_nul_bytes_rejected():
    with pytest.raises(AttachmentParseError) as info:
        parse(b"report\x00\x01\x02data", "txt", "text/plain")
    assert info.value.code == "content_rejected"


def test_text_truncated_to_parse_cap():
    limits = AttachmentLimits(max_parse_chars=10)
    result = parse(("字" * 50).encode("utf-8"), "txt", "text/plain", limits)
    assert result.truncated and len(result.text) == 10
    assert result.warnings and "截断" in result.warnings[0]


# -------------------------------------------------------- extension / MIME ---
def test_unknown_extension_unsupported():
    with pytest.raises(AttachmentParseError) as info:
        parse(b"data", "exe", "application/octet-stream")
    assert info.value.code == "unsupported_type"
    with pytest.raises(AttachmentParseError) as info:
        parse(b"data", "docx",
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert info.value.code == "unsupported_type"
    assert "docx" in info.value.message and "另存" in info.value.message


def test_mime_mismatch_rejected_and_equivalents_accepted():
    with pytest.raises(AttachmentParseError) as info:
        parse("hello".encode(), "txt", "application/pdf")
    assert info.value.code == "type_mismatch"
    assert parse("a,b\n1,2\n".encode(), "csv", "text/plain").kind == "csv"
    assert parse("a\tb\n1\t2\n".encode(), "tsv", "text/tab-separated-values").kind == "tsv"


# ------------------------------------------------------- magic / disguise ---
def test_executable_disguised_as_txt_rejected():
    with pytest.raises(AttachmentParseError) as info:
        parse(b"MZ\x90\x00" + b"\x00" * 64, "txt", "text/plain")
    assert info.value.code == "content_rejected" and "可执行" in info.value.message


def test_zip_disguised_as_csv_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("x.txt", "hi")
    with pytest.raises(AttachmentParseError) as info:
        parse(buffer.getvalue(), "csv", "text/csv")
    assert info.value.code == "content_rejected" and "归档" in info.value.message


def test_html_content_rejected():
    payload = "<!DOCTYPE html><html><body><p>公告</p></body></html>".encode("utf-8")
    with pytest.raises(AttachmentParseError) as info:
        parse(payload, "txt", "text/plain")
    assert info.value.code == "content_rejected" and "HTML" in info.value.message


def test_pdf_content_with_txt_extension_rejected():
    with pytest.raises(AttachmentParseError) as info:
        parse(build_minimal_pdf("hello"), "txt", "text/plain")
    assert info.value.code == "content_rejected"


def test_xlsx_content_with_csv_extension_rejected():
    with pytest.raises(AttachmentParseError) as info:
        parse(build_xlsx({"Sheet1": [["a", 1]]}), "csv", "text/csv")
    assert info.value.code == "content_rejected"


# ------------------------------------------------------------------ csv ---
def test_csv_structured_rows_and_ragged_warning():
    result = parse("period,revenue\n2024Q1,100\n2024Q2,120,extra\n".encode(), "csv", "text/csv")
    assert result.parse_method == "csv_table"
    assert result.stats == {"rows": 3, "columns": 3}
    assert "2024Q1\t100" in result.text and "120\textra" in result.text
    assert any("列数不一致" in warning for warning in result.warnings)


def test_tsv_parse():
    result = parse("period\trevenue\n2024Q1\t100\n".encode(), "tsv", "text/plain")
    assert result.parse_method == "tsv_table" and result.stats["rows"] == 2


def test_csv_row_limit_rejects_file():
    limits = AttachmentLimits(max_rows_per_table=3)
    payload = "\n".join(f"row{i},1" for i in range(5))
    with pytest.raises(AttachmentParseError) as info:
        parse(payload.encode(), "csv", "text/csv", limits)
    assert info.value.code == "limit_exceeded" and "每表 3 行" in info.value.message


def test_empty_csv_rejected():
    with pytest.raises(AttachmentParseError) as info:
        parse(b"", "csv", "text/csv")
    assert info.value.code == "empty_file"
    with pytest.raises(AttachmentParseError):
        parse(b"\n\n", "csv", "text/csv")


# ----------------------------------------------------------------- xlsx ---
def test_xlsx_multi_sheet_parse():
    data = build_xlsx({"利润表": [["period", "revenue"], ["2024Q1", 100]],
                       "资产负债表": [["item", "value"], ["cash", 42]]})
    result = parse(data, "xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert result.kind == "xlsx" and result.parse_method == "xlsx_readonly"
    assert result.stats["sheets"] == ["利润表", "资产负债表"]
    assert result.stats["rows"] == 4 and result.stats["cells"] == 8
    assert "## 利润表" in result.text and "2024Q1\t100" in result.text
    assert "## 资产负债表" in result.text and "cash\t42" in result.text
    assert any("未重新计算" in warning for warning in result.warnings)


def test_xlsx_octet_stream_mime_accepted():
    data = build_xlsx({"Sheet1": [["a", 1]]})
    result = parse(data, "xlsx", "application/octet-stream")
    assert result.stats["rows"] == 1


def test_xlsx_formula_cells_use_cached_values_warning():
    data = build_xlsx({"Sheet1": [["rev"], ["=SUM(B1:B9)"]]})
    result = parse(data, "xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert any("缓存值" in warning and "未重新计算" in warning for warning in result.warnings)


def test_xlsx_with_macro_project_rejected():
    data = rewrite_zip(build_xlsx({"Sheet1": [["a", 1]]}),
                       {"xl/vbaProject.bin": b"fake-vba"})
    with pytest.raises(AttachmentParseError) as info:
        parse(data, "xlsx",
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert info.value.code == "content_rejected" and "宏" in info.value.message


def test_xlsx_external_links_ignored_with_warning():
    data = rewrite_zip(build_xlsx({"Sheet1": [["a", 1]]}),
                       {"xl/externalLinks/externalLink1.xml": b"<x/>"})
    result = parse(data, "xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert result.stats["rows"] == 1
    assert any("外部链接" in warning for warning in result.warnings)


def test_xlsx_row_and_cell_limits():
    data = build_xlsx({"Sheet1": [[i, i + 1] for i in range(6)]})
    with pytest.raises(AttachmentParseError) as info:
        parse(data, "xlsx",
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              AttachmentLimits(max_rows_per_table=4))
    assert info.value.code == "limit_exceeded" and "每表 4 行" in info.value.message
    small = build_xlsx({"Sheet1": [["a", "b"], ["c", "d"]]})
    with pytest.raises(AttachmentParseError) as info:
        parse(small, "xlsx",
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              AttachmentLimits(max_workbook_cells=3))
    assert info.value.code == "limit_exceeded" and "单元格" in info.value.message


def test_xlsx_corrupt_zip_rejected():
    with pytest.raises(AttachmentParseError) as info:
        parse(b"PK\x03\x04" + b"garbage" * 8, "xlsx",
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert info.value.code == "parse_failed"


def test_xlsx_all_empty_cells_rejected():
    data = build_xlsx({"Sheet1": [[None, None], [None, None]]})
    with pytest.raises(AttachmentParseError) as info:
        parse(data, "xlsx",
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert info.value.code == "empty_file"


# ------------------------------------------------------------------ pdf ---
def test_pdf_text_layer_extracted_with_page_markers():
    data = build_minimal_pdf("Hello FinSight 2024", "第二页文字 Revenue")
    result = parse(data, "pdf", "application/pdf")
    assert result.kind == "pdf" and result.parse_method == "pdf_text_layer"
    assert result.stats["pages"] == 2 and result.stats["parsed_pages"] == 2
    assert "[第 1 页]" in result.text and "Hello FinSight 2024" in result.text
    assert "[第 2 页]" in result.text


def test_encrypted_pdf_reports_password_protected():
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("secret-pass")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(AttachmentParseError) as info:
        parse(buffer.getvalue(), "pdf", "application/pdf")
    assert info.value.code == "password_protected"
    assert PdfReader(io.BytesIO(buffer.getvalue())).is_encrypted


def test_scanned_pdf_reports_no_text_layer_without_ocr():
    data = make_pdf_blank_pages(2)
    with pytest.raises(AttachmentParseError) as info:
        parse(data, "pdf", "application/pdf")
    assert info.value.code == "no_text_layer"
    assert "文本层" in info.value.message and "OCR" in info.value.message


def test_pdf_page_limit():
    data = make_pdf_blank_pages(4)
    with pytest.raises(AttachmentParseError) as info:
        parse(data, "pdf", "application/pdf", AttachmentLimits(pdf_max_pages=3))
    assert info.value.code == "limit_exceeded" and "超过 3 页" in info.value.message


def test_corrupt_pdf_after_magic_rejected():
    with pytest.raises(AttachmentParseError) as info:
        parse(b"%PDF-1.4 this is not a pdf", "pdf", "application/pdf")
    assert info.value.code == "parse_failed"


def test_pdf_time_budget_aborts():
    data = build_minimal_pdf("hello", "world")
    clock = FakeClock(100.0, 1000.0)  # 首次调用取预算起点，第二次直接超预算
    with pytest.raises(AttachmentParseError) as info:
        parse(data, "pdf", "application/pdf", None, clock)
    assert info.value.code == "time_budget_exceeded"
    assert "60 秒" in info.value.message


def test_pdf_partial_extraction_keeps_parsed_pages():
    # 第 1 页有文本、第 2 页空白：parsed_pages 只计有文本的页。
    data = build_minimal_pdf("visible text", "")
    result = parse(data, "pdf", "application/pdf")
    assert result.stats["pages"] == 2 and result.stats["parsed_pages"] == 1
    assert "[第 1 页]" in result.text


# -------------------------------------------------------------- helpers ---
def test_sniff_content_table():
    assert sniff_content(b"MZ\x90\x00abc") == "executable"
    assert sniff_content(b"%PDF-1.4") == "pdf"
    assert sniff_content(b"PK\x03\x04zipdata") == "zip"
    assert sniff_content(b"<!doctype html><html>") == "html"
    assert sniff_content(b"\x00\x01binary") == "binary"
    assert sniff_content("普通文本".encode()) == "text"


def test_error_codes_have_reasons_and_remedies():
    from backend.app.research.attachment_parsers import REASON_BY_CODE, REMEDIES_BY_CODE
    assert set(REASON_BY_CODE) <= set(PARSE_ERROR_CODES)
    assert set(REMEDIES_BY_CODE) == set(PARSE_ERROR_CODES)
    assert all(remedies for remedies in REMEDIES_BY_CODE.values())


def test_attachment_gap_candidate_mapping():
    from backend.app.research.attachments import attachment_gap
    timeout_gap = attachment_gap("a.pdf", "time_budget_exceeded", "超时")
    assert timeout_gap.reason == "timeout" and timeout_gap.dimension == "local_attachment"
    gap = attachment_gap("a.pdf", "no_text_layer", "无文本层")
    assert gap.reason == "unsupported"
    assert any("视觉" in remedy for remedy in gap.remedies)
    assert gap.id and len(gap.id) == 32


def test_allowed_extension_whitelist():
    assert ALLOWED_EXTENSIONS == {"txt", "md", "markdown", "csv", "tsv", "xlsx", "pdf"}
    assert "docx" not in ALLOWED_EXTENSIONS and "html" not in ALLOWED_EXTENSIONS
