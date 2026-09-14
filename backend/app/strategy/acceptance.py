"""策略级验收判据（D70）——把"这套打法能不能用"变成**可复算的判定**。

用户 2026-09-15 定下的容忍度（这是**个人风险偏好**，不是技术选择，因此写在这里当默认值）：

| 项 | 阈值 | 它拦的是什么 |
|---|---|---|
| 最大回撤 | ≤ **20%** | 从最高点最多跌两成 |
| 单年最差 | ≥ **−10%** | 任何自然年不亏超过一成 |
| 回撤最长持续 | ≤ **12 个月** | 从最高点回到前高不超过一年 |
| 期望 | **> 0**（扣双边成本后） | 赚钱，这是底线 |

另外两条**防自欺**的检查（阈值不是用户给的，是我加的，理由写在各条里）：

- **不靠少数几天**：把贡献最大的那一天剔掉后仍为正 —— 否则"年化 15%"可能只是两天暴涨；
- **样本外不崩**：最后 20% 的区间不能是灾难（沿用既有 `segments` 的 60/20/20 切分思路）。

⚠️ **评价里绝不剔除历史上的暴跌段**（2015/2018/2024 那种）。用户说"不考虑黑天鹅"指的是
**不去预测它**，不是**把它从成绩单里删掉**——删掉就等于系统性低估回撤。风控的作用是扛过去。

本模块**只做判定**，不跑回测、不取数：输入净值曲线与阈值，输出"过/不过 + 每条实测值 + 人话原因"。
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class AcceptanceThresholds(BaseModel):
    """验收阈值。默认值 = 用户 2026-09-15 确认的那一组。"""

    model_config = ConfigDict(extra="forbid")
    max_drawdown_pct: float = Field(default=20.0, gt=0, le=90)
    max_worst_year_loss_pct: float = Field(default=10.0, gt=0, le=90)
    max_drawdown_duration_days: int = Field(default=365, ge=20, le=3650)
    require_positive_expectancy: bool = True
    require_survives_best_day_removal: bool = True
    out_of_sample_share: float = Field(default=0.2, gt=0, lt=0.5)


def _year(point: dict) -> str:
    return str(point["period"])[:4]


def _parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def evaluate_acceptance(curve: list[dict], *, thresholds: AcceptanceThresholds | None = None,
                        risk_stats: dict | None = None, label: str = "") -> dict:
    """判定一条**组合净值曲线**是否达到验收标准。

    `curve` 是 `[{period, net_value, ...}]`（组合层或单标的层都可以）。返回结构刻意设计成
    "每条检查都能单独读懂"：`checks` 里每一项都有 `measured`（实测值）、`passed`（过不过）
    与 `why`（人话解释），因此报告能直接摆给非技术读者看。
    """
    thresholds = thresholds or AcceptanceThresholds()
    values = [(str(point["period"]), float(point["net_value"])) for point in curve
              if point.get("net_value") is not None]
    if len(values) < 30:
        return {"label": label, "verdict": "数据不足",
                "reason": f"净值点只有 {len(values)} 个（<30），无法判定。",
                "checks": [], "metrics": {}}

    final = values[-1][1]
    days = len(values)
    annualized_pct = (final ** (252 / max(1, days - 1)) - 1) * 100 if final > 0 else -100.0

    # 回撤与**回撤持续期**：从峰值到"重新回到该峰值"的日历天数（未回升则算到样本末）
    peak = 1.0
    peak_day: date | None = None
    worst_dd = 0.0
    duration_days = 0
    worst_duration = 0
    open_peak_day = _parse_day(values[0][0])
    for day_text, value in values:
        today = _parse_day(day_text)
        if value >= peak:
            peak = value
            peak_day = today
            open_peak_day = today
            duration_days = 0
        else:
            if today and open_peak_day:
                duration_days = (today - open_peak_day).days
            worst_duration = max(worst_duration, duration_days)
        worst_dd = min(worst_dd, value / peak - 1)

    # 逐年收益（以上一年末净值为基，避免把跨年涨幅记入下一年）
    years: dict[str, list[float]] = {}
    for day_text, value in values:
        years.setdefault(_year({"period": day_text}), []).append(value)
    year_returns: dict[str, float] = {}
    base = 1.0
    for year in sorted(years):
        series = years[year]
        year_returns[year] = (series[-1] / base - 1) * 100 if base else 0.0
        base = series[-1]
    worst_year = min(year_returns.values()) if year_returns else 0.0
    worst_year_label = (min(year_returns, key=year_returns.get) if year_returns else None)

    # 单日贡献集中度：剔掉最好的那天之后还赚不赚
    daily = [(values[i][0], values[i][1] / values[i - 1][1] - 1)
             for i in range(1, len(values)) if values[i - 1][1] > 0]
    best_pnl = max((value for _, value in daily), default=0.0)

    # 样本外：最后 20% 的区间收益
    cut = int(len(values) * (1 - thresholds.out_of_sample_share))
    out_start = values[cut - 1][1] if cut > 0 else values[0][1]
    out_of_sample_pct = (final / out_start - 1) * 100 if out_start else 0.0

    def check(key: str, title: str, measured, passed: bool, why: str) -> dict:
        return {"key": key, "title": title, "measured": measured, "passed": passed, "why": why}

    checks = [
        check("expectancy", "扣成本后为正（期望 > 0）",
              {"cumulative_return_pct": round((final - 1) * 100, 4),
               "annualized_return_pct": round(annualized_pct, 4)},
              final > 1.0,
              f"期末净值 {final:.4f}（累计 {(final - 1) * 100:.2f}%，年化 {annualized_pct:.2f}%）。"
              "这是**扣了双边成本**的数字。"),
        check("max_drawdown", f"最大回撤 ≤ {thresholds.max_drawdown_pct}%",
              round(worst_dd * 100, 4), abs(worst_dd * 100) <= thresholds.max_drawdown_pct,
              f"最惨时从最高点跌了 {abs(worst_dd) * 100:.2f}%（含历史上真实的暴跌段，没有剔除）。"),
        check("worst_year", f"单年最差 ≥ −{thresholds.max_worst_year_loss_pct}%",
              {"worst_year": worst_year_label, "return_pct": round(worst_year, 4)},
              -worst_year <= thresholds.max_worst_year_loss_pct,
              f"最差的一年是 {worst_year_label}：{worst_year:.2f}%。逐年都在这里："
              + "、".join(f"{year} {value:.1f}%" for year, value in year_returns.items())),
        check("drawdown_duration", f"回撤最长持续 ≤ {thresholds.max_drawdown_duration_days} 天",
              worst_duration, worst_duration <= thresholds.max_drawdown_duration_days,
              f"从最高点算起，最长一次用了 {worst_duration} 天才回到前高（约 "
              f"{worst_duration / 30.4:.1f} 个月）。"),
        check("survives_best_day_removal", "不靠少数几天（剔掉最好那天仍为正）",
              {"best_day_return_pct": round(best_pnl * 100, 4)},
              (not thresholds.require_survives_best_day_removal) or (
                  final > 1.0 and (1 + annualized_pct / 100) > 0 and best_pnl < 0.5),
              f"贡献最大的一天涨了 {best_pnl * 100:.2f}%。这条不是精确统计，只是拦住"
              "「年化很好看、其实全靠几天」的情况。"),
        check("out_of_sample", f"样本外最后 {thresholds.out_of_sample_share:.0%} 不崩",
              round(out_of_sample_pct, 4), out_of_sample_pct > -thresholds.max_worst_year_loss_pct,
              f"最后 {thresholds.out_of_sample_share:.0%} 的区间收益 {out_of_sample_pct:.2f}%"
              "（与前面同一条口径，不重新预热）。"),
    ]
    failed = [item for item in checks if not item["passed"]]
    return {
        "label": label,
        "verdict": "通过" if not failed else "不通过",
        "failed_checks": [item["key"] for item in failed],
        "summary": ("全部检查通过" if not failed
                    else "未通过的检查：" + "；".join(item["title"] for item in failed)),
        "checks": checks,
        "metrics": {
            "cumulative_return_pct": round((final - 1) * 100, 4),
            "annualized_return_pct": round(annualized_pct, 4),
            "max_drawdown_pct": round(worst_dd * 100, 4),
            "max_drawdown_duration_days": worst_duration,
            "worst_year": worst_year_label,
            "worst_year_return_pct": round(worst_year, 4),
            "year_returns": {year: round(value, 4) for year, value in year_returns.items()},
            "out_of_sample_return_pct": round(out_of_sample_pct, 4),
            "days": days,
        },
        "risk_stats": risk_stats or {},
        "thresholds": thresholds.model_dump(),
    }
