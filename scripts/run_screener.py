"""C2 最小版实跑：横截面候选池 + 因子 Rank IC / 安慰剂通过率（D16 / D19）。

    .\\.venv\\Scripts\\python.exe scripts\\run_screener.py                 # 最新交易日的候选池
    .\\.venv\\Scripts\\python.exe scripts\\run_screener.py --ic --dates 24   # 因子评价（24 个月末）

结果落盘到 `data/exports/screener/<日期>/`（`candidate_pool.jsonl` / `ic_report.json`）。
**不产出买入清单**：输出一律标注为候选池，且带 `params_hash` 与全部限制说明。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402

from backend.app.market_warehouse.repository import WarehouseRepository  # noqa: E402
from backend.app.screener import ScreenerService, UniverseRules  # noqa: E402
from backend.app.settings import load_settings  # noqa: E402

CST = timezone(timedelta(hours=8))


def _trading_days(db_path: Path) -> list[int]:
    db = duckdb.connect(str(db_path), read_only=True)
    try:
        return [row[0] for row in db.execute(
            "SELECT DISTINCT date_ms FROM daily_k ORDER BY date_ms").fetchall()]
    finally:
        db.close()


def _month_end_sample(days: list[int], count: int, *, leave_out: int) -> list[int]:
    """等距抽取月末附近的交易日，并**留出前瞻窗口**（最后 leave_out 个交易日内不取）。"""
    usable = days[:-leave_out] if leave_out else days
    if count >= len(usable):
        return usable
    step = len(usable) / count
    return [usable[int(index * step)] for index in range(count)]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="C2 最小版：候选池与因子评价")
    parser.add_argument("--ic", action="store_true", help="同时跑因子 IC 与安慰剂对照")
    parser.add_argument("--dates", type=int, default=24, help="IC 抽样的评估日数量")
    parser.add_argument("--horizon", type=int, default=20, help="前瞻期（交易日）")
    parser.add_argument("--top", type=int, default=200, help="候选池输出条数（0=全部）")
    parser.add_argument("--draws", type=int, default=200, help="安慰剂重排次数")
    parser.add_argument("--min-turnover", type=float, default=2.0e7)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    settings = load_settings()
    db_path = settings.market_warehouse_path
    repository = WarehouseRepository(db_path)
    service = ScreenerService(repository)
    rules = UniverseRules(min_turnover_20d=args.min_turnover)

    days = _trading_days(db_path)
    if not days:
        print("本地行情仓库为空：请先同步全市场日 K。")
        return 1
    as_of = days[-1]
    day = datetime.fromtimestamp(as_of / 1000, CST).strftime("%Y-%m-%d")
    started = time.time()

    pool = service.rank(as_of, rules=rules, top_n=args.top or None)
    universe = pool["universe"]
    print(f"评估日 {day}｜横截面 {universe['raw_symbols']} 只 → 入池 {universe['kept']} 只"
          f"（{time.time() - started:.1f}s）")
    print("排除：" + "；".join(f"{RULE_LABEL.get(k, k)} {v}" for k, v in universe["exclusions"].items()))
    print(f"params_hash = {pool['params_hash']}｜候选 {pool['candidate_count']} 只"
          f"（标注为「{pool['label']}」）")
    for row in pool["candidates"][:5]:
        detail = "，".join(f"{key}={value['value']}" for key, value in row["factors"].items())
        print(f"  #{row['rank']} {row['thscode']} score={row['composite_score']} {detail}")

    report = None
    if args.ic:
        dates = _month_end_sample(days, args.dates, leave_out=args.horizon * 2 + 10)
        started = time.time()
        report = service.ic_report(dates, horizon_days=args.horizon, rules=rules,
                                   draws=args.draws)
        print(f"\n因子评价：{report['dates_usable']}/{report['dates_requested']} 个评估日可用"
              f"（{time.time() - started:.1f}s，每个评估日 {args.draws} 次安慰剂重排）")
        print(f"{'因子':<18}{'方向':<6}{'均值IC':>9}{'IC标准差':>10}{'t值':>8}{'IC>0占比':>10}")
        for factor_id, item in report["factors"].items():
            if not item.get("dates"):
                print(f"{factor_id:<18}{'—':<6}{item.get('reason', ''):>30}")
                continue
            print(f"{factor_id:<18}{item['direction']:<6}{item['mean_ic']:>9}"
                  f"{item['ic_sd']:>10}{item['t_stat']:>8}{item['positive_ic_share']:>10}")
        print("")
        for factor_id, item in report["factors"].items():
            if item.get("dates"):
                print(f"  {factor_id}：{item['verdict']}")
        print("\n判读：显著性看 **t 值**（|t| >= 1.96），不看安慰剂。"
              "逐标的置乱会把横截面相关一起打碎，零分布过窄，拿它当门槛会系统性放大通过率。")

    out_dir = Path(args.out) if args.out else ROOT / "data" / "exports" / "screener" / day
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "candidate_pool.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in pool["candidates"]) + "\n",
        encoding="utf-8", newline="\n")
    (out_dir / "pool_meta.json").write_text(
        json.dumps({key: value for key, value in pool.items() if key != "candidates"},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    if report is not None:
        (out_dir / "ic_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="\n")
    print(f"\n输出：{out_dir}")
    return 0


RULE_LABEL = {
    "non_positive_price": "价格异常", "insufficient_history": "历史不足",
    "below_min_turnover": "成交额过低", "suspected_limit_up": "疑似涨停",
    "no_bar_on_date": "当日无成交",
}


if __name__ == "__main__":
    raise SystemExit(main())
