"""HTTP client for Tushare Pro as an optional supplement source (plan 04 §2.4).

Official endpoint https://api.tushare.pro with the token inside the request
body; HTTPS only and TLS failures never downgrade to plaintext. Only the
fields needed for the confirmed entity/period are requested. Layered checks
mirror the hexin client: HTTP status -> business envelope -> fields/items
alignment (a short or long row rejects the whole payload instead of guessing).
Transient failures retry at most twice; permission and parameter rejections
never retry. Permission gates and rate limits are recognised from the official
documented error patterns, never presented as a permanent quota promise, and
upstream messages are never echoed back to the user.
"""

from __future__ import annotations

import asyncio
import json
from typing import Callable

import httpx

from ..errors import AppError
from .models import (
    ConnectionFailed,
    InvalidUpstreamResponse,
    MarketDataError,
    PermissionDenied,
    RateLimited,
    SourceDisabled,
    SourceNotConfigured,
    UpstreamTimeout,
)

TUSHARE_ORIGIN = "https://api.tushare.pro"
TUSHARE_PATH = "/"

RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}
RETRY_CEILING_SECONDS = 10.0
RETRY_BACKOFF_SECONDS = 0.5

_CODE_OK = 0
_VALID_FIELD_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_,")
_DATE_LEN = 8

# 官方文档化的拒绝文案特征（积分/权限、频控）。只用于归类缺口原因，不作为永久承诺。
_PERMISSION_HINTS = ("权限", "积分", "没有该接口", "尚未开通", "您暂时没有")
_RATE_HINTS = ("最多", "频率", "稍后再", "每天", "每分钟")


class TusharePermissionDenied(MarketDataError):
    gap_reason = "permission_denied"

    def __init__(self) -> None:
        super().__init__("当前 Token 对该接口没有权限（积分门槛或单独授权），已按缺口处理。", 403)


class TushareRateLimited(MarketDataError):
    gap_reason = "rate_limited"

    def __init__(self) -> None:
        super().__init__("已达到该接口的频率或次数限制，请稍后重试。", 429, True)


class BudgetExhausted(AppError):
    """Spent via the shared fetch-budget hook before any request goes out."""

    def __init__(self) -> None:
        super().__init__("budget_exhausted", "取数预算已用尽，本次未发起请求。", 429)


def _validation(message: str) -> AppError:
    return AppError("validation", message, 422)


def validate_ts_code(ts_code: str) -> str:
    from .identity import ts_code_from_thscode

    return ts_code_from_thscode(ts_code)


def validate_date_compact(value: str, *, label: str) -> str:
    text = str(value or "").strip()
    if len(text) != _DATE_LEN or not text.isdigit():
        raise _validation(label + "需要形如 20231231 的 8 位日期。")
    return text


def validate_fields(fields: str) -> str:
    cleaned = (fields or "").strip().strip(",")
    if not cleaned:
        raise _validation("fields 不能为空；补充源只请求必要字段。")
    for name in cleaned.split(","):
        name = name.strip()
        if not name or not set(name) <= _VALID_FIELD_CHARS:
            raise _validation("fields 含有无法解释的字段名。")
    return ",".join(name.strip() for name in cleaned.split(","))


