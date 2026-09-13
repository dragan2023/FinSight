"""等价性校验：面板 vs 旧实现（`cross_section` + 结算查询）逐位比对。

**这是"不拿准确度换速度"的唯一证据。** 在校验通过之前不允许把事件研究切到面板。

比对内容（每个抽样评估日）：
1. 标的集合是否一致（面板行 ⇔ 当日有正收盘 K 线）；
2. `turnover_today` / `turnover_20` / `close_20_ago` / `close_60_ago` 是否一致；
3. **前向收益**是否一致（含"一边为 NULL 另一边有值"的情形，那是最危险的不一致）；
4. **v5 可成交口径**（`next_open_ms` / `next_open` / `next_close` / `next_open_gap` /
   `forward_return_next_open` / `forward_return_next_intraday`）是否一致——口径从
   T 收盘挪到 T+1 开盘之后，**除权截断点也跟着挪**（只计入严格晚于买入日的公司行动），
   这一步最容易写错，所以必须与逐日查库逐位对齐，而不是只信 SQL 的注释；
5. 报告最大绝对差（浮点求和顺序可能带来 1e-16 级差异，记录下来而不是假装为零）。

抽样刻意包含：早期、中期、近期，以及**紧跟在除权密集期之后**的日期。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: **必须**显式把 stdout/stderr 设成 UTF-8：本脚本的输出（含中文与 ✅/❌）在管道/重定向下
#: 会按系统默认码页（本机 GBK）编码，`UnicodeEncodeError` 会让**判定行**打不出来——
#: 于是"比对通过"变成了退出码 1，读者只会看到一堆 traceback（实测踩过一次）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

import duckdb  # noqa: E402

from backend.app.market_warehouse.repository import WarehouseRepository  # noqa: E402
from backend.app.screener.service import total_return, total_return_between  # noqa: E402
from backend.app.settings import load_settings  # noqa: E402

SNAPSHOT_KEYS = ("turnover_today", "turnover_20", "close_20_ago", "close_60_ago")
#: v5 可成交口径里**直接来自结算行**的列（面板侧同名）。
NEXT_OPEN_KEYS = ("next_open_ms", "next_open", "next_close")
#: **相对容差**。绝对值容差在"20 个亿元级成交额求和"上会误报：2e10 的 1 ULP 就是 ~4e-6，
#: 而两条 SQL 的求和顺序不同，必然产生这个量级的差。用相对容差才是正确的判据。
RELATIVE_TOLERANCE = 1e-9


def _differs(left, right) -> bool:
    if left is None or right is None:
        return False
    scale = max(1.0, abs(left), abs(right))
    return abs(left - right) > RELATIVE_TOLERANCE * scale


def _relative(left, right) -> float:
    if left is None or right is None:
        return 0.0
    scale = max(1.0, abs(left), abs(right))
    return abs(left - right) / scale


def sample_dates(repository, panel_path) -> list[int]:
    db = duckdb.connect(str(panel_path), read_only=True)
    try:
        dates = [row[0] for row in db.execute(
            "SELECT DISTINCT date_ms FROM event_panel ORDER BY date_ms").fetchall()]
    finally:
        db.close()
    picks = [dates[index] for index in
             (30, 200, 400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2000, 2200)]
    return [date_ms for date_ms in picks if date_ms]


def main() -> int:
    repository = WarehouseRepository(load_settings().market_warehouse_path)
    panel_path = repository.event_panel_path
    status = repository.event_panel_status()
    print(f"面板就绪：{status['ready']}  文件 {panel_path}")
    print(f"面板版本：{status['meta'].get('schema_version')}")
    target_dates = sample_dates(repository, panel_path)
    print(f"抽样 {len(target_dates)} 个评估日\n")

    panel_rows: dict[int, dict[str, dict]] = {}
    for date_ms, rows in repository.event_panel_window(panel_path=panel_path, chunk_dates=10):
        if date_ms in target_dates:
            panel_rows[date_ms] = rows

    totals = {"snapshot_diff": 0, "forward_diff": 0, "next_open_diff": 0, "intraday_diff": 0,
              "gap_diff": 0, "missing": 0, "extra": 0, "null_mismatch": 0,
              "max_relative": 0.0, "compared": 0, "next_compared": 0}
    details: list[str] = []

    def compare(expected, actual, counter: str, context: str) -> None:
        """一边缺 → `null_mismatch`（最危险）；两边都有值 → 相对容差比较。"""
        nonlocal details
        if expected is None and actual is None:
            return
        if expected is None or actual is None:
            totals["null_mismatch"] += 1
            details.append(f"    {context}: 面板={actual!r} 旧实现={expected!r}（一边缺）")
            return
        if _differs(expected, actual):
            totals[counter] += 1
            details.append(f"    {context}: 面板={actual!r} 旧实现={expected!r}")
        totals["max_relative"] = max(totals["max_relative"], _relative(expected, actual))

    for date_ms in target_dates:
        cross = {row["thscode"]: row for row in repository.cross_section(date_ms=date_ms)}
        settle = {row["thscode"]: row for row in repository.settlement_returns(
            date_ms=date_ms, horizon_days=20)}
        settle_next = {row["thscode"]: row
                       for row in repository.settlement_returns_next_open(
                           date_ms=date_ms, horizon_days=20)}
        panel = panel_rows.get(date_ms, {})

        missing = [symbol for symbol in cross if symbol not in panel]
        extra = [symbol for symbol in panel if symbol not in cross]
        totals["missing"] += len(missing)
        totals["extra"] += len(extra)

        per_date = {key: 0 for key in ("snapshot_diff", "forward_diff", "next_open_diff",
                                       "intraday_diff", "gap_diff", "null_mismatch")}
        before = dict(totals)
        for symbol, panel_row in panel.items():
            cross_row = cross.get(symbol)
            if cross_row is not None:
                for key in SNAPSHOT_KEYS:
                    compare(cross_row.get(key), panel_row[key], "snapshot_diff",
                            f"[{date_ms}] {symbol} {key}")
            compare(total_return(settle[symbol]) if symbol in settle else None,
                    panel_row["forward_return"], "forward_diff",
                    f"[{date_ms}] {symbol} forward_return")

            next_row = settle_next.get(symbol)
            for key in NEXT_OPEN_KEYS:
                compare(next_row.get(key) if next_row else None, panel_row[key],
                        "next_open_diff", f"[{date_ms}] {symbol} {key}")
            live_gap = None
            if next_row:
                entry, close_t = next_row.get("next_open"), next_row.get("close_t")
                live_gap = (entry / close_t - 1 if entry and close_t and close_t > 0 else None)
            compare(live_gap, panel_row["next_open_gap"], "gap_diff",
                    f"[{date_ms}] {symbol} next_open_gap")
            compare(total_return_between(next_row.get("next_open"), next_row.get("horizon_close"),
                                         next_row.get("bonus", 0.0), next_row.get("dividend", 0.0),
                                         next_row.get("rights", 0.0), next_row.get("rights_cost", 0.0))
                    if next_row else None,
                    panel_row["forward_return_next_open"], "next_open_diff",
                    f"[{date_ms}] {symbol} forward_return_next_open")
            intraday = None
            if next_row:
                entry, close_next = next_row.get("next_open"), next_row.get("next_close")
                intraday = (close_next / entry - 1
                            if entry and entry > 0 and close_next and close_next > 0 else None)
            compare(intraday, panel_row["forward_return_next_intraday"], "intraday_diff",
                    f"[{date_ms}] {symbol} forward_return_next_intraday")
            if next_row:
                totals["next_compared"] += 1

        for key in per_date:
            per_date[key] = totals[key] - before[key]
        totals["compared"] += len(panel)
        print(f"  {date_ms}  标的 {len(panel):5d}（差集 -{len(missing)} +{len(extra)}）  "
              f"快照不符 {per_date['snapshot_diff']}  前向不符 {per_date['forward_diff']}  "
              f"T+1开户不符 {per_date['next_open_diff']}  次日日内不符 {per_date['intraday_diff']}  "
              f"跳空不符 {per_date['gap_diff']}  NULL 不一致 {per_date['null_mismatch']}  "
              f"最大相对差 {totals['max_relative']:.2e}")

    print(f"\n合计：比对 {totals['compared']} 个 (日期, 标的) 单元"
          f"（其中 {totals['next_compared']} 个有次日开盘口径）")
    print(f"  快照列不符 {totals['snapshot_diff']}；前向收益不符 {totals['forward_diff']}；"
          f"T+1 开盘口径不符 {totals['next_open_diff']}；次日日内不符 {totals['intraday_diff']}；"
          f"跳空不符 {totals['gap_diff']}；NULL 不一致 {totals['null_mismatch']}；"
          f"集合缺失 {totals['missing']} / 多出 {totals['extra']}")
    print(f"  最大相对差 {totals['max_relative']:.3e}（相对容差 {RELATIVE_TOLERANCE:.0e}）")
    if details:
        print(f"\n不一致明细（最多 12 条）：")
        for line in details[:12]:
            print(line)
        if len(details) > 12:
            print(f"    ...（其余 {len(details) - 12} 条略）")
    clean = all(totals[key] == 0 for key in
                ("snapshot_diff", "forward_diff", "next_open_diff", "intraday_diff",
                 "gap_diff", "null_mismatch", "missing", "extra"))
    print(f"\n判定：{'等价 OK' if clean else '不等价 FAIL —— 不允许切换'}")
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
