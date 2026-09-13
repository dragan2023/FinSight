"""涨跌停/炸板池同步（**人工显式触发**，不注册任何定时任务）。

第 0 步实测（见 `docs/荐股治理方案/14`）得出的三条硬约束，全部编码在这里：

1. **参数名必须是 `date_ms`**。实测写成 `date` 时服务端**静默忽略**、返回 `code=0` 且 0 条——
   与"当天真的没有涨停"长得一模一样。所以本模块只用 `date_ms`，并把每次取数的
   `status/total/fetched/error_code` 落进 `limit_pool_sync_days` 台账。
2. **可回溯范围是不对称的**：涨停池实测到 2022-01-05 仍有数据（≤2019 静默 0 条）；
   跌停池与炸板池**只支持近期**（2026-09-04 可，2025-06-03 报 `1002`）。
   因此本模块**不假设**任何范围，逐日如实记录结果；`1002` 与"0 条"都照实写进台账。
3. **`code=0` 且 0 条不等于"当天没涨停"**。台账把 `total` 与 `fetched` 都记下来，
   事后可以核对；报告里也会把"静默空"的日子单独列出来（A 股全市场某天 0 只涨停极其罕见，
   因此静默空高度可疑，但不能断言——所以是"可疑清单"，不是"错误清单"）。

红线：付费/配额调用只能由用户显式动作触发。本模块只被 `scripts/sync_limit_pools.py` 使用。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from ..errors import AppError

CST = timezone(timedelta(hours=8))
BASE_URL = "https://fuyao.aicubes.cn"

#: 池子名 → 接口路径。参数契约一致（`date_ms` / `page` / `size` / `sort_field` / `sort_dir`）。
POOLS = {
    "limit_up": "/api/a-share/special-data/limit-up-pool",
    "limit_down": "/api/a-share/special-data/limit-down-pool",
    "limit_break": "/api/a-share/special-data/limit-break-pool",
}

MAX_PAGE_SIZE = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LimitPoolSync:
    """按交易日逐日同步涨跌停/炸板池。每个交易日一次（或多次分页）请求。"""

    def __init__(self, repository, credential_source) -> None:
        #: `credential_source()` 返回 `(secret, ready)`——密钥只在请求头里用，绝不落盘/打印。
        self._repository = repository
        self._credential_source = credential_source

    async def sync_day(self, http: httpx.AsyncClient, date_ms: int, pool: str,
                       *, page_size: int = MAX_PAGE_SIZE) -> dict:
        if pool not in POOLS:
            raise AppError("invalid_pool", f"不支持的池子类型：{pool}", 422)
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise AppError("invalid_pagesize", f"page_size 需在 1–{MAX_PAGE_SIZE} 之间。", 422)
        key, ready = self._credential_source()
        if not key:
            raise AppError("credential_unavailable", "未取得可用的同花顺凭据，无法同步。", 409)
        fetched_at = _now_iso()
        rows: list[dict] = []
        page, pages, total, error_code, error_message = 1, None, None, None, None
        status = "ok"
        while True:
            try:
                response = await http.get(
                    f"{BASE_URL}{POOLS[pool]}",
                    params={"date_ms": date_ms, "page": page, "size": page_size},
                    headers={"X-api-key": key})
                body = response.json()
            except Exception as exc:  # 网络/JSON 层失败：如实记录，不静默
                status, error_message = "transport_error", f"{type(exc).__name__}: {exc}"[:200]
                break
            error_code = body.get("code")
            error_message = body.get("message")
            if error_code != 0:
                # 实测：跌停/炸板池对超出可回溯范围的日期报 1002（文案是"参数格式错误"，有误导性）
                status = "api_error"
                break
            data = body.get("data") or {}
            items = data.get("item") or []
            pagination = data.get("pagination") or {}
            total = pagination.get("total")
            pages = pagination.get("pages")
            rows.extend(item for item in items if item.get("thscode"))
            if not items or (pages is not None and page >= pages) or len(items) < page_size:
                break
            page += 1
            if page > 50:  # 防御：单日不可能有 50 页（10000 只涨停）
                status = "page_limit"
                break
        if status == "ok" and not rows:
            # 静默空：可能是真的没有，也可能是超出可回溯范围。如实标记为可疑，不猜。
            status = "ok_empty"
        self._repository.write_limit_pool_day(
            date_ms=date_ms, pool=pool, rows=rows, status=status, total=total,
            pages=pages, page_size=page_size, error_code=error_code,
            error_message=error_message, fetched_at=fetched_at)
        return {"date_ms": date_ms, "pool": pool, "status": status, "fetched": len(rows),
                "total": total, "pages": page, "error_code": error_code,
                "error_message": error_message}

    async def sync_dates(self, http: httpx.AsyncClient, dates: list[int], pools: list[str],
                         *, page_size: int = MAX_PAGE_SIZE, progress=None) -> dict:
        results = []
        for index, date_ms in enumerate(dates, 1):
            for pool in pools:
                results.append(await self.sync_day(http, date_ms, pool, page_size=page_size))
            if progress is not None:
                progress(index, len(dates), results[-len(pools):])
        return self.summarize(results)

    @staticmethod
    def summarize(results: list[dict]) -> dict:
        """按状态汇总。**逐字区分** ok / ok_empty / api_error / transport_error / page_limit。"""
        summary: dict[str, int] = {}
        days_with_data = 0
        for item in results:
            summary[item["status"]] = summary.get(item["status"], 0) + 1
            if item["fetched"]:
                days_with_data += 1
        suspicious = [item for item in results if item["status"] == "ok_empty"]
        failed = [item for item in results if item["status"] in ("api_error", "transport_error")]
        return {
            "requests": len(results), "by_status": summary, "days_with_data": days_with_data,
            "rows": sum(item["fetched"] for item in results),
            "suspicious_empty": [{"date_ms": item["date_ms"], "pool": item["pool"]}
                                 for item in suspicious],
            "failed": [{"date_ms": item["date_ms"], "pool": item["pool"],
                        "error_code": item["error_code"],
                        "error_message": item["error_message"]} for item in failed[:20]],
            "failed_count": len(failed),
            "note": "ok_empty 表示接口成功但返回 0 条：可能是当天确实没有，也可能是超出可回溯"
                    "范围（实测涨停池 ≤2019 静默返回 0 条）。台账已记 total/fetched，可事后核对；"
                    "**不要**把 ok_empty 当成'当天没涨停'的结论。",
        }