class TushareClient:
    """Capability-oriented Tushare client; token travels in the body only."""

    def __init__(self, http: httpx.AsyncClient, *,
                 credential_source: Callable[[], tuple[str | None, bool]],
                 sleep: Callable[[float], object] = asyncio.sleep,
                 max_retries: int = 2,
                 budget_spend: Callable[[], bool] | None = None) -> None:
        self._http = http
        self._credential_source = credential_source
        self._sleep = sleep
        self._max_retries = max_retries
        self._budget_spend = budget_spend

    def _credential(self) -> str:
        token, enabled = self._credential_source()
        if not token:
            raise SourceNotConfigured()
        if not enabled:
            raise SourceDisabled()
        return token

    async def call(self, api_name: str, params: dict, fields: str) -> dict:
        """One official API call; returns {"fields": [...], "rows": [dict]}.

        行内 None 保持 None（缺失即缺口候选），行长度与 fields 不对齐时整体拒绝。
        """
        token = self._credential()
        clean_fields = validate_fields(fields)
        if not isinstance(params, dict):
            raise _validation("params 必须是请求参数对象。")
        body = {"api_name": api_name, "token": token, "params": params, "fields": clean_fields}
        attempts = 0
        while True:
            attempts += 1
            if self._budget_spend is not None and not self._budget_spend():
                raise BudgetExhausted()
            try:
                response = await self._http.post(TUSHARE_ORIGIN + TUSHARE_PATH, json=body,
                                                 headers={"Accept": "application/json"})
            except httpx.TimeoutException:
                if attempts >= self._max_retries + 1:
                    raise UpstreamTimeout() from None
                await self._sleep(RETRY_BACKOFF_SECONDS * attempts)
                continue
            except httpx.TransportError:
                if attempts >= self._max_retries + 1:
                    raise ConnectionFailed() from None
                await self._sleep(RETRY_BACKOFF_SECONDS * attempts)
                continue
            except asyncio.CancelledError:
                raise
            if response.status_code in (401, 403):
                raise PermissionDenied()
            if response.status_code in RETRYABLE_HTTP_STATUS or response.status_code >= 500:
                if attempts >= self._max_retries + 1:
                    if response.status_code == 429:
                        raise RateLimited()
                    raise InvalidUpstreamResponse("数据源持续返回不可用状态。")
                await self._sleep(self._retry_delay(response))
                continue
            if response.status_code != 200:
                raise InvalidUpstreamResponse()
            try:
                envelope = response.json()
            except (json.JSONDecodeError, ValueError):
                raise InvalidUpstreamResponse() from None
            if not isinstance(envelope, dict) or not isinstance(envelope.get("code"), int):
                raise InvalidUpstreamResponse()
            code = envelope["code"]
            if code != _CODE_OK:
                classified = self._classify_rejection(code, envelope.get("msg"))
                if isinstance(classified, TushareRateLimited) and attempts <= self._max_retries:
                    await self._sleep(self._retry_delay(response))
                    continue
                raise classified
            data = envelope.get("data")
            if not isinstance(data, dict):
                raise InvalidUpstreamResponse()
            return self._validate_payload(data, clean_fields)

    @staticmethod
    def _classify_rejection(code: int, msg: object) -> MarketDataError:
        text = str(msg or "")
        if any(hint in text for hint in _PERMISSION_HINTS):
            return TusharePermissionDenied()
        if any(hint in text for hint in _RATE_HINTS):
            return TushareRateLimited()
        error = InvalidUpstreamResponse("接口返回了未成功的业务状态。")
        error.gap_reason = "invalid_response"
        return error

    @staticmethod
    def _validate_payload(data: dict, fields: str) -> dict:
        raw_fields = data.get("fields")
        items = data.get("items")
        if not isinstance(raw_fields, list) or not raw_fields \
                or not all(isinstance(f, str) for f in raw_fields):
            raise InvalidUpstreamResponse("响应缺少可解释的字段列表。")
        if not isinstance(items, list):
            raise InvalidUpstreamResponse("响应缺少数据行。")
        names = [f for f in raw_fields]
        for row in items:
            if not isinstance(row, list) or len(row) != len(names):
                raise InvalidUpstreamResponse("fields 与 items 未对齐，未采用任何数值。")
        requested = set(fields.split(","))
        if not requested <= set(names):
            raise InvalidUpstreamResponse("响应字段与请求字段不一致。")
        return {"fields": names, "rows": [dict(zip(names, row)) for row in items]}

    @staticmethod
    def _retry_delay(response: httpx.Response) -> float:
        raw = response.headers.get("Retry-After")
        if raw:
            try:
                return min(float(raw), RETRY_CEILING_SECONDS)
            except ValueError:
                pass
        return RETRY_BACKOFF_SECONDS

    # -- capability wrappers (only the fields the pipeline needs) -------------
    async def stock_basic(self, ts_code: str) -> list[dict]:
        code = validate_ts_code(ts_code)
        payload = await self.call(
            "stock_basic", {"ts_code": code},
            "ts_code,symbol,name,area,industry,market,list_date")
        return payload["rows"]

    async def daily_basic(self, ts_code: str, *, start_date: str, end_date: str) -> list[dict]:
        code = validate_ts_code(ts_code)
        payload = await self.call(
            "daily_basic",
            {"ts_code": code, "start_date": validate_date_compact(start_date, label="开始日期"),
             "end_date": validate_date_compact(end_date, label="结束日期")},
            "ts_code,trade_date,close,pe,pe_ttm,pb,ps_ttm,dv_ratio,total_mv")
        return payload["rows"]

    async def statement(self, api_name: str, ts_code: str, *, start_date: str,
                        end_date: str, report_type: str = "1") -> list[dict]:
        if api_name not in ("income", "balancesheet", "cashflow"):
            raise _validation("不支持的报表接口。")
        code = validate_ts_code(ts_code)
        field_sets = {
            "income": "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,update_flag,"
                      "total_revenue,revenue,operate_cost,operate_profit,total_profit,income_tax,"
                      "n_income,n_income_attr_p,basic_eps",
            "balancesheet": "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,update_flag,"
                            "total_assets,total_cur_assets,total_nca,money_cap,accounts_receiv,"
                            "total_liab,total_hldr_eqy_exc_min_int",
            "cashflow": "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,update_flag,"
                        "n_cashflow_act,n_cashflow_inv_act,n_cash_flows_fnc_act,"
                        "c_pay_acq_const_fiolta,c_pay_distr_pft_or_shrliq,n_incr_cash_cash_equ_nca",
        }
        payload = await self.call(
            api_name,
            {"ts_code": code, "start_date": validate_date_compact(start_date, label="开始日期"),
             "end_date": validate_date_compact(end_date, label="结束日期"),
             "report_type": str(report_type)},
            field_sets[api_name])
        return payload["rows"]

    async def fina_indicator(self, ts_code: str, *, start_date: str, end_date: str) -> list[dict]:
        code = validate_ts_code(ts_code)
        payload = await self.call(
            "fina_indicator",
            {"ts_code": code, "start_date": validate_date_compact(start_date, label="开始日期"),
             "end_date": validate_date_compact(end_date, label="结束日期")},
            "ts_code,ann_date,end_date,update_flag,grossprofit_margin,netprofit_margin,roe,"
            "debt_to_assets,current_ratio,quick_ratio,ocf_to_or")
        return payload["rows"]

    async def fina_audit(self, ts_code: str, *, start_date: str, end_date: str) -> list[dict]:
        code = validate_ts_code(ts_code)
        payload = await self.call(
            "fina_audit",
            {"ts_code": code, "start_date": validate_date_compact(start_date, label="开始日期"),
             "end_date": validate_date_compact(end_date, label="结束日期")},
            "ts_code,ann_date,end_date,audit_agency,audit_result,audit_opinion")
        return payload["rows"]

    async def fina_mainbz(self, ts_code: str, *, start_date: str, end_date: str,
                          type_x: str = "1") -> list[dict]:
        code = validate_ts_code(ts_code)
        payload = await self.call(
            "fina_mainbz",
            {"ts_code": code, "start_date": validate_date_compact(start_date, label="开始日期"),
             "end_date": validate_date_compact(end_date, label="结束日期"), "type": str(type_x)},
            "ts_code,end_date,type_x,curr_type,mainbz_income,mainbz_cost,mainbz_ratio")
        return payload["rows"]

    async def disclosure_date(self, ts_code: str, *, start_date: str, end_date: str) -> list[dict]:
        code = validate_ts_code(ts_code)
        payload = await self.call(
            "disclosure_date",
            {"ts_code": code, "start_date": validate_date_compact(start_date, label="开始日期"),
             "end_date": validate_date_compact(end_date, label="结束日期")},
            "ts_code,end_date,pre_date,actual_date,modify_date")
        return payload["rows"]


