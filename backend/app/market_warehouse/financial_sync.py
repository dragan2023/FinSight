"""财务数据同步（**人工显式触发**，D32/D36）。

与 `limit_pools.py` 的分工不同：那份直接打 HTTP（涨跌停池没有走 `MarketService`），
这份**复用 `MarketService.fetch_statement`**——因为它把鉴权、限流、重试、规范化
（字段白名单 + 单位 + gap 记录）都做好了，自己再写一遍必然与顾问链的口径漂移。

落库形状见 `repository.write_financial_symbol`：长表 `financial_facts` + 台账
`financial_sync_symbols`，**闸门**（`available_from_ms` / `available_basis`）由
`financials.available_from_ms` 算出——这一层是"能不能用于历史评价"的关键，见 `22` §1。

红线：**只被 `scripts/sync_financials.py` 调用**；不在应用启动、定时任务或顾问链里触发。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .financials import (BASIS_API_REPORT_DATE, day_text_ms, available_from_ms, iso_text_ms)

CST = timezone(timedelta(hours=8))
STATEMENTS = ("income", "balance", "cashflow")
#: 视为"取到了"的状态（`partial` 也算：部分字段缺失由 gaps 如实记录）。
OK_STATUSES = ("available", "partial")


class FinancialSync:
    def __init__(self, repository, market) -> None:
        self._repository = repository
        self._market = market

    async def sync_symbol(self, thscode: str, *, start_ms: int, end_ms: int, run_id: str,
                          fetched_at: str, refresh: bool = True) -> dict:
        """同步一只标的三张报表；**逐表独立**——一张表失败不影响另外两张。"""
        summary = {"thscode": thscode, "ok": False, "statements": {}}
        for statement in STATEMENTS:
            try:
                result = await self._market.fetch_statement(
                    statement, thscode, period="quarterly", start_ms=start_ms, end_ms=end_ms,
                    refresh=refresh)
            except Exception as exc:  # noqa: BLE001 - 单表失败必须能被台账如实记下
                # **记下消息本身**，不只记类型：实测踩过"台账写着 AppError、原因无从查起"，
                # 而原因是时间窗口超上限的一句明确提示。台账的价值就在于事后能查。
                self._repository.write_financial_symbol(
                    thscode=thscode, statement=statement, rows=[],
                    ledger={"status": "transport_error", "periods": 0, "fields": 0, "gaps": 0,
                            "requested_start_ms": start_ms, "requested_end_ms": end_ms,
                            "api_basis": None, "error_code": None,
                            "error_message": f"{type(exc).__name__}: {exc}"[:300],
                            "run_id": run_id, "fetched_at": fetched_at})
                summary["statements"][statement] = {
                    "status": "transport_error", "periods": 0, "fields": 0,
                    "error": f"{type(exc).__name__}: {exc}"[:120]}
                continue

            status = result.status.value
            rows, periods = self._to_rows(thscode, statement, result.records, run_id, fetched_at)
            self._repository.write_financial_symbol(
                thscode=thscode, statement=statement, rows=rows,
                ledger={"status": status, "periods": len(periods), "fields": len(rows),
                        "gaps": len(result.gaps), "requested_start_ms": start_ms,
                        "requested_end_ms": end_ms,
                        "api_basis": (result.records[0]["basis"] if result.records else None),
                        "error_code": None,
                        "error_message": None if status in OK_STATUSES else status,
                        "run_id": run_id, "fetched_at": fetched_at})
            summary["statements"][statement] = {"status": status, "periods": len(periods),
                                                "fields": len(rows), "gaps": len(result.gaps)}
        summary["ok"] = all(item["status"] in OK_STATUSES
                            for item in summary["statements"].values())
        return summary

    @staticmethod
    def _to_rows(thscode: str, statement: str, records: list[dict], run_id: str,
                 fetched_at: str) -> tuple[list[dict], set[str]]:
        """规范化记录 → `financial_facts` 行。**缺值不填 0**：没有值就没有那一行。"""
        rows: list[dict] = []
        periods: set[str] = set()
        for record in records:
            period_key = record.get("period_key")
            if not period_key or not record.get("period_end"):
                continue
            period_end_ms = day_text_ms(record["period_end"])
            disclosed_ms = iso_text_ms(record.get("disclosed_at"))
            gate, basis = available_from_ms(period_end_ms=period_end_ms,
                                            api_disclosed_ms=disclosed_ms)
            periods.add(period_key)
            for field, item in (record.get("fields") or {}).items():
                value = item.get("value")
                if value in (None, ""):
                    continue
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                rows.append({
                    "thscode": thscode, "statement": statement, "period_key": period_key,
                    "period_end_ms": period_end_ms, "field": field, "value": number,
                    "unit": item.get("unit"), "currency": item.get("currency"),
                    "disclosed_at_ms": disclosed_ms,
                    "available_from_ms": gate, "available_basis": basis,
                    "api_basis": record.get("basis") or "unknown",
                    "source": "ths:financials/statements", "run_id": run_id,
                    "fetched_at": fetched_at})
        return rows, periods


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["BASIS_API_REPORT_DATE", "FinancialSync", "STATEMENTS", "now_iso"]
