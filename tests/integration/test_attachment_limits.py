"""Attachment upload limits and safety integration tests (plan 04 sections 2.5 / 4).

全离线：MockTransport 对任何外部请求直接断言失败（证明上传链路零外发），
模型调用/任务/再分析计数全程不变。覆盖：会话越权、multipart 限定路由、
数量/单文件/合计/请求体限额、32-hex 防遍历、伪扩展与扫描件的结构化错误
与 DataGap 候选、reference_only/excerpt 留存、绝不自动触发再分析。
"""

from __future__ import annotations

import hashlib
import io
import sqlite3
import uuid
import zipfile
from contextlib import contextmanager

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from openpyxl import Workbook

from backend.app.agent.output_models import Coverage, OutputIntegrity, ResearchReport
from backend.app.main import create_app
from backend.app.research.attachments import AttachmentLimits, AttachmentStore, is_attachment_id
from backend.app.research.models import ResearchTask
from backend.app.research.repository import ResearchRepository
from backend.app.research.reports import ReportStore
from backend.app.settings import Settings
from backend.app.storage import Storage

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ---------------------------------------------------------------- fixtures ---
def build_xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "利润表"
    sheet.append(["period", "revenue"])
    sheet.append(["2024Q1", 100])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def make_pdf_blank_pages(count: int) -> bytes:
    from pypdf import PdfWriter
    writer = PdfWriter()
    for _ in range(count):
        writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.fixture
def make_harness(tmp_path):
    @contextmanager
    def make(**settings_kwargs) -> dict:
        settings = Settings(project_root=tmp_path,
                            encryption_key=Fernet.generate_key().decode(),
                            **settings_kwargs)

        def no_network(request):
            raise AssertionError("Offline tests must not make external requests")

        app = create_app(settings, transport=httpx.MockTransport(no_network))
        client = TestClient(app, base_url="http://127.0.0.1:8000")
        with client:
            token = client.get("/api/system/session",
                               headers={"X-FinSight-Client": "web"}).json()["token"]
            client.headers.update({"X-FinSight-Session": token})
            # 不经过模型：直接写入任务与报告行，附件上传只依赖报告存在。
            repository = ResearchRepository(Storage(settings.db_path))
            task = ResearchTask(id=uuid.uuid4().hex, question="附件上传集成测试")
            repository.save_task(task)
            report = ResearchReport(id=uuid.uuid4().hex, version=1, task_id=task.id,
                                    question="附件上传集成测试",
                                    coverage=Coverage.PARTIAL,
                                    output_integrity=OutputIntegrity.VALIDATED)
            # save() 为报告行生成新的持久化 id：必须用返回值，而不是内存对象 id。
            stored_report = ReportStore(repository).save(report)
            yield {"client": client, "settings": settings, "app": app,
                   "report_id": stored_report.id, "task_id": task.id,
                   "repository": repository}

    return make


@pytest.fixture
def harness(make_harness):
    with make_harness() as items:
        yield items


def upload(client, report_id: str, files: list, retention: str | None = None):
    data = {"report_id": report_id}
    if retention is not None:
        data["retention"] = retention
    return client.post("/api/supplements/files", data=data, files=files)


def db_count(settings, table: str) -> int:
    db = sqlite3.connect(settings.db_path)
    try:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        db.close()


# ------------------------------------------------------------- 安全边界 ---
def test_upload_requires_session_header(harness):
    client = harness["client"]
    saved_token = client.headers.pop("X-FinSight-Session")
    try:
        response = upload(client, harness["report_id"],
                          [("files", ("a.txt", b"hello", "text/plain"))])
        assert response.status_code == 403
        assert response.json()["error"]["category"] == "session_expired"
        response = client.post("/api/system/session", headers={})
        assert response.status_code in (403, 422)
    finally:
        client.headers["X-FinSight-Session"] = saved_token


def test_multipart_rejected_on_other_write_routes(harness):
    client = harness["client"]
    response = client.post("/api/models", files={"f": ("a.txt", b"x", "text/plain")})
    assert response.status_code == 415
    assert response.json()["error"]["category"] == "json_required"
    response = client.post("/api/research/tasks", files={"f": ("a.txt", b"x", "text/plain")})
    assert response.status_code == 415


def test_unknown_or_malformed_report_rejected(harness):
    client = harness["client"]
    response = upload(client, "0" * 32, [("files", ("a.txt", b"hi", "text/plain"))])
    assert response.status_code == 404
    response = upload(client, "../../etc/passwd",
                      [("files", ("a.txt", b"hi", "text/plain"))])
    assert response.status_code == 422
    assert response.json()["error"]["category"] == "validation"


