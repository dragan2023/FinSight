"""涨停事件研究：**执行**已预注册的设计（`limit_up_event_study_design@v1`）。

设计登记在 `scripts/governance_register_limit_up_fields.py`（进 `hypothesis_registry`）。
本模块只负责把它跑出来。**设计不在这里改**——要改设计就先改登记（新 reference），
否则就成了"看到结果再调口径"。

| 项 | 登记内容 | 实现位置 |
|---|---|---|
| 样本域 | 仅当日涨停池内标的 | `limit_pools` 表，`pool='limit_up'` |
| 结果 | 事件后 N 个交易日的公司行动总收益 | `settlement_returns` + `service.total_return` |
| 对照 | 同日**同一成交额十分位**的非涨停标的等权收益 | `cross_section` 的 `turnover_20` 分位 |
| 统计量 | 配对超额**按评估日聚合**后的时间序列 t 值 | `_t_stat` |
| 显著性 | `abs(t) >= 1.96`；**不**用安慰剂当门槛 | `_report()` 的 `significance` 字段 |
| 安慰剂 | ① 对照置换（同十分位重抽）② 日期错位 | `_study_date` 内逐日重抽 / `_date_shift_placebo` |

**为什么不做横截面因子**：这几个字段在池外标的**没有取值**（未定义，不是缺失）。
填 0 或当缺失塞进全市场横截面 IC，等于把"选择集"当成数值，算出来的 IC 无法解释。
所以事件研究是它们唯一合法的评价方式。

**结果侧纪律**：前向收益必然包含评估日之后的信息，只用于事后结算与评价，
**绝不可**作为信号输入（与 `repository.settlement_returns` 的 docstring 同一条）。

**两条实现纪律**（都是踩过之后写的）：

1. **重抽只在当日内存里做，做完就丢。** 把 1133 天 × 5000 只股票的前向收益全留下要几百 MB；
   所以没有"先全量收集、再统一重抽"的写法，每天把 200 个重抽结果留下就走。
2. **两个安慰剂必须与主统计量用同一个估计量。** 第一版日期错位用"全市场均值"当基准，
   而主统计量用"同十分位对照均值"——两者不是同一个量，实测其 |t| 95 分位高达 **10.08**，
   看起来像"主结果不显著"，实际是基准不一致造成的假象。已改为同一个配对估计量
   （`bucket_mean` + `bucket_by_symbol`），并有测试钉住（把池内标的收益构造成恰好等于
   同档对照均值，此时正确的日期错位必须给不出 t 值）。
"""
from __future__ import annotations

import math
import random

from .service import _stdev, total_return

#: 涨停池里可做事件研究的**数值**字段。`limit_up_reason` 是文本，需先定编码方案，
#: 因此**不在这里**（登记条目里写明"本次不评价"）。
FIELD_ENCODINGS = {
    "continue_day_cnt": {
        "name": "连板高度", "groups": ("连板(≥2)", "首板(=1)"),
        "split": "≥2 为高组，=1 为低组",
        "rule": lambda value, median: None if not isinstance(value, (int, float)) else value >= 2,
    },
    "seal_money": {
        "name": "封单额", "groups": ("封单高(>当日池内中位数)", "封单低(≤中位数)"),
        "split": "按**当日池内中位数**分组（相对口径，不是绝对金额）",
        "rule": lambda value, median: (None if not isinstance(value, (int, float))
                                       or median is None else value > median),
    },
}

#: 成交额分位桶数（十分位）。
DECILES = 10
#: 日期错位的最短位移：必须大于前瞻期，否则两段窗口重叠、错位就不成立。
MIN_SHIFT = 25
#: 日期错位的最大位移（交易日索引 Units）。
MAX_SHIFT = 250


def _clean(values) -> list[float]:
    """去掉 NaN 与 None。**None 表示"这个量算不出来"**（例如不足 3 天没有 t 值），
    必须剔除而不是当 0——当 0 会凭空造出一个"均值 0"的假结论。"""
    return [value for value in values if value is not None and value == value]


def _mean(values) -> float:
    clean = _clean(values)
    return sum(clean) / len(clean) if clean else float("nan")


def _t_stat(series) -> float | None:
    """配对超额**按评估日聚合**后的时间序列 t 值。

    按日聚合是关键：同一天几十只涨停股的收益高度相关（共同市场因子），
    把逐事件超额当成独立样本会把 |t| 放大数倍——与 `13` 号文档踩过的坑同源。
    """
    clean = _clean(series)
    if len(clean) < 3:
        return None
    sd = _stdev(clean)
    if sd <= 0:
        return None
    return (sum(clean) / len(clean)) / (sd / math.sqrt(len(clean)))


def _p95_abs(values) -> float | None:
    clean = sorted(abs(value) for value in _clean(values))
    if not clean:
        return None
    return round(clean[int(0.95 * (len(clean) - 1))], 6)


