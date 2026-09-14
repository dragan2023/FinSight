"""财务因子的横截面 IC 评价——**执行**已预注册的设计 `financial_factor_study_design@v1`。

设计与因子定义登记在 `scripts/governance_register_financial_factors.py`（进 `hypothesis_registry`）。
本模块只负责把它跑出来。**设计不在这里改**——要改就先改登记（新 reference），
否则就成了"看到结果再调口径"。

| 项 | 登记内容 | 实现位置 |
|---|---|---|
| 样本域 | 本地财务数据集 ∩ 当日通过既有股票池规则的标的 | `screener.service.eligible_rows` |
| 因子取值 | 6 个财务因子（F-Score 及其 4 个分量 + 归一化 F-Score） | `factor_values` |
| as-of | 只用 `available_from_ms <= T` 的报告期，且**各表取共同期** | `financials.select_common_period` |
| 结果 | 主检验 = `next_open`（T+1 开盘 → T+20 收盘，D53 的可成交口径） | `event_panel_window` |
| 统计量 | Rank IC 按评估日聚合后的**时间序列 t 值** | `report` |
| 安慰剂 | 逐标的置乱（**仅机制自检，不作门槛**，`13` 号文档的纠正） | `study_date` |
| 多重检验 | 声明的 36 条比较 → 族级 Bonferroni 门槛 | `report` 的 `multiplicity` |

**本轮必须量化披露的东西**：这 202 只标的是按 **2026-09-04 的成交额前 200** 选出来的，
却用来评价 2016 年以来的历史——标的层面存在"以未来流动性选样"的偏置。报告里的
`bias` 块给出每个评估日样本的成交额分位中位数与日期分布，**结论不得外推为全市场**。
"""
from __future__ import annotations

import math
import random
import statistics

from ..advisor.algorithms.f_score import calculate_f_score
from ..market_warehouse.financials import (
    compute_ratios,
    f_score_inputs,
    group_periods,
    select_common_period,
    select_prior_year_facts,
)
from .service import _spearman, _stdev, ScreenerService

#: 因子集与方向——**与登记条目逐字一致**，不在这里二次判断方向。
FACTOR_DIRECTIONS = {
    "f_score": "high",
    "f_score_ratio": "high",
    "roa": "high",
    "assets_debt_ratio": "low",
    "sale_gross_margin": "high",
    "total_assets_turnover_ratio": "high",
}
FACTORS = tuple(FACTOR_DIRECTIONS)
PLACEBO_DRAWS = 200
#: 每个评估日至少要有多少只有效样本才参与统计（登记里写的就是 20）。
MIN_SAMPLES = 20
#: 报告期统一性门槛：众数占比低于它就剔除该评估日（累计口径的比率跨 YTD 窗口不可比）。
MIN_MODAL_SHARE = 0.90
#: 分时段复现的切分（与 `13` 号文档的采样区间口径一致）。
REPLICATION_CUTOFF = "2021-01-01"


def factor_values(facts: dict[str, float], prior_facts: dict[str, float] | None
                  ) -> dict[str, float | None]:
    """按声明的定义算 6 个因子。缺值一律 `None`（不填补、不用 0 顶替）。"""
    values: dict[str, float | None] = dict(compute_ratios(facts))
    values["f_score"] = None
    values["f_score_ratio"] = None
    if prior_facts:
        report = calculate_f_score(f_score_inputs(facts), f_score_inputs(prior_facts),
                                   evidence_id="financial_ic")
        if report.get("eligible"):
            available = report.get("available_items") or 0
            values["f_score"] = float(report["score"])
            values["f_score_ratio"] = (report["score"] / available) if available else None
    return values