# --- 归一化：Tushare 行 -> 研究级记录 --------------------------------------
# null != 0 != 负值：空字段生成缺口候选；PERCENT 记法转比率；同一报告期多行
# （修订版本）保留最新披露版本并记录折叠数；report_type 决定合并/母公司与
# 累计/单季口径，缺失时口径记 unknown，绝不猜测。

from decimal import Decimal as _Decimal  # noqa: E402
from .field_dictionary import FieldKind, tushare_field_map  # noqa: E402
from .normalization import (  # noqa: E402
    BASIS_DAILY_BAR,
    BASIS_QUARTERLY_UNKNOWN,
    BASIS_SNAPSHOT,
    BASIS_UNKNOWN,
    GapCandidate,
    NormalizedField,
    NormalizedRecord,
    to_decimal,
    unit_for,
)

BASIS_SUPPLEMENT_STATEMENT = "supplement_statement"

REPORT_TYPE_BASIS = {
    "1": ("consolidated", "cumulative"),
    "2": ("consolidated", "single_quarter"),
    "3": ("consolidated", "single_quarter"),
    "4": ("consolidated_adjusted", "cumulative"),
    "5": ("consolidated_adjusted", "cumulative"),
    "6": ("parent", "cumulative"),
    "7": ("parent", "single_quarter"),
}


