"""涨跌停/炸板池同步的守卫测试（第 0 步实测的三条硬约束）。

实测结论（`docs/荐股治理方案/14` §6）在这里被钉成回归：

1. **参数名必须是 `date_ms`**——写成 `date` 会被服务端静默忽略（`code=0` 且 0 条），
   与"当天真的没有涨停"无法区分 → 测试断言请求参数里是 `date_ms`；
2. **三种"空"必须区分**：真没有（`ok_empty`）/ 接口报错（`api_error`，实测跌停池超范围报 `1002`）/
   传输失败（`transport_error`）→ 测试逐一覆盖，并断言汇总里把它们分开计数；
3. **写进去必须真的读得出来**——实测踩过：显式 `BEGIN` 后忘了 `commit()`，
   汇总报告"落库 260 行"而读回来 0 行 → 测试的读回走**另一个只读连接**。
"""

from __future__ import annotations

import pytest

from backend.app.errors import AppError
from backend.app.market_warehouse.limit_pools import LimitPoolSync
from backend.app.market_warehouse.repository import WarehouseRepository

DAY_MS = 1_700_000_000_000
THSCODE = "600519.SH"


class _StubResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _page(items: list[dict], *, total: int, pages: int, size: int = 200) -> dict:
    return {"code": 0, "message": "success", "data": {
        "timestamp": DAY_MS, "item": items,
        "pagination": {"total": total, "pages": pages, "size": size, "page": 1}}}


def _item(thscode: str, **overrides) -> dict:
    row = {"thscode": thscode, "ticker": thscode[:6], "name": "演示", "is_st": False,
           "is_new": False, "last_price": 10.0, "price_change_ratio_pct": 10.0,
           "limit_up_time": "09:34", "limit_up_reason": "演示原因",
           "continue_day_text": "2连板", "continue_day_cnt": 2, "seal_money": 1.0e8,
           "max_seal_money": 2.0e8}
    row.update(overrides)
    return row


class _StubHttp:
    """按 `page` 返回预置响应；记录每次请求以便断言参数。"""

    def __init__(self, pages: list[dict] | None = None, error: Exception | None = None) -> None:
        self._pages = pages or []
        self._error = error
        self.calls: list[dict] = []

    async def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        if self._error is not None:
            raise self._error
        page = int((params or {}).get("page", 1))
        if page - 1 < len(self._pages):
            return _StubResponse(self._pages[page - 1])
        return _StubResponse({"code": 0, "message": "success", "data": {
            "item": [], "pagination": {"total": 0, "pages": page, "size": 200, "page": page}}})


@pytest.fixture
def repository(tmp_path):
    return WarehouseRepository(tmp_path / "market.duckdb")


def _sync(repository, *, key="test-key") -> LimitPoolSync:
    return LimitPoolSync(repository, lambda: (key, True))


# --- 1) 参数契约 -----------------------------------------------------------

async def test_request_uses_date_ms_and_the_credential_header(repository):
    http = _StubHttp([_page([_item(THSCODE)], total=1, pages=1)])
    await _sync(repository).sync_day(http, DAY_MS, "limit_up")

    call = http.calls[0]
    assert "special-data/limit-up-pool" in call["url"]
    assert call["params"]["date_ms"] == DAY_MS, "必须用 date_ms：写成 date 会被静默忽略"
    assert "date" not in call["params"]
    assert call["headers"]["X-api-key"] == "test-key"


async def test_unknown_pool_and_bad_page_size_are_rejected(repository):
    http = _StubHttp()
    with pytest.raises(AppError) as excinfo:
        await _sync(repository).sync_day(http, DAY_MS, "limit_sideways")
    assert excinfo.value.category == "invalid_pool"
    with pytest.raises(AppError):
        await _sync(repository).sync_day(http, DAY_MS, "limit_up", page_size=500)
    assert not http.calls


async def test_missing_credential_is_refused(repository):
    http = _StubHttp()
    with pytest.raises(AppError) as excinfo:
        await _sync(repository, key=None).sync_day(http, DAY_MS, "limit_up")
    assert excinfo.value.category == "credential_unavailable"
    assert not http.calls


# --- 2) 写入必须真的可读（回归：显式 BEGIN 后必须 commit）-------------------