class FinancialFactorStudy:
    """财务因子 IC 评价。**每个参数都显式**，因为它们都会影响结论。"""

    def __init__(self, repository, *, screener: ScreenerService | None = None) -> None:
        self._repository = repository
        self._screener = screener or ScreenerService(repository)
        self._grouped: dict[str, dict] | None = None

    # --- 取数与对齐 -------------------------------------------------------
    def _facts_by_symbol(self) -> dict[str, dict]:
        """整表读一次并分组（约 18 万行）——PIT 选择在纯函数里做，可单测。"""
        if self._grouped is None:
            by_symbol: dict[str, list[dict]] = {}
            for row in self._repository.financial_facts_all():
                by_symbol.setdefault(row["thscode"], []).append(row)
            self._grouped = {symbol: group_periods(rows)
                             for symbol, rows in by_symbol.items()}
        return self._grouped

    def snapshots(self, date_ms: int, symbols: set[str] | None = None) -> dict[str, dict]:
        """当日每个标的的 PIT 快照与因子值（共同期 + 上年同季）。"""
        grouped = self._facts_by_symbol()
        out: dict[str, dict] = {}
        for symbol, periods in grouped.items():
            if symbols is not None and symbol not in symbols:
                continue
            period_end, facts, per_statement = select_common_period(periods, date_ms)
            if period_end is None or not facts:
                continue
            previous = select_prior_year_facts(periods, per_statement[
                next(iter(per_statement))]["period_key"], date_ms, thscode=symbol)
            out[symbol] = {
                "period_key": per_statement[next(iter(per_statement))]["period_key"],
                "period_end_ms": period_end,
                "available_basis": next(iter(per_statement.values()))["available_basis"],
                "values": factor_values(facts, previous["facts"] if previous else None),
            }
        return out

    def _returns(self, date_ms: int) -> dict[str, dict]:
        """前向收益（两条口径都在面板里；主检验用 `next_open`）。"""
        for stamp, rows in self._repository.event_panel_window(dates=[date_ms],
                                                              panel_ready_checked=True):
            if stamp == date_ms:
                return rows
        return {}

    @staticmethod
    def _oriented(values: list[float], factor: str) -> list[float]:
        return values if FACTOR_DIRECTIONS[factor] == "high" else [-value for value in values]

    # --- 单个评估日 -------------------------------------------------------
    def study_date(self, date_ms: int, *, basis: str = "next_open",
                   draws: int = PLACEBO_DRAWS, seed: int = 20260913) -> dict:
        """一个评估日：每个因子一条 Rank IC + 逐标的置乱安慰剂（机制自检）。"""
        kept, diagnostics, limit_up = self._screener.eligible_rows(date_ms)
        eligible = {row["thscode"] for row in kept}
        panel = self._returns(date_ms)
        column = "forward_return_next_open" if basis == "next_open" else "forward_return"
        returns = {symbol: row[column] for symbol, row in panel.items()
                   if row.get(column) is not None}
        snapshots = self.snapshots(date_ms, eligible)
        symbols = sorted(symbol for symbol in snapshots
                         if symbol in returns and symbol in panel)
        base = {"as_of": _day(date_ms), "basis": basis, "universe": len(eligible),
                "with_financials": len(snapshots), "samples": len(symbols),
                "limit_up_basis": limit_up.basis,
                "period_mix": _period_mix(snapshots),
                "bias": _liquidity_bias(panel, symbols)}
        if len(symbols) < MIN_SAMPLES:
            return {**base, "eligible": False,
                    "reason": f"有效样本仅 {len(symbols)} 只（< {MIN_SAMPLES}），该日不参与统计。",
                    "factors": {}}
        forward = [returns[symbol] for symbol in symbols]
        rng = random.Random(f"{date_ms}:{seed}")
        factors: dict[str, dict] = {}
        for factor in FACTORS:
            column_values = [snapshots[symbol]["values"].get(factor) for symbol in symbols]
            paired = [(index, value) for index, value in enumerate(column_values)
                      if value is not None]
            if len(paired) < MIN_SAMPLES:
                factors[factor] = {"samples": len(paired), "real_ic": None,
                                   "reason": "有效取值不足"}
                continue
            oriented = self._oriented([value for _, value in paired], factor)
            returns_paired = [forward[index] for index, _ in paired]
            real_ic = _spearman(oriented, returns_paired)
            placebo = []
            for _ in range(draws):
                shuffled = returns_paired[:]
                rng.shuffle(shuffled)
                value = _spearman(oriented, shuffled)
                if value is not None:
                    placebo.append(value)
            factors[factor] = {
                "samples": len(paired),
                "real_ic": None if real_ic is None else round(real_ic, 4),
                "placebo_mean": round(statistics.fmean(placebo), 6) if placebo else None,
                "placebo_sd": round(_stdev(placebo), 6) if len(placebo) > 1 else None,
                "iid_null_sd": round(1 / math.sqrt(len(paired) - 1), 6) if len(paired) > 2 else None,
            }
        return {**base, "eligible": True, "factors": factors}

    # --- 汇总 -------------------------------------------------------------
    def report(self, *, start_ms: int | None = None, end_ms: int | None = None,
               bases: tuple[str, ...] = ("next_open", "close_t"),
               draws: int = PLACEBO_DRAWS, seed: int = 20260913) -> dict:
        candidates = self._repository.month_end_trading_days(start_ms=start_ms, end_ms=end_ms)
        kept_dates: list[int] = []
        dropped: list[dict] = []
        for date_ms in candidates:
            kept, _, _ = self._screener.eligible_rows(date_ms)
            eligible = {row["thscode"] for row in kept}
            snapshots = self.snapshots(date_ms, eligible)
            mix = _period_mix(snapshots)
            share = mix["modal_share"] if mix else 0.0
            if share < MIN_MODAL_SHARE:
                dropped.append({"as_of": _day(date_ms), "modal_share": round(share, 4),
                                "samples": len(snapshots)})
            else:
                kept_dates.append(date_ms)

        report: dict = {
            "design": "hypothesis_registry: financial_factor_study_design@v1",
            "factors": {name: FACTOR_DIRECTIONS[name] for name in FACTORS},
            "evaluation_dates": {"candidates": len(candidates), "kept": len(kept_dates),
                                 "dropped": dropped},
            "modal_share_threshold": MIN_MODAL_SHARE,
            "min_samples": MIN_SAMPLES,
            "horizon_days": 20,
            "bases": {},
            "disclaimer": "研究参考，不构成投资建议。",
        }
        for basis in bases:
            per_date: list[dict] = []
            for date_ms in kept_dates:
                result = self.study_date(date_ms, basis=basis, draws=draws, seed=seed)
                if result.get("eligible"):
                    per_date.append(result)
            report["bases"][basis] = self._aggregate(basis, per_date, kept_dates, draws)
        report["bias"] = self._bias_summary(report["bases"])
        return report

    def _aggregate(self, basis: str, per_date: list[dict], kept_dates: list[int],
                   draws: int) -> dict:
        by_factor: dict[str, dict] = {}
        for factor in FACTORS:
            series = [(item["as_of"], item["factors"][factor]["real_ic"])
                      for item in per_date if factor in item["factors"]
                      and item["factors"][factor].get("real_ic") is not None]
            placebos = [item["factors"][factor].get("placebo_mean") for item in per_date
                        if factor in item["factors"]]
            placebos = [value for value in placebos if value is not None]
            by_factor[factor] = _series_stats([value for _, value in series],
                                              _dates(series), placebos, draws)
        by_period = {}
        for label, want_late in (("2016-2020", False), ("2021-", True)):
            subset = [item for item in per_date
                      if (item["as_of"] >= REPLICATION_CUTOFF) is want_late]
            if len(subset) < 3:
                continue
            for factor in FACTORS:
                series = [(item["as_of"], item["factors"][factor]["real_ic"])
                          for item in subset if factor in item["factors"]
                          and item["factors"][factor].get("real_ic") is not None]
                by_period[f"{factor}@{label}"] = _series_stats(
                    [value for _, value in series], _dates(series), [], draws)
        comparisons = _declared_comparisons()
        return {"basis": basis, "dates_used": len(per_date),
                "dates_candidates": len(kept_dates),
                "samples_median": (statistics.median([item["samples"] for item in per_date])
                                   if per_date else None),
                "period_mix_median_share": (statistics.median(
                    [item["period_mix"]["modal_share"] for item in per_date
                     if item["period_mix"]]) if per_date else None),
                # **逐日明细要留下**：登记时承诺了"量化披露样本选择偏置"，
                # 把每天的偏置指标与各因子 IC 一并落盘，别人才能复核（不能只给汇总）。
                "per_date": [{"as_of": item["as_of"], "samples": item["samples"],
                              "universe": item["universe"],
                              "modal_share": item["period_mix"].get("modal_share"),
                              "modal_period": item["period_mix"].get("modal"),
                              "liquidity_percentile_median": (item["bias"] or {}).get(
                                  "liquidity_percentile_median"),
                              "ic": {factor: item["factors"][factor]["real_ic"]
                                     for factor in FACTORS if factor in item["factors"]}}
                             for item in per_date],
                "bias": _bias_aggregate(per_date),
                "factors": by_factor, "by_period": by_period,
                "multiplicity": {
                    "declared_comparisons": comparisons,
                    "family": "financial_fundamentals",
                    "family_prior_statistics": 0,
                    "bonferroni_family_abs_t": round(_z(0.05 / comparisons), 4),
                    "note": "计数 = 6 因子 × 2 前向口径 × 3 区间 = 36，登记时写死；"
                            "本轮的 36 条必须计入**下一轮同族**的族级先验。",
                }}

    @staticmethod
    def _bias_summary(bases: dict) -> dict:
        return {
            "what": "这 202 只标的按 **2026-09-04 的成交额前 200** 选出，却被用于评价 2016 年以来的"
                    "历史 → **标的层面存在『以未来流动性选样』的偏置**。",
            "measured": "每个评估日的 `bias.liquidity_percentile_median` = 样本在**当日全市场**"
                        "成交额分位的中位数（0.5 = 中位，1.0 = 最活跃）。",
            "conclusion_limit": "结论**只能**表述为『在这个已选定的高流动性样本内』，"
                                "**不得**外推为全市场结论；样本内 IC 也不等于全市场 IC。",
            "per_basis": {basis: payload.get("bias") for basis, payload in bases.items()},
        }


