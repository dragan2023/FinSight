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
from ..market_data.normalization import BASIS_DAILY_BAR
from ..research.evidence_models import DataGap, DataResult, RequestedScope, ResultStatus
from .json_parser import JsonRejected, parse_tool_arguments

TOOL_NAMES = ("fetch_financials", "fetch_prices", "fetch_valuation",
              "read_evidence", "calculate_metrics")

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
    "read_evidence": ReadEvidenceArgs,
    "calculate_metrics": CalculateMetricsArgs,
}

FETCH_TOOLS = ("fetch_financials", "fetch_prices", "fetch_valuation")

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
                 now: datetime | None = None) -> None:
        self._market = market
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

    # -- model-driven entry --------------------------------------------------
    async def execute(self, call: dict) -> dict:
        """执行一次模型工具调用；坏参数向对应 call id 返回结构化错误。"""
        call_id = str(call.get("id") or "")
        function = call.get("function") or {}
        name = function.get("name")
        if name not in TOOL_NAMES:
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
        if name in FETCH_TOOLS:
            group = self._group_of(name, args)
            key = f"{name}:{_canonical_args(args.model_dump())}"
            cached = self._cache.get(key)
            if cached is not None:
                return {**cached, "call_id": call_id, "duplicate": True}
            if key in self._failed_keys:
                return self._error_outcome(call_id, name, "repeat_failed",
                                           "该调用此前已失败一次，不再重复执行；请直接基于已有证据继续分析。")
            if not self._identity.get("thscode"):
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
        return "valuation"

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
