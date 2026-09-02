"""Research event stream integration tests (plan 03 section 4, item 5).

跨块模型流合并、多 tool index、call id 配对、长思考只以字符计数公开、
finish=length/缺结束标记 → incomplete、主动取消、重复 POST 幂等、
刷新后按 seq 续读、进程重启 running→interrupted 不自动再付费。
全部离线：同花顺响应与模型 SSE 流均由 MockTransport 脚本提供。
"""

from __future__ import annotations

import asyncio
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
from backend.app.research.repository import ResearchRepository
from backend.app.research.tasks import TaskStore
from backend.app.settings import Settings
from backend.app.storage import Storage

KEY_V1 = "sk-fuyao-unit-key-one"
MODEL_PATH = "/chat/completions"
TITLES = {item["id"]: item["title"] for item in SECTION_TEMPLATES}


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
    if len(text) < 3:
        return [text]
    a, b = len(text) // 3, len(text) * 2 // 3
    return [text[:a], text[a:b], text[b:]]


TOOL_ARG_PIECES = {
    "income": ("fetch_financials", ['{"state', 'ment":"inc', 'ome"}']),
    "snapshot": ("fetch_prices", ['{"kind":"sna', 'pshot"}']),
    "history": ("fetch_prices", ['{"kind":"hi', 'story"}']),
    "broken": ("fetch_prices", ['{"kind":', 'broken']),
}


def tool_call_specs(calls: list[tuple[str, str]]) -> list[dict]:
    return [{"index": index, "id": call_id, "name": TOOL_ARG_PIECES[name][0],
             "argument_pieces": list(TOOL_ARG_PIECES[name][1])}
            for index, (name, call_id) in enumerate(calls)]


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


def sse_stream_response(script: "ResearchScript", spec: dict) -> httpx.Response:
    frames = spec_frames(spec)
    delay = float(spec.get("delay") or 0.0)

    async def stream():
        script.streams_open += 1
        completed = False
        try:
            for frame in frames:
                yield frame.encode("utf-8")
                if delay:
                    await asyncio.sleep(delay)
            completed = True
        except BaseException:  # 取消/断开：在途流被关闭
            script.streams_interrupted += 1
            raise
        finally:
            script.streams_open -= 1
            if completed:
                script.streams_completed += 1

    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=stream())


def build_section_object(section_id: str, evidence_ids: list[str], gap_ids: list[str]) -> dict:
    """脚本模型的章节输出：facts 引用真实证据/指标，risk 引用真实缺口。"""
    if section_id == "followups":
        return {"id": section_id, "title": TITLES[section_id], "status": "supported",
                "summary": "列出可检验的后续问题。", "claims": [], "counter_evidence": [],
                "follow_up_questions": ["下一期财报披露后，利润率趋势是否延续？"]}
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
            first_collect = not any(m.get("role") == "tool" for m in body.get("messages") or [])
            if first_collect:
                return {"reasoning_pieces": ["先", "想", "取", "数", "顺", "序。"],
                        "tool_calls": tool_call_specs(
                            [("income", "call_a"), ("snapshot", "call_b"), ("broken", "call_c")]),
                        "finish": "stop"}
            return {"content_pieces": ["收集", "完成。"], "finish": "stop"}
        if kind in {"section", "recovery", "regen", "repair"}:
            evidence_ids, gap_ids = material_ids(body)
            obj = build_section_object(section_id_of(body), evidence_ids, gap_ids)
            return {"content_pieces": split3(json.dumps(obj, ensure_ascii=False)),
                    "finish": "stop"}
        raise AssertionError(f"脚本未预期的模型请求类型：{kind}")
    return plan


def slow_plan(script: "ResearchScript"):
    """取数轮返回一条很慢的长流，供取消测试在途打断。"""
    def plan(kind: str, body: dict, index: int) -> dict:
        if kind == "collect":
            first_collect = not any(m.get("role") == "tool" for m in body.get("messages") or [])
            if first_collect:
                return {"content_pieces": ["进" * 40] * 60, "finish": "stop", "delay": 0.01}
            return {"content_pieces": ["收集完成。"], "finish": "stop"}
        if kind in {"section", "recovery", "regen", "repair"}:
            evidence_ids, gap_ids = material_ids(body)
            obj = build_section_object(section_id_of(body), evidence_ids, gap_ids)
            return {"content_pieces": split3(json.dumps(obj, ensure_ascii=False)),
                    "finish": "stop"}
        raise AssertionError(f"脚本未预期的模型请求类型：{kind}")
    return plan


