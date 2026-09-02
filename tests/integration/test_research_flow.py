"""Research task flow integration tests (plan 03 section 4, item 4).

缺数据报告可完成、程序缺口不被模型删除、补充资料只保存不触发任何调用、
主动再分析产生新版本（parent_report_id）且旧版本保持可读；所有金融源不可用
时仍给出条件研究框架。全部离线：同花顺响应与模型 SSE 流均由 MockTransport
脚本提供（deepseek 官方地址被脚本拦截，不发生真实网络请求与真实计费）。
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import contextmanager

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend.app.agent.output_models import SECTION_TEMPLATES
from backend.app.main import create_app
from backend.app.settings import Settings

KEY_V1 = "sk-fuyao-unit-key-one"
MODEL_PATH = "/chat/completions"
TITLES = {item["id"]: item["title"] for item in SECTION_TEMPLATES}
REASONING_TEXT = "先取利润表与估值，再决定是否补价格。"


# ---------------------------------------------------------------- helpers ---
def envelope(data, code=0, message="success"):
    return {"code": code, "message": message, "request_id": "rid", "data": data}


def search_item(thscode="600519.SH", ticker="600519", name="贵州茅台"):
    return {"thscode": thscode, "ticker": ticker, "name": name,
            "exchange": thscode.split(".")[1], "asset_type": "a-share", "currency": "CNY"}


def income_row():
    return {"thscode": "600519.SH", "ticker": "600519", "period": "annual",
            "fiscal_year": 2024, "fiscal_period": "FY", "report_date_ms": 1735574400000,
            "period_end_ms": 1735574400000, "currency": "CNY",
            "operating_income": 174144000000, "net_profit": 93000000000,
            "parent_holder_net_profit": 86000000000}


def bar(date_ms, close):
    return {"date_ms": date_ms, "open_price": close, "high_price": close,
            "low_price": close, "close_price": close, "prev_price": close,
            "price_change": 0, "price_change_ratio_pct": 0.0,
            "volume": 100, "turnover": close * 100}


def last_user_text(body: dict) -> str:
    users = [m.get("content", "") for m in body.get("messages", []) if m.get("role") == "user"]
    return users[-1] if users else ""


def classify_turn(body: dict) -> str:
    """按提示词特征区分取数轮 / 章节轮 / 恢复轮（与 prompts.py 文案对齐）。"""
    messages = body.get("messages") or []
    system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
    if system.startswith("你是本机金融研究助手的数据收集阶段"):
        return "collect"
    text = last_user_text(body)
    if text.startswith("你上一条针对章节"):
        return "recovery"
    if "未通过 JSON 结构校验" in text[:80]:
        return "repair"
    if "被程序判定为内容不合格" in text[:100]:
        return "regen"
    return "section"


def section_id_of(body: dict) -> str:
    match = (re.search(r'章节 id 必须是 "([a-z_]+)"', last_user_text(body))
             or re.search(r"章节【([a-z_]+)】", last_user_text(body)))
    assert match, f"无法从模型请求中识别章节 id：{last_user_text(body)[:120]}"
    return match.group(1)


def material_ids(body: dict) -> tuple[list[str], list[str]]:
    """从材料文本提取证据 ID 与缺口 ID（缺口行以 "- <id>: 维度" 开头）。"""
    text = last_user_text(body)
    gap_ids = re.findall(r"- ([0-9a-f]{32}): 维度", text)
    all_ids = re.findall(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", text)
    evidence_ids = [item for item in dict.fromkeys(all_ids) if item not in gap_ids]
    return evidence_ids, gap_ids


def split3(text: str) -> list[str]:
    """把章节 JSON 拆成三段，模拟模型流式跨块输出。"""
    if len(text) < 3:
        return [text]
    a, b = len(text) // 3, len(text) * 2 // 3
    return [text[:a], text[a:b], text[b:]]


TOOL_ARG_PIECES = {
    "income": ("fetch_financials", ['{"state', 'ment":"income"}']),
    "balance": ("fetch_financials", ['{"stat', 'ement":"balance"}']),
    "cashflow": ("fetch_financials", ['{"stat', 'ement":"cashflow"}']),
    "valuation": ("fetch_valuation", ["{", "}"]),
    "snapshot": ("fetch_prices", ['{"kind":"sna', 'pshot"}']),
    "history": ("fetch_prices", ['{"kind":"hi', 'story"}']),
}


def tool_call_specs(names: list[str]) -> list[dict]:
    return [{"index": index, "id": f"call_{name}", "name": TOOL_ARG_PIECES[name][0],
             "argument_pieces": list(TOOL_ARG_PIECES[name][1])}
            for index, name in enumerate(names)]


def spec_frames(spec: dict) -> list[str]:
    """把响应规格展开成 OpenAI 兼容 SSE 帧（finish=None 表示缺结束标记）。"""
    frames: list[str] = []

    def emit(delta: dict, finish: str | None = None) -> None:
        chunk = {"id": "chatcmpl-scripted", "object": "chat.completion.chunk", "created": 1,
                 "model": "deepseek-v4-flash-vision-exp",
                 "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        frames.append("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n")

    for piece in spec.get("reasoning_pieces") or []:
        emit({"reasoning_content": piece})
    for call in spec.get("tool_calls") or []:
        emit({"tool_calls": [{"index": call["index"], "id": call["id"], "type": "function",
                              "function": {"name": call["name"], "arguments": ""}}]})
        for piece in call["argument_pieces"]:
            emit({"tool_calls": [{"index": call["index"], "function": {"arguments": piece}}]})
    for piece in spec.get("content_pieces") or []:
        emit({"content": piece})
    if spec.get("finish") is not None:
        emit({}, finish=spec["finish"])
    frames.append("data: [DONE]\n\n")
    return frames


def sse_stream_response(spec: dict) -> httpx.Response:
    async def stream():
        for frame in spec_frames(spec):
            yield frame.encode("utf-8")

    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=stream())


def build_section_object(section_id: str, evidence_ids: list[str], gap_ids: list[str]) -> dict:
    """脚本模型的章节输出：facts 引用真实证据/指标，risk 引用真实缺口。"""
    if section_id == "followups":
        return {"id": section_id, "title": TITLES[section_id], "status": "supported",
                "summary": "列出可检验的后续问题。", "claims": [], "counter_evidence": [],
                "follow_up_questions": ["下一期财报披露后，利润率趋势是否延续？",
                                        "估值数据恢复后，当前估值处于什么区间？"]}
    if section_id == "facts":
        claims = [{"kind": "fact", "text": "结构化财报与行情快照由程序指标呈现，未经模型改写。",
                   "evidence_refs": evidence_ids[:1], "metric_refs": ["net_margin"],
                   "gap_refs": []}]
        status = "supported" if evidence_ids else "conditional"
    elif section_id == "risk":
        claims = [{"kind": "inference", "text": "若负债结构未改善，财务风险需要持续跟踪。",
                   "evidence_refs": [], "metric_refs": [], "gap_refs": gap_ids[:1]}]
        status = "conditional"
    else:
        claims = [{"kind": "inference", "text": "现有材料不足以给出确定性结论，仅给条件性判断。",
                   "evidence_refs": [], "metric_refs": [], "gap_refs": []}]
        status = "conditional"
    return {"id": section_id, "title": TITLES[section_id], "status": status,
            "summary": "由脚本模型生成的测试章节。", "claims": claims,
            "counter_evidence": [], "follow_up_questions": ["补充哪些数据可以验证该判断？"]}


def default_plan(script: "ResearchScript"):
    def plan(kind: str, body: dict, index: int) -> dict:
        if kind == "collect":
            # 每个任务的第一轮取数（会话中还没有任何 tool 结果）才回工具调用。
            first_collect = not any(m.get("role") == "tool"
                                    for m in body.get("messages") or [])
            if first_collect and script.collect_calls:
                return {"reasoning_pieces": [script.reasoning_text],
                        "tool_calls": tool_call_specs(script.collect_calls),
                        "finish": "stop"}
            return {"content_pieces": ["收集完成：结构化数据已取齐或已记录缺口。"],
                    "finish": "stop"}
        if kind in {"section", "recovery", "regen", "repair"}:
            evidence_ids, gap_ids = material_ids(body)
            obj = build_section_object(section_id_of(body), evidence_ids, gap_ids)
            return {"content_pieces": split3(json.dumps(obj, ensure_ascii=False)),
                    "finish": "stop"}
        raise AssertionError(f"脚本未预期的模型请求类型：{kind}")
    return plan


class ResearchScript:
    """脚本化上游：同花顺数据端点 + 模型端点（官方 deepseek 地址），全离线。"""

    def __init__(self, *, all_down: bool = False) -> None:
        self.calls: list[tuple[str, dict, str]] = []
        self.model_bodies: list[dict] = []
        self.collect_calls = ["income", "balance", "cashflow", "valuation", "snapshot"]
        self.reasoning_text = REASONING_TEXT
        self.all_down = all_down
        self.plan = None  # 测试可覆盖 (kind, body, index) -> dict

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == MODEL_PATH:
            return self._model_turn(request)
        params = {key: value for key, value in request.url.params.items()}
        self.calls.append((request.url.path, params, request.headers.get("X-api-key", "")))
        return self._hexin(request.url.path)

    def _hexin(self, path: str) -> httpx.Response:
        if self.all_down and path != "/api/meta/tickers/search":
            return httpx.Response(200, json=envelope(None, 3001, "scripted down"))
        if path == "/api/meta/tickers/search":
            return httpx.Response(200, json=envelope({"timestamp": 1, "item": [search_item()]}))
        if path == "/api/a-share/prices/snapshot":
            return httpx.Response(200, json=envelope(
                {"timestamp": None, "item": [{"thscode": "600519.SH", "ticker": "600519",
                                              "last_price": 1299.52, "volume": 100}]}))
        if path == "/api/a-share/prices/historical":
            return httpx.Response(200, json=envelope(
                {"timestamp": None, "item": [bar(1756656000000, 100.0), bar(1756742400000, 120.0)],
                 "thscode": "600519.SH", "interval": "1d", "adjust": "forward"}))
        if path == "/api/a-share/financials/income-statements":
            return httpx.Response(200, json=envelope(
                {"timestamp": 1735574400000, "item": [income_row()]}))
        if path == "/api/a-share/financials/balance-sheets":
            return httpx.Response(200, json=envelope(None, 4001, "qps exceeded"))
        if path == "/api/a-share/financials/cash-flow-statements":
            return httpx.Response(200, json=envelope(None, 3002, "not ready"))
        if path == "/api/a-share/valuations/snapshot":
            return httpx.Response(200, json=envelope(None, 3001, "ticker not found"))
        return httpx.Response(404, json=envelope(None, 3001, "no route"))

    def _model_turn(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        index = len(self.model_bodies)
        self.model_bodies.append(body)
        kind = classify_turn(body)
        plan = self.plan or default_plan(self)
        spec = plan(kind, body, index)
        assert spec is not None, f"脚本未预期的模型请求：{kind}（第 {index} 轮）"
        return sse_stream_response(spec)


# ---------------------------------------------------------------- fixtures ---
@pytest.fixture
def make_research(tmp_path):
    """工厂返回上下文管理器：with 期间保持应用 lifespan 与后台任务存活。"""
    @contextmanager
    def make(**script_kwargs):
        script = ResearchScript(**script_kwargs)
        settings = Settings(project_root=tmp_path, encryption_key=Fernet.generate_key().decode())

        def build_client():
            return TestClient(create_app(settings, transport=httpx.MockTransport(script)),
                              base_url="http://127.0.0.1:8000")

        client = build_client()
        with client:
            token = client.get("/api/system/session",
                               headers={"X-FinSight-Client": "web"}).json()["token"]
            client.headers.update({"X-FinSight-Session": token, "Content-Type": "application/json"})
            created = client.post("/api/integrations/profiles", json={
                "provider": "hexin_finance", "name": "同花顺联调", "api_key": KEY_V1,
                "enabled": True})
            assert created.status_code == 201, created.text
            model = client.post("/api/models", json={
                "provider": "deepseek", "api_key": "sk-offline-research-key", "active": True})
            assert model.status_code == 201, model.text
            confirm(client)
            yield {"client": client, "script": script, "settings": settings,
                   "build_client": build_client, "model": model.json()}

    return make


@pytest.fixture
def research(make_research):
    with make_research() as harness:
        yield harness


def confirm(client):
    response = client.post("/api/data/resolve", json={"query": "600519"})
    assert response.status_code == 200, response.text
    assert response.json()["candidates"], "search should return at least one candidate"
    response = client.post("/api/data/resolve", json={"confirm_thscode": "600519.SH"})
    assert response.status_code == 200, response.text
    return response.json()["identity"]


def wait_for_status(client, task_id: str, statuses, timeout: float = 15.0) -> dict:
    if isinstance(statuses, str):
        statuses = {statuses}
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/research/tasks/{task_id}")
        assert response.status_code == 200, response.text
        last = response.json()["task"]
        if last["status"] in statuses:
            return last
        time.sleep(0.02)
    pytest.fail(f"任务状态未到达 {statuses}，当前为 {last and last['status']}")


def db_count(settings, table: str) -> int:
    db = sqlite3.connect(settings.db_path)
    try:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        db.close()


def run_task(harness, *, question="600519 盈利质量如何？", client_request_id, **extra):
    client = harness["client"]
    response = client.post("/api/research/tasks", json={
        "question": question, "thscode": "600519.SH",
        "client_request_id": client_request_id, **extra})
    assert response.status_code == 202, response.text
    created = response.json()["task"]
    return created, wait_for_status(client, created["id"], "completed")


# ------------------------------------------------------------------ tests ---
def test_missing_data_report_completes_and_keeps_program_gaps(research):
    """利润表可取、其余来源失败：任务 completed，程序缺口完整保留。"""
    client, script = research["client"], research["script"]
    created, task = run_task(research, client_request_id="flow-missing-data-1")
    assert created["status"] in {"queued", "running"}
    assert created["budget"]["max_llm_requests"] == 24
    assert task["status"] == "completed" and task["finished_at"]
    # 授权快照随任务保存（公开快照不含授权细节，从持久化行校验）
    db = sqlite3.connect(research["settings"].db_path)
    try:
        row = db.execute("SELECT authorization_json FROM research_tasks WHERE id = ?",
                         (task["id"],)).fetchone()
    finally:
        db.close()
    assert json.loads(row[0]) == {"sources": ["hexin_structured"], "web_search": False}
    # 用量：1 取数轮（含推理）+ 1 收集收尾 + 5 个章节
    assert task["usage"]["llm_requests"] == 7
    assert task["usage"]["fetches"] == 7  # income 1 + balance 限流重试 3 + 其余 3
    assert task["usage"]["tool_rounds"] == 1
    assert task["usage"]["reasoning_chars"] == len(script.reasoning_text)

    report_id = task["report_id"]
    assert report_id
    body = client.get(f"/api/research/reports/{report_id}")
    assert body.status_code == 200, body.text
    report = body.json()["report"]
    assert body.json()["raw_sections"] == []
    assert report["coverage"] == "partial"
    assert report["output_integrity"] == "validated"
    assert report["model_snapshot"]["mode"] == "tools"
    assert report["model_snapshot"]["provider"] == "deepseek"
    assert len(report["evidence_ids"]) == 2
    net_margin = next(m for m in report["metrics"] if m["metric"] == "net_margin")
    assert net_margin["value"] is not None
    # gaps 不被模型删除：模型章节未逐一提及这些缺口，报告仍完整列出程序缺口
    gap_reasons = {gap["reason"] for gap in report["data_gaps"]}
    assert {"rate_limited", "not_published", "unsupported"} <= gap_reasons
    sections = {section["id"]: section for section in report["sections"]}
    assert sections["facts"]["status"] == "supported"
    evidence_refs = sections["facts"]["claims"][0]["evidence_refs"]
    assert evidence_refs and set(evidence_refs) <= set(report["evidence_ids"])
    referenced_gap_ids = sections["risk"]["claims"][0]["gap_refs"]
    assert referenced_gap_ids
    assert set(referenced_gap_ids) <= {gap["id"] for gap in report["data_gaps"]}
    assert sections["followups"]["status"] == "supported"
    assert sections["followups"]["follow_up_questions"]
    # REST 读取的进度事件只包含公开进度，reasoning 只以计数出现
    detail = client.get(f"/api/research/tasks/{task['id']}").json()
    types = {event["type"] for event in detail["events"]}
    assert {"task.created", "phase", "section", "report", "usage", "task.completed"} <= types
    dumped = json.dumps(detail, ensure_ascii=False)
    assert "reasoning_content" not in dumped and script.reasoning_text not in dumped
    # 数据来源调用：income 1 次；balance 限流后在 02 客户端退避与任务层重试下多次尝试
    paths = [call[0] for call in script.calls]
    assert paths.count("/api/a-share/financials/income-statements") == 1
    assert paths.count("/api/a-share/financials/balance-sheets") >= 3


def test_all_sources_unavailable_still_delivers_conditional_framework(make_research):
    """所有金融源不可用：报告明确没有业务事实，仍给出条件研究框架。"""
    with make_research(all_down=True) as harness:
        client = harness["client"]
        created, task = run_task(harness, client_request_id="flow-all-down-1")
        assert created["status"] in {"queued", "running"}
        assert task["status"] == "completed"
        report = client.get(f"/api/research/reports/{task['report_id']}").json()["report"]
        assert report["coverage"] == "no_business_evidence"
        assert report["output_integrity"] == "validated"
        assert report["data_gaps"], "程序缺口必须保留"
        assert all(metric["value"] is None for metric in report["metrics"])
        sections = {section["id"]: section for section in report["sections"]}
        assert sections["facts"]["status"] == "conditional"
        assert "条件性判断" in json.dumps(sections, ensure_ascii=False)
        assert sections["followups"]["follow_up_questions"]
        # 模型没有拒绝分析：每个模板章节都有产出
        assert set(sections) == {item["id"] for item in SECTION_TEMPLATES}


def test_supplement_saved_without_triggering_any_call(research):
    """补充资料只保存：不触发模型调用、不创建任务、不生成报告。"""
    client, script, settings = research["client"], research["script"], research["settings"]
    _, task = run_task(research, client_request_id="flow-supplement-1")
    report_id = task["report_id"]
    bodies_before = len(script.model_bodies)
    tasks_before = db_count(settings, "research_tasks")
    reports_before = db_count(settings, "research_reports")

    response = client.post(f"/api/research/reports/{report_id}/supplements", json={
        "kind": "user_provided",
        "content": "管理层电话会记录：一季度营收指引保持两位数增长。",
        "fields": {"period": "2025Q1", "unit": "CNY", "not_allowed": "应被剥离"}})
    assert response.status_code == 201, response.text
    supplement = response.json()["supplement"]
    assert supplement["report_id"] == report_id
    assert supplement["task_id"] == task["id"]
    assert supplement["kind"] == "user_provided"
    assert supplement["referenced_by_report_id"] is None
    assert supplement["fields"] == {"period": "2025Q1", "unit": "CNY"}

    response = client.post(f"/api/research/reports/{report_id}/supplements", json={
        "kind": "user_assumption", "content": "假设当前估值已反映市场预期。"})
    assert response.status_code == 201, response.text
    response = client.post(f"/api/research/reports/{report_id}/supplements", json={
        "kind": "user_provided", "content": "字" * 8001})
    assert response.status_code == 422
    response = client.post(f"/api/research/reports/{'0' * 32}/supplements",
                           json={"kind": "user_provided", "content": "孤儿补充"})
    assert response.status_code == 404

    assert len(script.model_bodies) == bodies_before, "保存补充不得触发任何模型调用"
    assert db_count(settings, "research_tasks") == tasks_before
    assert db_count(settings, "research_reports") == reports_before
    assert client.get(f"/api/research/tasks/{task['id']}").json()["task"]["status"] == "completed"


def test_reanalyze_creates_child_version_and_keeps_old_readable(research):
    """主动再分析：新报告引用 parent_report_id 与被选补充，旧报告原样可读。"""
    client, script, settings = research["client"], research["script"], research["settings"]
    _, first_task = run_task(research, client_request_id="flow-reanalyze-1")
    first_report_id = first_task["report_id"]
    before = client.get(f"/api/research/reports/{first_report_id}").json()
    supplement = client.post(f"/api/research/reports/{first_report_id}/supplements", json={
        "kind": "user_provided", "content": "管理层电话会记录：一季度营收指引保持两位数增长。",
        "fields": {"period": "2025Q1"}}).json()["supplement"]
    bodies_after_first = len(script.model_bodies)

    response = client.post(f"/api/research/reports/{first_report_id}/reanalyze", json={
        "supplement_ids": [supplement["id"]], "client_request_id": "flow-reanalyze-child"})
    assert response.status_code == 202, response.text
    child_task = response.json()["task"]
    child = wait_for_status(client, child_task["id"], "completed")

    child_report = client.get(f"/api/research/reports/{child['report_id']}").json()["report"]
    assert child_report["parent_report_id"] == first_report_id
    assert child_report["question"] == before["report"]["question"]
    assert child_report["scope"] == before["report"]["scope"]
    assert child["usage"]["llm_requests"] == 7
    # 本次再分析的材料里包含被选中的用户补充（证明补充确实进入新材料）
    child_bodies = script.model_bodies[bodies_after_first:]
    joined = json.dumps(child_bodies, ensure_ascii=False)
    assert "管理层电话会记录" in joined and "[user_provided]" in joined
    # 旧报告原样可读（不可变）
    after = client.get(f"/api/research/reports/{first_report_id}").json()
    assert after["report"] == before["report"]
    assert after["raw_sections"] == before["raw_sections"]
    # 补充资料被显式引用：referenced_by_report_id 指向新报告
    db = sqlite3.connect(settings.db_path)
    try:
        row = db.execute("SELECT referenced_by_report_id FROM research_supplements WHERE id = ?",
                         (supplement["id"],)).fetchone()
    finally:
        db.close()
    assert row and row[0] == child["report_id"]


def test_reanalyze_replay_idempotent_and_conflict_rejected(research):
    """再分析幂等：同 client_request_id 同内容返回原任务不二次执行；不同内容 409。"""
    client, script = research["client"], research["script"]
    _, first_task = run_task(research, client_request_id="flow-replay-1")
    first_report_id = first_task["report_id"]
    supplement = client.post(f"/api/research/reports/{first_report_id}/supplements", json={
        "kind": "user_provided", "content": "再分析幂等测试补充。"}).json()["supplement"]
    payload = {"supplement_ids": [supplement["id"]], "client_request_id": "ra-idem-0001"}

    response = client.post(f"/api/research/reports/{first_report_id}/reanalyze", json=payload)
    assert response.status_code == 202, response.text
    task = wait_for_status(client, response.json()["task"]["id"], "completed")
    bodies_after_first = len(script.model_bodies)

    replay = client.post(f"/api/research/reports/{first_report_id}/reanalyze", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["task"]["id"] == task["id"]
    assert replay.json()["task"]["report_id"] == task["report_id"]
    assert len(script.model_bodies) == bodies_after_first, "重放不得再次执行"

    conflict = client.post(f"/api/research/reports/{first_report_id}/reanalyze", json={
        **payload, "supplement_ids": []})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["category"] == "request_conflict"

    unknown = client.post(f"/api/research/reports/{first_report_id}/reanalyze", json={
        "supplement_ids": ["f" * 32], "client_request_id": "ra-unknown-01"})
    assert unknown.status_code == 422

    # 换一个 client_request_id：允许再次发起（新任务），不影响旧任务
    again = client.post(f"/api/research/reports/{first_report_id}/reanalyze", json={
        "supplement_ids": [supplement["id"]], "client_request_id": "ra-again-0002"})
    assert again.status_code == 202
    assert again.json()["task"]["id"] != task["id"]
    wait_for_status(client, again.json()["task"]["id"], "completed")
