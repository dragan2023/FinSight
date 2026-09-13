"""C2 最小版：可复现的横截面排序 + **安慰剂通过率**（决策 D16 / D19）。

**它不产出荐股结论，也不产出"买入清单"。** 它只回答两件事：

1. 给定 `(评估日, 股票池规则, 因子集, 权重)`，能不能**确定性复现**同一份排序？
   （含 `params_hash`，重复运行逐字节一致）
2. 这些因子在横截面上**有没有可复现的预测力**？——用 Rank IC 与**安慰剂对照**判定：
   把前向收益在标的之间随机重排（破坏因子—收益的真实联系）后重算 IC，
   真实 IC 若跑不赢这个零分布，就没有证据（D19：每个因子预注册时必须同时声明安慰剂通过率）。

为什么先做口径而不是先做选股：批次 3 的实测结论是「10,572 次试验挑出 143 个通过者，
而安慰剂能挑出 279 个」（`07` 号文档）。在那种量级下，"挑通过者"本身不是可靠方法，
所以 C2 的第一件事是把**评价口径**做实。
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from datetime import datetime, timedelta, timezone

from ..errors import AppError
from .factors import FACTOR_BY_ID, FACTOR_IDS, FACTOR_SET_VERSION, LIQUIDITY_COLUMN
from .universe import RULE_NOTES, UNSUPPORTED_EXCLUSIONS, UniverseRules, apply_rules

CST = timezone(timedelta(hours=8))

#: 安慰剂重排次数。零分布用重排均值与 95 分位，不假设正态。
PLACEBO_DRAWS = 200

LIMITATIONS = [
    "本机行情仓库不含退市证券，历史统计存在乐观偏置（幸存者偏差）。",
    "无法剔除 ST / *ST：本地数据没有 is_st 字段。",
    "涨跌停与停牌只用了价量代理指标，与交易所口径不同。",
    "因子只用「量价 + 流动性」：财务因子算不出来（缺 Z-Score 与 ROA 所需字段）。",
    "横截面 IC 只说明**同期**的相对关系，不构成任何个股推荐；本模块不产出买入清单。",
]


def _day(date_ms: int) -> str:
    return datetime.fromtimestamp(date_ms / 1000, CST).strftime("%Y-%m-%d")


def _to_ms(date_str: str) -> int:
    return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=CST).timestamp() * 1000)


def ascending_ranks(values: list[float]) -> list[float]:
    """平均秩（并列取均值），从 0 开始。用于百分位与 Spearman。"""
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        average = (position + end) / 2.0
        for index in range(position, end + 1):
            ranks[order[index]] = average
        position = end + 1
    return ranks


def percentile_preference(values: list[float], direction: str) -> list[float]:
    """把因子值折算成 0–1 的"偏好分"：1 = 该因子最看多。

    `direction="high"` 时数值越大越好；`"low"` 时越小越好。**方向写错会让 IC 符号
    完全反过来**，所以方向只在 `factors.py` 里声明一次，不在这里二次判断。
    """
    if not values:
        return []
    ranks = ascending_ranks(values)
    span = max(ranks) if ranks else 0.0
    normalized = [(rank / span if span else 0.5) for rank in ranks]
    return normalized if direction == "high" else [1.0 - value for value in normalized]


def _factor_value(row: dict, factor_id: str) -> float | None:
    if factor_id == "momentum_60d":
        base = row.get("close_60_ago")
        if not base or base <= 0:
            return None
        # 总收益口径：窗口内除权除息已折算（见 repository.cross_section）
        return ((row["close"] * (1 + row.get("bonus_60", 0.0)) + row.get("div_60", 0.0)
                 - row.get("rights_cost_60", 0.0)) / base - 1)
    if factor_id == "bias_20":
        ma20 = row.get("ma20")
        if not ma20 or ma20 <= 0:
            return None
        return row["close"] / ma20 - 1
    if factor_id == "volatility_20d":
        vol = row.get("vol20")
        if vol is None:
            return None
        return vol * math.sqrt(252)
    return None


def _stdev(values: list[float]) -> float:
    """样本标准差（n−1）。样本不足 2 个返回 0。"""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _verdict(t_stat: float | None) -> str:
    """按 |t| 给结论——**只陈述统计量，不夸大**。"""
    if t_stat is None:
        return "样本不足，无法判定"
    if t_stat >= 1.96:
        return ("均值 IC 为正且 |t| >= 1.96；但仍需注意：未做多重检验校正、"
                "存在幸存者偏差、未计交易成本与可执行性——**不构成本身可交易的结论**")
    if t_stat <= -1.96:
        return "均值 IC 显著为负：声明的方向与数据相反，应重新检视先验逻辑"
    return f"与噪声无异（|t| = {abs(t_stat):.2f} < 1.96）"


def _spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) < 3:
        return None
    a, b = ascending_ranks(left), ascending_ranks(right)
    mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    var_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
    var_b = math.sqrt(sum((y - mean_b) ** 2 for y in b))
    if var_a == 0 or var_b == 0:
        return None
    return cov / (var_a * var_b)


class ScreenerService:
    def __init__(self, repository) -> None:
        self._repository = repository

    # --- 横截面排序 -------------------------------------------------------
    def rank(self, date_ms: int, *, rules: UniverseRules | None = None,
             factor_ids: tuple[str, ...] = FACTOR_IDS,
             weights: dict[str, float] | None = None, top_n: int | None = 200) -> dict:
        rules = rules or UniverseRules()
        unknown = [item for item in factor_ids if item not in FACTOR_BY_ID]
        if unknown:
            raise AppError("screener_invalid", f"未登记的因子：{unknown}（方案 A.1 要求先登记）", 422)
        weights = {item: float((weights or {}).get(item, 1.0)) for item in factor_ids}

        raw = self._repository.cross_section(date_ms=date_ms)
        kept, exclusions = apply_rules(raw, rules)

        # 逐因子算值 → 折算偏好分（0–1，越大越看多）
        values: dict[str, list[float | None]] = {}
        preference: dict[str, list[float]] = {}
        for factor_id in factor_ids:
            column = [_factor_value(row, factor_id) for row in kept]
            values[factor_id] = column
            present = [item for item in column if item is not None]
            scores = percentile_preference([item if item is not None else 0.0 for item in column],
                                           FACTOR_BY_ID[factor_id].direction)
            # 缺失值不参与：偏好分置 None，加权时按可用因子归一化，绝不填 0 假装"最看空"。
            preference[factor_id] = [score if column[index] is not None else None
                                     for index, score in enumerate(scores)] if present else []

        total_weight = sum(weights.values()) or 1.0
        rows: list[dict] = []
        for index, row in enumerate(kept):
            parts, used = 0.0, 0.0
            detail = {}
            for factor_id in factor_ids:
                score = (preference[factor_id] or [None] * len(kept))[index]
                detail[factor_id] = {
                    "value": (None if values[factor_id][index] is None
                              else round(values[factor_id][index], 8)),
                    "preference": None if score is None else round(score, 6),
                    "direction": FACTOR_BY_ID[factor_id].direction,
                }
                if score is not None:
                    parts += weights[factor_id] * score
                    used += weights[factor_id]
            rows.append({
                "thscode": row["thscode"],
                "composite_score": round(parts / used, 6) if used > 0 else None,
                "factors_used": round(used / total_weight, 4),
                "factors": detail,
                "liquidity": {LIQUIDITY_COLUMN["id"]: round(row.get("turnover_20") or 0.0, 2)},
                "bars": row["bars"],
                "data_as_of": _day(date_ms),
                "data_source": "local_duckdb:daily_k + adjustment_factors"
                               "（公司行动总收益口径，ex_date_ms <= as_of 截断）",
            })
        # 确定性排序：分高在前，同分按代码升序（**这条 tie-break 是可复现的关键**）
        rows = [row for row in rows if row["composite_score"] is not None]
        rows.sort(key=lambda item: (-item["composite_score"], item["thscode"]))
        for position, row in enumerate(rows, 1):
            row["rank"] = position

        params_hash = self._params_hash(date_ms=date_ms, rules=rules, factor_ids=factor_ids,
                                       weights=weights, top_n=top_n)
        return {
            "as_of": _day(date_ms), "as_of_ms": date_ms,
            "params_hash": params_hash, "factor_set_version": FACTOR_SET_VERSION,
            "label": "候选池（研究用，不是买入清单）",
            "universe": {
                "raw_symbols": len(raw), "kept": len(kept),
                "exclusions": exclusions, "rule_notes": RULE_NOTES,
                "unsupported_exclusions": UNSUPPORTED_EXCLUSIONS,
                "rules": rules.as_dict(),
            },
            "factors": [{**{"id": factor.id, "name": factor.name, "direction": factor.direction,
                            "prior": factor.prior}, "weight": weights[factor.id]}
                        for factor in (FACTOR_BY_ID[item] for item in factor_ids)],
            "candidates": rows[:top_n] if top_n else rows,
            "candidate_count": len(rows),
            "limitations": LIMITATIONS,
            "disclaimer": "研究参考，不构成投资建议。",
        }

    @staticmethod
    def _params_hash(*, date_ms: int, rules: UniverseRules, factor_ids: tuple[str, ...],
                     weights: dict[str, float], top_n: int | None) -> str:
        payload = {"factor_set_version": FACTOR_SET_VERSION, "as_of_ms": date_ms,
                   "rules": rules.as_dict(), "factor_ids": list(factor_ids),
                   "weights": {key: round(value, 8) for key, value in sorted(weights.items())},
                   "top_n": top_n}
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    # --- 因子评价：Rank IC 与安慰剂 ---------------------------------------
    def ic_for_date(self, date_ms: int, *, horizon_days: int = 20,
                    rules: UniverseRules | None = None,
                    factor_ids: tuple[str, ...] = FACTOR_IDS,
                    draws: int = PLACEBO_DRAWS, seed: int = 20260913) -> dict:
        """单个评估日的 Rank IC，以及**逐标的置乱**的安慰剂分布。

        IC 用「偏好分 vs 前向收益」的 Spearman 相关：方向已在偏好分里折进去，
        因此 **IC > 0 表示该因子的偏好方向确实赚钱**，符号可直接解释。

        ⚠️ **安慰剂在这里只是"机制自检"，不是显著性检验。** 逐标的置乱会把个股收益之间
        的横截面相关（共同市场因子）一起打碎，于是零分布的离散度远小于真实 IC 的离散度
        （实测：真实逐日 sd 0.095–0.157，IID 零分布 sd ≈ 0.023，差 4–7 倍）。
        拿它当显著性门槛会**系统性放大通过率**——本模块第一版就踩了这个坑，
        实测得到 0.24–0.52 的"通过率"，而真零分布下该值应≈0.05。
        显著性一律由 `ic_report` 里的 **IC 时间序列 t 值**给出。
        """
        rules = rules or UniverseRules()
        raw = self._repository.cross_section(date_ms=date_ms)
        kept, _ = apply_rules(raw, rules)
        if len(kept) < 20:
            return {"as_of": _day(date_ms), "eligible": False,
                    "reason": f"股票池仅 {len(kept)} 只，不足 20 只，无法计算横截面 IC。",
                    "factors": {}}
        outcome = {row["thscode"]: row
                   for row in self._repository.settlement_returns(
                       date_ms=date_ms, horizon_days=horizon_days)}
        rows = [row for row in kept if row["thscode"] in outcome]
        if len(rows) < 20:
            return {"as_of": _day(date_ms), "eligible": False,
                    "reason": f"前向窗口齐备的标的仅 {len(rows)} 只（前瞻期 {horizon_days} 个交易日），"
                              "该评估日暂不参与 IC 统计。", "factors": {}}
        forward = [self._total_return(outcome[row["thscode"]]) for row in rows]
        rng = random.Random(f"{date_ms}:{seed}")

        report = {}
        for factor_id in factor_ids:
            column = [_factor_value(row, factor_id) for row in rows]
            paired = [(index, value) for index, value in enumerate(column) if value is not None]
            if len(paired) < 20:
                continue
            factor_values = [value for _, value in paired]
            returns = [forward[index] for index, _ in paired]
            # 方向：high 直接用因子值排序，low 取负 → IC > 0 恒表示"偏好方向赚钱"
            oriented = factor_values if FACTOR_BY_ID[factor_id].direction == "high" \
                else [-value for value in factor_values]
            real_ic = _spearman(oriented, returns)
            placebo = []
            for _ in range(draws):
                shuffled = returns[:]
                rng.shuffle(shuffled)
                value = _spearman(oriented, shuffled)
                if value is not None:
                    placebo.append(value)
            placebo.sort()
            report[factor_id] = {
                "samples": len(paired), "real_ic": None if real_ic is None else round(real_ic, 4),
                "placebo_mean": round(sum(placebo) / len(placebo), 6) if placebo else None,
                "placebo_sd": (round(_stdev(placebo), 6) if len(placebo) > 1 else None),
                "iid_null_sd": round(1 / math.sqrt(len(paired) - 1), 6) if len(paired) > 2 else None,
                "draws": len(placebo),
            }
        return {"as_of": _day(date_ms), "eligible": True, "horizon_days": horizon_days,
                "samples": len(rows), "factors": report}

    @staticmethod
    def _total_return(row: dict) -> float:
        """窗口总收益：`(close_h × (1+送股+配股) + 分红 − 配股成本) / close_T − 1`。"""
        base = row["close_t"]
        return ((row["horizon_close"] * (1 + row.get("bonus", 0.0)) + row.get("dividend", 0.0)
                 - row.get("rights_cost", 0.0)) / base - 1) if base else 0.0

    def ic_report(self, dates: list[int], *, horizon_days: int = 20,
                  rules: UniverseRules | None = None,
                  factor_ids: tuple[str, ...] = FACTOR_IDS,
                  draws: int = PLACEBO_DRAWS) -> dict:
        """多日汇总：每个因子的均值 IC、**IC 时间序列 t 值**与安慰剂机制自检。

        **显著性来自 t 值，不来自安慰剂。** `t = mean(IC) / (sd(IC) / sqrt(T))`：
        IC 的逐日离散度本身就包含了横截面相关带来的不确定性，而逐标的置乱会把它抹掉
        （见 `ic_for_date` 的说明）。安慰剂在这里的作用是**机制自检**：它应当≈0，
        否则说明取数或对齐环节有问题。

        `t` 的可解释性依赖"各评估日的 IC 大致独立"：采样是按交易日等距抽取
        （默认 24 个点跨全历史、间隔约 100 个交易日），远大于前瞻期，重叠可忽略。
        """
        per_date = [self.ic_for_date(date_ms, horizon_days=horizon_days, rules=rules,
                                     factor_ids=factor_ids, draws=draws) for date_ms in dates]
        usable = [item for item in per_date if item["eligible"]]
        summary = {}
        for factor_id in factor_ids:
            entries = [item["factors"][factor_id] for item in usable
                       if factor_id in item["factors"]]
            ics = [item["real_ic"] for item in entries if item["real_ic"] is not None]
            if not ics:
                summary[factor_id] = {"dates": 0, "reason": "没有任何可用交易日",
                                      "name": FACTOR_BY_ID[factor_id].name,
                                      "direction": FACTOR_BY_ID[factor_id].direction}
                continue
            mean_ic = sum(ics) / len(ics)
            ic_sd = _stdev(ics)
            t_stat = (mean_ic / (ic_sd / math.sqrt(len(ics)))) if ic_sd > 0 else None
            placebo_means = [item["placebo_mean"] for item in entries
                             if item["placebo_mean"] is not None]
            summary[factor_id] = {
                "dates": len(ics), "name": FACTOR_BY_ID[factor_id].name,
                "direction": FACTOR_BY_ID[factor_id].direction,
                "mean_ic": round(mean_ic, 4), "ic_sd": round(ic_sd, 4),
                "t_stat": None if t_stat is None else round(t_stat, 3),
                "positive_ic_share": round(sum(1 for value in ics if value > 0) / len(ics), 4),
                "placebo_mean_ic": round(sum(placebo_means) / len(placebo_means), 6)
                                   if placebo_means else None,
                "iid_null_sd": entries[0].get("iid_null_sd"),
                "verdict": _verdict(t_stat),
            }
        return {"horizon_days": horizon_days, "dates_requested": len(dates),
                "dates_usable": len(usable), "placebo_draws": draws,
                "sampling": "按交易日等距抽取，间隔远大于前瞻期，IC 之间近似独立",
                "method": "显著性 = IC 时间序列的 t 值；安慰剂（逐标的置乱）仅作机制自检，"
                          "其零分布离散度远小于真实 IC 离散度（实测差 4–7 倍），"
                          "**不得**用作显著性门槛",
                "multiple_testing": f"本次试验了 {len(factor_ids)} 个因子；按方案 A.2，"
                                    "多重检验计数必须包含**全部尝试过的**因子（含放弃的），"
                                    "该计数写入治理台账的 trial_count",
                "factors": summary, "per_date": per_date, "limitations": LIMITATIONS}
