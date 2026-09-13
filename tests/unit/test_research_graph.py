"""Research graph tests (plan 03 section 4, item 3).

覆盖：无财务、有价格、全无数据、工具不可用、工具参数失败、预算耗尽均走正确
分支；一次格式修复/截断恢复/空答重生成后不能再自动循环；storage v6 迁移。
全部离线：模型为脚本假模型，取数为本地脚本结果，不访问网络、不使用真实密钥。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from types import SimpleNamespace

from backend.app.agent.output_models import (
    SECTION_TEMPLATES,
    Coverage,
    OutputIntegrity,
    ResearchReport,
    SectionStatus,
)
from backend.app.agent.research_graph import MODE_TEMPLATE, MODE_TOOLS, ResearchGraph, TurnResult
from backend.app.errors import AppError
from backend.app.market_data.identity import SecurityIdentity
from backend.app.research.evidence_models import (
    DataGap,
    DataResult,
    RequestedScope,
    ResultStatus,
)
from backend.app.research.models import RawSectionText, SupplementKind, TaskBudget, TaskStatus
from backend.app.research.repository import ResearchRepository
from backend.app.research.reports import ReportStore
from backend.app.research.tasks import TaskStore
from backend.app.storage import CURRENT_SCHEMA_VERSION, Storage

SCOPE = {"thscode": "600519.SH"}
TITLES = {item["id"]: item["title"] for item in SECTION_TEMPLATES}


# ---------------------------------------------------------------- helpers ---
def field_entry(value, unit="CNY", kind="amount"):
    return {"value": value, "raw_value": None, "unit": unit, "kind": kind, "source_field": "x"}


def statement_records(statement):
    if statement == "income":
        fields = {"operating_income": field_entry("174144000000"),
                  "net_profit": field_entry("93000000000"),
                  "parent_holder_net_profit": field_entry("86000000000")}
    elif statement == "balance":
        fields = {"assets_total": field_entry("300000000000"),
                  "total_debt": field_entry("120000000000")}
    else:
        fields = {"act_cash_flow_net": field_entry("88000000000")}
    return [{"thscode": "600519.SH", "period_key": "2024-FY", "period_end": None,
             "disclosed_at": None, "basis": "annual_report", "currency": "CNY",
             "fields": fields}]


def bar(date, close):
    return {"thscode": "600519.SH", "period_key": date, "period_end": None,
            "disclosed_at": None, "basis": "daily_bar", "currency": "CNY",
            "fields": {"close_price": field_entry(close, unit="CNY", kind="price")}}


def unavailable(capability, reason):
    return DataResult(status=ResultStatus.UNAVAILABLE, records=[], evidence_ids=[],
                      gaps=[DataGap(id=uuid.uuid4().hex, dimension=capability, fields=[],
                                    reason=reason, impact="测试缺口。", remedies=[])],
                      requested_scope=RequestedScope(capability=capability),
                      returned_scope={"count": 0})


class FakeMarket:
    """脚本化数据服务：只实现图用到的窄接口；不触网。"""

    def __init__(self, *, identity=True, statements="available", prices="available",
                 snapshot="available", valuations="unavailable"):
        self._identity = identity
        self._statements = statements
        self._prices = prices
        self._snapshot = snapshot
        self._valuations = valuations
        self.calls = {"statement": 0, "history": 0, "snapshot": 0, "valuation": 0}
        self._evidence = {}

    def require_identity(self, thscode):
        if not self._identity:
            raise AppError("not_found", "请先检索并确认标的。", 404)
        return SecurityIdentity(thscode=thscode, ticker=thscode.split(".")[0], name="测试公司",
                                exchange="SH", asset_type="a-share", currency="CNY",
                                confirmed_source="user_selected")

    def _store(self, result):
        for evidence_id in result.evidence_ids:
            meta = SimpleNamespace(capability=result.requested_scope.capability,
                                   basis="annual_report", source_time=None,
                                   record_count=len(result.records))
            self._evidence[evidence_id] = (meta, result.records, [])
        return result

    async def fetch_statement(self, statement, thscode, period="annual", limit=4, **kw):
        self.calls["statement"] += 1
        if self._statements == "available":
            return self._store(DataResult(
                status=ResultStatus.AVAILABLE, records=statement_records(statement),
                evidence_ids=[uuid.uuid4().hex], gaps=[],
                requested_scope=RequestedScope(capability="financial_statements"),
                returned_scope={"count": 1}))
        return unavailable("financial_statements", self._statements)

    async def fetch_price_history(self, thscode, *, start_ms, end_ms, **kw):
        self.calls["history"] += 1
        if self._prices == "available":
            return self._store(DataResult(
                status=ResultStatus.AVAILABLE,
                records=[bar("2025-06-30", "100"), bar("2025-07-01", "120")],
                evidence_ids=[uuid.uuid4().hex], gaps=[],
                requested_scope=RequestedScope(capability="price_history"),
                returned_scope={"count": 2}))
        return unavailable("price_history", self._prices)

    async def fetch_price_snapshot(self, thscodes, **kw):
        self.calls["snapshot"] += 1
        if self._snapshot == "available":
            record = dict(bar("2025-07-01", "120"))
            record["fields"] = {"last_price": field_entry("120", unit="CNY", kind="price")}
            return self._store(DataResult(
                status=ResultStatus.AVAILABLE, records=[record],
                evidence_ids=[uuid.uuid4().hex], gaps=[],
                requested_scope=RequestedScope(capability="price_snapshot"),
                returned_scope={"count": 1}))
        return unavailable("price_snapshot", self._snapshot)

    async def fetch_valuations(self, thscodes, **kw):
        self.calls["valuation"] += 1
        return unavailable("valuation_snapshot", self._valuations)

    def evidence(self, evidence_id):
        return self._evidence.get(evidence_id)


class FakeLLM:
    """脚本化模型：按顺序弹出 TurnResult；记录每次收到的消息。"""

    def __init__(self, script, mode=MODE_TOOLS):
        self.script = list(script)
        self.mode = mode
        self.calls = []

    def snapshot(self):
        from backend.app.agent.output_models import ModelSnapshot
        return ModelSnapshot(provider="deepseek", model="fake-model", output_label="64K",
                             mode=self.mode, json_mode=False)

    async def turn(self, messages, *, tools=None):
        self.calls.append({"messages": json.loads(json.dumps(messages)), "tools": tools})
        if not self.script:
            raise AssertionError("脚本耗尽：图发出了未预期的模型请求")
        step = self.script.pop(0)
        if isinstance(step, dict):  # 裸工具调用脚本 → 带调用的模型回合
            step = TurnResult(content="", finish_reason="tool_calls", tool_calls=[step])
        return step


def section_turn(section_id, *, status="conditional", claims=None, followups=None,
                 counter=None, finish="stop"):
    claims = claims if claims is not None else [
        {"kind": "inference", "text": "基于既有材料的条件性判断，不构成事实认定。"}]
    followups = followups if followups is not None else ["后续可验证：补齐数据后复核。"]
    payload = {"id": section_id, "title": TITLES[section_id], "status": status,
               "summary": "ok", "claims": claims, "counter_evidence": counter or [],
               "follow_up_questions": followups}
    return TurnResult(content=json.dumps(payload, ensure_ascii=False), finish_reason=finish)


def five_sections(**overrides):
    turns = []
    for item in SECTION_TEMPLATES:
        turn = overrides.get(item["id"])
        if turn is None:
            turn = section_turn(item["id"])
        if turn is not None:
            turns.append(turn)
    return turns


COLLECTION_DONE = TurnResult(content="收集完成：已有材料足够。")


def tool_call(call_id, name, arguments):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def build_env(tmp_path):
    storage = Storage(tmp_path / "t.db")
    storage.initialize()
    repository = ResearchRepository(storage)
    return repository, TaskStore(repository), ReportStore(repository)


async def run_graph(env, market, llm, *, budget=None, scope=SCOPE,
                    question="测试公司经营质量如何？"):
    _, tasks, reports = env
    task = tasks.create(question=question, scope=scope)
    tasks.mark_running(task.id)
    graph = ResearchGraph(market=market, model=llm, tasks=tasks, reports=reports, budget=budget)
    state = await graph.run(task_id=task.id, question=question, scope=scope)
    return task.id, state, reports.list_for_task(task.id)


def section_by_id(report, section_id):
    return next(s for s in report.sections if s.id == section_id)


# -------------------------------------------------------------------- tests ---
async def test_template_mode_price_without_financials(tmp_path):
    """有价格、无财务：报告完成，coverage=partial，价格节 supported，财务节 unavailable。"""
    env = build_env(tmp_path)
    market = FakeMarket(statements="not_published", valuations="not_published")
    llm = FakeLLM(five_sections(
        financials=section_turn("financials", status="unavailable", claims=[],
                                followups=["补充财务数据后复核。"]),
        price_valuation=section_turn(
            "price_valuation", status="supported",
            claims=[{"kind": "fact", "text": "区间涨跌与最大回撤以程序指标为准。",
                     "metric_refs": ["range_change", "max_drawdown"]}]),
    ), mode=MODE_TEMPLATE)
    task_id, state, stored = await run_graph(env, market, llm)
    assert len(stored) == 1
    report = stored[0].report
    assert report.coverage == Coverage.PARTIAL
    assert report.output_integrity == OutputIntegrity.VALIDATED
    assert section_by_id(report, "financials").status == SectionStatus.UNAVAILABLE
    assert section_by_id(report, "price_valuation").status == SectionStatus.SUPPORTED
    # 缺口不被模型删除：财务缺口仍在报告里
    assert any(gap.reason == "not_published" and gap.dimension == "financial_statements"
               for gap in report.data_gaps)
    assert state["usage"]["fetches"] == 6 and state["usage"]["llm_requests"] == 5
    assert report.model_snapshot is not None and report.model_snapshot.mode == MODE_TEMPLATE
    assert any("能力降级" in item for item in report.limitations)
    assert any("降级" in notice["detail"] for notice in state["notices"])
    task = env[1].get(task_id)
    assert task.status == TaskStatus.COMPLETED and task.report_id == stored[0].id


async def test_no_data_and_no_tools_still_completes_with_framework(tmp_path):
    """全无数据且工具不可用：任务 completed，coverage=no_business_evidence，仍给框架章节。"""
    env = build_env(tmp_path)
    llm = FakeLLM([
        tool_call("call-1", "fetch_financials", json.dumps({"statement": "income"})),
        COLLECTION_DONE,
        *five_sections(),
    ])
    task_id, state, stored = await run_graph(env, None, llm)
    report = stored[0].report
    assert report.coverage == Coverage.NO_BUSINESS_EVIDENCE
    assert report.output_integrity == OutputIntegrity.VALIDATED
    assert any(gap.dimension == "identity" for gap in report.data_gaps)
    assert state["usage"]["llm_requests"] == 7 and state["usage"]["fetches"] == 0
    assert state["usage"]["tool_rounds"] == 1
    # 结构化错误回给了对应 call id（身份未确认也返回结构化结果而不是崩溃）
    tool_messages = [m for call in llm.calls for m in call["messages"] if m.get("role") == "tool"]
    assert tool_messages and json.loads(tool_messages[0]["content"])["status"] == "ok"
    task = env[1].get(task_id)
    assert task.status == TaskStatus.COMPLETED


async def test_bad_tool_arguments_get_structured_error_and_one_correction(tmp_path):
    """坏参数：向对应 call id 返回结构化错误；改正有效；完全相同的坏参数不再执行。"""
    env = build_env(tmp_path)
    market = FakeMarket(prices="not_published", snapshot="not_published")
    llm = FakeLLM([
        tool_call("call-1", "fetch_financials", json.dumps({"statement": "profits"})),
        tool_call("call-2", "fetch_financials", json.dumps({"statement": "income"})),
        tool_call("call-3", "fetch_financials", json.dumps({"statement": "profits"})),
        COLLECTION_DONE,
        *five_sections(),
    ])
    task_id, state, stored = await run_graph(env, market, llm)
    tool_messages = {}
    for call in llm.calls:
        for message in call["messages"]:
            if message.get("role") == "tool":
                tool_messages[message["tool_call_id"]] = json.loads(message["content"])
    assert tool_messages["call-1"]["status"] == "error"
    assert tool_messages["call-1"]["error"]["reason"] == "invalid_arguments"
    assert tool_messages["call-2"]["status"] == "ok"
    assert tool_messages["call-2"]["result"]["status"] == "available"
    assert tool_messages["call-3"]["status"] == "error"
    assert tool_messages["call-3"]["error"]["reason"] == "repeat_failed"
    # 只有一次真实取数（call-2）；坏参数从未触达数据服务
    assert market.calls["statement"] == 1
    assert state["usage"]["fetches"] == 1
    report = stored[0].report
    assert report.coverage == Coverage.PARTIAL
    metric_names = {metric.metric for metric in report.metrics}
    assert "net_margin" in metric_names
    assert env[1].get(task_id).status == TaskStatus.COMPLETED


async def test_duplicate_tool_call_reuses_task_result(tmp_path):
    """相同工具+相同规范化参数的重复调用复用本任务结果，不再产生新取数。"""
    env = build_env(tmp_path)
    market = FakeMarket(statements="not_published", snapshot="not_published")
    llm = FakeLLM([
        TurnResult(content="", finish_reason="tool_calls",
                   tool_calls=[tool_call("call-1", "fetch_prices", json.dumps({})),
                               tool_call("call-2", "fetch_prices", json.dumps({}))]),
        COLLECTION_DONE,
        *five_sections(),
    ])
    _, state, stored = await run_graph(env, market, llm)
    tool_messages = [json.loads(m["content"]) for call in llm.calls
                     for m in call["messages"] if m.get("role") == "tool"]
    assert tool_messages[0]["duplicate"] is False
    assert tool_messages[1]["duplicate"] is True
    assert market.calls["history"] == 1
    assert state["usage"]["fetches"] == 1


async def test_llm_budget_exhaustion_keeps_partial_content(tmp_path):
    """LLM 预算耗尽：不再新开付费请求，已完成内容保留，缺的章节如实标注。"""
    env = build_env(tmp_path)
    llm = FakeLLM([section_turn("facts"), section_turn("financials")])
    budget = TaskBudget(max_llm_requests=2)
    task_id, state, stored = await run_graph(env, FakeMarket(), llm, budget=budget)
    report = stored[0].report
    assert state["usage"]["llm_requests"] == 2
    assert state["usage"]["fetches"] == 0  # 预留优先给章节，取数轮被跳过
    assert section_by_id(report, "facts").status == SectionStatus.CONDITIONAL
    assert section_by_id(report, "followups").status == SectionStatus.UNAVAILABLE
    assert "调用预算已用尽" in section_by_id(report, "followups").summary
    assert report.output_integrity == OutputIntegrity.PARTIAL
    assert any("预算" in item for item in report.limitations)
    assert env[1].get(task_id).status == TaskStatus.COMPLETED


async def test_fetch_budget_exhaustion_stops_template_early(tmp_path):
    """取数预算耗尽：固定模板提前结束，已取得的数据照常参与分析。"""
    env = build_env(tmp_path)
    from backend.app.research.models import TaskBudget
    llm = FakeLLM(five_sections(), mode=MODE_TEMPLATE)
    budget = TaskBudget(max_fetches=2)
    task_id, state, stored = await run_graph(env, FakeMarket(), llm, budget=budget)
    assert state["usage"]["fetches"] == 2
    report = stored[0].report
    assert report.coverage == Coverage.PARTIAL
    metrics = {metric.metric: metric.value for metric in report.metrics}
    assert metrics["net_margin"] is not None
    assert env[1].get(task_id).status == TaskStatus.COMPLETED


async def test_format_failure_falls_back_to_natural_language_once(tmp_path):
    """JSON 无法读取时只降级一次，页面可读正文不包含原始 JSON。"""
    env = build_env(tmp_path)
    llm = FakeLLM([
        COLLECTION_DONE,
        TurnResult(content="抱歉，我无法完成：{a:1}{b:2}", finish_reason="stop"),  # 多个竞争对象 → 拒绝
        section_turn("facts"),
        *five_sections()[1:],
    ])
    task_id, state, stored = await run_graph(env, FakeMarket(prices="not_published",
                                                             snapshot="not_published"), llm)
    report = stored[0].report
    assert section_by_id(report, "facts").status == SectionStatus.CONDITIONAL
    # 1 次收集 + facts 生成/修复 2 次 + 其余 4 节各 1 次
    assert state["usage"]["llm_requests"] == 7
    assert len(llm.calls) == 7
    facts_calls = [call for call in llm.calls
                   if any("事实概览" in m.get("content", "") for m in call["messages"]
                          if m.get("role") == "user")]
    assert len(facts_calls) == 2  # 生成 + 一次修复，绝无第三次
    assert not section_by_id(report, "facts").summary.lstrip().startswith("{")
    assert state["section_states"]["facts"]["integrity"] == "markdown_rewritten"
    assert report.output_integrity == OutputIntegrity.PARTIAL


async def test_truncation_recovery_once_and_stops(tmp_path):
    """finish_reason=length → incomplete；自动恢复一次成功后即正常收尾。"""
    env = build_env(tmp_path)
    llm = FakeLLM([
        COLLECTION_DONE,
        TurnResult(content='{"id": "facts"', finish_reason="length"),
        section_turn("facts"),
        *five_sections()[1:],
    ])
    _, state, stored = await run_graph(env, FakeMarket(prices="not_published",
                                                       snapshot="not_published"), llm)
    report = stored[0].report
    assert section_by_id(report, "facts").status == SectionStatus.CONDITIONAL
    assert state["usage"]["llm_requests"] == 7
    facts_calls = [call for call in llm.calls
                   if any("【facts" in m.get("content", "") for m in call["messages"]
                          if m.get("role") == "user")]
    assert len(facts_calls) == 2  # 生成 + 一次恢复，绝无第三次
    assert report.output_integrity == OutputIntegrity.VALIDATED
    assert stored[0].raw_sections == []


async def test_truncation_recovery_failure_keeps_incomplete_section(tmp_path):
    """恢复也截断：章节保持 incomplete，原始文本仅存于 raw_sections，不再循环。"""
    env = build_env(tmp_path)
    llm = FakeLLM([
        COLLECTION_DONE,
        TurnResult(content='{"id": "facts"', finish_reason="length"),
        TurnResult(content='{"id": "facts", "cl', finish_reason="length"),
        *five_sections()[1:],
    ])
    _, state, stored = await run_graph(env, FakeMarket(prices="not_published",
                                                       snapshot="not_published"), llm)
    report = stored[0].report
    facts = section_by_id(report, "facts")
    assert facts.status == SectionStatus.UNAVAILABLE and not facts.claims
    assert state["usage"]["llm_requests"] == 7
    assert len(stored[0].raw_sections) == 1
    assert stored[0].raw_sections[0].reason == "truncated_after_recovery"
    # 截断的原始文本不进入已验证章节对象
    assert all(s.id != "facts" or s.summary != stored[0].raw_sections[0].text for s in report.sections)
    assert report.output_integrity == OutputIntegrity.TRUNCATED
    assert any("截断" in item for item in report.limitations)


async def test_empty_answer_regen_once_then_accept(tmp_path):
    """空答（无论断无待验证问题）按内容不合格重生成一次；再失败就如实保留，不循环。"""
    env = build_env(tmp_path)
    empty = section_turn("facts", status="unavailable", claims=[], followups=[])
    llm = FakeLLM([COLLECTION_DONE, empty, section_turn("facts"), *five_sections()[1:]])
    _, state, stored = await run_graph(env, FakeMarket(prices="not_published",
                                                       snapshot="not_published"), llm)
    report = stored[0].report
    assert len(section_by_id(report, "facts").claims) == 1
    facts_calls = [call for call in llm.calls
                   if any("【facts" in m.get("content", "") for m in call["messages"]
                          if m.get("role") == "user")]
    assert len(facts_calls) == 2

    # 重生成后仍为空答：保留为 unavailable，绝不第三次
    env2 = build_env(tmp_path)
    llm2 = FakeLLM([COLLECTION_DONE, empty, empty, *five_sections()[1:]])
    _, state2, stored2 = await run_graph(env2, FakeMarket(prices="not_published",
                                                          snapshot="not_published"), llm2)
    facts2 = section_by_id(stored2[0].report, "facts")
    assert facts2.status == SectionStatus.UNAVAILABLE
    facts_calls2 = [call for call in llm2.calls
                    if any("【facts" in m.get("content", "") for m in call["messages"]
                           if m.get("role") == "user")]
    assert len(facts_calls2) == 2
    assert stored2[0].report.output_integrity == OutputIntegrity.VALIDATED


async def test_reasoning_is_counted_but_never_persisted(tmp_path):
    """reasoning 只记字符数：报告与持久化内容不含隐藏推理。"""
    env = build_env(tmp_path)
    scripted = [TurnResult(content=COLLECTION_DONE.content, finish_reason="stop", reasoning_chars=77)]
    scripted += [TurnResult(content=turn.content, finish_reason="stop", reasoning_chars=11)
                 for turn in five_sections()]
    llm = FakeLLM(scripted)
    _, state, stored = await run_graph(env, FakeMarket(), llm)
    assert state["usage"]["reasoning_chars"] > 0
    report_json = stored[0].report.model_dump_json()
    assert "reasoning" not in report_json
    blob = json.dumps(stored[0].model_dump(), ensure_ascii=False, default=str)
    assert "reasoning_content" not in blob


async def test_metric_conflict_triggers_one_content_regen(tmp_path):
    """正文数字与被引指标冲突：同节内容重生成一次；修复后冲突消失。"""
    env = build_env(tmp_path)
    conflicting = section_turn(
        "price_valuation", status="supported",
        claims=[{"kind": "fact", "text": "区间上涨了 95%。",
                 "metric_refs": ["range_change"]}])
    llm = FakeLLM([
        tool_call("call-1", "fetch_prices", json.dumps({})),
        COLLECTION_DONE,
        *five_sections(price_valuation=conflicting),
        section_turn("price_valuation"),  # 内容重生成回合（排在 risk 之前被消费）
    ])
    _, state, stored = await run_graph(env, FakeMarket(statements="not_published",
                                                       snapshot="not_published"), llm)
    assert state["usage"]["fetches"] == 1  # 价格数据确实取得了，冲突才有依据
    section = section_by_id(stored[0].report, "price_valuation")
    assert len(section.claims) == 1 and "95" not in section.claims[0].text
    price_calls = [call for call in llm.calls
                   if any("【price_valuation" in m.get("content", "") for m in call["messages"]
                          if m.get("role") == "user")]
    assert len(price_calls) == 2  # 生成 + 一次内容重生成


# ---------------------------------------------------- storage v6 迁移测试 ---
V5_SCHEMA = """
CREATE TABLE model_profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, base_url TEXT NOT NULL,
  model TEXT NOT NULL, encrypted_api_key TEXT, temperature REAL NOT NULL, max_tokens INTEGER NOT NULL,
  timeout REAL NOT NULL, active INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT 'custom', options_json TEXT NOT NULL DEFAULT '{}',
  output_policy TEXT NOT NULL DEFAULT 'custom', legacy_max_tokens INTEGER);
