"""涨停事件研究：**执行**已预注册的设计（`limit_up_event_study_design@v1/@v2`）。

设计登记在 `scripts/governance_register_limit_up_fields.py`（进 `hypothesis_registry`）。
本模块只负责把它跑出来。**设计不在这里改**——要改设计就先改登记（新 reference），
否则就成了"看到结果再调口径"。

| 项 | 登记内容 | 实现位置 |
|---|---|---|
| 样本域 | 仅当日涨停池内标的 | `limit_pools` 表，`pool='limit_up'` |
| 结果 | 事件后 N 个交易日的公司行动总收益 | `settlement_returns` + `service.total_return` |
| 对照 | 同日**同一成交额十分位**的非涨停标的等权收益 | `cross_section` 的 `turnover_20` 分位 |
| 统计量 | 配对超额**按评估日聚合**后的时间序列 t 值 | `_t_stat` |
| 显著性 | `abs(t) >= 1.96`（多重检验校正后的门槛一并给出） | `_report()` 的 `significance` / `multiplicity` |
| 安慰剂① | 对照置换（同层重抽）——**唯一合法的显著性参照** | `_study_date` 内逐日重抽 |
| 安慰剂② | 日期错位——**已判定无效**，降级为诊断（见下） | `_date_shift_placebo` |

**为什么不做横截面因子**：这几个字段在池外标的**没有取值**（未定义，不是缺失）。
填 0 或当缺失塞进全市场横截面 IC，等于把"选择集"当成数值，算出来的 IC 无法解释。
所以事件研究是它们唯一合法的评价方式。

**结果侧纪律**：前向收益必然包含评估日之后的信息，只用于事后结算与评价，
**绝不可**作为信号输入（与 `repository.settlement_returns` 的 docstring 同一条）。

**四条实现纪律**（都是踩过之后写的）：

1. **重抽只在当日内存里做，做完就丢。** 把 1133 天 × 5000 只股票的前向收益全留下要几百 MB。
2. **安慰剂必须与主统计量用同一个估计量**（踩过：第一版日期错位用全市场均值当基准）。
3. **日期错位这个安慰剂本身是无效的**（`v2` 登记）：位移为正等于**用未来的涨停名单
   度量今天**（前视选择）；固定位移又让逐日超额**强自相关**，而 t 假设逐日独立 →
   |t| 被系统性放大（实测 |t| 95 分位 9.76，而应为≈1.96）。它保留为**诊断**，
   **不得**充当零分布。
4. **稳健性检验必须是"同日同池的相对比较"或"事前特征匹配"**：
   - 池内子样本层（`_strata`）：同为涨停股、同一天，标的类型差异被差分掉；
   - 事前特征匹配：用 `close_20_ago / close_60_ago − 1` 这类**不含评估日当天**的窗口，
     若用含当天涨停的窗口（`vol20`/`ma20`），匹配变量本身就被事件污染了。
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from .service import _stdev, total_return

CST = timezone(timedelta(hours=8))

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

#: 成交额分位桶数（十分位）；事前动量的分组数（三分位）。
DECILES = 10
PRIOR_GROUPS = 3


def _ratio(numerator, denominator) -> float | None:
    """比值；分子分母必须都是数字且分母 > 0。**不做截断**（截断就是调参）。"""
    if not isinstance(numerator, (int, float)) or not isinstance(denominator, (int, float)):
        return None
    if denominator <= 0:
        return None
    return numerator / denominator


#: **派生字段**：由「涨停池条目 × 当日横截面」算出来，而不是池子接口直接给的。
#: 定义只写在这里一处，评估侧与登记侧引用同一份说明。
DERIVED_FIELDS = {
    "seal_ratio": {
        "name": "封成比（封单额 ÷ 当日成交额）",
        "groups": ("封成比高(>当日池内中位数)", "封成比低(≤中位数)"),
        "split": "按**当日池内中位数**分组（相对口径，不是绝对金额）",
        "rule": lambda value, median: (None if not isinstance(value, (int, float))
                                       or median is None else value > median),
        "build": lambda item, row: _ratio(item.get("seal_money"), row.get("turnover_today")),
        "note": "分母用**当日成交额**：封板日的成交额本身被压缩，因此这个比值同时刻画"
                "「封单有多厚」与「有多没人卖」。它是无量纲的，**不含绝对规模**——"
                "这正是为了排掉『封单额与市值正相关』那个混淆（D44）。",
    },
}

#: 评估侧统一用这张表（池子原生字段 + 派生字段）。
ALL_FIELDS = {**FIELD_ENCODINGS, **DERIVED_FIELDS}
#: 日期错位的最短/最大位移（交易日索引）；该安慰剂已判定无效，仅保留为诊断。
MIN_SHIFT = 25
MAX_SHIFT = 250
#: 分时段复验的分界：**2025 年及以后**（涨停池覆盖 2022-01-04 起，2025 年后上游零失败）。
REPLICATION_CUTOFF = "2025-01-01"


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


def _z_for_two_sided(alpha: float) -> float:
    """双侧显著性 `alpha` 对应的 |z|（用 erf 反解，不引入 scipy）。"""
    target = 1 - alpha / 2
    low, high = 0.0, 10.0
    for _ in range(200):
        mid = (low + high) / 2
        if 0.5 * (1 + math.erf(mid / math.sqrt(2))) < target:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def _day(date_ms: int) -> str:
    return datetime.fromtimestamp(date_ms / 1000, CST).strftime("%Y-%m-%d")


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
    def _quantiles(values: dict[str, float], groups: int) -> dict[str, int]:
        """按数值切等频分组（0 = 最小的一组）。"""
        if not values:
            return {}
        ordered = sorted(values.items(), key=lambda item: item[1])
        total = len(ordered)
        return {symbol: min(groups - 1, position * groups // total)
                for position, (symbol, _) in enumerate(ordered)}

    @staticmethod
    def _prior_momentum(row: dict) -> float | None:
        """**事件前**的 40 日动量：`close_20_ago / close_60_ago − 1`。

        窗口是 (T−60, T−20]，**完全不含评估日当天**——这是"事前特征匹配"的核心要求：
        若改用含当天涨停的窗口（`vol20`、`ma20`），匹配变量本身就被事件污染了。
        """
        early, late = row.get("close_60_ago"), row.get("close_20_ago")
        if not early or early <= 0 or not late or late <= 0:
            return None
        return late / early - 1

    @staticmethod
    def _strata(field: str):
        """每个字段**预先声明**的子样本层（v2/v3 稳健性检验）。

        池内分层是"同日同池的相对比较"，对"这类标的本来就这样"的混淆免疫：
        两组都是涨停股、同一天、同一套对照，差别只在分组变量上。
        **同一族的字段共用同一套分层**（`seal_*` 都是"封单类"，按连板结构分）。
        """
        def above(value, median):
            return isinstance(value, (int, float)) and median is not None and value > median

        def at_or_below(value, median):
            return isinstance(value, (int, float)) and median is not None and value <= median

        if field in ("seal_money", "seal_ratio"):
            # 封单类变量的区分度会不会只是"连板结构"的影子？在首板/连板内部各自再比一次。
            return (("all", lambda item, med: True),
                    ("first_board", lambda item, med: item.get("continue_day_cnt") == 1),
                    ("multi_board",
                     lambda item, med: (item.get("continue_day_cnt") or 0) >= 2))
        # 连板高度的"无区分度"是否被封单结构掩盖？在封单高/低内部各自再比一次。
        return (("all", lambda item, med: True),
                ("high_seal",
                 lambda item, med: above(item.get("seal_money"), med.get("seal_money"))),
                ("low_seal",
                 lambda item, med: at_or_below(item.get("seal_money"), med.get("seal_money"))))

    @staticmethod
    def _field_values(field: str, pool: dict[str, dict],
                      cross_by_symbol: dict[str, dict]) -> dict[str, float]:
        """当日**池内**该字段的取值（派生字段在这里算）。

        派生字段缺当日横截面行或分母 ≤ 0 时**不给值**——绝不能拿 0 或全网均值去凑，
        否则"缺数据"会被当成"低封成比"混进低组，直接把结论做出来。
        """
        if field in DERIVED_FIELDS:
            build = DERIVED_FIELDS[field]["build"]
            values = {}
            for symbol, item in pool.items():
                row = cross_by_symbol.get(symbol)
                if row is None:
                    continue
                value = build(item, row)
                if value is not None:
                    values[symbol] = value
            return values
        return {symbol: item[field] for symbol, item in pool.items()
                if isinstance(item.get(field), (int, float))}

    @staticmethod
    def _is_replication(date_ms: int) -> bool:
        """是否属于分时段复验的后半段（2025 年及以后）。"""
        return _day(date_ms) >= REPLICATION_CUTOFF

    # --- 单日 -------------------------------------------------------------
    def _study_date(self, date_ms: int, pool: dict[str, dict], *, horizon_days: int,
                    fields: tuple[str, ...], draws: int, rng: random.Random,
                    pool_union: set[str]) -> dict[str, dict]:
        """一个评估日：逐字段 × 逐变体算真实超额 + 当日的 `draws` 次对照置换。

        取数（横截面 + 前向收益）**只做一次**，所有字段与所有变体共用——这是把
        "一个字段一遍"改成"一遍出全部"的原因：两个字段各跑一遍会让取数成本翻倍，
        而分层与匹配变体的边际成本只是记账与重抽。原始收益当天用完就丢。
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
        buckets = self._quantiles(turnover, DECILES)
        prior = {}
        for row in cross:
            value = self._prior_momentum(row)
            if value is not None:
                prior[row["thscode"]] = value
        terciles = self._quantiles(prior, PRIOR_GROUPS)

        # 两套对照网格：粗（成交额十分位）与细（十分位 × 事前动量三分位）。
        # 对照池排除涨停标的自身，否则超额会被自己压成 0。
        coarse: dict[int, list[float]] = {}
        fine: dict[tuple[int, int], list[float]] = {}
        for symbol, bucket in buckets.items():
            if symbol in pool or symbol not in outcome:
                continue
            value = outcome[symbol]
            coarse.setdefault(bucket, []).append(value)
            fine.setdefault((bucket, terciles.get(symbol, -1)), []).append(value)
        if not coarse:
            return {}
        # 每格对照均值**只算一次**：置换的 innermost 循环要跑 draws × 事件数 次，
        # 在里面重算几百个元素的均值就是几十亿次运算。
        coarse_mean = {key: _mean(values) for key, values in coarse.items()}
        fine_mean = {key: _mean(values) for key, values in fine.items()}
        if not any(value == value for value in coarse_mean.values()):
            return {}

        details: dict[str, dict] = {}
        cross_by_symbol = {row["thscode"]: row for row in cross}
        # 分层要用到**另一个字段**的中位数（例如"封单高"这一层）。原生字段与派生字段
        # 都按同一套 `_field_values` 取，绝不两处各写一遍。
        field_values = {name: self._field_values(name, pool, cross_by_symbol)
                        for name in ALL_FIELDS}
        medians = {name: (sorted(values.values())[len(values) // 2] if values else None)
                   for name, values in field_values.items()}

        for field in fields:
            encoding = ALL_FIELDS[field]
            values = field_values[field]
            median = medians[field]

            variants: dict[str, dict] = {}
            for name, predicate in self._strata(field):
                for matched in (False, True):
                    if matched and name != "all":
                        continue    # 精细匹配只在 all 层做：**预先声明**，避免统计量爆炸
                    variant = ("matched_" if matched else "base_") + name
                    means = fine_mean if matched else coarse_mean
                    grids = fine if matched else coarse
                    events: dict[str, list[tuple]] = {"high": [], "low": []}
                    for symbol, item in pool.items():
                        if symbol not in outcome or symbol not in buckets:
                            continue
                        if not predicate(item, medians):
                            continue
                        flag = encoding["rule"](values.get(symbol), median)
                        if flag is None:
                            continue
                        key = ((buckets[symbol], terciles.get(symbol, -1)) if matched
                               else buckets[symbol])
                        if key not in means or means[key] != means[key]:
                            continue    # 该格没有对照 → 这个事件不参与，绝不拿别的格子凑
                        events["high" if flag else "low"].append((key, outcome[symbol]))
                    variants[variant] = {"events": events, "means": means, "grid": grids,
                                         "matched": matched}

            detail: dict = {"date_ms": date_ms, "median": median, "variants": {}}
            for variant, payload in variants.items():
                means, grids = payload["means"], payload["grid"]
                entry: dict = {}
                for side in ("high", "low"):
                    values = payload["events"][side]
                    entry[f"{side}_events"] = len(values)
                    entry[f"{side}_excess"] = _mean([value - means[key]
                                                     for key, value in values])
                permuted = {side: [] for side in ("high", "low")}
                for _ in range(draws):
                    for side in ("high", "low"):
                        candidates = payload["events"][side]
                        sampled = []
                        for key, _ in candidates:
                            options = grids[key]
                            sampled.append(options[rng.randrange(len(options))] - means[key])
                        permuted[side].append(_mean(sampled))
                entry["permuted"] = permuted
                entry["events_used"] = entry["high_events"] + entry["low_events"]
                detail["variants"][variant] = entry
            # 向后兼容：顶层字段 = base_all 变体（旧调用方与测试直接读 results/permuted）
            detail.update(detail["variants"]["base_all"])
            detail["control_size"] = sum(len(values) for values in coarse.values())
            detail["control_buckets"] = len(coarse)
            detail["control_cells_matched"] = len(fine)
            details[field] = detail

        # 日期错位（**已判定无效**，仅留作诊断）所需的极小留存：池内标的当日收益、
        # 它落在哪个十分位、以及各十分位的对照均值。**不留**全市场收益。
        shared = {
            "pool_returns": {symbol: outcome[symbol] for symbol in pool_union
                             if symbol in outcome},
            "bucket_by_symbol": {symbol: buckets[symbol] for symbol in pool_union
                                 if symbol in buckets},
            "bucket_mean": coarse_mean,
            "pool_codes": list(pool),
        }
        for detail in details.values():
            detail.update(shared)
        return details

    # --- 日期错位（诊断，非零分布）-----------------------------------------
    def _date_shift_placebo(self, per_date: dict[int, dict], dates: list[int], *,
                            draws: int, seed: int) -> dict:
        """把**池子整体平移到随机其它交易日**。

        ⚠️ **它不是零分布**（v2 登记）：位移为正等于用未来的涨停名单度量今天，
        而固定位移又让逐日超额强自相关，两者都会把 |t| 抬到 9 以上（实测 95 分位 9.76，
        而应为≈1.96）。保留它是为了那个**诊断**信息：非事件队列的同档超额也不为 0，
        说明成交额十分位匹配没有中和标的类型差异。**不得**当显著性门槛。
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
                    values.append(value - mean)      # 与主统计量同一个配对估计量
                if values:
                    series.append(_mean(values))
            statistic = _t_stat(series)
            if statistic is not None:
                statistics.append(statistic)
        return {"kind": "日期错位（**已判定无效，仅作诊断**）",
                "draws": len(statistics),
                "t_mean": round(_mean(statistics), 6) if statistics else None,
                "t_abs_p95": _p95_abs(statistics),
                "verdict": "无效：位移为正＝用未来名单度量今天（前视选择）；固定位移使逐日"
                           "超额强自相关而 t 假设独立 → |t| 被系统性放大。**不得**当门槛。",
                "diagnostic_value": "非事件队列的同档配对超额也不为 0 → 成交额十分位匹配"
                                    "没有中和标的类型差异 → 跨样本的『水平』差异不得归因于事件。"}

    # --- 汇总 -------------------------------------------------------------
    def _variant_report(self, field: str, per_date: dict[int, dict], usable: list[int],
                        variant: str, draws: int) -> dict:
        entries = [per_date[date_ms]["variants"][variant] for date_ms in usable]
        results = {}
        for side, label in zip(("high", "low"), ALL_FIELDS[field]["groups"]):
            series = [entry[f"{side}_excess"] for entry in entries]
            results[side] = {
                "group": label, "events": sum(entry[f"{side}_events"] for entry in entries),
                "mean_excess": round(_mean(series), 6), "t_stat": _t_stat(series),
                "days_positive": sum(1 for value in _clean(series) if value > 0),
            }
        diff = [entry["high_excess"] - entry["low_excess"] for entry in entries]
        results["high_minus_low"] = {"group": "高组 − 低组", "events": None,
                                     "mean_excess": round(_mean(diff), 6),
                                     "t_stat": _t_stat(diff),
                                     "days_positive": sum(1 for value in _clean(diff) if value > 0)}
        # 合并逐日置换：**同一抽签序号跨日**组合成一条可能的历史，再算 t 分布。
        # 高组−低组也要有自己的零分布——不然"没有区分度"这句话就没有对照。
        by_group = {}
        for side in ("high", "low", "high_minus_low"):
            statistics = []
            for draw in range(draws):
                if side == "high_minus_low":
                    series = [entry["permuted"]["high"][draw] - entry["permuted"]["low"][draw]
                              for entry in entries]
                else:
                    series = [entry["permuted"][side][draw] for entry in entries]
                statistics.append(_t_stat(series))
            clean = _clean(statistics)
            by_group[side] = {"t_mean": round(_mean(clean), 6) if clean else None,
                              "t_abs_p95": _p95_abs(clean), "t_draws": len(clean),
                              "real_t": results[side]["t_stat"]}
        matched = variant.startswith("matched_")
        stratum = variant.split("_", 1)[1]
        return {
            "variant": variant,
            "matching": ("成交额十分位 × 事前 40 日动量三分位" if matched
                         else "成交额十分位"),
            "stratum": stratum,
            "events": sum(entry["events_used"] for entry in entries),
            "results": results,
            "placebo": {"kind": "对照置换（同层重抽，保样本量与日期分布）",
                        "by_group": by_group},
        }

    def _report(self, field: str, per_date: dict[int, dict], *, horizon_days: int,
                draws: int, seed: int, dates_with_pool: int,
                family_prior_statistics: int = 0) -> dict:
        usable = sorted(per_date)
        names = sorted(per_date[usable[0]]["variants"]) if usable else []
        variants = {name: self._variant_report(field, per_date, usable, name, draws)
                    for name in names}
        base = variants.get("base_all", {})

        # 分时段复验：只对**主检验端点**（各变体的「高−低」）做，属于复现而非新家族，
        # 但仍计入多重检验计数（方案 A.2：计数必须包含全部看过的比较）。
        replication = {}
        for name, payload in variants.items():
            for label, wanted in (("2022-2024", False), ("2025-2026", True)):
                subset = [date_ms for date_ms in usable
                          if self._is_replication(date_ms) is wanted]
                if len(subset) < 3:
                    continue
                series = [per_date[d]["variants"][name]["high_excess"]
                          - per_date[d]["variants"][name]["low_excess"] for d in subset]
                replication[f"{name}@{label}"] = {
                    "dates": len(subset), "mean_excess": round(_mean(series), 6),
                    "t_stat": _t_stat(series)}

        statistics_tested = len(variants) * 3 + len(replication)
        family_total = statistics_tested + max(0, int(family_prior_statistics))
        primary_threshold = round(_z_for_two_sided(0.05 / 2), 3)
        all_threshold = round(_z_for_two_sided(0.05 / statistics_tested), 3)
        family_threshold = round(_z_for_two_sided(0.05 / family_total), 3)
        return {
            "field": field, "field_name": ALL_FIELDS[field]["name"],
            "groups": ALL_FIELDS[field]["groups"], "split": ALL_FIELDS[field]["split"],
            "horizon_days": horizon_days, "draws": draws,
            "dates_with_pool": dates_with_pool, "dates_usable": len(usable),
            "events_total": base.get("events", 0),
            "control_size_median": (sorted(per_date[d]["control_size"] for d in usable)[
                len(usable) // 2] if usable else None),
            "results": base.get("results", {}),
            "variants": variants,
            "by_period": replication,
            "multiplicity": {
                "variants": names,
                "statistics_tested": statistics_tested,
                "definition": f"{len(variants)} 个变体 × 3 个统计量 + {len(replication)} 个"
                              f"分时段复现 = {statistics_tested}",
                "primary_endpoints": ["base_all.high_minus_low（每字段一条，共 2 条）"],
                "per_test_abs_t": 1.96,
                "bonferroni_primary_abs_t": primary_threshold,
                "bonferroni_all_abs_t": all_threshold,
                "family_prior_statistics": max(0, int(family_prior_statistics)),
                "family_total_statistics": family_total,
                "bonferroni_family_abs_t": family_threshold,
                "note": "按方案 A.2，多重检验计数必须包含**全部尝试过的**比较（含本轮新增的"
                        "稳健性变体、分时段复现，以及**同一族此前已做过的**比较——"
                        "`family_prior_statistics` 就是上一轮已看过的条数）；主检验只有 2 条，"
                        "其余是稳健性检验。三套门槛都给出来，读者可自行判断哪些结论同时过严格门槛。",
            },
            "placebo": {
                "permutation": {**base.get("placebo", {}),
                                "role": "**唯一合法的显著性参照**（同日同层重抽）",
                                "note": "真零分布下 |t| 的 95 分位应≈1.96；"
                                        "`real_t` 与 `t_abs_p95` 可直接对照。"},
                "date_shift": self._date_shift_placebo(per_date, usable, draws=draws, seed=seed),
            },
            "significance": "看 t 值。逐检验门槛 1.96；多重检验校正后的门槛见 `multiplicity`"
                            f"（主检验 |t|≥{primary_threshold}，本次全部比较 |t|≥{all_threshold}，"
                            f"含同族此前比较的 |t|≥{family_threshold}）。"
                            "安慰剂只作机制自检，**不得**当作显著性门槛。",
            "design": "hypothesis_registry: limit_up_event_study_design@v1 / @v2",
            "limitations": [
                "涨停池只覆盖已同步的交易日（实测可回溯到 2022-01-05，更早的日子静默返回 0 条），"
                "样本是 2022 年以后的市场状态，不可外推。",
                "对照匹配成交额十分位（精细变体再加事前 40 日动量三分位），"
                "**没有**匹配行业、市值、β——配对超额里仍含这些未控制维度的差异。",
                "本地行情库不含退市标的（幸存者偏差），『涨停后一路走坏直至退市』的情形"
                "不可能出现在样本里，因此负向超额被**低估**、正向超额被高估。",
                "这是**超额**（相对同层对照），不是可实现收益：不计交易成本、不考虑次日"
                "难以成交，也不构成任何可执行的多空策略。封板标的**当日收盘价买不到**，"
                "而窗口隐含『按 close_T 成交』。",
                "逐日超额存在重叠样本（连板连续上榜）与市场状态持续，t 值假设逐日独立，"
                "因此 |t| 偏乐观；分时段复验与多重检验门槛是为此设的防线。",
                "前向收益含评估日之后的信息，**只用于事后评价**，不得作为信号输入。",
            ],
            "disclaimer": "研究参考，不构成投资建议。",
        }

    # --- 主入口 -----------------------------------------------------------
    def run_fields(self, *, fields: tuple[str, ...], start_ms: int | None = None,
                   end_ms: int | None = None, horizon_days: int = 20, draws: int = 200,
                   seed: int = 20260913, progress=None,
                   family_prior_statistics: int = 0) -> dict[str, dict]:
        """**一次遍历**算出多个字段的全部变体：取数只做一遍，字段与变体各自分组与重抽。"""
        unknown = [item for item in fields if item not in ALL_FIELDS]
        if unknown:
            raise ValueError(f"不支持的事件研究字段：{unknown}（可选 {list(ALL_FIELDS)}）")
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
                    "reason": "没有任何评估日同时齐备『池内标的 + 同层对照 + 前向窗口』。"}
                continue
            reports[field] = self._report(field, per_field[field], horizon_days=horizon_days,
                                          draws=draws, seed=seed, dates_with_pool=len(dates),
                                          family_prior_statistics=family_prior_statistics)
        return reports

    def run(self, *, start_ms: int | None = None, end_ms: int | None = None,
            horizon_days: int = 20, field: str = "continue_day_cnt",
            draws: int = 200, seed: int = 20260913, progress=None) -> dict:
        """单字段入口（返回结构保持 `results` = base_all，向后兼容）。"""
        return self.run_fields(fields=(field,), start_ms=start_ms, end_ms=end_ms,
                               horizon_days=horizon_days, draws=draws, seed=seed,
                               progress=progress)[field]
