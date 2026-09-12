"""Read-only research tools (plan 03 sections 2.2 / 2.4).

白名单只有五个领域方法：fetch_financials / fetch_prices / fetch_valuation /
read_evidence / calculate_metrics。没有任意 HTTP、SQL、Python、shell、文件路径
工具，执行器也不接触任何 Key（取数经由 02 的 MarketDataService）。

- 工具参数只接受严格 JSON：parse_tool_arguments 通过后还要过 extra=forbid 的
  Pydantic 参数模型；坏参数向对应 call id 返回结构化错误，允许模型在剩余预算
  内改正一次，完全相同的坏参数不重复执行。
- 相同工具 + 相同规范化参数的重复调用复用本任务结果，防止循环收费。
- 每个逻辑取数最多两次暂时错误重试（rate_limited/timeout），重试计入取数预算。
- 来源失败返回结构化 DataGap，绝不用"缺数"拒绝整份分析。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..analysis.financial_metrics import (
    debt_ratio,
    net_margin,
    net_profit_yoy,
    ocf_vs_net_profit,
    revenue_yoy,
)
from ..analysis.models import MetricValue
from ..analysis.price_metrics import max_drawdown, range_change
from ..errors import AppError
from ..web_research.models import WebSearchRequest
from ..market_data.normalization import BASIS_DAILY_BAR
from ..research.evidence_models import DataGap, DataResult, RequestedScope, ResultStatus
from .json_parser import JsonRejected, parse_tool_arguments

TOOL_NAMES = ("fetch_financials", "fetch_prices", "fetch_valuation",
              "fetch_index_context", "fetch_market_pulse", "fetch_fund_profile",
              "fetch_fund_portfolio", "fetch_fund_performance", "query_market_warehouse",
              "fetch_news", "search_knowledge", "search_web", "record_advice",
              "query_advice_ledger", "read_evidence", "calculate_metrics")

MAX_FETCH_RETRIES = 2
TRANSIENT_GAP_REASONS = ("rate_limited", "timeout")
MAX_READ_RECORDS = 8
MAX_EXCERPT_CHARS = 6000
DEFAULT_HISTORY_DAYS = 365

TOOL_SCHEMAS: list[dict] = [
    {"type": "function", "function": {
        "name": "fetch_financials",
        "description": "获取已确认标的的财务报表（利润表/资产负债表/现金流量表）。只读取数，计入取数预算。",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "required": ["statement"],
            "properties": {
                "statement": {"type": "string", "enum": ["income", "balance", "cashflow"],
                              "description": "income=利润表, balance=资产负债表, cashflow=现金流量表"},
                "period": {"type": "string", "enum": ["annual", "quarterly"],
                           "description": "默认 annual；季度行口径未确认，仅原始保存"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 8,
                          "description": "最多返回的期数，默认 4"},
            },
        }}},
    {"type": "function", "function": {
        "name": "fetch_prices",
        "description": "获取已确认标的的价格数据：日线区间（默认近一年）或最新快照。只读取数，计入取数预算。",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "required": [],
            "properties": {
                "kind": {"type": "string", "enum": ["history", "snapshot"],
                         "description": "默认 history；snapshot 为最新成交快照"},
                "start": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$",
                          "description": "history 起始日 YYYY-MM-DD，缺省为近一年"},
                "end": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$",
                        "description": "history 结束日 YYYY-MM-DD，缺省为今天"},
            },
        }}},
    {"type": "function", "function": {
        "name": "fetch_valuation",
        "description": "获取已确认标的的估值快照（市盈率/市净率等）。只读取数，计入取数预算。",
        "parameters": {"type": "object", "additionalProperties": False, "required": [], "properties": {}}},
    },
    {"type": "function", "function": {
        "name": "read_evidence",
        "description": "按证据 ID 读取本任务已获证据的摘录（少量记录片段）。本地读取，不产生外部调用。",
        "parameters": {
            "type": "object", "additionalProperties": False, "required": ["evidence_id"],
            "properties": {"evidence_id": {"type": "string", "description": "本任务取数返回的 32 位证据 ID"}},
        }}},
    {"type": "function", "function": {
        "name": "calculate_metrics",
        "description": "用确定性程序对已取得的数据计算指标（同比/净利率/资产负债率/现金流净利比/区间涨跌/最大回撤）。",
        "parameters": {
            "type": "object", "additionalProperties": False, "required": [],
            "properties": {"metrics": {"type": "array", "items": {"type": "string"}, "maxItems": 16,
                                       "description": "要计算的指标名，缺省为全部可用指标"}},
        }}},
]

# New-domain schemas deliberately contain no URL, SQL or provider-path field.
# They are kept beside the compatibility schemas so every model-facing tool
# still has one strict Pydantic contract at execution time.
TOOL_SCHEMAS.extend([
    {"type": "function", "function": {"name": "fetch_index_context",
        "description": "获取已确认指数的快照与当前成分语境；只读取数。",
        "parameters": {"type": "object", "additionalProperties": False, "required": [], "properties": {}}}},
    {"type": "function", "function": {"name": "fetch_market_pulse",
        "description": "获取指定日期的一个市场观察池；只读取数。",
        "parameters": {"type": "object", "additionalProperties": False, "required": ["date_ms"], "properties": {
            "date_ms": {"type": "integer", "minimum": 1},
            "kind": {"type": "string", "enum": ["limit_up", "limit_down", "limit_break", "limit_ladder", "skyrocket", "hot", "anomaly", "dragon_tiger"]}}}}},
    {"type": "function", "function": {"name": "fetch_fund_profile",
        "description": "获取已确认基金的基本资料；只读取数。",
        "parameters": {"type": "object", "additionalProperties": False, "required": [], "properties": {
            "fund_type": {"type": "string", "enum": ["otc", "exchange", "reits"]}}}}},
    {"type": "function", "function": {"name": "fetch_fund_portfolio",
        "description": "获取已确认基金最近披露的股票持仓；不表示实时持仓。",
        "parameters": {"type": "object", "additionalProperties": False, "required": [], "properties": {
            "fund_type": {"type": "string", "enum": ["otc", "exchange", "reits"]}}}}},
    {"type": "function", "function": {"name": "fetch_fund_performance",
        "description": "获取已确认基金的净值序列；净值口径由证据记录。",
        "parameters": {"type": "object", "additionalProperties": False, "required": [], "properties": {
            "fund_type": {"type": "string", "enum": ["otc", "exchange", "reits"]},
            "range_name": {"type": "string", "enum": ["month", "tmonth", "hyear", "year", "twoyear", "tyear", "fyear"]}}}}},
    {"type": "function", "function": {"name": "query_market_warehouse",
        "description": "查询本机全市场仓库的固定指标；没有网络、SQL 或任意筛选表达式。",
        "parameters": {"type": "object", "additionalProperties": False, "required": ["date_ms", "metric"], "properties": {
            "date_ms": {"type": "integer", "minimum": 1},
            "metric": {"type": "string", "enum": ["breadth", "liquidity", "sample"]},
            "min_turnover": {"type": "number", "minimum": 0},
            "page": {"type": "integer", "minimum": 1, "maximum": 10000},
            "page_size": {"type": "integer", "minimum": 1, "maximum": 500}}}}},
    {"type": "function", "function": {"name": "fetch_news",
        "description": "检索本机订阅的财经新闻时间线（本地读取，不产生外部调用）。"
                       "新闻内容属于线索：引用时必须说明是新闻线索，数字与事实需第二来源或工具数据交叉验证。",
        "parameters": {"type": "object", "additionalProperties": False, "required": [], "properties": {
            "thscode": {"type": "string", "pattern": r"^[0-9]{6}\.(SH|SZ|BJ)$",
                        "description": "可选；只看与该标的相关的新闻，缺省返回最新新闻"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20,
                      "description": "最多返回的条数，默认 10"}}}}},
    {"type": "function", "function": {"name": "search_knowledge",
        "description": "在用户上传的私有知识库（本地库）中检索相关文档分块，"
                       "返回摘录、文档名与原文定位（标题/页码/行号）。"
                       "命中属于可核验佐证：引用时必须注明文档名与定位。",
        "parameters": {"type": "object", "additionalProperties": False, "required": ["query"], "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 200,
                      "description": "检索词；避免输入检索语法符号"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 8,
                      "description": "最多返回的分块数，默认 4"}}}}},
    {"type": "function", "function": {"name": "search_web",
        "description": "用用户已配置并授权的搜索服务查询公开网页信息（返回标题/摘要/链接，不抓网页正文）。"
                       "结果是线索：引用前必须交叉验证；只有用户本次明确允许联网时才可调用。",
        "parameters": {"type": "object", "additionalProperties": False, "required": ["query"], "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 200,
                      "description": "搜索关键词"},
            "count": {"type": "integer", "minimum": 1, "maximum": 8,
                      "description": "结果条数，默认 5"}}}}},
    {"type": "function", "function": {"name": "record_advice",
        "description": "把一条结构化建议卡登记进本机建议台账（可跟踪、可复盘）。"
                       "仅在证据齐备、方向明确时调用；卡片必须含三情景与失效条件。",
        "parameters": {"type": "object", "additionalProperties": False,
                       "required": ["card"], "properties": {
            "card": {"type": "object", "description": "完整建议卡 JSON（schema_version=finsight.suggestion.v1）"}}}}},
    {"type": "function", "function": {"name": "query_advice_ledger",
        "description": "查询本机建议台账：历史建议、结算结果与滚动命中率统计。",
        "parameters": {"type": "object", "additionalProperties": False, "required": [], "properties": {
            "status": {"type": "string", "enum": ["tracking", "settled", "void"],
                       "description": "可选；按状态过滤"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50,
                      "description": "最多返回条数，默认 20"}}}}},
])


class FetchFinancialsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: Literal["income", "balance", "cashflow"]
    period: Literal["annual", "quarterly"] = "annual"
    limit: int = Field(default=4, ge=1, le=8, strict=True)


class FetchPricesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["history", "snapshot"] = "history"
    start: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class FetchValuationArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FetchIndexContextArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FetchMarketPulseArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_ms: int = Field(gt=0, strict=True)
    kind: Literal["limit_up", "limit_down", "limit_break", "limit_ladder", "skyrocket", "hot", "anomaly", "dragon_tiger"] = "limit_up"


class FetchFundArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fund_type: Literal["otc", "exchange", "reits"] = "otc"
    range_name: Literal["month", "tmonth", "hyear", "year", "twoyear", "tyear", "fyear"] = "year"


class FetchNewsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thscode: str | None = Field(default=None, pattern=r"^[0-9]{6}\.(SH|SZ|BJ)$")
    limit: int = Field(default=10, ge=1, le=20)


class SearchWebArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200)
    count: int = Field(default=5, ge=1, le=8, strict=True)


class RecordAdviceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card: dict


class QueryAdviceLedgerArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["tracking", "settled", "void"] | None = None
    limit: int = Field(default=20, ge=1, le=50, strict=True)


class SearchKnowledgeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200)
    top_k: int = Field(default=4, ge=1, le=8)


class QueryWarehouseArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_ms: int = Field(gt=0, strict=True)
    metric: Literal["breadth", "liquidity", "sample"]
    min_turnover: float = Field(default=0, ge=0)
    page: int = Field(default=1, ge=1, le=10000, strict=True)
    page_size: int = Field(default=100, ge=1, le=500, strict=True)


class ReadEvidenceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class CalculateMetricsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: list[str] = Field(default_factory=list, max_length=16)


ARGUMENT_MODELS = {
    "fetch_financials": FetchFinancialsArgs,
    "fetch_prices": FetchPricesArgs,
    "fetch_valuation": FetchValuationArgs,
    "fetch_index_context": FetchIndexContextArgs,
    "fetch_market_pulse": FetchMarketPulseArgs,
    "fetch_fund_profile": FetchFundArgs,
    "fetch_fund_portfolio": FetchFundArgs,
    "fetch_fund_performance": FetchFundArgs,
    "query_market_warehouse": QueryWarehouseArgs,
    "fetch_news": FetchNewsArgs,
    "search_knowledge": SearchKnowledgeArgs,
    "search_web": SearchWebArgs,
    "record_advice": RecordAdviceArgs,
    "query_advice_ledger": QueryAdviceLedgerArgs,
    "read_evidence": ReadEvidenceArgs,
    "calculate_metrics": CalculateMetricsArgs,
}

FETCH_TOOLS = ("fetch_financials", "fetch_prices", "fetch_valuation", "fetch_index_context",
               "fetch_market_pulse", "fetch_fund_profile", "fetch_fund_portfolio", "fetch_fund_performance")

STATEMENT_GROUPS = {"income": "income", "balance": "balance", "cashflow": "cashflow"}

# 固定取数模板：自定义模型无稳定 tool calling 时的程序化降级（计划 §2.2）。
TEMPLATE_FETCH_PLAN: list[tuple[str, dict]] = [
    ("fetch_financials", {"statement": "income", "period": "annual", "limit": 4}),
    ("fetch_financials", {"statement": "balance", "period": "annual", "limit": 4}),
    ("fetch_financials", {"statement": "cashflow", "period": "annual", "limit": 4}),
    ("fetch_prices", {"kind": "history"}),
    ("fetch_prices", {"kind": "snapshot"}),
    ("fetch_valuation", {}),
]


def template_fetch_plan_for_scope(scope: dict, now: datetime | None = None) -> list[tuple[str, dict]]:
    """Closed, program-owned fallback plans for every typed domain.

    This path lets a task retain factual evidence and precise gaps when no
    language model is configured; it never derives a URL, SQL statement or
    provider capability from model text.
    """
    kind = scope.get("type", "general")
    if kind in {"company_overview", "general", "company_quality", "company_comparison", "thesis_review"}:
        return list(TEMPLATE_FETCH_PLAN)
    if kind in {"index_strength", "sector_breadth", "sector_relative"}:
        return [("fetch_index_context", {})]
    if kind in {"fund_overview", "fund_quality"}:
        return [("fetch_fund_profile", {}), ("fetch_fund_portfolio", {}), ("fetch_fund_performance", {})]
    if kind in {"fund_compare", "fund_overlap"}:
        return [("fetch_fund_profile", {}), ("fetch_fund_performance", {})]
    date_ms = scope.get("end_ms") or scope.get("start_ms")
    if kind in {"market_pulse", "market_review"} and date_ms:
        return [("fetch_market_pulse", {"date_ms": date_ms})]
    if kind == "market_breadth" and date_ms:
        return [("query_market_warehouse", {"date_ms": date_ms, "metric": "breadth"})]
    return []


def compute_metric_set(records: dict[str, list[dict]],
                       group_evidence: dict[str, list[str]]) -> list[MetricValue]:
    """确定性指标：数值全部由程序计算，null 是缺数而非零（02 契约复用）。"""
    income = records.get("income", [])
    balance = records.get("balance", [])
    cashflow = records.get("cashflow", [])
    fin_ids = list(group_evidence.get("financials", []))
    metrics = [
        revenue_yoy(income, evidence_ids=fin_ids),
        net_profit_yoy(income, evidence_ids=fin_ids),
        net_margin(income, evidence_ids=fin_ids),
        debt_ratio(balance, evidence_ids=fin_ids),
        ocf_vs_net_profit(income, cashflow, evidence_ids=fin_ids),
    ]
    history = [record for record in records.get("price_history", [])
               if record.get("basis") == BASIS_DAILY_BAR]
    price_ids = list(group_evidence.get("price_history", []))
    metrics.append(range_change(history, evidence_ids=price_ids))
    metrics.append(max_drawdown(history, evidence_ids=price_ids))
    return metrics


def _canonical_args(args: dict) -> str:
    return json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ToolExecutor:
    """一次任务运行内共享的工具执行器：缓存、预算与证据累积都在这里。"""

    def __init__(self, *, market, identity: dict | None, usage: dict, budget: dict,
                 warehouse=None, news=None, knowledge=None, web=None, ledger=None,
                 now: datetime | None = None, allowed_tools: set[str] | None = None) -> None:
        self._market = market
        self._warehouse = warehouse
        self._news = news
        self._knowledge = knowledge
        self._web = web
        self._ledger = ledger
        self._identity = identity or {}
        self.usage = dict(usage)
        self.budget = dict(budget)
        self._now = now or datetime.now()
        self.records: dict[str, list[dict]] = {
            "income": [], "balance": [], "cashflow": [],
            "price_history": [], "price_snapshot": [], "valuation": [],
        }
        self.group_evidence: dict[str, list[str]] = {}
        self.evidence_index: dict[str, dict] = {}
        self.gaps: list[dict] = []
        self._cache: dict[str, dict] = {}
        self._failed_keys: set[str] = set()
        self._allowed_tools = allowed_tools or set(TOOL_NAMES)

    # -- model-driven entry --------------------------------------------------
    async def execute(self, call: dict) -> dict:
        """执行一次模型工具调用；坏参数向对应 call id 返回结构化错误。"""
        call_id = str(call.get("id") or "")
        function = call.get("function") or {}
        name = function.get("name")
        if name not in self._allowed_tools:
            return self._error_outcome(call_id, name, "unknown_tool",
                                       "调用了未授权的工具；请改用白名单工具或直接继续分析。")
        raw = function.get("arguments")
        args, error = self._parse_args(name, raw)
        if error is not None:
            reason, message = error
            bad_key = name + ":bad:" + _canonical_args({"raw": str(raw)})
            if bad_key in self._failed_keys:
                return self._error_outcome(call_id, name, "repeat_failed",
                                           "相同的坏参数已失败过一次，不再执行；请直接基于已有证据继续分析。")
            self._failed_keys.add(bad_key)
            return self._error_outcome(call_id, name, reason, message)
        if not call_id:
            return self._error_outcome(call_id, name, "unknown_tool", "工具调用缺少 call id，已忽略。")
        return await self._dispatch(name, args, call_id)

    # -- program (template) entry -------------------------------------------
    async def execute_program(self, name: str, args: dict) -> dict:
        """固定取数模板路径：参数由程序给定，同样走预算/重试/去重。"""
        if name not in self._allowed_tools:
            return self._error_outcome(None, name, "unknown_tool", "该固定计划不属于本研究范围。")
        validated = ARGUMENT_MODELS[name].model_validate(args)
        return await self._dispatch(name, validated, None)

    # -- parsing -------------------------------------------------------------
    def _parse_args(self, name: str, raw: object) -> tuple[object, tuple[str, str] | None]:
        try:
            parsed = parse_tool_arguments(raw if isinstance(raw, str) else "")
        except JsonRejected as error:
            return None, ("invalid_arguments", f"工具参数不是合法 JSON（{error.reason}）；请按 schema 修正后重试。")
        try:
            return ARGUMENT_MODELS[name].model_validate(parsed), None
        except ValidationError:
            return None, ("invalid_arguments", "工具参数不符合该工具的 schema 或取值范围；请按 schema 修正后重试。")

    # -- dispatch ------------------------------------------------------------
    async def _dispatch(self, name: str, args, call_id: str | None) -> dict:
        if name == "query_market_warehouse":
            return self._query_warehouse(args, call_id)
        if name == "fetch_news":
            return self._fetch_news(args, call_id)
        if name == "search_knowledge":
            return await self._search_knowledge(args, call_id)
        if name == "search_web":
            return await self._search_web(args, call_id)
        if name == "record_advice":
            return await self._record_advice(args, call_id)
        if name == "query_advice_ledger":
            return await self._query_advice_ledger(args, call_id)
        if name in FETCH_TOOLS:
            group = self._group_of(name, args)
            key = f"{name}:{_canonical_args(args.model_dump())}"
            cached = self._cache.get(key)
            if cached is not None:
                return {**cached, "call_id": call_id, "duplicate": True}
            if key in self._failed_keys:
                return self._error_outcome(call_id, name, "repeat_failed",
                                           "该调用此前已失败一次，不再重复执行；请直接基于已有证据继续分析。")
            if name != "fetch_market_pulse" and not self._identity.get("thscode"):
                outcome = self._identity_gap_outcome(name, call_id)
                self._cache[key] = outcome
                return outcome
            if not self._spend("fetches"):
                return self._error_outcome(call_id, name, "budget_exhausted",
                                           "取数预算已用尽，不能再发起新取数；请基于已有证据继续分析。")
            result = await self._fetch_with_retry(name, args)
            outcome = self._record_fetch(name, group, result, call_id)
            if result.status == ResultStatus.UNAVAILABLE:
                self._failed_keys.add(key)
            else:
                self._cache[key] = outcome
            return outcome
        if name == "read_evidence":
            return self._read_evidence(args, call_id)
        if name == "calculate_metrics":
            return self._calculate_metrics(args, call_id)
        return self._error_outcome(call_id, name, "unknown_tool", "调用了未授权的工具。")

    def _group_of(self, name: str, args) -> str:
        if name == "fetch_financials":
            return {"income": "income", "balance": "balance", "cashflow": "cashflow"}[args.statement]
        if name == "fetch_prices":
            return "price_snapshot" if args.kind == "snapshot" else "price_history"
        return {"fetch_valuation": "valuation", "fetch_index_context": "index_context",
                "fetch_market_pulse": "market_pulse", "fetch_fund_profile": "fund_profile",
                "fetch_fund_portfolio": "fund_portfolio", "fetch_fund_performance": "fund_performance"}[name]

    # -- fetch ---------------------------------------------------------------
    async def _fetch_with_retry(self, name: str, args) -> DataResult:
        """暂时错误（rate_limited/timeout）最多重试两次；每次尝试都计入取数预算。"""
        attempt = 0
        while True:
            attempt += 1
            result = await self._call_market(name, args)
            transient = (result.status == ResultStatus.UNAVAILABLE
                         and bool(result.gaps)
                         and result.gaps[0].reason in TRANSIENT_GAP_REASONS)
            if not transient or attempt > MAX_FETCH_RETRIES or not self._spend("fetches"):
                return result

    async def _call_market(self, name: str, args) -> DataResult:
        if self._market is None:
            return self._unavailable_result(name)
        if name == "fetch_market_pulse":
            return await self._market.fetch_market_event(args.kind, date_ms=args.date_ms)
        thscode = self._identity["thscode"]
        if name == "fetch_financials":
            return await self._market.fetch_statement(
                args.statement, thscode, period=args.period, limit=args.limit)
        if name == "fetch_prices":
            if args.kind == "snapshot":
                return await self._market.fetch_price_snapshot([thscode])
            start, end = self._history_window(args)
            return await self._market.fetch_price_history(thscode, start_ms=start, end_ms=end)
        if name == "fetch_valuation":
            return await self._market.fetch_valuations([thscode])
        if name == "fetch_index_context":
            return await self._market.fetch_index_snapshot([thscode])
        if name == "fetch_fund_profile":
            return await self._market.fetch_fund_profile(thscode, fund_type=args.fund_type)
        if name == "fetch_fund_portfolio":
            return await self._market.fetch_fund_holdings(thscode, fund_type=args.fund_type)
        if name == "fetch_fund_performance":
            return await self._market.fetch_fund_nav(thscode, fund_type=args.fund_type, range_name=args.range_name)
        return self._unavailable_result(name)

    def _history_window(self, args: FetchPricesArgs) -> tuple[int, int]:
        end = datetime.strptime(args.end, "%Y-%m-%d") if args.end else self._now
        start = (datetime.strptime(args.start, "%Y-%m-%d") if args.start
                 else end - timedelta(days=DEFAULT_HISTORY_DAYS))
        return int(start.timestamp() * 1000), int(end.timestamp() * 1000)

    def _unavailable_result(self, name: str) -> DataResult:
        capability = {"fetch_financials": "financial_statements",
                      "fetch_prices": "price_history",
                      "fetch_valuation": "valuation_snapshot"}.get(name, name)
        gap = DataGap(id=uuid.uuid4().hex, dimension=capability, fields=[],
                      security_id=None, period=None, reason="not_configured",
                      impact="金融数据服务不可用或未配置，本次未取得该能力数据。",
                      remedies=["在设置中配置并启用金融数据服务后，重新发起分析。"])
        return DataResult(status=ResultStatus.UNAVAILABLE, records=[], evidence_ids=[],
                          gaps=[gap],
                          requested_scope=RequestedScope(capability=capability),
                          returned_scope={"count": 0})

    def _identity_gap_outcome(self, name: str, call_id: str | None) -> dict:
        gap = DataGap(id=uuid.uuid4().hex, dimension="identity", fields=["thscode"],
                      security_id=None, period=None, reason="not_configured",
                      impact="标的身份未确认，无法发起结构化取数。",
                      remedies=["先在资料预览中检索并确认标的，再重新分析。"])
        self.gaps.append(gap.model_dump())
        return {"call_id": call_id, "tool": name, "status": "ok", "duplicate": False,
                "error": None,
                "result": {"capability": name, "status": "unavailable", "record_count": 0,
                           "evidence_ids": [], "gaps": [gap.model_dump()],
                           "note": "标的身份未确认；请直接基于问题与分析框架继续。"}}

    def _record_fetch(self, name: str, group: str, result: DataResult,
                      call_id: str | None) -> dict:
        capability = result.requested_scope.capability
        evidence_key = "financials" if group in ("income", "balance", "cashflow") else group
        self.group_evidence.setdefault(evidence_key, []).extend(result.evidence_ids)
        self.records.setdefault(group, []).extend(result.records)
        for gap in result.gaps:
            self.gaps.append(gap.model_dump())
        for evidence_id in result.evidence_ids:
            period = next((record.get("period_key") for record in result.records
                           if record.get("period_key")), None)
            self.evidence_index[evidence_id] = {"capability": capability, "period": period,
                                                "thscode": self._identity.get("thscode")}
        gaps_payload = [{"id": gap.id, "dimension": gap.dimension, "fields": gap.fields,
                         "reason": gap.reason, "impact": gap.impact} for gap in result.gaps]
        return {"call_id": call_id, "tool": name, "status": "ok", "duplicate": False,
                "error": None,
                "result": {"capability": capability, "status": result.status,
                           "record_count": len(result.records),
                           "evidence_ids": list(result.evidence_ids), "gaps": gaps_payload,
                           "note": "如需数值摘录，用 read_evidence 按证据 ID 读取。"}}

    def _query_warehouse(self, args: QueryWarehouseArgs, call_id: str | None) -> dict:
        if self._warehouse is None:
            return self._error_outcome(call_id, "query_market_warehouse", "not_configured", "本机全市场仓库不可用。")
        try:
            result = self._warehouse.repository.query(**args.model_dump())
        except ValueError as error:
            return self._error_outcome(call_id, "query_market_warehouse", "invalid_arguments", str(error))
        # DuckDB results are local, deterministic research material.  Keep a
        # bounded record in task state so a usable warehouse query is not
        # mislabeled as "no business evidence" merely because it has no
        # upstream evidence ID.
        self.records.setdefault("market_warehouse", []).append({
            "period_key": str(result.get("date_ms", args.date_ms)),
            "basis": result.get("basis", "unadjusted_daily_k"),
            "fields": {key: {"value": value, "unit": ""}
                       for key, value in result.items()
                       if key in {"sample", "up", "down", "flat"} and value is not None},
        })
        return {"call_id": call_id, "tool": "query_market_warehouse", "status": "ok", "duplicate": False,
                "error": None, "result": result}

    def _fetch_news(self, args: FetchNewsArgs, call_id: str | None) -> dict:
        """本机新闻时间线：本地读取不计取数预算；内容一律是线索（lead）。"""
        if self._news is None:
            return self._error_outcome(call_id, "fetch_news", "not_configured",
                                       "本机新闻订阅不可用。")
        thscode = args.thscode or (self._identity.get("thscode") or None)
        try:
            items, _total = self._news.list_items(thscode=thscode, limit=args.limit)
        except Exception:
            return self._error_outcome(call_id, "fetch_news", "news_unavailable",
                                       "本机新闻读取失败，请稍后重试。")
        payload_items = [{
            "title": item.title,
            "summary": item.summary,
            "link": item.link,
            "source_name": item.feed_name,
            "published_ms": item.published_ms,
            "related": [{"thscode": link.thscode, "name": link.name,
                         "confidence": link.confidence} for link in item.tickers],
        } for item in items]
        self.records.setdefault("news", []).append({
            "period_key": str(items[0].published_ms) if items else "",
            "basis": "rss_lead",
            "fields": {"news_count": {"value": len(items), "unit": "条"}},
        })
        return {"call_id": call_id, "tool": "fetch_news", "status": "ok", "duplicate": False,
                "error": None,
                "result": {"capability": "fetch_news", "status": "ok",
                           "record_count": len(items), "items": payload_items,
                           "note": "以上是新闻线索：只能作为情绪与事件输入；"
                                   "关键数字与事实必须经第二来源或工具数据交叉验证后才能作为依据。"}}

    async def _search_knowledge(self, args: SearchKnowledgeArgs, call_id: str | None) -> dict:
        """私有知识库检索：本地读取；命中是可核验佐证，需回链文档名与定位。"""
        if self._knowledge is None:
            return self._error_outcome(call_id, "search_knowledge", "not_configured",
                                       "本机知识库不可用。")
        try:
            result = await self._knowledge.search(args.query, top_k=args.top_k)
        except AppError as error:
            return self._error_outcome(call_id, "search_knowledge", error.category,
                                       error.message)
        except Exception:
            return self._error_outcome(call_id, "search_knowledge", "kb_unavailable",
                                       "知识库检索失败，请稍后重试。")
        if not result.hits:
            return self._error_outcome(call_id, "search_knowledge", "not_found",
                                       "知识库中没有匹配内容；可换用其他关键词。")
        self.records.setdefault("knowledge", []).append({
            "period_key": "",
            "basis": "kb_citation",
            "fields": {"hit_count": {"value": len(result.hits), "unit": "块"}},
        })
        return {"call_id": call_id, "tool": "search_knowledge", "status": "ok",
                "duplicate": False, "error": None,
                "result": {"capability": "search_knowledge", "status": "ok",
                           "record_count": len(result.hits),
                           "hits": [hit.model_dump(mode="json") for hit in result.hits],
                           "note": result.note}}


    async def _search_web(self, args: SearchWebArgs, call_id: str | None) -> dict:
        """授权搜索：只返回标题/摘要/链接，不抓网页正文（红线 §4）。"""
        if self._web is None:
            return self._error_outcome(call_id, "search_web", "not_configured",
                                       "本机搜索服务不可用；联网搜索需要用户在设置页接入并授权。")
        try:
            request = WebSearchRequest(query=args.query, count=args.count)
            result = await self._web.search("bocha", request)
        except AppError as error:
            return self._error_outcome(call_id, "search_web", error.category, error.message)
        except (TypeError, ValueError, KeyError):
            return self._error_outcome(call_id, "search_web", "search_failed",
                                       "搜索请求失败，请稍后重试。")
        items = [item.model_dump(mode="json") for item in result.items]
        self.records.setdefault("web_search", []).append({
            "period_key": "",
            "basis": "web_lead",
            "fields": {"hit_count": {"value": len(items), "unit": "条"}},
        })
        return {"call_id": call_id, "tool": "search_web", "status": "ok",
                "duplicate": False, "error": None,
                "result": {"capability": "search_web", "status": "ok",
                           "record_count": len(items), "items": items,
                           "note": "以上是网页搜索线索：只作为情绪与事件输入；"
                                   "关键数字与事实必须经第二来源或工具数据交叉验证后才能作为依据。"}}

    async def _record_advice(self, args: RecordAdviceArgs, call_id: str | None) -> dict:
        """建议登记：完整校验 SuggestionCard 后写台账；失败给出可修复原因。"""
        if self._ledger is None:
            return self._error_outcome(call_id, "record_advice", "not_configured",
                                       "建议台账不可用。")
        from ..advisor.output_models import SuggestionCard
        from ..advisor.suggestion_validation import validate_suggestion
        try:
            card = SuggestionCard.model_validate(args.card)
        except ValidationError as error:
            return self._error_outcome(call_id, "record_advice", "invalid_card",
                                       "建议卡结构不符合契约：" + str(error).split("\n")[0][:220])
        issues = validate_suggestion(card)
        if issues:
            return self._error_outcome(call_id, "record_advice", "invalid_card",
                                       "建议卡未通过内容校验：" + "；".join(issues[:3]))
        entry = self._ledger.register(card.model_dump(mode="json"))
        return {"call_id": call_id, "tool": "record_advice", "status": "ok",
                "duplicate": False, "error": None,
                "result": {"capability": "record_advice", "status": "ok",
                           "advice_id": entry["id"],
                           "note": "建议已登记台账，可按失效条件跟踪；研究参考，不构成投资建议。"}}

    async def _query_advice_ledger(self, args: QueryAdviceLedgerArgs, call_id: str | None) -> dict:
        if self._ledger is None:
            return self._error_outcome(call_id, "query_advice_ledger", "not_configured",
                                       "建议台账不可用。")
        entries = self._ledger.list(status=args.status, limit=args.limit)
        calibration = self._ledger.calibration()
        return {"call_id": call_id, "tool": "query_advice_ledger", "status": "ok",
                "duplicate": False, "error": None,
                "result": {"capability": "query_advice_ledger", "status": "ok",
                           "record_count": len(entries), "entries": entries,
                           "calibration": calibration,
                           "note": "台账含历史建议与结算结果；命中率仅用于校准展示，不构成对未来结果的承诺。"}}

    # -- read / calculate ----------------------------------------------------
    def _read_evidence(self, args: ReadEvidenceArgs, call_id: str | None) -> dict:
        evidence_id = args.evidence_id
        if self._market is None:
            return self._error_outcome(call_id, "read_evidence", "unknown_evidence", "证据存储不可用。")
        if evidence_id not in self.evidence_index:
            return self._error_outcome(call_id, "read_evidence", "unknown_evidence",
                                       "证据 ID 不属于本任务授权证据，已拒绝读取。")
        found = self._market.evidence(evidence_id)
        if found is None:
            return self._error_outcome(call_id, "read_evidence", "unknown_evidence",
                                       "证据不存在或已被清理。")
        meta, records, _gaps = found
        excerpt_records = []
        for record in list(records)[:MAX_READ_RECORDS]:
            fields = {}
            for key, value in list((record.get("fields") or {}).items())[:8]:
                fields[key] = {"value": value.get("value"), "unit": value.get("unit")}
            excerpt_records.append({"period_key": record.get("period_key"),
                                    "basis": record.get("basis"),
                                    "currency": record.get("currency"), "fields": fields})
        while excerpt_records and len(json.dumps(excerpt_records, ensure_ascii=False)) > MAX_EXCERPT_CHARS:
            excerpt_records = excerpt_records[:max(1, len(excerpt_records) // 2)]
        return {"call_id": call_id, "tool": "read_evidence", "status": "ok",
                "duplicate": False, "error": None,
                "result": {"evidence_id": evidence_id, "capability": meta.capability,
                           "basis": meta.basis, "source_time": meta.source_time,
                           "record_count": meta.record_count, "records": excerpt_records}}

    def _calculate_metrics(self, args: CalculateMetricsArgs, call_id: str | None) -> dict:
        metrics = compute_metric_set(self.records, self.group_evidence)
        if args.metrics:
            known = {metric.metric for metric in metrics}
            unknown = [name for name in args.metrics if name not in known]
            if unknown:
                return self._error_outcome(call_id, "calculate_metrics", "unknown_metric",
                                           "请求的指标不在可计算范围内：" + "、".join(sorted(unknown)))
            metrics = [metric for metric in metrics if metric.metric in set(args.metrics)]
        return {"call_id": call_id, "tool": "calculate_metrics", "status": "ok",
                "duplicate": False, "error": None,
                "result": {"metrics": [metric.model_dump() for metric in metrics],
                           "note": "数值由程序计算；正文用 metric_refs 引用，不要自行改写数字。"}}

    # -- helpers -------------------------------------------------------------
    def _spend(self, kind: str) -> bool:
        if int(self.usage.get(kind, 0)) >= int(self.budget.get(f"max_{kind}", 0)):
            return False
        self.usage[kind] = int(self.usage.get(kind, 0)) + 1
        return True

    def _error_outcome(self, call_id: str, tool: str | None, reason: str, message: str) -> dict:
        return {"call_id": call_id, "tool": tool, "status": "error", "duplicate": False,
                "result": None, "error": {"reason": reason, "message": message}}
