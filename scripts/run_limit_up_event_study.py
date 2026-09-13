"""跑涨停事件研究（预注册设计 `limit_up_event_study_design@v1`）。

**只读本地数据，不联网。** 涨停池的历史由 `scripts/sync_limit_pools.py` 人工触发回补。

    # 连板高度（默认）：≥2 板 vs 首板
    .\\.venv\\Scripts\\python.exe scripts\\run_limit_up_event_study.py

    # 封单额：按当日池内中位数分高低
    .\\.venv\\Scripts\\python.exe scripts\\run_limit_up_event_study.py --field seal_money

    # 只跑一段区间 / 减少重抽（调试用；正式结论不要用减少的重抽次数）
    .\\.venv\\Scripts\\python.exe scripts\\run_limit_up_event_study.py --draws 50

结果落盘 `data/exports/event-study/<日期>/<field>.json`。**不产出买入清单。**
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

from backend.app.market_warehouse.repository import WarehouseRepository  # noqa: E402
from backend.app.screener.events import FIELD_ENCODINGS, LimitUpEventStudy  # noqa: E402
from backend.app.settings import load_settings  # noqa: E402

CST = timezone(timedelta(hours=8))


def day_text(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, CST).strftime("%Y-%m-%d")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="涨停事件研究（预注册设计）")
    parser.add_argument("--field", default="continue_day_cnt",
                        choices=sorted(FIELD_ENCODINGS), help="要研究的池子字段")
    parser.add_argument("--horizon", type=int, default=20, help="前瞻期（交易日）")
    parser.add_argument("--draws", type=int, default=200, help="安慰剂重抽次数")
    parser.add_argument("--from", dest="from_text", default="", help="起始交易日 YYYY-MM-DD")
    parser.add_argument("--to", dest="to_text", default="", help="结束交易日 YYYY-MM-DD")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    settings = load_settings()
    repository = WarehouseRepository(settings.market_warehouse_path)
    study = LimitUpEventStudy(repository)

    def to_ms(text: str) -> int | None:
        return (int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=CST).timestamp() * 1000)
                if text else None)

    start_ms, end_ms = to_ms(args.from_text), to_ms(args.to_text)
    dates = study.event_dates(start_ms=start_ms, end_ms=end_ms)
    if not dates:
        print("本地涨停池没有内容：先跑 scripts/sync_limit_pools.py --pools limit_up（人工触发）。")
        return 1
    print(f"涨停池覆盖 {len(dates)} 个交易日（{day_text(dates[0])} → {day_text(dates[-1])}）"
          f"｜字段 {args.field}（{FIELD_ENCODINGS[args.field]['name']}）"
          f"｜前瞻 {args.horizon} 日｜重抽 {args.draws} 次")
    print("提示：每个评估日要跑 2 次取数（横截面 + 前向收益），千级交易日需要十几分钟。")

    started = time.time()
    last = [0]

    def progress(index: int, total: int, usable: int) -> None:
        last[0] = usable
        print(f"  {index}/{total} 已处理，可用 {usable} 天（{time.time() - started:.0f}s）",
              flush=True)

    report = study.run(start_ms=start_ms, end_ms=end_ms, horizon_days=args.horizon,
                       field=args.field, draws=args.draws, progress=progress)
    elapsed = time.time() - started

    print(f"\n可用评估日 {report.get('dates_usable', 0)}/{report.get('dates_with_pool', 0)}"
          f"｜事件 {report.get('events_total', 0)} 个｜{elapsed:.0f}s")
    if not report.get("results"):
        print(f"未能得出结论：{report.get('reason', '')}")
        return 1
    print(f"\n分组：{report['split']}")
    print(f"{'组':<24}{'事件数':>8}{'日均超额':>12}{'t值':>9}{'正超额天数':>12}")
    for key in ("high", "low", "high_minus_low"):
        item = report["results"][key]
        events = "—" if item["events"] is None else item["events"]
        print(f"{item['group']:<24}{events:>8}{item['mean_excess']:>12}"
              f"{str(item['t_stat']):>9}{item['days_positive']:>12}")
    placebo = report["placebo"]
    print(f"\n安慰剂①对照置换：t 均值 {placebo['permutation']['by_group']['high']['t_mean']}"
          f"，|t| 95 分位 {placebo['permutation']['by_group']['high']['t_abs_p95']}"
          f"（真零分布下应≈1.96）")
    shift = placebo["date_shift"]
    print(f"安慰剂②日期错位：{shift['draws']} 次平移，t 均值 {shift['t_mean']}，"
          f"|t| 95 分位 {shift['t_abs_p95']}")
    print(f"\n{report['significance']}")
    for value in report["limitations"]:
        print(f"  ⚠ {value}")

    out_dir = (Path(args.out) if args.out
               else ROOT / "data" / "exports" / "event-study"
               / datetime.now(CST).strftime("%Y-%m-%d"))
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{args.field}.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
                      encoding="utf-8", newline="\n")
    print(f"\n输出：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