CREATE TABLE api_profiles (id TEXT PRIMARY KEY, provider TEXT NOT NULL, name TEXT NOT NULL UNIQUE,
  encrypted_api_key TEXT, enabled INTEGER NOT NULL, options_json TEXT NOT NULL DEFAULT '{}',
  credential_version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE api_capability_tests (profile_id TEXT NOT NULL REFERENCES api_profiles(id) ON DELETE CASCADE,
  capability TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
  credential_version INTEGER NOT NULL, tested_at TEXT NOT NULL, PRIMARY KEY (profile_id, capability));
CREATE TABLE security_identities (thscode TEXT PRIMARY KEY, provider TEXT NOT NULL, ticker TEXT NOT NULL,
  name TEXT NOT NULL, exchange TEXT, asset_type TEXT NOT NULL, currency TEXT NOT NULL,
  confirmed_source TEXT NOT NULL, confirmed_at TEXT NOT NULL);
CREATE TABLE evidence (id TEXT PRIMARY KEY, provider TEXT NOT NULL, profile_id TEXT, capability TEXT NOT NULL,
  endpoint_path TEXT NOT NULL, params_json TEXT NOT NULL, security_id TEXT, thscode TEXT, basis TEXT NOT NULL,
  source_time TEXT, fetched_at TEXT NOT NULL, content_hash TEXT NOT NULL, record_count INTEGER NOT NULL,
  schema_version TEXT NOT NULL, storage_policy TEXT NOT NULL, license_note TEXT NOT NULL DEFAULT '',
  records_json TEXT NOT NULL, gaps_json TEXT NOT NULL DEFAULT '[]', duplicate_versions INTEGER NOT NULL DEFAULT 0);
CREATE TABLE data_gaps (id TEXT PRIMARY KEY, evidence_id TEXT REFERENCES evidence(id) ON DELETE CASCADE,
  dimension TEXT NOT NULL, fields_json TEXT NOT NULL, security_id TEXT, period TEXT, reason TEXT NOT NULL,
  impact TEXT NOT NULL, blocking_for_json TEXT NOT NULL DEFAULT '[]', remedies_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL);
CREATE TABLE data_cache (cache_key TEXT PRIMARY KEY, provider TEXT NOT NULL, profile_id TEXT,
  credential_version INTEGER NOT NULL, endpoint_path TEXT NOT NULL, schema_version TEXT NOT NULL,
  payload_json TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
"""


def test_storage_migration_from_v5_reaches_current_and_preserves_rows(tmp_path):
    """v5 库必须一路迁移到当前版本，且既有 evidence 行与新增研究表都完好。"""
    path = tmp_path / "v5.db"
    with sqlite3.connect(path) as db:
        db.executescript(V5_SCHEMA)
        db.execute(
            "INSERT INTO evidence VALUES ('e1', 'hexin', NULL, 'financial_statements', '/x', '{}',"
            " NULL, '600519.SH', 'annual_report', NULL, '2026-09-01', 'hash', 1,"
            " 'finsight.evidence.v1', 'reference_only', '', '[]', '[]', 0)")
        db.execute("PRAGMA user_version = 5")
    storage = Storage(path)
    storage.initialize()
    storage.initialize()  # 幂等
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"research_tasks", "research_events", "research_reports",
                "research_supplements"} <= tables
        assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 1
        # 新表结构：任务事件以 (task_id, seq) 为主键，供 SSE 增量读取
        columns = {row[1] for row in db.execute("PRAGMA table_info(research_events)")}
        assert {"task_id", "seq", "type", "payload_json"} <= columns


def test_task_report_supplement_roundtrip(tmp_path):
    env_repo, _tasks, reports = build_env(tmp_path)
    from backend.app.agent.output_models import ModelSnapshot
    from backend.app.research.models import TaskBudget
    tasks = TaskStore(env_repo)
    task = tasks.create(question="测试", scope=SCOPE,
                        budget=TaskBudget(max_llm_requests=3))
    tasks.mark_running(task.id)
    assert tasks.get(task.id).status == TaskStatus.RUNNING
    tasks.event(task.id, "phase", {"detail": "测试事件"})
    tasks.event(task.id, "phase", {"detail": "第二条"})
    events = tasks.events_after(task.id)
    assert [event.seq for event in events] == [1, 2, 3]
    assert events[0].type == "task.created" and events[1].type == "phase"
    report = ResearchReport(
        id="r1", version=1, task_id=task.id, question="测试", scope=SCOPE,
        model_snapshot=ModelSnapshot(provider="deepseek", model="m", output_label="64K",
                                     mode=MODE_TEMPLATE),
        coverage=Coverage.PARTIAL, output_integrity=OutputIntegrity.PARTIAL)
    stored = reports.save(report, raw_sections=[RawSectionText(
        id="facts", title="事实概览", reason="unparseable_no_repair", text="原始文本")])
    stored2 = reports.save(report.model_copy(update={"id": "r2"}))
    assert (stored.version, stored2.version) == (1, 2)
    loaded = reports.get(stored.id)
    assert loaded.raw_sections[0].text == "原始文本"
    assert reports.list_for_task(task.id)[0].report.coverage == Coverage.PARTIAL

    from backend.app.research.supplements import SupplementStore
    supplements = SupplementStore(env_repo)
    item = supplements.add(report_id=stored.id, kind=SupplementKind.USER_PROVIDED,
                           content="2024 年营业收入 1741.44 亿元（来源：公司年报）",
                           fields={"period": "2024-FY", "unit": "CNY"})
    assert supplements.list(stored.id)[0].content.startswith("2024")
    supplements.mark_referenced([item.id], stored2.id)
    assert supplements.list(stored.id)[0].referenced_by_report_id == stored2.id
    tasks.mark_completed(task.id, report_id=stored2.id)
    assert tasks.get(task.id).status == TaskStatus.COMPLETED
    assert tasks.get(task.id).finished_at is not None