class LimitUpEventStudy:
    """事件研究。**每个参数都显式**，因为它们都会影响结论。"""

    def __init__(self, repository) -> None:
        self._repository = repository

    # --- 取数与对齐 -------------------------------------------------------
    def event_dates(self, *, start_ms: int | None = None, end_ms: int | None = None) -> list[int]:
        """有涨停池**内容**的交易日（按台账实际落库的内容，不按日历猜）。"""
        rows = self._repository.limit_pool_rows(pool="limit_up", start_ms=start_ms,
                                                end_ms=end_ms)
        return sorted({row["date_ms"] for row in rows})

    @staticmethod
    def _deciles(values: dict[str, float]) -> dict[str, int]:
        """按成交额切十分位（0 = 最小的十分位）。"""
        if not values:
            return {}
        ordered = sorted(values.items(), key=lambda item: item[1])
        total = len(ordered)
        return {symbol: min(DECILES - 1, position * DECILES // total)
                for position, (symbol, _) in enumerate(ordered)}

    def _study_date(self, date_ms: int, pool: dict[str, dict], *, horizon_days: int,
                    fields: tuple[str, ...], draws: int, rng: random.Random,
                    pool_union: set[str]) -> dict[str, dict]:
        """一个评估日：逐字段算真实超额 + 当日的 `draws` 次对照置换（用完即丢原始收益）。

        取数（横截面 + 前向收益）**只做一次**，所有字段共用——两个字段各跑一遍会让
        取数成本翻倍，而它们的样本域与对照完全相同。
        """
        cross = self._repository.cross_section(date_ms=date_ms)
        outcome = {row["thscode"]: total_return(row)
                   for row in self._repository.settlement_returns(
                       date_ms=date_ms, horizon_days=horizon_days)}
        if not cross or not outcome:
            return {}
        turnover = {row["thscode"]: row["turnover_20"] for row in cross
                    if (row.get("turnover_20") or 0) > 0}
        if not turnover:
            return {}
        buckets = self._deciles(turnover)
        # 对照池：当日有成交、有前向收益、且**不在涨停池里**的标的，按十分位分组
        control: dict[int, list[float]] = {}
        for symbol, bucket in buckets.items():
            if symbol in pool or symbol not in outcome:
                continue
            control.setdefault(bucket, []).append(outcome[symbol])
        if not control:
            return {}
        # 每个十分位的对照均值**只算一次**：置换的 innermost 循环要跑
        # draws × 事件数 次，若在里面重算 500 个元素的均值就是几十亿次运算。
        bucket_mean = {bucket: _mean(values) for bucket, values in control.items()}
        if not any(value == value for value in bucket_mean.values()):
            return {}

        details: dict[str, dict] = {}
        for field in fields:
            encoding = FIELD_ENCODINGS[field]
            numeric = sorted(item[field] for item in pool.values()
                             if isinstance(item.get(field), (int, float)))
            median = numeric[len(numeric) // 2] if numeric else None
            events: dict[str, list[tuple[int, float]]] = {"high": [], "low": []}
            for item in pool.values():
                symbol = item.get("thscode")
                if symbol not in outcome or symbol not in buckets:
                    continue
                flag = encoding["rule"](item.get(field), median)
                if flag is None:
                    continue
                bucket = buckets[symbol]
                if not control.get(bucket):
                    continue      # 该十分位没有对照 → 这个事件不参与，绝不拿别的桶凑
                events["high" if flag else "low"].append((bucket, outcome[symbol]))

            detail: dict = {"date_ms": date_ms, "median": median}
            for side in ("high", "low"):
                values = events[side]
                detail[f"{side}_events"] = len(values)
                detail[f"{side}_excess"] = _mean([value - bucket_mean[bucket]
                                                  for bucket, value in values])
            permuted = {side: [] for side in ("high", "low")}
            for _ in range(draws):
                for side in ("high", "low"):
                    values = []
                    for bucket, _ in events[side]:
                        matched = control[bucket]
                        values.append(matched[rng.randrange(len(matched))]
                                      - bucket_mean[bucket])
                    permuted[side].append(_mean(values))
            detail["permuted"] = permuted
            detail["events_used"] = detail["high_events"] + detail["low_events"]
            # 对照规模要报出来：事件被跳过的唯一原因就是"该十分位没有对照"，
            # 不给出这个数字，读者无法判断"事件少"是市场本来如此还是对照没配上。
            detail["control_size"] = sum(len(values) for values in control.values())
            detail["control_buckets"] = len(control)
            details[field] = detail

        # 日期错位所需的**极小**留存：池内标的的当日收益、它落在哪个十分位、
        # 以及各十分位的对照均值。**不留**全市场收益——
        # 池内标的在任意一天通常只有一两百只有数据。
        shared = {
            "pool_returns": {symbol: outcome[symbol] for symbol in pool_union
                             if symbol in outcome},
            "bucket_by_symbol": {symbol: buckets[symbol] for symbol in pool_union
                                 if symbol in buckets},
            "bucket_mean": bucket_mean,
            "pool_codes": list(pool),
        }
        for detail in details.values():
            detail.update(shared)
        return details

    # --- 日期错位 ---------------------------------------------------------
    def _date_shift_placebo(self, per_date: dict[int, dict], dates: list[int], *,
                            draws: int, seed: int) -> dict:
        """把**池子整体平移到随机其它交易日**，配对超额应≈0。

        **必须与主统计量用同一个估计量**：主统计量是"事件收益 − 同十分位对照均值"，
        所以这里也必须是"被平移过来的标的收益 − 该标的在**当天**所在十分位的对照均值"。
        第一版用了全市场均值当基准，实测 |t| 95 分位 10.08——那不是"主结果不显著"，
        而是基准不一致（全市场均值会引入 beta/热度偏差）。
        """
        rng = random.Random(f"shift:{seed}")
        window = MAX_SHIFT - MIN_SHIFT + 1
        statistics = []
        for _ in range(draws):
            offset = rng.randrange(window) + MIN_SHIFT
            if rng.random() < 0.5:
                offset = -offset
            series = []
            for index, date_ms in enumerate(dates):
                target = index + offset
                if not 0 <= target < len(dates):
                    continue
                current = per_date[date_ms]
                other = per_date[dates[target]]
                values = []
                for symbol in other["pool_codes"]:
                    bucket = current["bucket_by_symbol"].get(symbol)
                    if bucket is None:
                        continue
                    mean = current["bucket_mean"].get(bucket)
                    value = current["pool_returns"].get(symbol)
                    if value is None or mean is None or mean != mean:
                        continue
                    values.append(value - mean)      # ← 与主统计量同一个配对估计量
                if values:
                    series.append(_mean(values))
            statistic = _t_stat(series)
            if statistic is not None:
                statistics.append(statistic)
        return {"kind": "日期错位（池子整体平移，配对估计量与主统计量一致）",
                "draws": len(statistics),
                "t_mean": round(_mean(statistics), 6) if statistics else None,
                "t_abs_p95": _p95_abs(statistics),
                "note": f"位移 {MIN_SHIFT}–{MAX_SHIFT} 个交易日（最短位移大于前瞻期，"
                        f"避免窗口重叠）。真零分布下 |t| 的 95 分位应≈1.96。"}

    # --- 汇总 -------------------------------------------------------------
    def _report(self, field: str, per_date: dict[int, dict], *, horizon_days: int,
                draws: int, seed: int, dates_with_pool: int) -> dict:
        usable = sorted(per_date)
        results = {}
        for side, label in zip(("high", "low"), FIELD_ENCODINGS[field]["groups"]):
            series = [per_date[date_ms][f"{side}_excess"] for date_ms in usable]
            results[side] = {
                "group": label, "events": sum(per_date[d][f"{side}_events"] for d in usable),
                "mean_excess": round(_mean(series), 6), "t_stat": _t_stat(series),
                "days_positive": sum(1 for value in _clean(series) if value > 0),
            }
        diff = [per_date[d]["high_excess"] - per_date[d]["low_excess"] for d in usable]
        results["high_minus_low"] = {"group": "高组 − 低组", "events": None,
                                     "mean_excess": round(_mean(diff), 6),
                                     "t_stat": _t_stat(diff),
                                     "days_positive": sum(1 for value in _clean(diff) if value > 0)}

        # 合并逐日置换：**同一抽签序号跨日**组合成一条可能的历史，再算 t 分布。
        # 高组−低组也要有自己的零分布——不然"没有区分度"这句话就没有对照。
        permutation = {}
        for side in ("high", "low", "high_minus_low"):
            statistics = []
            for draw in range(draws):
                if side == "high_minus_low":
                    series = [per_date[d]["permuted"]["high"][draw]
                              - per_date[d]["permuted"]["low"][draw] for d in usable]
                else:
                    series = [per_date[d]["permuted"][side][draw] for d in usable]
                statistics.append(_t_stat(series))
            clean = _clean(statistics)
            permutation[side] = {
                "t_mean": round(_mean(clean), 6) if clean else None,
                "t_abs_p95": _p95_abs(clean),
                "t_draws": len(clean),
                "real_t": results[side]["t_stat"]}
        return {
            "field": field, "field_name": FIELD_ENCODINGS[field]["name"],
            "groups": FIELD_ENCODINGS[field]["groups"], "split": FIELD_ENCODINGS[field]["split"],
            "horizon_days": horizon_days, "draws": draws,
            "dates_with_pool": dates_with_pool,
            "dates_usable": len(usable),
            "events_total": sum(per_date[d]["events_used"] for d in usable),
            "control_size_median": sorted(per_date[d]["control_size"] for d in usable)[
                len(usable) // 2] if usable else None,
            "results": results,
            "placebo": {
                "permutation": {
                    "kind": "对照置换（同十分位重抽，保样本量与日期分布）",
                    "by_group": permutation,
                    "note": "真零分布下 t 的绝对值 95 分位应≈1.96；明显偏离说明零分布构造有问题。"
                            "`real_t` 与 `t_abs_p95` 可直接对照。",
                },
                "date_shift": self._date_shift_placebo(per_date, usable, draws=draws, seed=seed),
            },
            "significance": "看 t 值（|t| >= 1.96）。安慰剂用于机制自检与对照，"
                            "**不得**当作显著性门槛（`13` 号文档的方法学修正）。",
            "design": "hypothesis_registry: limit_up_event_study_design@v1",
            "limitations": [
                "涨停池只覆盖已同步的交易日（实测可回溯到 2022-01-05，更早的日子静默返回 0 条），"
                "样本是 2022 年以后的市场状态，不可外推。",
                "对照只匹配成交额十分位，**没有**匹配行业、市值、β——配对超额里仍含这些"
                "未控制维度的差异。",
                "本地行情库不含退市标的（幸存者偏差），『涨停后一路走坏直至退市』的情形"
                "不可能出现在样本里，因此负向超额被**低估**、正向超额被高估。",
                "这是**超额**（相对同档对照），不是可实现收益：不计交易成本、不考虑次日"
                "难以成交、也不构成任何可执行的多空策略。",
                "前向收益含评估日之后的信息，**只用于事后评价**，不得作为信号输入。",
            ],
            "disclaimer": "研究参考，不构成投资建议。",
        }

    # --- 主入口 -----------------------------------------------------------
    def run_fields(self, *, fields: tuple[str, ...], start_ms: int | None = None,
                   end_ms: int | None = None, horizon_days: int = 20, draws: int = 200,
                   seed: int = 20260913, progress=None) -> dict[str, dict]:
        """**一次遍历**算出多个字段：取数只做一遍，字段各自分组与重抽。"""
        unknown = [item for item in fields if item not in FIELD_ENCODINGS]
        if unknown:
            raise ValueError(f"不支持的事件研究字段：{unknown}（可选 {list(FIELD_ENCODINGS)}）")
        if not fields:
            raise ValueError("至少要指定一个字段。")
        if draws < 1 or not 1 <= horizon_days <= 250:
            raise ValueError("重抽次数必须为正、前瞻期需在 1–250 个交易日之间。")

        rows = self._repository.limit_pool_rows(pool="limit_up", start_ms=start_ms,
                                                end_ms=end_ms)
        by_date: dict[int, dict[str, dict]] = {}
        for row in rows:
            by_date.setdefault(row["date_ms"], {})[row["thscode"]] = row
        dates = sorted(by_date)
        if not dates:
            return {field: {"field": field, "dates_usable": 0,
                            "reason": "本地涨停池没有内容：先跑 scripts/sync_limit_pools.py "
                                      "（人工触发）再评价。"} for field in fields}
        pool_union = {symbol for items in by_date.values() for symbol in items}
        rng = random.Random(f"{'+'.join(fields)}:{seed}")

        per_field: dict[str, dict[int, dict]] = {field: {} for field in fields}
        for index, date_ms in enumerate(dates, 1):
            details = self._study_date(date_ms, by_date[date_ms], horizon_days=horizon_days,
                                       fields=fields, draws=draws, rng=rng,
                                       pool_union=pool_union)
            for field, detail in details.items():
                if detail["events_used"]:
                    per_field[field][date_ms] = detail
            if progress is not None and index % 25 == 0:
                progress(index, len(dates),
                         {field: len(per_field[field]) for field in fields})

        reports = {}
        for field in fields:
            if not per_field[field]:
                reports[field] = {
                    "field": field, "dates_usable": 0, "dates_with_pool": len(dates),
                    "reason": "没有任何评估日同时齐备『池内标的 + 同十分位对照 + 前向窗口』。"}
                continue
            reports[field] = self._report(field, per_field[field], horizon_days=horizon_days,
                                          draws=draws, seed=seed, dates_with_pool=len(dates))
        return reports

    def run(self, *, start_ms: int | None = None, end_ms: int | None = None,
            horizon_days: int = 20, field: str = "continue_day_cnt",
            draws: int = 200, seed: int = 20260913, progress=None) -> dict:
        """单字段入口（保持返回结构不变）。"""
        return self.run_fields(fields=(field,), start_ms=start_ms, end_ms=end_ms,
                               horizon_days=horizon_days, draws=draws, seed=seed,
                               progress=progress)[field]