def test_invalid_retention_and_too_many_files(harness):
    client, report_id = harness["client"], harness["report_id"]
    response = upload(client, report_id,
                      [("files", ("a.txt", b"hi", "text/plain"))], retention="full")
    assert response.status_code == 422
    response = upload(client, report_id,
                      [("files", (f"f{i}.txt", b"hi", "text/plain")) for i in range(6)])
    assert response.status_code == 422
    assert "最多上传 5 个" in response.json()["error"]["message"]


# --------------------------------------------------------------- 正常路径 ---
def test_reference_only_upload_saves_supplements_without_raw_file(harness):
    client, settings, report_id = harness["client"], harness["settings"], harness["report_id"]
    response = upload(client, report_id, [
        ("files", ("纪要.txt", "管理层讨论：一季度毛利率提升。".encode("utf-8"), "text/plain")),
        ("files", ("数据.csv", "period,revenue\n2024Q1,100\n".encode("utf-8"), "text/csv")),
    ])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["failed"] == [] and body["gaps"] == []
    assert len(body["saved"]) == 2 and len(body["supplements"]) == 2
    txt_record, csv_record = body["saved"]
    assert is_attachment_id(txt_record["id"])
    assert txt_record["display_name"] == "纪要.txt"
    assert txt_record["storage_policy"] == "reference_only"
    assert txt_record["raw_file_retained"] is False
    assert txt_record["excerpt_path"] is None
    assert txt_record["sha256"] == hashlib.sha256(
        "管理层讨论：一季度毛利率提升。".encode("utf-8")).hexdigest()
    assert txt_record["kind"] == "text" and csv_record["kind"] == "csv"
    assert csv_record["stats"] == {"rows": 2, "columns": 2}
    txt_supplement = body["supplements"][0]
    assert txt_supplement["kind"] == "user_provided"
    assert txt_supplement["referenced_by_report_id"] is None
    assert txt_supplement["fields"]["dimension"] == "local_attachment"
    assert "纪要.txt" in txt_supplement["fields"]["source_note"]
    assert "plain_text" in txt_supplement["fields"]["source_note"]
    assert "毛利率" in txt_supplement["content"]
    csv_supplement = body["supplements"][1]
    assert "csv_table" in csv_supplement["fields"]["source_note"]
    assert "2024Q1\t100" in csv_supplement["content"]
    # reference_only：原始文件与摘录都不落盘
    attachments_dir = settings.project_root / "data" / "attachments"
    assert not attachments_dir.exists() or list(attachments_dir.iterdir()) == []
    # 数据库行可查
    repository = harness["repository"]
    row = repository.get_attachment(txt_record["id"])
    assert row is not None and row.display_name == "纪要.txt"


def test_excerpt_retention_writes_only_id_named_excerpt(harness):
    client, settings, report_id = harness["client"], harness["settings"], harness["report_id"]
    long_text = "营收数据行。" * 50
    response = upload(client, report_id,
                      [("files", ("note.txt", long_text.encode("utf-8"), "text/plain"))],
                      retention="excerpt")
    assert response.status_code == 200, response.text
    record = response.json()["saved"][0]
    assert record["storage_policy"] == "excerpt"
    assert record["raw_file_retained"] is False
    excerpt_path = settings.project_root / record["excerpt_path"]
    assert excerpt_path.exists()
    # 目录里只有 32-hex 命名的摘录文件；原文件名绝不出现在文件系统
    entries = sorted(p.name for p in excerpt_path.parent.iterdir())
    assert entries == [f"{record['id']}.excerpt.txt"]
    assert all(is_attachment_id(name.split(".")[0]) for name in entries)
    assert "营收数据行" in excerpt_path.read_text(encoding="utf-8")


def test_xlsx_upload_end_to_end(harness):
    client, report_id = harness["client"], harness["report_id"]
    response = upload(client, report_id,
                      [("files", ("报表.xlsx", build_xlsx_bytes(), XLSX_MIME))])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["failed"] == []
    record = body["saved"][0]
    assert record["kind"] == "xlsx" and record["parse_method"] == "xlsx_readonly"
    assert record["stats"]["rows"] == 2 and record["stats"]["cells"] == 4
    assert "2024Q1\t100" in body["supplements"][0]["content"]
    assert any("未重新计算" in warning for warning in record["warnings"])


