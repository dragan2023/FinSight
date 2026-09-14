"""财务数据同步（**人工显式触发**）——落库 point-in-time 财务事实 + 取数台账。

    # 看看这次会取哪些标的、打多少次请求（不写任何东西）
    .\\.venv\\Scripts\\python.exe scripts\\sync_financials.py --dry-run

    # 正式同步（D36 范围：候选池 + 已确认标的）
    .\\.venv\\Scripts\\python.exe scripts\\sync_financials.py

    # 只取指定的几只（调试用）+ 顺带做指标交叉校验
    .\\.venv\\Scripts\\python.exe scripts\\sync_financials.py --symbols 600519.SH,000001.SZ \\
      --indicators-check 2

范围（D36：**不做全市场逐季回补**）由 `--universe` 决定：

| 取值 | 含义 |
|---|---|
| `candidate` | **最新交易日**按 20 日成交额排序的前 N 只（D13 的同一口径，默认 N=200） |
| `confirmed` | 业务库里 `security_identities` 的已确认标的 |
| `candidate+confirmed` | 上面两者的并集（默认） |
| `explicit` | 由 `--symbols` 给出 |

为什么默认只有 ~200 只：全市场 5286 只 × 3 张表 ≈ 1.6 万次请求，而当前因子层**尚无
显著因子**（`13` §1）；先把范围收窄、把 point-in-time 口径做对，比铺量更值（D36）。

**披露口径**（`22` 号文档 §1）：接口的 `report_date_ms` 实测不可信（利润表/现金流量表
整体晚约一年），因此落库时另算一个**闸门** `available_from_ms = min(接口日期, 法定披露截止日)`，
两者都留档、并标明采信了哪一个。详见 `backend/app/market_warehouse/financials.py` 的模块文档。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.main import create_app  # noqa: E402

from backend.app.market_warehouse.financial_sync import FinancialSync, now_iso  # noqa: E402
from backend.app.market_warehouse.repository import WarehouseRepository  # noqa: E402
from backend.app.settings import load_settings  # noqa: E402

CST = timezone(timedelta(hours=8))
DEFAULT_TOP = 200
DEFAULT_YEARS = 10
#: 与 `strategy/service.py` 收单标的实验时用的同一条判据（不另发明一套）。
STOCK_CODE = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")


def day_text(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, CST).strftime("%Y-%m-%d")


def ms_at(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=CST).timestamp() * 1000)


def confirmed_symbols(db_path) -> list[str]:
    """业务库里的"已确认标的"（`security_identities`，`confirmed_source` 记着是谁确认的）。

    **只取股票**：该表里还可能有指数（实测 `881166.TI` 军工装备就躺在里面，
    `asset_type = a-share-index`），指数没有财务报表，取它纯属浪费请求。
    判据沿用项目既有的标的代码正则（`strategy/service.py` 收单标的实验时用同一条），
    不另发明一套。
    """
    try:
        con = sqlite3.connect(db_path)
        try:
            names = [row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            if "security_identities" not in names:
                return []
            columns = [row[1] for row in con.execute("PRAGMA table_info('security_identities')")]
            key = next((name for name in ("thscode", "code", "symbol") if name in columns), None)
            if key is None:
                return []
            codes = [row[0] for row in con.execute(f'SELECT "{key}" FROM security_identities')
                     if row[0]]
            return [code for code in codes if STOCK_CODE.match(code)]
        finally:
            con.close()
    except sqlite3.Error:
        return []


def candidate_symbols(repository: WarehouseRepository, *, as_of_ms: int, top: int) -> list[str]:
    """最新交易日的流动性前 N（按 20 日成交额；与事件研究的分档口径同源）。"""
    rows = repository.cross_section(date_ms=as_of_ms)
    ranked = sorted((row for row in rows if (row.get("turnover_20") or 0) > 0),
                    key=lambda row: row["turnover_20"], reverse=True)
    return [row["thscode"] for row in ranked[:top]]


def resolve_universe(args, repository: WarehouseRepository, *, as_of_ms: int) -> list[tuple[str, str]]:
    """→ [(thscode, 入选理由)]；理由会随 meta 落库，让"范围"可审计。"""
    if args.universe == "explicit":
        return [(code.strip(), "explicit") for code in args.symbols.split(",") if code.strip()]
    picked: list[tuple[str, str]] = []
    if args.universe in ("candidate", "candidate+confirmed"):
        picked += [(code, f"candidate:top{args.top}@{day_text(as_of_ms)}")
                   for code in candidate_symbols(repository, as_of_ms=as_of_ms, top=args.top)]
    if args.universe in ("confirmed", "candidate+confirmed"):
        picked += [(code, "confirmed:security_identities")
                   for code in confirmed_symbols(load_settings().db_path)]
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for code, reason in picked:
        if code not in seen:
            seen.add(code)
            unique.append((code, reason))
    return unique


def universe_hash(pairs: list[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for code, _ in sorted(pairs):
        digest.update(code.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


async def run(args) -> int:
    settings = load_settings()
    repository = WarehouseRepository(settings.market_warehouse_path)
    as_of_ms = repository.latest_daily_ms()
    if as_of_ms is None:
        print("行情库为空：先导入行情数据。")
        return 1
    universe = resolve_universe(args, repository, as_of_ms=as_of_ms)
    if not universe:
        print("范围为空：检查 --universe / --symbols。")
        return 1

    end_ms = ms_at(*datetime.fromtimestamp(as_of_ms / 1000, CST).timetuple()[:3])
    # 数据源的上限是**跨度**（`HISTORY_WINDOW_CEILING_MS = 10 × 365 天`），不是"10 个自然年"：
    # 用「今年 − 10 年 再取 1 月 1 日」会多出闰日与年初那几个月，直接超出上限被拒
    # （实测踩过：`AppError`，而且当时台账只记了异常**类型**、没记消息，白查一轮）。
    start_ms = end_ms - (10 * 365 - args.years_margin_days) * 86_400_000
    ledger = {(row["thscode"], row["statement"]): row for row in repository.financial_sync_ledger()}
    pending = [item for item in universe
               if not args.skip_synced or any(
                   ledger.get((item[0], statement), {}).get("status") not in
                   ("available", "partial") for statement in ("income", "balance", "cashflow"))]

    print(json.dumps({
        "phase": "start", "universe": len(universe), "pending": len(pending),
        "top": args.top, "as_of": day_text(as_of_ms),
        "window": [day_text(start_ms), day_text(end_ms)],
        "requests_planned": len(pending) * 3, "universe_hash": universe_hash(universe)[:16],
        "mode": "dry-run" if args.dry_run else "write"}, ensure_ascii=False))
    if args.dry_run:
        for code, reason in universe[:10]:
            print(f"  {code}  {reason}")
        if len(universe) > 10:
            print(f"  …（其余 {len(universe) - 10} 只略）")
        return 0
    if not pending:
        print("没有待同步标的（--skip-synced 命中全部）。")
        return 0

    run_id = f"financials-{datetime.now(CST).strftime('%Y%m%d-%H%M%S')}"
    fetched_at = now_iso()
    repository.write_financial_meta({
        "version": "financial_pit_v1", "run_id": run_id, "fetched_at": fetched_at,
        "universe_size": len(universe), "universe_hash": universe_hash(universe),
        "universe": json.dumps([code for code, _ in universe]),
        "universe_reasons": json.dumps(dict(universe), ensure_ascii=False),
        "window_start_ms": start_ms, "window_end_ms": end_ms,
        "scope": args.universe, "period": "quarterly",
        "gate_rule": "min(api_report_date, statutory_deadline) + 1 day",
    })

    app = create_app()
    ok = fail = 0
    started = time.monotonic()
    async with app.router.lifespan_context(app):
        sync = FinancialSync(repository, app.state.market)
        semaphore = asyncio.Semaphore(args.concurrency)

        async def one(code: str) -> tuple[str, dict]:
            async with semaphore:
                summary = await sync.sync_symbol(code, start_ms=start_ms, end_ms=end_ms,
                                                 run_id=run_id, fetched_at=fetched_at)
                return code, summary

        for start in range(0, len(pending), args.concurrency):
            batch = pending[start:start + args.concurrency]
            for code, summary in await asyncio.gather(*(one(code) for code, _ in batch)):
                if summary["ok"]:
                    ok += 1
                else:
                    fail += 1
                    print(json.dumps({"phase": "symbol_failed", "thscode": code,
                                      "statements": summary["statements"]},
                                     ensure_ascii=False), flush=True)
            done = min(start + args.concurrency, len(pending))
            elapsed = time.monotonic() - started
            rate = done / elapsed if elapsed else 0
            print(json.dumps({"phase": "progress", "done": done, "total": len(pending),
                              "ok": ok, "fail": fail,
                              "rate_per_min": round(rate * 60, 1),
                              "eta_minutes": round((len(pending) - done) / rate / 60, 1)
                              if rate else -1}, ensure_ascii=False), flush=True)
            if args.sleep:
                await asyncio.sleep(args.sleep)

    status = repository.financial_dataset_status()
    print(json.dumps({"phase": "complete", "ok": ok, "fail": fail, "run_id": run_id,
                      "elapsed_min": round((time.monotonic() - started) / 60, 1),
                      "dataset": status["counts"], "ready": status["ready"]},
                     ensure_ascii=False))
    if args.indicators_check:
        await cross_check(repository, limit=args.indicators_check)
    return 0 if fail == 0 else 1


#: 本地比率 → 厂商指标名（只有 ROA 两者叫法不同，其余同名）。
VENDOR_INDICATOR = {
    "roa": "total_assets_net_ratio",
    "assets_debt_ratio": "assets_debt_ratio",
    "sale_gross_margin": "sale_gross_margin",
    "total_assets_turnover_ratio": "total_assets_turnover_ratio",
}


async def cross_check(repository, *, limit: int) -> None:
    """把**本地算出的比率**与 `indicators` 端点返回的指标对照——验证我们的公式。

    这一步是"自己算"能被接受的前提：如果本地 ROA 与厂商的 `total_assets_net_ratio`
    系统性不符，那么"不采信 indicators"就是个错误决定。**只做抽样校验、不落库**：
    indicators 响应里没有披露日（`normalize_indicators` 给 `disclosed_at=None`），
    因此它无法进入历史 point-in-time 数据集（见 `22` §2）。
    """
    from backend.app.market_warehouse.financials import f_score_inputs

    as_of_ms = repository.latest_daily_ms()
    symbols = sorted({row["thscode"] for row in repository.financial_sync_ledger()
                      if row["status"] in ("available", "partial")})
    app = create_app()
    async with app.router.lifespan_context(app):
        market = app.state.market
        compared = 0
        for code in symbols[:limit]:
            snapshot = repository.financial_snapshot_asof(thscode=code, as_of_ms=as_of_ms)
            current = (snapshot or {}).get("current")
            if not current:
                continue
            local = f_score_inputs(current["facts"])
            year, _, quarter = str(current["period_key"]).partition("-")
            try:
                result = await market.fetch_indicators(code, f"{year}-{quarter.lstrip('Q')}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {code} indicators 取数失败：{type(exc).__name__}", flush=True)
                continue
            vendor: dict[str, float] = {}
            for record in result.records:
                for name, item in (record.get("fields") or {}).items():
                    if item.get("value") not in (None, ""):
                        try:
                            vendor[name] = float(item["value"])
                        except (TypeError, ValueError):
                            continue
            print(f"\n  {code} 报告期 {current['period_key']}（本地口径 vs 厂商指标）")
            for key, vendor_key in VENDOR_INDICATOR.items():
                mine, theirs = local.get(key), vendor.get(vendor_key)
                if mine is None or theirs is None:
                    print(f"    {key:<28} 本地 {mine} ／ 厂商 {theirs}（有缺失，不比较）")
                    continue
                compared += 1
                relative = abs(mine - theirs) / max(1e-9, abs(theirs))
                flag = "✅" if relative <= 0.05 else "⚠ 差异 > 5%"
                print(f"    {key:<28} 本地 {mine:.6f} ／ 厂商 {theirs:.6f}"
                      f"  相对差 {relative:.2%} {flag}")
        print(json.dumps({"phase": "indicators_cross_check", "symbols": len(symbols[:limit]),
                          "compared": compared}, ensure_ascii=False))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="财务数据同步（人工触发）")
    parser.add_argument("--universe", default="candidate+confirmed",
                        choices=("candidate", "confirmed", "candidate+confirmed", "explicit"))
    parser.add_argument("--symbols", default="", help="--universe explicit 时使用，逗号分隔")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help="候选池取前 N 只")
    parser.add_argument("--years", type=int, default=DEFAULT_YEARS, help="回溯年数（接口上限 10 年）")
    parser.add_argument("--years-margin-days", type=int, default=10,
                        help="距数据源跨度上限留出的余量（天）；改小会让请求贴着上限，容易自找拒绝")
    parser.add_argument("--skip-synced", action="store_true",
                        help="台账里三表都已取到的标的直接跳过（断点续跑）")
    parser.add_argument("--concurrency", type=int, default=4,
                        help="并发标的数（每只 3 个请求；限流会被服务端拒绝，故默认保守 4）")
    parser.add_argument("--sleep", type=float, default=0.2, help="每批之间的礼貌间隔（秒）")
    parser.add_argument("--indicators-check", type=int, default=0, metavar="N",
                        help="同步后抽 N 只做本地比率 vs 厂商指标的交叉校验")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写任何东西")
    args = parser.parse_args()
    if args.universe == "explicit" and not args.symbols.strip():
        parser.error("--universe explicit 需要同时给 --symbols")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