async def test_written_rows_are_visible_through_a_fresh_connection(repository):
    http = _StubHttp([_page([_item("600519.SH"), _item("000001.SZ", is_st=True)],
                            total=2, pages=1)])
    result = await _sync(repository).sync_day(http, DAY_MS, "limit_up")
    assert result["fetched"] == 2

    # 读回走另一个只读连接——这样才能暴露"事务没提交"这类问题
    rows = repository.limit_pool_rows(pool="limit_up", date_ms=DAY_MS)
    assert [row["thscode"] for row in rows] == ["000001.SZ", "600519.SH"]
    assert any(row["is_st"] for row in rows)
    assert rows[0]["fetched_at"]

    ledger = repository.limit_pool_sync_days(pool="limit_up")
    assert len(ledger) == 1
    assert ledger[0]["status"] == "ok" and ledger[0]["fetched"] == 2


async def test_resync_same_day_replaces_rows_instead_of_duplicating(repository):
    sync = _sync(repository)
    await sync.sync_day(_StubHttp([_page([_item("600519.SH")], total=1, pages=1)]), DAY_MS, "limit_up")
    await sync.sync_day(_StubHttp([_page([_item("000001.SZ"), _item("000002.SZ")],
                                        total=2, pages=1)]), DAY_MS, "limit_up")

    rows = repository.limit_pool_rows(pool="limit_up", date_ms=DAY_MS)
    assert [row["thscode"] for row in rows] == ["000001.SZ", "000002.SZ"]


# --- 3) 三种"空"必须区分 ---------------------------------------------------

async def test_silent_empty_is_marked_suspicious_not_conclusive(repository):
    """接口成功但 0 条：可能是真没有，也可能是超出可回溯范围（实测涨停池 ≤2019 静默 0 条）。"""
    http = _StubHttp([_page([], total=0, pages=1)])
    result = await _sync(repository).sync_day(http, DAY_MS, "limit_up")

    assert result["status"] == "ok_empty"
    ledger = repository.limit_pool_sync_days(pool="limit_up")
    assert ledger[0]["status"] == "ok_empty"
    summary = LimitPoolSync.summarize([result])
    assert len(summary["suspicious_empty"]) == 1
    assert "不要" in summary["note"] and "当天没涨停" in summary["note"]


async def test_api_error_is_recorded_with_its_code_and_not_as_empty(repository):
    """实测：跌停池/炸板池对超出可回溯范围的日期报 1002（文案是"参数格式错误"，有误导性）。"""
    http = _StubHttp([{"code": 1002, "message": "Invalid parameter format"}])
    result = await _sync(repository).sync_day(http, DAY_MS, "limit_down")

    assert result["status"] == "api_error" and result["error_code"] == 1002
    assert result["fetched"] == 0
    ledger = repository.limit_pool_sync_days(pool="limit_down")[0]
    assert ledger["status"] == "api_error" and ledger["error_code"] == 1002
    assert repository.limit_pool_rows(pool="limit_down") == []
    summary = LimitPoolSync.summarize([result])
    assert summary["failed_count"] == 1
    assert summary["suspicious_empty"] == [], "接口报错不得被当成可疑空"


async def test_transport_error_is_recorded(repository):
    http = _StubHttp(error=RuntimeError("connection reset"))
    result = await _sync(repository).sync_day(http, DAY_MS, "limit_up")
    assert result["status"] == "transport_error"
    assert "connection reset" in (result["error_message"] or "")
    assert repository.limit_pool_sync_days(pool="limit_up")[0]["status"] == "transport_error"


async def test_summary_counts_the_three_kinds_separately(repository):
    ok = {"date_ms": 1, "pool": "limit_up", "status": "ok", "fetched": 5, "total": 5,
          "pages": 1, "error_code": 0, "error_message": "success"}
    empty = {"date_ms": 2, "pool": "limit_up", "status": "ok_empty", "fetched": 0, "total": 0,
             "pages": 1, "error_code": 0, "error_message": "success"}
    failed = {"date_ms": 3, "pool": "limit_down", "status": "api_error", "fetched": 0, "total": None,
              "pages": 1, "error_code": 1002, "error_message": "Invalid parameter format"}
    summary = LimitPoolSync.summarize([ok, empty, failed])

    assert summary["by_status"] == {"ok": 1, "ok_empty": 1, "api_error": 1}
    assert summary["rows"] == 5 and summary["days_with_data"] == 1
    assert len(summary["suspicious_empty"]) == 1 and summary["failed_count"] == 1