# --------------------------------------------------- 失败隔离与 DataGap ---
def test_failed_files_do_not_block_others(harness):
    client, report_id = harness["client"], harness["report_id"]
    response = upload(client, report_id, [
        ("files", ("good.txt", "有效内容。".encode("utf-8"), "text/plain")),
        ("files", ("evil.txt", b"MZ\x90\x00" + b"\x00" * 32, "text/plain")),
        ("files", ("scan.pdf", make_pdf_blank_pages(1), "application/pdf")),
        ("files", ("bad.csv", "营收，利润\n".encode("gbk"), "text/csv")),
    ])
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["display_name"] for item in body["saved"]] == ["good.txt"]
    codes = {item["display_name"]: item["code"] for item in body["failed"]}
    assert codes == {"evil.txt": "content_rejected", "scan.pdf": "no_text_layer",
                     "bad.csv": "decode_error"}
    assert len(body["gaps"]) == 3
    for gap in body["gaps"]:
        assert gap["reason"] == "unsupported"
        assert gap["dimension"] == "local_attachment"
        assert gap["remedies"], "每个失败必须带补救入口"
    scan_gap = next(gap for gap in body["gaps"] if "scan.pdf" in gap["impact"])
    assert any("视觉" in remedy for remedy in scan_gap["remedies"])
    assert len(body["supplements"]) == 1


def test_traversal_filename_rejected_without_writes(harness):
    client, settings, report_id = harness["client"], harness["settings"], harness["report_id"]
    response = upload(client, report_id, [
        ("files", ("../evil.txt", b"hello", "text/plain")),
        ("files", ("sub\\dir.txt", b"hello", "text/plain")),
    ])
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["code"] for item in body["failed"]] == ["invalid_filename", "invalid_filename"]
    assert body["saved"] == [] and body["supplements"] == []
    assert not (settings.project_root / "evil.txt").exists()
    attachments_dir = settings.project_root / "data" / "attachments"
    assert not attachments_dir.exists() or list(attachments_dir.iterdir()) == []


# ------------------------------------------------------------- 限额 ---
def test_per_file_and_total_limits_with_small_caps(harness):
    client, settings, app = harness["client"], harness["settings"], harness["app"]
    repository = ResearchRepository(Storage(settings.db_path))
    app.state.attachments = AttachmentStore(
        repository, settings,
        AttachmentLimits(max_file_bytes=64, max_total_bytes=100))
    response = upload(client, harness["report_id"], [
        ("files", ("small.txt", b"a" * 60, "text/plain")),
        ("files", ("big.txt", b"b" * 100, "text/plain")),
        ("files", ("tail.txt", b"c" * 60, "text/plain")),
    ])
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["display_name"] for item in body["saved"]] == ["small.txt"]
    codes = {item["display_name"]: item["code"] for item in body["failed"]}
    assert codes == {"big.txt": "too_large", "tail.txt": "total_too_large"}
    too_large_gap = next(gap for gap in body["gaps"] if "big.txt" in gap["impact"])
    assert any("压缩" in remedy or "大小" in remedy for remedy in too_large_gap["remedies"])


def test_request_body_cap_enforced_by_middleware(make_harness):
    with make_harness(attachment_request_cap_bytes=1024) as harness:
        client, report_id = harness["client"], harness["report_id"]
        response = upload(client, report_id,
                          [("files", ("big.txt", b"x" * 4096, "text/plain"))])
        assert response.status_code == 413
        assert response.json()["error"]["category"] == "payload_too_large"
        # 正常大小不受影响
        ok = upload(client, report_id,
                    [("files", ("small.txt", b"ok", "text/plain"))])
        assert ok.status_code == 200 and ok.json()["failed"] == []


# ------------------------------------------- 不外发、不自动再分析 ---
def test_upload_never_triggers_reanalysis_or_external_calls(harness):
    client, settings, report_id = harness["client"], harness["settings"], harness["report_id"]
    tasks_before = db_count(settings, "research_tasks")
    reports_before = db_count(settings, "research_reports")
    supplements_before = db_count(settings, "research_supplements")
    response = upload(client, report_id, [
        ("files", ("纪要.txt", "会议要点：资本开支维持克制。".encode("utf-8"), "text/plain")),
    ], retention="excerpt")
    assert response.status_code == 200, response.text
    # 上传/解析链路零外部请求：MockTransport(no_network) 未触发断言即为证。
    assert db_count(settings, "research_tasks") == tasks_before
    assert db_count(settings, "research_reports") == reports_before
    assert db_count(settings, "research_supplements") == supplements_before + 1
    events = harness["repository"].list_events(harness["task_id"], 0)
    assert events == [], "上传不得产生任何任务事件或再分析"
    supplement = response.json()["supplements"][0]
    assert supplement["referenced_by_report_id"] is None
