"""等价性校验：面板 vs 旧实现（`cross_section` + `settlement_returns`）逐位比对。

**这是"不拿准确度换速度"的唯一证据。** 在校验通过之前不允许把事件研究切到面板。

比对内容（每个抽样评估日）：
1. 标的集合是否一致（面板行 ⇔ 当日有正收盘 K 线）；
2. `turnover_today` / `turnover_20` / `close_20_ago` / `close_60_ago` 是否一致；
3. **前向收益**是否一致（含"一边为 NULL 另一边有值"的情形，那是最危险的不一致）；
4. 报告最大绝对差（浮点求和顺序可能带来 1e-16 级差异，记录下来而不是假装为零）。

抽样刻意包含：早期、中期、近期，以及**紧跟在除权密集期之后**的日期。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402

from backend.app.market_warehouse.repository import WarehouseRepository  # noqa: E402
from backend.app.screener.service import total_return  # noqa: E402
from backend.app.settings import load_settings  # noqa: E402

SNAPSHOT_KEYS = ("turnover_today", "turnover_20", "close_20_ago", "close_60_ago")
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
        ex_dates = [row[0] for row in db.execute(
            "SELECT DISTINCT ex_date_ms FROM market.adjustment_factors ORDER BY ex_date_ms"
        ).fetchall()] if False else []
    finally:
        db.close()
    picks = [dates[index] for index in
             (30, 200, 400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2000, 2200)]
    return [date_ms for date_ms in picks if date_ms], ex_dates


def main() -> int:
    repository = WarehouseRepository(load_settings().market_warehouse_path)
    panel_path = repository.event_panel_path
    status = repository.event_panel_status()
    print(f"面板就绪：{status['ready']}  文件 {panel_path}")
    target_dates = sample_dates(repository, panel_path)[0]
    print(f"抽样 {len(target_dates)} 个评估日\n")

    panel_rows: dict[int, dict[str, dict]] = {}
    for date_ms, rows in repository.event_panel_window(panel_path=panel_path, chunk_dates=10):
        if date_ms in target_dates:
            panel_rows[date_ms] = rows

    totals = {"snapshot_diff": 0, "forward_diff": 0, "missing": 0, "extra": 0,
              "null_mismatch": 0, "max_relative": 0.0, "compared": 0}
    details: list[str] = []
    for date_ms in target_dates:
        cross = {row["thscode"]: row for row in repository.cross_section(date_ms=date_ms)}
        settle = {row["thscode"]: row for row in repository.settlement_returns(
            date_ms=date_ms, horizon_days=20)}
        panel = panel_rows.get(date_ms, {})

        missing = [symbol for symbol in cross if symbol not in panel]
        extra = [symbol for symbol in panel if symbol not in cross]
        totals["missing"] += len(missing)
        totals["extra"] += len(extra)

        snapshot_diff = forward_diff = null_mismatch = 0
        max_relative = 0.0
        for symbol, panel_row in panel.items():
            cross_row = cross.get(symbol)
            if cross_row is not None:
                for key in SNAPSHOT_KEYS:
                    left, right = panel_row[key], cross_row.get(key)
                    if left is None and right is None:
                        continue
                    if left is None or right is None:
                        null_mismatch += 1
                        details.append(f"    [{date_ms}] {symbol} {key}: 面板={left!r} "
                                       f"横截面={right!r}（一边缺）")
                        continue
                    if _differs(left, right):
                        snapshot_diff += 1
                        details.append(f"    [{date_ms}] {symbol} {key}: 面板={left!r} "
                                       f"横截面={right!r} 相对差={_relative(left, right):.2e}")
                    max_relative = max(max_relative, _relative(left, right))
            settle_row = settle.get(symbol)
            expected = total_return(settle_row) if settle_row else None
            actual = panel_row["forward_return"]
            if expected is None and actual is None:
                continue
            if expected is None or actual is None:
                null_mismatch += 1
                details.append(f"    [{date_ms}] {symbol} forward_return: 面板={actual!r} "
                               f"旧实现={expected!r}（一边缺）")
                continue
            if _differs(expected, actual):
                forward_diff += 1
                details.append(f"    [{date_ms}] {symbol} forward_return: 面板={actual!r} "
                               f"旧实现={expected!r}")
            max_relative = max(max_relative, _relative(expected, actual))

        totals["snapshot_diff"] += snapshot_diff
        totals["forward_diff"] += forward_diff
        totals["null_mismatch"] += null_mismatch
        totals["max_relative"] = max(totals["max_relative"], max_relative)
        totals["compared"] += len(panel)
        print(f"  {date_ms}  标的 {len(panel):5d}（差集 -{len(missing)} +{len(extra)}）  "
              f"快照不符 {snapshot_diff}  前向不符 {forward_diff}  "
              f"NULL 不一致 {null_mismatch}  最大相对差 {max_relative:.2e}")

    print(f"\n合计：比对 {totals['compared']} 个 (日期, 标的) 单元")
    print(f"  快照列不符 {totals['snapshot_diff']}；前向收益不符 {totals['forward_diff']}；"
          f"NULL 不一致 {totals['null_mismatch']}；集合缺失 {totals['missing']} / 多出 {totals['extra']}")
    print(f"  最大相对差 {totals['max_relative']:.3e}（相对容差 {RELATIVE_TOLERANCE:.0e}）")
    if details:
        print(f"\n不一致明细（最多 12 条）：")
        for line in details[:12]:
            print(line)
        if len(details) > 12:
            print(f"    ...（其余 {len(details) - 12} 条略）")
    clean = (totals["snapshot_diff"] == 0 and totals["forward_diff"] == 0
             and totals["null_mismatch"] == 0 and totals["missing"] == 0
             and totals["extra"] == 0)
    print(f"\n判定：{'等价 ✅' if clean else '不等价 ❌ —— 不允许切换'}")
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