# --- 4) 分页 ---------------------------------------------------------------

async def test_pagination_follows_pages_until_exhausted(repository):
    first = _page([_item(f"60000{index}.SH") for index in range(3)], total=5, pages=2)
    second = _page([_item("600100.SH"), _item("600101.SH"), _item("600102.SH")][:2],
                   total=5, pages=2)
    http = _StubHttp([first, second])

    result = await _sync(repository).sync_day(http, DAY_MS, "limit_up", page_size=3)

    assert result["fetched"] == 5
    assert [call["params"]["page"] for call in http.calls] == [1, 2]
    assert len(repository.limit_pool_rows(pool="limit_up")) == 5


async def test_pagination_stops_on_a_short_page(repository):
    http = _StubHttp([_page([_item(THSCODE)] * 1, total=1, pages=1)])
    result = await _sync(repository).sync_day(http, DAY_MS, "limit_up", page_size=200)
    assert len(http.calls) == 1 and result["pages"] == 1


# --- 5) 深回补必须可断点续跑 ------------------------------------------------
# 实测规模：涨停池回补到 2022 是 1133 个交易日 = 1133 次请求，要跑十几分钟。
# 中途断掉后重跑若把已取过的日子**再发一遍**，既浪费配额也不礼貌（同花顺免费但限流）。

async def test_skip_synced_does_not_re_request_days_already_in_the_ledger(repository):
    sync = _sync(repository)
    await sync.sync_day(_StubHttp([_page([_item(THSCODE)], total=1, pages=1)]), DAY_MS, "limit_up")

    assert (DAY_MS, "limit_up") in sync.synced_keys()
    resume_http = _StubHttp()
    summary = await sync.sync_dates(resume_http, [DAY_MS], ["limit_up"],
                                    already=sync.synced_keys())

    assert resume_http.calls == [], "台账里已有的组合不得再发请求"
    assert summary["by_status"] == {"skipped": 1}, "跳过要如实计数，不假装同步过"
    assert summary["rows"] == 0


async def test_skip_synced_is_per_pool_not_per_day(repository):
    """涨停池已同步不代表跌停池也已同步——跳过必须按 (日期, 池子) 组合判断。"""
    sync = _sync(repository)
    await sync.sync_day(_StubHttp([_page([_item(THSCODE)], total=1, pages=1)]), DAY_MS, "limit_up")

    http = _StubHttp([_page([_item(THSCODE)], total=1, pages=1)])
    summary = await sync.sync_dates(http, [DAY_MS], ["limit_up", "limit_down"],
                                    already=sync.synced_keys())

    assert [call["url"].rsplit("/", 1)[-1] for call in http.calls] == ["limit-down-pool"]
    assert summary["by_status"] == {"skipped": 1, "ok": 1}


async def test_failed_days_stay_in_the_ledger_and_are_retried_when_not_skipped(repository):
    """失败是有价值的证据，不能被静默覆盖；要重试就显式去掉 --skip-synced。"""
    sync = _sync(repository)
    await sync.sync_day(_StubHttp([{"code": 1002, "message": "out of range"}]), DAY_MS, "limit_down")
    assert repository.limit_pool_sync_days(pool="limit_down")[0]["status"] == "api_error"

    http = _StubHttp([_page([_item("000001.SZ", is_st=True)], total=1, pages=1)])
    await sync.sync_day(http, DAY_MS, "limit_down")

    assert repository.limit_pool_sync_days(pool="limit_down")[0]["status"] == "ok"
    assert repository.limit_pool_rows(pool="limit_down", date_ms=DAY_MS)[0]["is_st"]


async def test_sleep_between_requests_is_applied(repository, monkeypatch):
    """礼貌性间隔：深回补时不得无间隔地连续打请求。"""
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("backend.app.market_warehouse.limit_pools.asyncio.sleep", fake_sleep)
    http = _StubHttp([_page([_item(THSCODE)], total=1, pages=1), _page([_item(THSCODE)], total=1, pages=1)])
    await _sync(repository).sync_dates(http, [DAY_MS, DAY_MS + 86_400_000], ["limit_up"],
                                       sleep_seconds=0.2)

    assert slept == [0.2, 0.2]
