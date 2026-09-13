"""跑涨停事件研究（预注册设计 `limit_up_event_study_design@v1`–`@v5`）。

**只读本地数据，不联网。** 涨停池的历史由 `scripts/sync_limit_pools.py` 人工触发回补。

    # 连板高度（默认）：≥2 板 vs 首板
    .\\.venv\\Scripts\\python.exe scripts\\run_limit_up_event_study.py

    # 封单额：按当日池内中位数分高低
    .\\.venv\\Scripts\\python.exe scripts\\run_limit_up_event_study.py --field seal_money

    # 可成交性检验：三条口径并排（close_T / T+1 开盘 / T+1 日内）
    .\\.venv\\Scripts\\python.exe scripts\\run_limit_up_event_study.py --field seal_ratio ^
      --return-basis all --family-prior-statistics seal_ratio=63 ^
      --out data\\exports\\event-study\\<日期>-tradability

    # 只跑一段区间 / 减少重抽（调试用；正式结论不要用减少的重抽次数）
    .\\.venv\\Scripts\\python.exe scripts\\run_limit_up_event_study.py --draws 50

结果落盘 `data/exports/event-study/<目录>/<field>[.<口径>].json`。**不产出买入清单。**

`--return-basis` 换的是**结果口径（端点）**，属于定义变更：必须先在
`scripts/governance_register_limit_up_fields.py` 里预注册新 reference（方案 A.1），
否则就是"看到结果再换口径"。
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
from backend.app.screener.events import (  # noqa: E402
    ALL_FIELDS,
    FIELD_FAMILIES,
    OUTCOME_BASES,
    LimitUpEventStudy,
    parse_family_prior_statistics)
from backend.app.settings import load_settings  # noqa: E402

CST = timezone(timedelta(hours=8))


def day_text(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, CST).strftime("%Y-%m-%d")


def print_report(field: str, report: dict, elapsed: float, peak_mb: float | None) -> None:
    print(f"\n{'=' * 72}\n字段 {field}（{ALL_FIELDS[field]['name']}）"
          f"｜可用评估日 {report.get('dates_usable', 0)}/{report.get('dates_with_pool', 0)}"
          f"｜事件 {report.get('events_total', 0)} 个｜共用时 {elapsed:.0f}s")
    basis = report.get("outcome_basis")
    if basis:
        print(f"结果口径：{basis['id']} = {basis['name']}"
              f"｜可成交：{'是' if basis['tradable'] else '**否**（当日买不到）'}")
    path = report.get("data_path") or {}
    print(f"数据通路：{path.get('source')}"
          + (f"（{'本次新建面板' if path.get('rebuilt') else '命中面板缓存'}，"
             f"{path.get('schema_version')}）" if path.get("source") == "panel" else "")
          + (f"｜回落原因：{path.get('reason')}" if path.get("reason") else ""))
    if peak_mb is not None:
        print(f"Python 内存峰值：{peak_mb:.0f}MB")
    if not report.get("results"):
        print(f"未能得出结论：{report.get('reason', '')}")
        return
    print(f"分组：{report['split']}")
    print(f"{'组':<34}{'事件数':>9}{'日均超额':>12}{'t值':>10}{'正超额天数':>12}")
    for key in ("high", "low", "high_minus_low"):
        item = report["results"][key]
        events = "—" if item["events"] is None else item["events"]
        print(f"{item['group']:<34}{events:>9}{item['mean_excess']:>12}"
              f"{str(item['t_stat']):>10}{item['days_positive']:>12}")
    gap = (report.get("descriptive") or {}).get("next_open_gap")
    if gap:
        print(f"\n「买不到」的量化（**描述量，不入门槛**）：次日开盘跳空 高组 "
              f"{gap['high_mean']}／低组 {gap['low_mean']}／高−低 {gap['mean_excess']}"
              f"（事件 {gap['events_high']}+{gap['events_low']}）")
        print("  高封单/高封成比那批股票次日开盘相对今收盘的跳空更大 → 「当日买不到」的缺口。")
    print(f"\n安慰剂①对照置换（真零分布下 |t| 95 分位应≈1.96）：")
    for key, stat in report["placebo"]["permutation"]["by_group"].items():
        print(f"  {key:<16}real_t={stat['real_t']}  t均值={stat['t_mean']}  "
              f"|t|95分位={stat['t_abs_p95']}")

    # --- 稳健性变体矩阵（预注册清单；主检验 = base_all 的『高−低』）---
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
          f"**族 {multiplicity['family']} 此前 {multiplicity['family_prior_statistics']} 条**"
          f"（族级共 {multiplicity['family_total_statistics']} 条）|t|≥"
          f"{multiplicity['bonferroni_family_abs_t']}")

    shift = report["placebo"]["date_shift"]
    print(f"\n安慰剂②日期错位（**已判定无效，仅作诊断**）：{shift['draws']} 次平移，"
          f"|t| 95 分位 {shift['t_abs_p95']}")
    print(f"  {shift['diagnostic_value']}")
    print(f"\n{report['significance']}")
    for value in report["limitations"]:
        print(f"  ⚠ {value}")


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
    parser.add_argument("--family-prior-statistics", default="0",
                        help="族级多重检验计数的**先验部分**（A.2：计数必须含同族看过的全部比较）。"
                             "两种写法：裸整数（所有字段共用）或按字段『字段=条数,字段=条数』"
                             "（推荐——`17` §4.3 修的就是全局值把同族门槛算松的问题）。"
                             "语义：该字段所属族在**本轮该字段之外**已看过的比较条数，"
                             "含此前轮次与本轮同族其它字段；按字段给时**必须逐个列全**。")
    parser.add_argument("--source", default="panel", choices=("panel", "live"),
                        help="数据通路：panel=预计算面板（快，默认）；live=逐日查库（慢，"
                             "用于与面板做等价比对）")
    parser.add_argument("--return-basis", default="close_t",
                        help="结果口径（端点），逗号分隔可一次跑多个："
                             "close_t=T 收盘起算（既有口径，**不可成交**）；"
                             "next_open=T+1 开盘起算（**可成交口径**）；"
                             "next_intraday=T+1 开盘→T+1 收盘；all=三条都跑。"
                             "换口径＝换定义，必须已在新 reference 里预注册（A.1）。")
    parser.add_argument("--profile", action="store_true",
                        help="打开诊断：每 25 个评估日打印墙钟增量与 Python 内存增量。"
                             "⚠️ **tracemalloc 会追踪每一次分配，实测把 37 秒的跑变成 973 秒"
                             "（26×）**——只看内存趋势时用，别拿它的墙钟当性能数字")
    parser.add_argument("--from", dest="from_text", default="", help="起始交易日 YYYY-MM-DD")
    parser.add_argument("--to", dest="to_text", default="", help="结束交易日 YYYY-MM-DD")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    settings = load_settings()
    repository = WarehouseRepository(settings.market_warehouse_path)
    study = LimitUpEventStudy(repository)
    fields = tuple(sorted(ALL_FIELDS)) if args.field == "all" else (args.field,)
    family_priors = parse_family_prior_statistics(args.family_prior_statistics)
    if isinstance(family_priors, int) and len(fields) > 1:
        print(f"⚠ --family-prior-statistics 给的是裸整数 {family_priors}，将套用到全部 "
              f"{len(fields)} 个字段。同族字段（"
              + "、".join(f"{family}={[f for f in fields if FIELD_FAMILIES[f] == family]}"
                          for family in sorted({FIELD_FAMILIES[f] for f in fields}))
              + "）应当按字段给，否则门槛会被算松（`17` §4.3）。")

    if args.return_basis == "all":
        bases = tuple(sorted(OUTCOME_BASES))
    else:
        bases = tuple(part.strip() for part in args.return_basis.split(",") if part.strip())
    unknown_bases = [basis for basis in bases if basis not in OUTCOME_BASES]
    if not bases or unknown_bases:
        print(f"--return-basis 含未知口径 {unknown_bases}（可选 {sorted(OUTCOME_BASES)}，或 all）")
        return 2

    def to_ms(text: str) -> int | None:
        return (int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=CST).timestamp() * 1000)
                if text else None)

    start_ms, end_ms = to_ms(args.from_text), to_ms(args.to_text)
    dates = study.event_dates(start_ms=start_ms, end_ms=end_ms)
    if not dates:
        print("本地涨停池没有内容：先跑 scripts/sync_limit_pools.py --pools limit_up（人工触发）。")
        return 1
    print(f"涨停池覆盖 {len(dates)} 个交易日（{day_text(dates[0])} → {day_text(dates[-1])}）"
          f"｜字段 {list(fields)}｜前瞻 {args.horizon} 日｜重抽 {args.draws} 次"
          f"｜口径 {list(bases)}")
    print("提示：取数对所有字段**只做一遍**，字段各自分组与重抽；"
          "换口径要重跑一遍（置换重抽依赖该口径的对照收益）。千级交易日每口径几十分钟。")

    out_dir = (Path(args.out) if args.out
               else ROOT / "data" / "exports" / "event-study"
               / datetime.now(CST).strftime("%Y-%m-%d"))
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    tracer = None
    if args.profile:
        import tracemalloc  # noqa: PLC0415

        tracemalloc.start(5)
        tracer = tracemalloc
    checkpoint = {"time": started, "peak": 0}

    def progress(index: int, total: int, usable: dict) -> None:
        brief = "、".join(f"{key}:{value}" for key, value in usable.items())
        now = time.time()
        extra = f"（本次 {now - checkpoint['time']:.0f}s"
        if tracer is not None:
            current, peak = tracer.get_traced_memory()
            checkpoint["peak"] = max(checkpoint["peak"], peak)
            extra += f"，Python 内存 {current / 1048576:.0f}MB，峰值 {peak / 1048576:.0f}MB"
        checkpoint["time"] = now
        extra += "）"
        print(f"  {index}/{total} 已处理，可用 {brief}（累计 {now - started:.0f}s）{extra}",
              flush=True)

    exit_code = 0
    summary: dict[str, dict[str, dict]] = {}
    for basis in bases:
        spec = OUTCOME_BASES[basis]
        print(f"\n{'#' * 72}\n# 结果口径 {basis}：{spec['name']}\n{'#' * 72}")
        print(f"  买点：{spec['buy_point']}")
        print(f"  公司行动截断：{spec['corporate_action_cutoff']}")
        print(f"  可成交：{'是' if spec['tradable'] else '**否**——当日封板到收盘就买不到'}")
        reports = study.run_fields(fields=fields, start_ms=start_ms, end_ms=end_ms,
                                   horizon_days=args.horizon, draws=args.draws,
                                   progress=progress, family_prior_statistics=family_priors,
                                   source=args.source, return_basis=basis)
        elapsed = time.time() - started
        peak_mb = checkpoint["peak"] / 1048576 if tracer is not None else None
        for field, report in reports.items():
            print_report(field, report, elapsed, peak_mb)
            if not report.get("results"):
                exit_code = 1
                continue
            suffix = "" if basis == "close_t" else f".{basis}"
            target = out_dir / f"{field}{suffix}.json"
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
                              encoding="utf-8", newline="\n")
            print(f"\n输出：{target}")
            base_all = report["variants"]["base_all"]["results"]
            summary.setdefault(field, {})[basis] = {
                "high": base_all["high"]["mean_excess"],
                "low": base_all["low"]["mean_excess"],
                "high_minus_low": base_all["high_minus_low"]["mean_excess"],
                "t": base_all["high_minus_low"]["t_stat"],
                "events": report.get("events_total"),
                "dates_usable": report.get("dates_usable"),
                "family_threshold": report["multiplicity"]["bonferroni_family_abs_t"],
            }

    if len(bases) > 1:
        print(f"\n{'=' * 72}\n三口径并排（各口径的 base_all『高−低』；主检验是可成交口径）\n{'=' * 72}")
        for field, per_basis in summary.items():
            print(f"\n字段 {field}（{ALL_FIELDS[field]['name']}）")
            print(f"{'口径':<16}{'事件':>8}{'高组':>11}{'低组':>11}{'高−低':>11}{'t值':>9}"
                  f"{'族级门槛':>10}")
            for basis in bases:
                item = per_basis.get(basis)
                if not item:
                    continue
                print(f"{basis:<16}{str(item['events']):>8}{item['high']:>11}{item['low']:>11}"
                      f"{item['high_minus_low']:>11}{str(item['t']):>9}"
                      f"{item['family_threshold']:>10}")
            closed = per_basis.get("close_t")
            opened = per_basis.get("next_open")
            if closed and opened:
                delta = opened["high_minus_low"] - closed["high_minus_low"]
                line = (f"  → 换成可成交口径后：{opened['high_minus_low']} vs "
                        f"{closed['high_minus_low']}，变化 {delta:+.4f}")
                # 只有对照口径本身**离 0 足够远**时，百分比才有意义。
                # 否则会打出「1317%」这种看着惊人、实际只是分母接近 0 的数字（实测踩过）。
                if abs(closed["high_minus_low"]) >= 0.002:
                    line += (f"（剩 {opened['high_minus_low'] / closed['high_minus_low'] * 100:.0f}%）")
                else:
                    line += "（对照口径本身≈0，百分比无意义，只看绝对值）"
                print(line)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