# --- 小工具 ------------------------------------------------------------------

def _day(date_ms: int) -> str:
    from datetime import datetime, timedelta, timezone

    return datetime.fromtimestamp(date_ms / 1000,
                                 timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def _dates(series: list[tuple[str, float]]) -> list[str]:
    return [label for label, _ in series]


def _period_mix(snapshots: dict[str, dict]) -> dict:
    """当日样本的报告期构成：众数占比太低说明跨了 YTD 窗口，比率不可比。"""
    if not snapshots:
        return {}
    counts: dict[str, int] = {}
    for item in snapshots.values():
        counts[item["period_key"]] = counts.get(item["period_key"], 0) + 1
    modal, count = max(counts.items(), key=lambda kv: kv[1])
    return {"modal": modal, "modal_share": count / len(snapshots),
            "distinct": len(counts), "counts": counts}


def _liquidity_bias(panel: dict[str, dict], symbols: list[str]) -> dict | None:
    """样本在**当日全市场**成交额分位的中位数——用于量化样本选择偏置。"""
    turnover = {symbol: row.get("turnover_20") for symbol, row in panel.items()
                if (row.get("turnover_20") or 0) > 0}
    if len(turnover) < 50 or not symbols:
        return None
    ordered = sorted(turnover.values())
    ranks = {symbol: (sum(1 for value in ordered if value < turnover[symbol])
                      / max(1, len(ordered) - 1))
             for symbol in turnover}
    picked = [ranks[symbol] for symbol in symbols if symbol in ranks]
    if not picked:
        return None
    return {"liquidity_percentile_median": round(statistics.median(picked), 4),
            "liquidity_percentile_p10": round(sorted(picked)[len(picked) // 10], 4),
            "universe_size": len(turnover)}


def _bias_aggregate(per_date: list[dict]) -> dict:
    """把逐日的样本选择偏置指标汇总——登记时承诺了"量化披露"，就不能只给一句话。"""
    percentiles = [(item["bias"] or {}).get("liquidity_percentile_median") for item in per_date]
    percentiles = sorted(value for value in percentiles if value is not None)
    universes = [(item["bias"] or {}).get("universe_size") for item in per_date]
    universes = [value for value in universes if value]
    if not percentiles:
        return {"dates_with_metric": 0,
                "note": "未能算出偏置指标（当日全市场成交额样本不足）。"}
    return {
        "dates_with_metric": len(percentiles),
        "liquidity_percentile_median": round(statistics.median(percentiles), 4),
        "liquidity_percentile_p10": round(percentiles[len(percentiles) // 10], 4),
        "liquidity_percentile_min": round(percentiles[0], 4),
        "market_universe_median": int(statistics.median(universes)) if universes else None,
        "interpretation": "样本在**当日全市场**成交额分位的中位数：1.0 = 全市场最活跃，"
                          "0.5 = 中位。这个数越高，说明样本被『以未来流动性选样』偏置得越厉害，"
                          "结论越不能外推。",
    }


def _series_stats(values: list[float], dates: list[str], placebos: list[float],
                  draws: int) -> dict:
    clean = [value for value in values if value is not None]
    if len(clean) < 3:
        return {"dates": len(clean), "mean_ic": None, "t_stat": None,
                "verdict": "样本不足（<3 个评估日），无法判定"}
    mean_ic = statistics.fmean(clean)
    sd = _stdev(clean)
    t_stat = (mean_ic / (sd / math.sqrt(len(clean)))) if sd > 0 else None
    return {
        "dates": len(clean), "mean_ic": round(mean_ic, 4), "ic_sd": round(sd, 4),
        "t_stat": None if t_stat is None else round(t_stat, 4),
        "positive_ic_share": round(sum(1 for value in clean if value > 0) / len(clean), 4),
        "placebo_mean_median": (round(statistics.median(placebos), 6) if placebos else None),
        "placebo_draws": draws,
        "verdict": _verdict(t_stat),
    }


def _verdict(t_stat: float | None) -> str:
    if t_stat is None:
        return "样本不足，无法判定"
    if t_stat >= 1.96:
        return "IC 为正且 |t| ≥ 1.96（**仍需过族级门槛**，并注意样本选择偏置）"
    if t_stat <= -1.96:
        return "IC 显著为负：声明的方向与数据相反，应重新检视先验"
    return f"与噪声无异（|t| = {abs(t_stat):.2f} < 1.96）"


def _declared_comparisons() -> int:
    return len(FACTORS) * 2 * 3


def _z(alpha: float) -> float:
    from .events import _z_for_two_sided

    return _z_for_two_sided(alpha)
