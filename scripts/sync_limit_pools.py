"""同步涨跌停/炸板池（**人工显式触发**，不做后台定时）。

第 0 步实测结论（见 `docs/荐股治理方案/14`）：**可回溯范围是不对称的**——
涨停池实测到 2022-01-05 仍有数据，跌停池与炸板池只支持近期。
所以本脚本默认只同步**最近 N 个交易日**，深历史回补必须显式指定区间。

    # 最近 250 个交易日（默认）：三种池子都取
    .\\.venv\\Scripts\\python.exe scripts\\sync_limit_pools.py --days 250

    # 只回补涨停池到 2022（实测可回溯；约 1130 个交易日 = 1130 次请求）
    .\\.venv\\Scripts\\python.exe scripts\\sync_limit_pools.py `
        --from 2022-01-04 --to 2026-09-04 --pools limit_up

    # 先干跑看规模，不发请求
    .\\.venv\\Scripts\\python.exe scripts\\sync_limit_pools.py --days 20 --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402
import httpx  # noqa: E402

from backend.app.api_config.repository import ApiRepository  # noqa: E402
from backend.app.api_config.service import ApiConfigService  # noqa: E402
from backend.app.credentials import CredentialRoot  # noqa: E402
from backend.app.market_data.service import MarketDataService  # noqa: E402
from backend.app.market_warehouse.limit_pools import POOLS, LimitPoolSync  # noqa: E402
from backend.app.market_warehouse.repository import WarehouseRepository  # noqa: E402
from backend.app.model_config.repository import ModelRepository  # noqa: E402
from backend.app.model_config.service import ModelService  # noqa: E402
from backend.app.settings import load_settings  # noqa: E402
from backend.app.storage import Storage  # noqa: E402

CST = timezone(timedelta(hours=8))


def trading_days(db_path: Path) -> list[int]:
    db = duckdb.connect(str(db_path), read_only=True)
    try:
        return [row[0] for row in db.execute(
            "SELECT DISTINCT date_ms FROM daily_k ORDER BY date_ms").fetchall()]
    finally:
        db.close()


def day_ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=CST).timestamp() * 1000)


def day_text(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, CST).strftime("%Y-%m-%d")


def select_dates(days: list[int], *, from_text: str, to_text: str, count: int) -> list[int]:
    if from_text or to_text:
        start = day_ms(from_text) if from_text else days[0]
        end = day_ms(to_text) if to_text else days[-1]
        return [ms for ms in days if start <= ms <= end]
    return days[-count:]


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="同步涨跌停/炸板池（人工触发）")
    parser.add_argument("--days", type=int, default=250, help="最近 N 个交易日（默认 250）")
    parser.add_argument("--from", dest="from_text", default="", help="起始交易日 YYYY-MM-DD")
    parser.add_argument("--to", dest="to_text", default="", help="结束交易日 YYYY-MM-DD")
    parser.add_argument("--pools", default="limit_up,limit_down,limit_break",
                        help=f"逗号分隔，可选：{','.join(POOLS)}")
    parser.add_argument("--page-size", type=int, default=200, help="单页条数（上限 200）")
    parser.add_argument("--dry-run", action="store_true", help="只报告规模，不发请求")
    parser.add_argument("--proxy", default="", help="如需代理，如 http://127.0.0.1:7890")
    args = parser.parse_args()

    pools = [item.strip() for item in args.pools.split(",") if item.strip()]
    unknown = [item for item in pools if item not in POOLS]
    if unknown:
        print(f"不支持的池子：{unknown}；可选 {list(POOLS)}")
        return 1

    settings = load_settings()
    days = trading_days(settings.market_warehouse_path)
    if not days:
        print("本地行情仓库为空：请先同步全市场日 K（涨跌停池的交易日列表来自它）。")
        return 1
    dates = select_dates(days, from_text=args.from_text, to_text=args.to_text, count=args.days)
    if not dates:
        print("按给定区间没有匹配到交易日。")
        return 1
    print(f"待同步：{len(dates)} 个交易日 × {len(pools)} 个池子 = {len(dates) * len(pools)} 次请求"
          f"（{day_text(dates[0])} → {day_text(dates[-1])}）")
    print(f"池子：{pools}")
    if args.dry_run:
        print("（--dry-run：未发请求）")
        return 0

    storage = Storage(settings.db_path)
    root = CredentialRoot(storage, settings)
    models = ModelService(ModelRepository(storage), root, settings)
    integrations = ApiConfigService(root, ApiRepository(storage), settings, models)
    repository = WarehouseRepository(settings.market_warehouse_path)
    client_kwargs = {"trust_env": False}
    if args.proxy:
        client_kwargs = {"proxy": args.proxy, "trust_env": False}

    def progress(index: int, total: int, last: list[dict]) -> None:
        if index % 10 == 0 or index == total:
            brief = "，".join(f"{item['pool']}:{item['status']}({item['fetched']})" for item in last)
            print(f"  {index}/{total} {day_text(last[0]['date_ms'])} {brief}", flush=True)

    async with httpx.AsyncClient(follow_redirects=False,
                                 timeout=httpx.Timeout(40, connect=15), **client_kwargs) as http:
        market = MarketDataService(integrations, storage, http)
        sync = LimitPoolSync(repository, market.credential_source)
        summary = await sync.sync_dates(http, dates, pools, page_size=args.page_size,
                                        progress=progress)

    print("\n== 汇总 ==")
    print(f"  请求 {summary['requests']} 次，落库 {summary['rows']} 行，"
          f"有数据的交易日-池子组合 {summary['days_with_data']}")
    print(f"  按状态：{summary['by_status']}")
    if summary["suspicious_empty"]:
        print(f"  ⚠ 接口成功但 0 条（可疑，**不等于**当天没涨停）：{len(summary['suspicious_empty'])} 个"
              f"，最早的 3 个 {summary['suspicious_empty'][:3]}")
    if summary["failed_count"]:
        print(f"  ⚠ 取数失败：{summary['failed_count']} 个，样例 {summary['failed'][:3]}")
    print(f"\n  {summary['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