def iso_date(compact: object) -> str | None:
    text = str(compact or "").strip()
    if len(text) == 8 and text.isdigit():
        return text[0:4] + "-" + text[4:6] + "-" + text[6:8]
    return None


def consolidation_period_basis(report_type: object) -> tuple[str, str]:
    return REPORT_TYPE_BASIS.get(str(report_type or "").strip(), ("unknown", "unknown"))


def _tushare_period(api_name: str, row: dict) -> str | None:
    key = "trade_date" if api_name == "daily_basic" else "end_date"
    return iso_date(row.get(key))


def _tushare_disclosed(row: dict) -> str | None:
    return iso_date(row.get("f_ann_date") or row.get("ann_date"))


def _tushare_basis(api_name: str, row: dict) -> str:
    if api_name == "daily_basic":
        return BASIS_DAILY_BAR
    if api_name == "stock_basic":
        return BASIS_SNAPSHOT
    if api_name in ("income", "balancesheet", "cashflow"):
        period = iso_date(row.get("end_date")) or ""
        return BASIS_QUARTERLY_UNKNOWN if not period.endswith("12-31") else "annual_report"
    return BASIS_UNKNOWN


def normalize_tushare_rows(api_name: str, rows: list[dict], ts_code: str,
                           dimension: str) -> tuple[list[NormalizedRecord], list[GapCandidate], int]:
    """Deterministic normalization for Tushare rows of one confirmed api_name."""
    spec_map = tushare_field_map(api_name)
    by_period: dict[str, tuple[dict, str]] = {}
    duplicates = 0
    for row in rows or []:
        period = _tushare_period(api_name, row)
        if period is None:
            continue
        disclosed = _tushare_disclosed(row) or ""
        if period in by_period:
            duplicates += 1
            if disclosed < by_period[period][1]:
                continue
        by_period[period] = (row, disclosed)
    records: list[NormalizedRecord] = []
    gaps: list[GapCandidate] = []
    for period, (row, _) in sorted(by_period.items(), reverse=True):
        currency = str(row.get("curr_type") or "CNY")
        fields: dict[str, NormalizedField] = {}
        for upstream, raw in row.items():
            mapping = spec_map.get(upstream)
            if mapping is None:
                continue
            kind = mapping.kind
            value = to_decimal(raw)
            if value is not None and kind is FieldKind.PERCENT:
                value = value / _Decimal(100)
            unit = unit_for(kind)
            display = None
            if kind is FieldKind.DATE:
                display = iso_date(raw)
            elif value is not None:
                display = format(value, "f")
            normalized = NormalizedField(
                source_field=upstream, label=mapping.label, kind=kind, unit=unit,
                raw_value=None if raw is None else str(raw),
                value=display,
                currency=currency if kind in (FieldKind.AMOUNT, FieldKind.PRICE) else None)
            fields[mapping.canonical] = normalized
            if value is None and raw is None:
                gaps.append(GapCandidate(
                    field=mapping.canonical, dimension=dimension, period_key=period,
                    reason="not_published"))
        records.append(NormalizedRecord(
            thscode=ts_code, period_key=period, period_end=period,
            disclosed_at=_tushare_disclosed(row), basis=_tushare_basis(api_name, row),
            currency=currency if api_name in ("income", "balancesheet", "cashflow", "fina_mainbz") else None,
            fields=fields))
    return records, gaps, duplicates
