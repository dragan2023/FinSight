"""User-authorized full-market financial-statements sync (income/balance/cashflow).

Per-symbol quarterly range mode (superset of annual: FY rows carry fiscal_period="FY").
Resumable via a JSONL progress log; credentials stay in local config and are never printed.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.main import create_app  # noqa: E402
from app.market_data.models import RateLimited, SourceNotConfigured  # noqa: E402

START_MS = 1473177600000  # 2016-09-02 00:00 Asia/Shanghai，10 年窗口上限内的最早覆盖（FY2016 年报披露起）
END_MS = 1788451200000    # last trading day in the research window
CONCURRENCY = 6
SYMBOL_RETRY_ROUNDS = 3
RATE_LIMIT_COOLDOWN_S = 30.0
FAILURE_ABORT_STREAK = 200
PROGRESS = PROJECT_ROOT / "logs" / "financials_sync_progress.jsonl"


def _symbols() -> list[str]:
    db = duckdb.connect(str(PROJECT_ROOT / "data/market/finsight_market.duckdb"), read_only=True)
    try:
        rows = db.execute("SELECT DISTINCT thscode FROM daily_k ORDER BY thscode").fetchall()
    finally:
        db.close()
    return [r[0] for r in rows]


def _done_symbols() -> set[str]:
    done: set[str] = set()
    if not PROGRESS.exists():
        return done
    for line in PROGRESS.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("ok"):
            done.add(row["thscode"])
    return done


async def _sync_symbol(market, sym: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        for attempt in range(1, SYMBOL_RETRY_ROUNDS + 1):
            try:
                results = []
                for statement in ("income", "balance", "cashflow"):
                    results.append(await market.fetch_statement(
                        statement, sym, period="quarterly",
                        start_ms=START_MS, end_ms=END_MS, refresh=True))
                statuses = [r.status.value for r in results]
                counts = [len(r.records) for r in results]
                gaps = sum(len(r.gaps) for r in results)
                ok = all(s in ("available", "partial") for s in statuses)
                return {"thscode": sym, "ok": ok, "statuses": statuses,
                        "records": counts, "gaps": gaps, "attempts": attempt}
            except RateLimited:
                if attempt == SYMBOL_RETRY_ROUNDS:
                    return {"thscode": sym, "ok": False, "error": "rate_limited_persistent"}
                await asyncio.sleep(RATE_LIMIT_COOLDOWN_S * attempt)
            except SourceNotConfigured:
                return {"thscode": sym, "ok": False, "error": "source_not_configured"}
            except Exception as exc:  # noqa: BLE001 - single-symbol failure must not stop the batch
                name = type(exc).__name__
                if attempt == SYMBOL_RETRY_ROUNDS:
                    return {"thscode": sym, "ok": False, "error": name}
                await asyncio.sleep(2.0 * attempt)
    return {"thscode": sym, "ok": False, "error": "unreachable"}


async def main() -> None:
    symbols = _symbols()
    done = _done_symbols()
    pending = [s for s in symbols if s not in done]
    print(json.dumps({"phase": "start", "universe": len(symbols), "already_done": len(done),
                      "pending": len(pending), "start_ms": START_MS, "end_ms": END_MS,
                      "concurrency": CONCURRENCY}), flush=True)
    if not pending:
        print(json.dumps({"phase": "nothing_to_do"}), flush=True)
        return
    app = create_app()
    async with app.router.lifespan_context(app):
        market = app.state.market
        sem = asyncio.Semaphore(CONCURRENCY)
        streak_fail = 0
        ok_n = fail_n = 0
        t0 = time.monotonic()
        PROGRESS.parent.mkdir(exist_ok=True)
        with PROGRESS.open("a", encoding="utf-8") as log:
            for sym in pending:
                row = await _sync_symbol(market, sym, sem)
                row["ts"] = datetime.now(timezone.utc).isoformat()
                log.write(json.dumps(row, ensure_ascii=False) + "\n")
                log.flush()
                if row["ok"]:
                    ok_n += 1
                    streak_fail = 0
                else:
                    fail_n += 1
                    streak_fail += 1
                    if row.get("error") == "source_not_configured":
                        print(json.dumps({"phase": "abort", "reason": "credentials_missing"}), flush=True)
                        return
                    if streak_fail >= FAILURE_ABORT_STREAK:
                        print(json.dumps({"phase": "abort", "reason": "failure_streak", "streak": streak_fail}), flush=True)
                        return
                if (ok_n + fail_n) % 100 == 0:
                    elapsed = time.monotonic() - t0
                    rate = (ok_n + fail_n) / elapsed if elapsed > 0 else 0
                    remain = (len(pending) - ok_n - fail_n) / rate if rate > 0 else -1
                    print(json.dumps({"phase": "progress", "done": ok_n + fail_n,
                                      "pending_total": len(pending), "ok": ok_n, "fail": fail_n,
                                      "rate_per_min": round(rate * 60, 1),
                                      "eta_minutes": round(remain / 60, 1)}), flush=True)
        print(json.dumps({"phase": "complete", "ok": ok_n, "fail": fail_n,
                          "elapsed_min": round((time.monotonic() - t0) / 60, 1)}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
