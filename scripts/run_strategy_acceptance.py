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
from backend.app.portfolio.construction import ConstructionPolicy  # noqa: E402
from backend.app.settings import load_settings  # noqa: E402
from backend.app.strategy.acceptance import (  # noqa: E402
    AcceptanceThresholds,
    evaluate_acceptance,
)
from backend.app.strategy.local_bars import local_price_bars  # noqa: E402
from backend.app.strategy.regime import (  # noqa: E402
    market_index_returns,
    market_regime_series,
    realized_vol_series,
    regime_gate,
    vol_target_exposure,
)
from backend.app.strategy.registry import get_risk_profile  # noqa: E402

CST = timezone(timedelta(hours=8))
DEFAULT_STRATEGIES = ("trend_20_60_v1", "reversal_5d_v1")
#: 默认四档：无风控（对照）/ 保守 / 波动率目标 15% / 波动率目标 10%。
#: 两档波动目标必须一起跑：只知道"某一档好看"无法判断是不是系数碰巧，
#: 单调性（目标越低 → 回撤越浅 → 收益越低）才是它真的在起作用的证据。
#: 下跌市不交易那两档（risk_regime_v1 / risk_regime_slow_v1）2026-09-15 已实测未通过，
#: 结论记在 25 号文档；需要复跑时用 --risk-profiles 显式指定。
DEFAULT_PROFILES = ("risk_none_v1", "risk_conservative_v1", "risk_voltarget_v1",
                    "risk_voltarget_low_v1")


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
    #: **显式的总仓位上限**。默认 0.95（留 5% 现金覆盖换手成本）。
    #: 2026-09-15 之前没有这个参数，"投多少"是被行业上限**隐式**决定的；而验收脚本把
    #: 所有标的的行业都写成 `未分类`，于是"行业上限 30%"退化成了"总仓位 30%"，
    #: 对照的等权买入持有却是满仓——那个对比不公平，而且报告里看不出来。
    parser.add_argument("--max-total-exposure", type=float, default=0.95)
    #: 组合构建方案：`rank_equal`（历史对照）/ `inverse_vol` / `equal_risk`。
    #: 三个一起跑才能说清"是风险分配起了作用，还是别的"。
    parser.add_argument("--constructions", default="rank_equal,inverse_vol,equal_risk")
    parser.add_argument("--turnover-band", type=float, default=0.0,
                        help="换手带：目标与当前权重之差小于它就不调仓（0 = 每次调）")
    parser.add_argument("--risk-lookback-days", type=int, default=120)
    parser.add_argument("--shrinkage", type=float, default=0.5)
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
    constructions = [item.strip() for item in args.constructions.split(",") if item.strip()]
    unknown = [item for item in constructions
               if item not in ("rank_equal", "inverse_vol", "equal_risk")]
    if unknown:
        print(f"未知的组合构建方案：{unknown}")
        return 1

    def policy_for(scheme: str) -> ConstructionPolicy | None:
        """`rank_equal` 走**历史路径**（`construction=None`），保证"新旧对比只有一个变量"。"""
        if scheme == "rank_equal":
            return None
        return ConstructionPolicy(
            scheme=scheme, lookback_days=args.risk_lookback_days, shrinkage=args.shrinkage,
            turnover_band=args.turnover_band, max_positions=args.max_positions,
            max_single_weight=args.max_single_weight,
            max_industry_weight=args.max_industry_weight,
            max_total_exposure=args.max_total_exposure)

    started = time.time()
    print(f"目标股票池：流动性前 {args.symbols} 只（截至 "
          f"{datetime.fromtimestamp(as_of_ms / 1000, CST):%Y-%m-%d}）"
          f"｜成本 {args.cost_bps}bps/边｜{len(strategies)} 策略 × {len(profiles)} 风控档位 × "
          f"{len(constructions)} 组合构建方案")
    print(f"总仓位上限 {args.max_total_exposure:.0%}（显式；留现金覆盖成本）｜"
          f"单票 {args.max_single_weight:.0%}｜行业 {args.max_industry_weight:.0%}")
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

    # 波动率目标：波动率序列只算一次，不同档位只是在同一序列上取不同的目标值。
    vol_by_lookback: dict[int, dict[str, float]] = {}
    exposure_by_profile: dict[str, dict[str, float]] = {}
    vol_profiles = [get_risk_profile(pid) for pid in profiles
                    if get_risk_profile(pid).target_vol_pct is not None]
    if vol_profiles:
        market_returns = market_index_returns(repository, end_ms=as_of_ms)
        for profile in vol_profiles:
            if profile.vol_lookback_days not in vol_by_lookback:
                vol_by_lookback[profile.vol_lookback_days] = realized_vol_series(
                    market_returns, lookback_days=profile.vol_lookback_days)
            exposure_by_profile[profile.id] = vol_target_exposure(
                vol_by_lookback[profile.vol_lookback_days],
                target_vol=profile.target_vol_pct, min_exposure=profile.min_exposure)
        for lookback, vols in sorted(vol_by_lookback.items()):
            values = sorted(vols.values())
            middle = values[len(values) // 2] if values else 0.0
            print(f"市场已实现波动（{lookback} 日年化）：中位 {middle:.1%}｜"
                  f"最低 {values[0]:.1%}｜最高 {values[-1]:.1%}｜样本 {len(values)} 天")
        for profile in vol_profiles:
            series = exposure_by_profile[profile.id]
            if not series:
                continue
            values = [series[key] for key in sorted(series)]
            print(f"  仓位序列 {profile.id}：均值 {sum(values) / len(values):.1%}｜"
                  f"最低 {min(values):.1%}｜满仓占 "
                  f"{sum(1 for value in values if value >= 0.999999) / len(values):.0%} 的交易日")

    results: list[dict] = []
    for strategy in strategies:
        for profile_id in profiles:
            profile = get_risk_profile(profile_id)
            for scheme in constructions:
                outcome = run_portfolio_backtest(
                    targets, strategy_ids=[strategy], one_way_cost_bps=args.cost_bps,
                    max_positions=args.max_positions, max_single_weight=args.max_single_weight,
                    max_industry_weight=args.max_industry_weight,
                    max_total_exposure=args.max_total_exposure,
                    analysis_start_date=analysis_start, risk=profile,
                    regime=(regime_gates.get(profile.regime_ma_days)
                            if profile.regime_filter else None),
                    vol_exposure=exposure_by_profile.get(profile_id),
                    construction=policy_for(scheme))
                curve = outcome["portfolio"]["curve"]
                judgement = evaluate_acceptance(
                    curve, thresholds=thresholds, risk_stats=outcome.get("risk_stats"),
                    label=f"{strategy} × {profile_id} × {scheme}")
                judgement["strategy_id"] = strategy
                judgement["risk_profile_id"] = profile_id
                judgement["construction_scheme"] = scheme
                judgement["portfolio_metrics"] = outcome["portfolio"]["metrics"]
                judgement["construction_audit"] = outcome.get("construction")
                judgement["baselines"] = {
                    "equal_weight_buy_hold": outcome["baselines"]["equal_weight_buy_hold"]["metrics"],
                    "cash": outcome["baselines"]["cash"],
                }
                judgement["comparison"] = outcome["comparison"]
                results.append(judgement)
                portfolio_metrics = judgement["portfolio_metrics"]
                print(f"  ✓ {strategy:<17}{profile_id:<22}{scheme:<13} "
                      f"累计 {judgement['metrics']['cumulative_return_pct']:>9.2f}%  "
                      f"年化 {judgement['metrics']['annualized_return_pct']:>8.2f}%  "
                      f"回撤 {judgement['metrics']['max_drawdown_pct']:>7.2f}%  "
                      f"仓位 {portfolio_metrics.get('avg_invested_weight_pct', 0):>6.1f}%  "
                      f"→ {judgement['verdict']}")

    print(f"\n{'=' * 116}\n验收判定（阈值：最大回撤 ≤{thresholds.max_drawdown_pct}%｜"
          f"单年最差 ≥−{thresholds.max_worst_year_loss_pct}%｜回撤持续 ≤"
          f"{thresholds.max_drawdown_duration_days} 天｜期望 >0）\n{'=' * 116}")
    header = (f"{'策略':<17}{'风控档位':<22}{'构建方案':<13}{'累计':>9}{'年化':>8}"
              f"{'最大回撤':>9}{'单年最差':>9}{'回撤天数':>8}{'平均仓位':>9}{'换手':>8}  判定")
    print(header)
    for item in results:
        metrics = item["metrics"]
        portfolio_metrics = item["portfolio_metrics"]
        print(f"{item['strategy_id']:<17}{item['risk_profile_id']:<22}"
              f"{item['construction_scheme']:<13}"
              f"{metrics['cumulative_return_pct']:>9.2f}{metrics['annualized_return_pct']:>8.2f}"
              f"{metrics['max_drawdown_pct']:>9.2f}{metrics['worst_year_return_pct']:>9.2f}"
              f"{metrics['max_drawdown_duration_days']:>8}"
              f"{portfolio_metrics.get('avg_invested_weight_pct', 0):>8.1f}%"
              f"{portfolio_metrics.get('annualized_turnover_pct', 0):>7.0f}%  {item['verdict']}")

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
                     "selection": f"liquidity_top_{args.symbols}",
                     "industry_data": "none：本地无行业分类数据，**行业上限未生效**"},
        "cost_bps_one_way": args.cost_bps,
        "portfolio_limits": {
            "max_positions": args.max_positions,
            "max_single_weight": args.max_single_weight,
            "max_industry_weight": args.max_industry_weight,
            "max_industry_weight_applied": False,
            "max_total_exposure": args.max_total_exposure,
        },
        "constructions": constructions,
        "construction_params": {"risk_lookback_days": args.risk_lookback_days,
                                "shrinkage": args.shrinkage,
                                "turnover_band": args.turnover_band},
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