def truncation_plan(script: "ResearchScript"):
    """facts 首次输出被 length 截断；恢复请求完整返回但缺结束标记。"""
    truncated = '{"id":"facts","title":"事实概览","status":"supported","summ'
    def plan(kind: str, body: dict, index: int) -> dict:
        if kind == "collect":
            return {"content_pieces": ["收集完成。"], "finish": "stop"}
        if kind == "section":
            evidence_ids, gap_ids = material_ids(body)
            if section_id_of(body) == "facts":
                return {"content_pieces": [truncated], "finish": "length"}
            obj = build_section_object(section_id_of(body), evidence_ids, gap_ids)
            return {"content_pieces": split3(json.dumps(obj, ensure_ascii=False)),
                    "finish": "stop"}
        if kind == "recovery":
            # 恢复输出是合法 JSON，但没有结束标记：仍不得判完整。
            evidence_ids, gap_ids = material_ids(body)
            obj = build_section_object("facts", evidence_ids, gap_ids)
            return {"content_pieces": [json.dumps(obj, ensure_ascii=False)], "finish": None}
        raise AssertionError(f"脚本未预期的模型请求类型：{kind}")
    return plan


class ResearchScript:
    """脚本化上游：同花顺数据端点 + 模型端点（官方 deepseek 地址），全离线。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str]] = []
        self.model_bodies: list[dict] = []
        self.streams_open = 0
        self.streams_completed = 0
        self.streams_interrupted = 0
        self.plan = None  # 测试可覆盖 (kind, body, index) -> dict

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == MODEL_PATH:
            return self._model_turn(request)
        params = {key: value for key, value in request.url.params.items()}
        self.calls.append((request.url.path, params, request.headers.get("X-api-key", "")))
        return self._hexin(request.url.path)

    def _hexin(self, path: str) -> httpx.Response:
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
        return sse_stream_response(self, spec)


# ---------------------------------------------------------------- fixtures ---
@pytest.fixture
def make_research(tmp_path):
    """工厂返回上下文管理器；settings 复用同一实例，便于重启场景共享数据库。"""
    settings = Settings(project_root=tmp_path, encryption_key=Fernet.generate_key().decode())

    @contextmanager
    def make():
        script = ResearchScript()

        def build_client():
            return TestClient(create_app(settings, transport=httpx.MockTransport(script)),
                              base_url="http://127.0.0.1:8000")

        client = build_client()
        with client:
            token = client.get("/api/system/session",
                               headers={"X-FinSight-Client": "web"}).json()["token"]
            client.headers.update({"X-FinSight-Session": token, "Content-Type": "application/json"})
            # 重启场景共用同一数据库：配置已存在时直接复用，不重复创建。
            if not client.get("/api/integrations/profiles").json():
                created = client.post("/api/integrations/profiles", json={
                    "provider": "hexin_finance", "name": "同花顺联调", "api_key": KEY_V1,
                    "enabled": True})
                assert created.status_code == 201, created.text
            if not any(item["active"] for item in client.get("/api/models").json()):
                model = client.post("/api/models", json={
                    "provider": "deepseek", "api_key": "sk-offline-research-key",
                    "active": True})
                assert model.status_code == 201, model.text
            confirm(client)
            yield {"client": client, "script": script, "settings": settings,
                   "build_client": build_client}

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


def wait_until(predicate, timeout: float = 10.0, message: str = "条件未满足") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    pytest.fail(message)


def create_task(client, *, question="600519 盈利质量如何？", client_request_id=None, **extra):
    payload = {"question": question, "thscode": "600519.SH", **extra}
    if client_request_id:
        payload["client_request_id"] = client_request_id
    response = client.post("/api/research/tasks", json=payload)
    assert response.status_code == 202, response.text
    return response.json()["task"]


def read_sse(client, url: str) -> list[dict]:
    """按字节块读取 SSE 流并手工分帧：帧可以跨传输块到达。"""
    frames: list[dict] = []
    with client.stream("GET", url) as response:
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "no-store" in response.headers.get("cache-control", "")
        buffer = b""
        for chunk in response.iter_bytes():
            buffer += chunk
            while b"\n\n" in buffer:
                raw, buffer = buffer.split(b"\n\n", 1)
                frames.append(parse_frame(raw.decode("utf-8")))
    assert not buffer.strip(), "SSE 流结束时不应残留不完整帧"
    return [frame for frame in frames if frame["event"] is not None or frame["data"] is not None]


def parse_frame(raw: str) -> dict:
    event_type = None
    data_parts: list[str] = []
    for line in raw.splitlines():
        if line.startswith("event: "):
            event_type = line[len("event: "):]
        elif line.startswith("data: "):
            data_parts.append(line[len("data: "):])
        # ": ping" 心跳注释行忽略
    return {"event": event_type,
            "data": json.loads("".join(data_parts)) if data_parts else None}


def event_types(frames: list[dict]) -> list[str]:
    return [frame["data"]["type"] for frame in frames
            if frame["data"] and "type" in frame["data"]]


def frame_seqs(frames: list[dict]) -> list[int]:
    return [frame["data"]["seq"] for frame in frames
            if frame["data"] and "seq" in frame["data"]]


# ------------------------------------------------------------------ tests ---
def test_stream_lifecycle_multi_tool_index_call_pairing_and_reasoning(research):
    """SSE 全程：跨块合并、多 tool index、call id 配对、reasoning 只见字符数。"""
    client, script = research["client"], research["script"]
    created = create_task(client, client_request_id="stream-live-0001")
    frames = read_sse(client, f"/api/research/tasks/{created['id']}/events")

    # 首帧快照、末帧 end，业务 seq 单调递增
    assert frames[0]["event"] == "snapshot"
    assert frames[0]["data"]["question"] == created["question"]
    assert frames[-1]["event"] == "end"
    assert frames[-1]["data"]["status"] == "completed"
    seqs = frame_seqs(frames)
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs) and seqs[0] >= 1
    types = set(event_types(frames))
    assert {"snapshot", "task.created", "phase", "reasoning", "section",
            "report", "usage", "task.completed", "end"} <= types

    # 长思考只以字符计数出现：事件只有 chars，且全文无思考文本
    reasoning_frames = [frame for frame in frames
                        if frame["data"] and frame["data"].get("type") == "reasoning"]
    assert reasoning_frames
    for frame in reasoning_frames:
        assert set(frame["data"]) == {"type", "chars", "seq"}
        assert frame["data"]["chars"] > 0
    # 事件按模型请求轮聚合：单个取数轮的思考字符合并为一个事件
    assert sum(frame["data"]["chars"] for frame in reasoning_frames) == len("先想取数顺序。")
    raw_dump = json.dumps(frames, ensure_ascii=False)
    assert "reasoning_content" not in raw_dump and "先想取数" not in raw_dump

    task = wait_for_status(client, created["id"], "completed")
    assert task["usage"]["reasoning_chars"] == \
        sum(frame["data"]["chars"] for frame in reasoning_frames)

    # 章节事件覆盖全部模板章节
    section_frames = [frame for frame in frames
                      if frame["data"] and frame["data"].get("type") == "section"]
    assert {frame["data"]["id"] for frame in section_frames} == \
        {item["id"] for item in SECTION_TEMPLATES}
    facts_event = next(frame for frame in section_frames if frame["data"]["id"] == "facts")
    assert facts_event["data"]["status"] == "supported"

    # 模型侧：第二个请求的会话里 assistant 携带多个 tool index，tool 消息按 call id 配对
    assert len(script.model_bodies) >= 2
    second_messages = script.model_bodies[1]["messages"]
    assistant = next(m for m in second_messages if m.get("role") == "assistant")
    assert [call["id"] for call in assistant["tool_calls"]] == ["call_a", "call_b", "call_c"]
    assert [call["function"]["name"] for call in assistant["tool_calls"]] == \
        ["fetch_financials", "fetch_prices", "fetch_prices"]
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"statement":"income"}'
    tool_messages = [m for m in second_messages if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["call_a", "call_b", "call_c"]
    outcomes = [json.loads(m["content"]) for m in tool_messages]
    assert [o["call_id"] for o in outcomes] == ["call_a", "call_b", "call_c"]
    assert outcomes[0]["status"] == "ok" and outcomes[0]["result"]["evidence_ids"]
    assert outcomes[2]["status"] == "error"
    assert outcomes[2]["error"]["reason"] == "invalid_arguments"
    # 上游数据端点被真实执行（income + snapshot），坏参数不触发取数
    paths = [call[0] for call in script.calls]
    assert paths.count("/api/a-share/financials/income-statements") == 1
    assert paths.count("/api/a-share/prices/snapshot") == 1
    # 报告可读且引用了真实证据
    report = client.get(f"/api/research/reports/{task['report_id']}").json()["report"]
    assert report["output_integrity"] == "validated"
    usage_frame = next(frame for frame in frames if frame["data"].get("type") == "usage")
    assert usage_frame["data"]["llm_requests"] == 7
    assert usage_frame["data"]["fetches"] == 2
    assert usage_frame["data"]["tool_rounds"] == 1


def test_truncated_and_unfinished_sections_marked_incomplete(research):
    """finish=length 与缺结束标记都算 incomplete；恢复请求计入预算且只一次。"""
    client, script = research["client"], research["script"]
    script.plan = truncation_plan(script)
    created = create_task(client, client_request_id="stream-trunc-0001")
    task = wait_for_status(client, created["id"], "completed")
    assert task["status"] == "completed"
    # 1 收集 + facts 截断 1 次 + 恢复 1 次 + 其余 4 章节
    assert task["usage"]["llm_requests"] == 7

    body = client.get(f"/api/research/reports/{task['report_id']}")
    report = body.json()["report"]
    raw_sections = body.json()["raw_sections"]
    assert report["output_integrity"] == "truncated"
    assert any("截断" in item for item in report["limitations"])
    assert len(raw_sections) == 1
    raw = raw_sections[0]
    assert raw["id"] == "facts" and raw["reason"] == "truncated_after_recovery"
    # 恢复输出本身是合法 JSON，但缺结束标记 → 仍不判完整、整体作废保留原文
    recovered = json.loads(raw["text"])
    assert recovered["id"] == "facts"
    sections = {section["id"]: section for section in report["sections"]}
    assert sections["facts"]["status"] == "unavailable"
    assert "截断" in sections["facts"]["summary"]
    assert sections["financials"]["status"] == "conditional"


def test_duplicate_post_reuses_task_without_second_run(research):
    """同 client_request_id 同内容返回原任务不二次执行；不同内容 409。"""
    client, script = research["client"], research["script"]
    payload = {"question": "600519 现在值得研究吗？", "thscode": "600519.SH",
               "client_request_id": "dup-idem-0001"}
    first = client.post("/api/research/tasks", json=payload)
    assert first.status_code == 202, first.text
    task_id = first.json()["task"]["id"]
    wait_for_status(client, task_id, "completed")
    bodies = len(script.model_bodies)

    replay = client.post("/api/research/tasks", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["task"]["id"] == task_id
    assert len(script.model_bodies) == bodies, "重放不得再次执行"

    conflict = client.post("/api/research/tasks", json={**payload, "question": "换个问题？"})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["category"] == "request_conflict"

    # 无 client_request_id：同一内容两次提交是两个任务
    a = create_task(client, question="600519 概况")
    b = create_task(client, question="600519 概况")
    assert a["id"] != b["id"]
    wait_for_status(client, a["id"], "completed")
    wait_for_status(client, b["id"], "completed")


def test_cancel_running_and_queued_tasks(research):
    """取消运行中任务关闭在途模型流；取消排队任务不产生任何执行痕迹。"""
    client, script, settings = research["client"], research["script"], research["settings"]
    script.plan = slow_plan(script)
    running = create_task(client, question="长任务A", client_request_id="cancel-a-0001")
    wait_until(lambda: script.streams_open >= 1, message="慢流未开始")
    queued = create_task(client, question="排队任务B")
    assert client.get(f"/api/research/tasks/{queued['id']}").json()["task"]["status"] == "queued"

    # 取消排队任务：直接取消，不进入运行、不触发任何取数或模型调用
    response = client.post(f"/api/research/tasks/{queued['id']}/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["task"]["status"] == "cancelled"
    queued_events = client.get(f"/api/research/tasks/{queued['id']}").json()["events"]
    assert {event["type"] for event in queued_events} == {"task.created", "task.cancelled"}

    # 取消运行中任务：在途流被关闭，任务 cancelled，无报告
    response = client.post(f"/api/research/tasks/{running['id']}/cancel")
    assert response.status_code == 200, response.text
    body = response.json()["task"]
    assert body["status"] == "cancelled" and body["finished_at"]
    assert script.streams_interrupted >= 1 and script.streams_completed == 0
    assert script.streams_open == 0
    again = client.post(f"/api/research/tasks/{running['id']}/cancel")
    assert again.status_code == 200 and again.json()["task"]["status"] == "cancelled"
    events = client.get(f"/api/research/tasks/{running['id']}").json()["events"]
    assert "task.cancelled" in {event["type"] for event in events}
    db = sqlite3.connect(settings.db_path)
    try:
        reports = db.execute("SELECT COUNT(*) FROM research_reports WHERE task_id = ?",
                             (running["id"],)).fetchone()[0]
    finally:
        db.close()
    assert reports == 0


def test_event_stream_resume_with_after_seq(research):
    """刷新续读：after={seq} 只补发更晚事件；快照始终先行，旧事件不重放。"""
    client, script = research["client"], research["script"]
    script.plan = default_plan(script)
    created = create_task(client, client_request_id="stream-resume-01")
    wait_for_status(client, created["id"], "completed")
    events_url = f"/api/research/tasks/{created['id']}/events"

    full = read_sse(client, events_url)
    seqs = frame_seqs(full)
    assert len(seqs) >= 5
    cut = seqs[len(seqs) // 2]

    resumed = read_sse(client, f"{events_url}?after={cut}")
    assert resumed[0]["event"] == "snapshot"
    resumed_seqs = frame_seqs(resumed)
    assert resumed_seqs == [seq for seq in seqs if seq > cut]
    assert resumed[-1]["event"] == "end"

    tail = read_sse(client, f"{events_url}?after={seqs[-1]}")
    assert [frame["event"] for frame in tail] == ["snapshot", "end"]


def test_restart_sweeps_running_and_queued_to_interrupted(make_research):
    """进程重启：running/queued 收尾为 interrupted，不自动再次付费。"""
    with make_research() as first:
        client, script, settings = first["client"], first["script"], first["settings"]
        created = create_task(client, client_request_id="restart-done-001")
        completed = wait_for_status(client, created["id"], "completed")
        report_id = completed["report_id"]
        # 直接在库中构造"进程崩溃"遗留：running 与 queued 各一条
        storage = Storage(settings.db_path)
        storage.initialize()
        store = TaskStore(ResearchRepository(storage))
        running = store.create(question="重启前正在运行的任务", scope={"thscode": "600519.SH"},
                               client_request_id="crash-running-1")
        store.mark_running(running.id)
        store.event(running.id, "phase",
                    {"type": "phase", "phase": "collect", "detail": "重启前的最后进度。"})
        queued = store.create(question="重启前排队中的任务", client_request_id="crash-queued-01")
    # with 退出模拟进程退出；同一数据库重新启动应用
    with make_research() as second:
        client2, script2 = second["client"], second["script"]
        db = sqlite3.connect(second["settings"].db_path)
        try:
            total_tasks = db.execute("SELECT COUNT(*) FROM research_tasks").fetchone()[0]
        finally:
            db.close()
        assert total_tasks == 3, "重启不得自动创建新任务"
        for task_id in (running.id, queued.id):
            task = client2.get(f"/api/research/tasks/{task_id}").json()["task"]
            assert task["status"] == "interrupted"
            assert task["finished_at"]
        events = client2.get(f"/api/research/tasks/{running.id}").json()["events"]
        types = [event["type"] for event in events]
        assert "phase" in types, "既有公开事件保持可读"
        assert "task.interrupted" in types
        assert len(script2.model_bodies) == 0, "重启后不得自动发起任何模型请求"
        assert client2.get(f"/api/research/reports/{report_id}").status_code == 200
