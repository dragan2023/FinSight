"""跑**策略级验收**（D70/D71）：同一批策略 × 不同风控档位，逐条对阈值判定。

    # 默认：20 只流动性最好的标的、2 条已有策略、3 个风控档位
    .\\.venv\\Scripts\\python.exe scripts\\run_strategy_acceptance.py

    # 只看趋势策略、对照"有风控 vs 无风控"
    .\\.venv\\Scripts\\python.exe scripts\\run_strategy_acceptance.py --strategies trend_20_60_v1

**只读本地数据，不联网。** 成本按单边 `--cost-bps`（默认 10bps）双边计入。

判据（用户 2026-09-15 定下）：最大回撤 ≤20%、单年最差 ≥−10%、回撤最长持续 ≤365 天、
扣成本后期望 >0、不靠少数几天、样本外最后 20% 不崩。**评价里不剔除历史暴跌段。**

产出 `data/exports/strategy-acceptance/<日期>/report.json`，含每条检查的实测值与人话原因。
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
from backend.app.portfolio.backtest import PortfolioTarget, run_portfolio_backtest  # noqa: E402
from backend.app.settings import load_settings  # noqa: E402
from backend.app.strategy.acceptance import (  # noqa: E402
    AcceptanceThresholds,
    evaluate_acceptance,
)
from backend.app.strategy.local_bars import local_price_bars  # noqa: E402
from backend.app.strategy.regime import market_regime_series, regime_gate  # noqa: E402
from backend.app.strategy.registry import get_risk_profile  # noqa: E402

CST = timezone(timedelta(hours=8))
DEFAULT_STRATEGIES = ("trend_20_60_v1", "reversal_5d_v1")
#: 默认四档：无风控（对照）/ 保守 / 保守+下跌市不交易（60 日）/ 同规则但 120 日慢线
#: （慢线是用来判断"这条规则是不是靠短期择时碰运气"的对照）。
DEFAULT_PROFILES = ("risk_none_v1", "risk_conservative_v1", "risk_regime_v1",
                    "risk_regime_slow_v1")


def build_universe(repository, *, top: int, as_of_ms: int) -> list[str]:
    """流动性前 N（按 20 日成交额）——用一个**固定、可复现**的股票池，避免随手挑标的。"""
    rows = repository.cross_section(date_ms=as_of_ms)
    ranked = sorted((row for row in rows if (row.get("turnover_20") or 0) > 0),
                    key=lambda row: row["turnover_20"], reverse=True)
    return [row["thscode"] for row in ranked[:top]]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="策略级验收（D70）")
    parser.add_argument("--symbols", type=int, default=20, help="股票池取流动性前 N 只")
    parser.add_argument("--strategies", default=",".join(DEFAULT_STRATEGIES))
    parser.add_argument("--risk-profiles", default=",".join(DEFAULT_PROFILES))
    parser.add_argument("--cost-bps", type=int, default=10, help="单边成本（bps）")
    parser.add_argument("--max-positions", type=int, default=10)
    parser.add_argument("--max-single-weight", type=float, default=0.10)
    parser.add_argument("--max-industry-weight", type=float, default=0.30)
    parser.add_argument("--warmup-bars", type=int, default=252, help="预热 K 线不计入成绩")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    settings = load_settings()
    repository = WarehouseRepository(settings.market_warehouse_path)
    as_of_ms = repository.latest_daily_ms()
    if as_of_ms is None:
        print("行情库为空。")
        return 1
    symbols = build_universe(repository, top=args.symbols, as_of_ms=as_of_ms)
    strategies = [item.strip() for item in args.strategies.split(",") if item.strip()]
    profiles = [item.strip() for item in args.risk_profiles.split(",") if item.strip()]

    started = time.time()
    print(f"目标股票池：流动性前 {args.symbols} 只（截至 "
          f"{datetime.fromtimestamp(as_of_ms / 1000, CST):%Y-%m-%d}）"
          f"｜成本 {args.cost_bps}bps/边｜{len(strategies)} 策略 × {len(profiles)} 风控档位")
    # 流动性排名里混着次新股（K 线不够预热），所以**边筛边取**：按流动性顺序往下走，
    # 只保留历史长度够用的，直到凑满目标数量。否则会得到"组合里只剩 4 只"这种结果。
    ranked = build_universe(repository, top=args.symbols * 6, as_of_ms=as_of_ms)
    targets: list[PortfolioTarget] = []
    for code in ranked:
        if len(targets) >= args.symbols:
            break
        bars, _ = local_price_bars(repository, code, start_ms=0, end_ms=as_of_ms)
        if len(bars) < args.warmup_bars + 60:
            continue
        targets.append(PortfolioTarget(thscode=code, name=code, industry="未分类", bars=bars))
    if len(targets) < 5:
        print(f"可用标的只有 {len(targets)} 只（已检查流动性前 {len(ranked)} 名），不足以组合。")
        return 1
    # 预热期不计入成绩：从每只标的第 warmup_bars 根 K 线之后开始统计
    start_dates = sorted(target.bars[args.warmup_bars].date for target in targets)
    analysis_start = start_dates[len(start_dates) // 2]
    print(f"有效标的 {len(targets)} 只｜评价起点 {analysis_start}（预热 {args.warmup_bars} 根）")

    thresholds = AcceptanceThresholds()
    # 市场状态序列（下跌市不交易）只在需要时才算：实测 0.7 秒一条，代价可忽略。
    regime_gates: dict[int, dict[str, bool]] = {}
    for profile_id in profiles:
        profile = get_risk_profile(profile_id)
        if profile.regime_filter and profile.regime_ma_days not in regime_gates:
            series = market_regime_series(repository, end_ms=as_of_ms,
                                          ma_days=profile.regime_ma_days)
            regime_gates[profile.regime_ma_days] = regime_gate(series)
    if regime_gates:
        summary = "、".join(f"{days} 日均线（走强占 "
                           f"{sum(1 for value in gate.values() if value) / max(1, len(gate)):.0%} 的交易日）"
                           for days, gate in sorted(regime_gates.items()))
        print(f"市场状态（等权全市场指数）：{summary}")

    results: list[dict] = []
    for strategy in strategies:
        for profile_id in profiles:
            profile = get_risk_profile(profile_id)
            outcome = run_portfolio_backtest(
                targets, strategy_ids=[strategy], one_way_cost_bps=args.cost_bps,
                max_positions=args.max_positions, max_single_weight=args.max_single_weight,
                max_industry_weight=args.max_industry_weight,
                analysis_start_date=analysis_start, risk=profile,
                regime=(regime_gates.get(profile.regime_ma_days)
                        if profile.regime_filter else None))
            curve = outcome["portfolio"]["curve"]
            judgement = evaluate_acceptance(curve, thresholds=thresholds,
                                            risk_stats=outcome.get("risk_stats"),
                                            label=f"{strategy} × {profile_id}")
            judgement["portfolio_metrics"] = outcome["portfolio"]["metrics"]
            judgement["baselines"] = {
                "equal_weight_buy_hold": outcome["baselines"]["equal_weight_buy_hold"]["metrics"],
                "cash": outcome["baselines"]["cash"],
            }
            judgement["comparison"] = outcome["comparison"]
            results.append(judgement)
            print(f"  ✓ {strategy:<18}{profile_id:<22} "
                  f"累计 {judgement['metrics']['cumulative_return_pct']:>9.2f}%  "
                  f"年化 {judgement['metrics']['annualized_return_pct']:>8.2f}%  "
                  f"最大回撤 {judgement['metrics']['max_drawdown_pct']:>7.2f}%  "
                  f"单年最差 {judgement['metrics']['worst_year_return_pct']:>7.2f}%  "
                  f"→ {judgement['verdict']}")

    print(f"\n{'=' * 104}\n验收判定（阈值：最大回撤 ≤{thresholds.max_drawdown_pct}%｜"
          f"单年最差 ≥−{thresholds.max_worst_year_loss_pct}%｜回撤持续 ≤"
          f"{thresholds.max_drawdown_duration_days} 天｜期望 >0）\n{'=' * 104}")
    header = (f"{'策略':<18}{'风控档位':<22}{'累计':>10}{'年化':>9}{'最大回撤':>10}"
              f"{'单年最差':>10}{'回撤天数':>9}  判定")
    print(header)
    for item in results:
        metrics = item["metrics"]
        print(f"{item['label'].split(' × ')[0]:<18}"
              f"{item['label'].split(' × ')[1]:<22}"
              f"{metrics['cumulative_return_pct']:>10.2f}{metrics['annualized_return_pct']:>9.2f}"
              f"{metrics['max_drawdown_pct']:>10.2f}{metrics['worst_year_return_pct']:>10.2f}"
              f"{metrics['max_drawdown_duration_days']:>9}  {item['verdict']}")

    print(f"\n对照基线（同一股票池、同一区间）：")
    for item in results[:1]:
        base = item["baselines"]["equal_weight_buy_hold"]
        print(f"  等权买入持有：累计 {base.get('cumulative_return_pct')}%｜"
              f"年化 {base.get('annualized_return_pct')}%｜最大回撤 {base.get('max_drawdown_pct')}%")

    failed = [item for item in results if item["verdict"] != "通过"]
    if failed:
        print(f"\n未通过明细（每条都给出实测值与人话原因）：")
        for item in failed:
            print(f"\n  【{item['label']}】{item['summary']}")
            for check in item["checks"]:
                mark = "✅" if check["passed"] else "❌"
                print(f"    {mark} {check['title']}：{check['why']}")
    print(f"\n风控拦下了什么（合计，跨标的×策略）：")
    stats = results[-1].get("risk_stats") or {}
    for key, value in stats.items():
        print(f"  {key:<32}{value}")

    out_dir = (Path(args.out) if args.out
               else ROOT / "data" / "exports" / "strategy-acceptance"
               / datetime.now(CST).strftime("%Y-%m-%d"))
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "report.json"
    target.write_text(json.dumps({
        "as_of": datetime.fromtimestamp(as_of_ms / 1000, CST).strftime("%Y-%m-%d"),
        "universe": {"size": len(targets), "symbols": [item.thscode for item in targets],
                     "selection": f"liquidity_top_{args.symbols}"},
        "cost_bps_one_way": args.cost_bps,
        "risk_profiles": {pid: get_risk_profile(pid).model_dump() for pid in profiles},
        "thresholds": thresholds.model_dump(),
        "results": results,
        "elapsed_seconds": round(time.time() - started, 1),
    }, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    print(f"\n耗时 {time.time() - started:.0f}s｜输出：{target}")
    print("\n研究参考，不构成投资建议。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
