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
from backend.app.screener.events import ALL_FIELDS, LimitUpEventStudy  # noqa: E402
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
    parser.add_argument("--field", default="all",
                        choices=sorted(ALL_FIELDS) + ["all"],
                        help="要研究的字段；all = 一次遍历把全部字段都算出来")
    parser.add_argument("--horizon", type=int, default=20, help="前瞻期（交易日）")
    parser.add_argument("--draws", type=int, default=200, help="安慰剂重抽次数")
    parser.add_argument("--family-prior-statistics", type=int, default=0,
                        help="同一族**此前已看过**的比较条数（A.2：多重检验计数必须含它）")
    parser.add_argument("--from", dest="from_text", default="", help="起始交易日 YYYY-MM-DD")
    parser.add_argument("--to", dest="to_text", default="", help="结束交易日 YYYY-MM-DD")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    settings = load_settings()
    repository = WarehouseRepository(settings.market_warehouse_path)
    study = LimitUpEventStudy(repository)
    fields = tuple(sorted(ALL_FIELDS)) if args.field == "all" else (args.field,)

    def to_ms(text: str) -> int | None:
        return (int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=CST).timestamp() * 1000)
                if text else None)

    start_ms, end_ms = to_ms(args.from_text), to_ms(args.to_text)
    dates = study.event_dates(start_ms=start_ms, end_ms=end_ms)
    if not dates:
        print("本地涨停池没有内容：先跑 scripts/sync_limit_pools.py --pools limit_up（人工触发）。")
        return 1
    print(f"涨停池覆盖 {len(dates)} 个交易日（{day_text(dates[0])} → {day_text(dates[-1])}）"
          f"｜字段 {list(fields)}｜前瞻 {args.horizon} 日｜重抽 {args.draws} 次")
    print("提示：每个评估日 2 次取数；取数对所有字段**只做一遍**，"
          "字段各自分组与重抽。千级交易日需要几十分钟。")

    started = time.time()

    def progress(index: int, total: int, usable: dict) -> None:
        brief = "、".join(f"{key}:{value}" for key, value in usable.items())
        print(f"  {index}/{total} 已处理，可用 {brief}（{time.time() - started:.0f}s）", flush=True)

    reports = study.run_fields(fields=fields, start_ms=start_ms, end_ms=end_ms,
                               horizon_days=args.horizon, draws=args.draws, progress=progress,
                               family_prior_statistics=args.family_prior_statistics)
    elapsed = time.time() - started

    out_dir = (Path(args.out) if args.out
               else ROOT / "data" / "exports" / "event-study"
               / datetime.now(CST).strftime("%Y-%m-%d"))
    out_dir.mkdir(parents=True, exist_ok=True)
    exit_code = 0
    for field, report in reports.items():
        print(f"\n{'=' * 72}\n字段 {field}（{ALL_FIELDS[field]['name']}）"
              f"｜可用评估日 {report.get('dates_usable', 0)}/{report.get('dates_with_pool', 0)}"
              f"｜事件 {report.get('events_total', 0)} 个｜共用时 {elapsed:.0f}s")
        if not report.get("results"):
            print(f"未能得出结论：{report.get('reason', '')}")
            exit_code = 1
            continue
        print(f"分组：{report['split']}")
        print(f"{'组':<28}{'事件数':>9}{'日均超额':>12}{'t值':>10}{'正超额天数':>12}")
        for key in ("high", "low", "high_minus_low"):
            item = report["results"][key]
            events = "—" if item["events"] is None else item["events"]
            print(f"{item['group']:<28}{events:>9}{item['mean_excess']:>12}"
                  f"{str(item['t_stat']):>10}{item['days_positive']:>12}")
        print(f"\n安慰剂①对照置换（真零分布下 |t| 95 分位应≈1.96）：")
        for key, stat in report["placebo"]["permutation"]["by_group"].items():
            print(f"  {key:<16}real_t={stat['real_t']}  t均值={stat['t_mean']}  "
                  f"|t|95分位={stat['t_abs_p95']}")

        # --- 稳健性变体矩阵（v3 预注册清单；主检验 = base_all 的『高−低』）---
        print(f"\n变体矩阵（每个变体的『高−低』都有**自己的零分布**）：")
        print(f"{'变体':<20}{'对照匹配':<30}{'事件':>7}{'高组':>11}{'低组':>11}"
              f"{'高−低':>11}{'t值':>8}{'零分布95':>10}")
        for name, payload in report["variants"].items():
            diff = payload["results"]["high_minus_low"]
            null = payload["placebo"]["by_group"]["high_minus_low"]["t_abs_p95"]
            print(f"{name:<20}{payload['matching']:<30}{payload['events']:>7}"
                  f"{payload['results']['high']['mean_excess']:>11}"
                  f"{payload['results']['low']['mean_excess']:>11}"
                  f"{diff['mean_excess']:>11}{str(diff['t_stat']):>8}{str(null):>10}")
        if report["by_period"]:
            print(f"\n分时段复现（『高−低』）：")
            for key, item in report["by_period"].items():
                print(f"  {key:<34}{item['dates']:>5} 天  超额 {item['mean_excess']:>10}"
                      f"  t={item['t_stat']}")
        multiplicity = report["multiplicity"]
        print(f"\n多重检验：{multiplicity['definition']}")
        print(f"  逐检验 |t|≥{multiplicity['per_test_abs_t']}；"
              f"主检验 |t|≥{multiplicity['bonferroni_primary_abs_t']}；"
              f"本次全部比较 |t|≥{multiplicity['bonferroni_all_abs_t']}；"
              f"**含同族此前 {multiplicity['family_prior_statistics']} 条**"
              f"（共 {multiplicity['family_total_statistics']} 条）|t|≥"
              f"{multiplicity['bonferroni_family_abs_t']}")

        shift = report["placebo"]["date_shift"]
        print(f"\n安慰剂②日期错位（**已判定无效，仅作诊断**）：{shift['draws']} 次平移，"
              f"|t| 95 分位 {shift['t_abs_p95']}")
        print(f"  {shift['diagnostic_value']}")
        print(f"\n{report['significance']}")
        for value in report["limitations"]:
            print(f"  ⚠ {value}")
        target = out_dir / f"{field}.json"
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
                          encoding="utf-8", newline="\n")
        print(f"\n输出：{target}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
