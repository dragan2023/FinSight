"""Supplement API, mapping and reanalysis integration tests (plan 04 §2.6 / §4).

全部离线且零模型：不配置任何模型/API 档案，MockTransport 对任何外部请求
直接断言失败。覆盖计划验收点——未外发授权时所有搜索/模型客户端零调用；
保存、字段映射确认、主动再分析（引用旧版补充、生成子版本）、保留旧版完整
可验证；DELETE 引用保护；source-preview 只本地预览数据流与预算。
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend.app.agent.output_models import Coverage, OutputIntegrity, ResearchReport
from backend.app.agent.research_graph import ResearchGraph
from backend.app.main import create_app
from backend.app.research.models import ResearchTask
from backend.app.research.repository import ResearchRepository
from backend.app.research.reports import ReportStore
from backend.app.settings import Settings
from backend.app.storage import Storage


# ---------------------------------------------------------------- fixtures ---
@contextmanager
def harness_scope(tmp_path):
    """无模型/无 API 档案 + 断网 transport：任何外发或模型调用都会立刻显形。"""
    settings = Settings(project_root=tmp_path,
                        encryption_key=Fernet.generate_key().decode())

    def no_network(request):
        raise AssertionError("Offline tests must not make external requests")

    app = create_app(settings, transport=httpx.MockTransport(no_network))
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    with client:
        token = client.get("/api/system/session",
                           headers={"X-FinSight-Client": "web"}).json()["token"]
        client.headers.update({"X-FinSight-Session": token})
        repository = ResearchRepository(Storage(settings.db_path))
        task = ResearchTask(id=uuid.uuid4().hex, question="补充资料再分析集成测试")
        repository.save_task(task)
        report = ResearchReport(id=uuid.uuid4().hex, version=1, task_id=task.id,
                                question="补充资料再分析集成测试",
                                coverage=Coverage.PARTIAL,
                                output_integrity=OutputIntegrity.VALIDATED)
        stored_report = ReportStore(repository).save(report)
        # 关联任务与报告（save_task 为 upsert），source-preview 才能看到资料清单。
        task.report_id = stored_report.id
        repository.save_task(task)
        yield {"client": client, "settings": settings, "app": app,
               "repository": repository, "report_id": stored_report.id,
               "task_id": task.id}


@pytest.fixture
def harness(tmp_path):
    with harness_scope(tmp_path) as items:
        yield items


def upload_attachment(client, report_id: str, name: str = "纪要.txt",
                      content: bytes | None = None, retention="excerpt"):
    if content is None:
        content = "会议要点：资本开支维持克制。".encode("utf-8")
    return client.post("/api/supplements/files", data={"report_id": report_id,
                                                       "retention": retention},
                       files=[("files", (name, content, "text/plain"))])


def wait_for_status(client, task_id: str, status: str, timeout: float = 15.0) -> dict:
    import time
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/research/tasks/{task_id}")
        assert response.status_code == 200, response.text
        last = response.json()["task"]
        if last["status"] == status:
            return last
        time.sleep(0.02)
    pytest.fail(f"任务状态未到达 {status}，当前为 {last and last['status']}")


# ------------------------------------------------- 保存 → 映射 → 再分析 ---
def test_save_map_and_reanalyze_references_supplements(harness):
    client, settings = harness["client"], harness["settings"]
    report_id = harness["report_id"]
    before = client.get(f"/api/research/reports/{report_id}").json()

    text_supplement = client.post(f"/api/research/reports/{report_id}/supplements", json={
        "kind": "user_provided", "content": "管理层电话会：全年指引维持不变。",
        "fields": {"period": "2025Q1"}}).json()["supplement"]

    uploaded = upload_attachment(client, report_id)
    assert uploaded.status_code == 200, uploaded.text
    attachment = uploaded.json()["saved"][0]
    assert attachment["excerpt_path"] is not None
    attachment_supplement = uploaded.json()["supplements"][0]

    # 字段映射确认：只保存，不触发任何计算或调用
    mapping = client.put(f"/api/supplements/{attachment['id']}/mapping", json={
        "security_id": "600519.SH", "period": "2024Q1", "unit": "CNY"})
    assert mapping.status_code == 200, mapping.text
    mapped = mapping.json()["attachment"]
    assert mapped["mapping"] == {"security_id": "600519.SH", "period": "2024Q1", "unit": "CNY"}
    assert mapped["mapping_confirmed_at"]

    # 预览：元数据 + 摘录 + 关联补充（未被引用）
    preview = client.get(f"/api/supplements/{attachment['id']}")
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert "会议要点" in preview_body["excerpt"]
    assert preview_body["supplement"]["referenced_by_report_id"] is None
    assert preview_body["attachment"]["mapping"]["period"] == "2024Q1"

    # 主动再分析：显式选择两条补充；全程零模型/零外发（无档案 + 断网 transport）
    response = client.post(f"/api/research/reports/{report_id}/reanalyze", json={
        "supplement_ids": [text_supplement["id"], attachment_supplement["id"]],
        "client_request_id": "supp-reanalyze-1"})
    assert response.status_code == 202, response.text
    task = wait_for_status(client, response.json()["task"]["id"], "completed")
    assert task["usage"]["llm_requests"] == 0
    assert task["usage"]["fetches"] == 0
    child_report_id = task["report_id"]
    child = client.get(f"/api/research/reports/{child_report_id}").json()
    assert child["report"]["parent_report_id"] == report_id

    # 旧版完整可验证：报告与原始章节逐字节不变
    after = client.get(f"/api/research/reports/{report_id}").json()
    assert after == before

    # 两条补充都被显式引用；未再分析的补充保持未引用
    listing = client.get(f"/api/supplements/attachments?report_id={report_id}").json()
    referenced = {s["id"]: s["referenced_by_report_id"] for s in listing["supplements"]}
    assert referenced[text_supplement["id"]] == child_report_id
    assert referenced[attachment_supplement["id"]] == child_report_id


def test_mapping_candidates_are_deterministic_and_unconfirmed(harness):
    """批次7：候选值解析时确定性提取并入库存档；候选 ≠ 确认。"""
    client, report_id = harness["client"], harness["report_id"]
    content = "贵州茅台（600519.SH）2024年第一季度报告：营业收入按人民币万元列示。".encode("utf-8")
    uploaded = upload_attachment(client, report_id, name="quarter.txt", content=content)
    assert uploaded.status_code == 200, uploaded.text
    record = uploaded.json()["saved"][0]
    assert record["mapping_candidates"] == {
        "security_id": "600519.SH", "period": "2024Q1", "unit": "CNY",
        "note": "金额以万元计"}
    preview = client.get(f"/api/supplements/{record['id']}").json()
    assert preview["attachment"]["mapping_candidates"]["security_id"] == "600519.SH"
    # 候选只用于预填：mapping 仍为空，确认必须经 PUT mapping。
    assert preview["attachment"]["mapping"] == {}
    assert preview["attachment"]["mapping_confirmed_at"] is None


def test_mapping_and_preview_validation(harness):
    client = harness["client"]
    uploaded = upload_attachment(harness["client"], harness["report_id"])
    attachment_id = uploaded.json()["saved"][0]["id"]
    empty = client.put(f"/api/supplements/{attachment_id}/mapping", json={})
    assert empty.status_code == 422
    bad = client.put(f"/api/supplements/{attachment_id}/mapping", json={
        "security_id": "600519.XX"})
    assert bad.status_code == 422
    extra = client.put(f"/api/supplements/{attachment_id}/mapping", json={
        "metric": "revenue"})
    assert extra.status_code == 422
    unknown = client.put(f"/api/supplements/{'f' * 32}/mapping", json={"unit": "CNY"})
    assert unknown.status_code == 404
    unknown_preview = client.get(f"/api/supplements/{'e' * 32}")
    assert unknown_preview.status_code == 404
    # 非法格式 id（非 32-hex）→ 422；".." 等穿越片段到不了路由（HTTP 客户端已规范化），
    # 磁盘路径只由 32-hex ID 构成，显示名在写入侧另行拒绝。
    malformed = client.get("/api/supplements/not-a-valid-id")
    assert malformed.status_code == 422
    unknown_report_list = client.get("/api/supplements/attachments?report_id=" + "0" * 32)
    assert unknown_report_list.status_code == 404


def test_delete_referenced_protected_and_unreferenced_removed(harness):
    client, settings, repository = harness["client"], harness["settings"], harness["repository"]
    report_id = harness["report_id"]
    keep = upload_attachment(client, report_id, name="keep.txt").json()["saved"][0]
    drop = upload_attachment(client, report_id, name="drop.txt",
                             content="待删除的摘录内容。".encode("utf-8")).json()
    drop_attachment, drop_supplement = drop["saved"][0], drop["supplements"][0]
    drop_excerpt = settings.project_root / drop_attachment["excerpt_path"]
    assert drop_excerpt.exists()

    response = client.post(f"/api/research/reports/{report_id}/reanalyze", json={
        "supplement_ids": [keep["supplement_id"]], "client_request_id": "supp-del-1"})
    assert response.status_code == 202, response.text
    wait_for_status(client, response.json()["task"]["id"], "completed")

    # DELETE 属写操作：沿用全局「写操作只接受 application/json」契约（前端 client 恒带）。
    json_headers = {"Content-Type": "application/json"}
    referenced_delete = client.delete(f"/api/supplements/{keep['id']}", headers=json_headers)
    assert referenced_delete.status_code == 409
    assert referenced_delete.json()["error"]["category"] == "request_conflict"

    unreferenced_delete = client.delete(f"/api/supplements/{drop_attachment['id']}",
                                        headers=json_headers)
    assert unreferenced_delete.status_code == 200, unreferenced_delete.text
    assert unreferenced_delete.json()["excerpt_removed"] is True
    assert not drop_excerpt.exists(), "摘录文件必须随附件删除"
    listing = client.get(f"/api/supplements/attachments?report_id={report_id}").json()
    assert [a["id"] for a in listing["attachments"]] == [keep["id"]]
    assert drop_supplement["id"] not in {s["id"] for s in listing["supplements"]}


def test_source_preview_is_local_only(harness):
    client, report_id, task_id = harness["client"], harness["report_id"], harness["task_id"]
    upload_attachment(client, report_id)
    client.post(f"/api/research/reports/{report_id}/supplements", json={
        "kind": "user_assumption", "content": "假设毛利率环比持平。"})

    preview = client.post(f"/api/research/tasks/{task_id}/source-preview", json={})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    flows = {flow["flow"]: flow for flow in body["flows"]}
    assert set(flows) == {"hexin_structured", "model_llm", "web_search", "local_attachment"}
    assert flows["web_search"]["enabled"] is False
    assert "未授权" in flows["web_search"]["budget_note"]
    assert flows["local_attachment"]["budget_note"].endswith("1 个附件资料、2 条用户补充。")
    assert "本地" in body["notice"] and "未发起" in body["notice"]
    assert body["budget"]["max_searches"] == 0
    assert body["usage"]["llm_requests"] == 0

    unknown = client.post(f"/api/research/tasks/{'a' * 32}/source-preview", json={})
    assert unknown.status_code == 404


def test_material_lines_carry_source_note():
    """补充材料的来源标注进入模型材料；无来源时不附加后缀。"""
    graph = ResearchGraph(market=None, model=None, tasks=None, reports=None,
                          supplements=None)
    materials = graph._materials({
        "supplements": [
            {"kind": "user_provided", "content": "自有台账：营收持平。",
             "fields": {"source_note": "附件「纪要.txt」（text/plain）"}},
            {"kind": "user_assumption", "content": "假设估值已反映预期。", "fields": {}},
        ]}, "facts")
    assert "来源：附件「纪要.txt」" in materials
    assert "[user_assumption] 假设估值已反映预期。\n" in materials + "\n"
