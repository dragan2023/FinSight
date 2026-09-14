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
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from .service import _stdev, total_return, total_return_between

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

#: 评估侧统一用这张表（池子原生字段 + 派生字段）。**`--field all` 用的就是它**。
ALL_FIELDS = {**FIELD_ENCODINGS, **DERIVED_FIELDS}

#: **外部字段**：取值不来自涨停池，而是按 point-in-time 从**别的数据集**取（本轮是财务的
#: F-Score）。它们刻意**不进入 `ALL_FIELDS`**：`--field all` 会把 `ALL_FIELDS` 全跑一遍，
#: 而没有注入 provider 时外部字段只会产出"没有可用评估日"的空报告，白白污染既有产物。
#: 要评价必须**显式指定** `fields=("f_score",)` 并注入 provider。
#:
#: 机制上仍是"字段"而不是另写一套估计量：分组、对照匹配、分层、重抽、多重检验计数
#: 全部复用事件研究的同一条代码路径（`13`/`17` 号文档反复强调：估计量只能有一份）。
EXTERNAL_FIELDS = {
    "f_score": {
        "name": "Piotroski F-Score（本地口径，来自财务数据集）",
        "groups": ("F-Score 高(>当日池内中位数)", "F-Score 低(≤中位数)"),
        "split": "按**当日池内中位数**分组（相对口径，不是绝对分值）",
        "rule": lambda value, median: (None if not isinstance(value, (int, float))
                                       or median is None else value > median),
        "note": "外部字段：由 `screener.financial_ic.FinancialFactorProvider` 按**事件当日**"
                "做 point-in-time 取值（只用 `available_from_ms <= 事件日` 的报告期，"
                "且各表取共同期；缺值不给组，不填补）。",
    },
    "f_score_ratio": {
        "name": "F-Score 归一化（得分 ÷ 可算项数，外部财务数据）",
        "groups": ("归一化 F-Score 高(>当日池内中位数)", "归一化 F-Score 低(≤中位数)"),
        "split": "按**当日池内中位数**分组",
        "rule": lambda value, median: (None if not isinstance(value, (int, float))
                                       or median is None else value > median),
        "note": "与 `f_score` **同源**（同一批判定），只为处理『可算项数随行业变化』"
                "（银行没有营业成本 → 可算 6 项）带来的不可比；不是独立证据。",
    },
}

#: 校验与查表用的全集：池内字段 + 派生字段 + 外部字段。
KNOWN_FIELDS = {**ALL_FIELDS, **EXTERNAL_FIELDS}

#: 字段所属的**统计族**。族级多重检验计数**按族**累加（方案 A.2）——
#: `seal_money` 与 `seal_ratio` 是同一底层数据的两种口径（高度相关，`17` §5 已写明
#: "不构成独立复现"），因此看过其中一个就等于看过这一族；`continue_day_cnt` 是另一族。
#:
#: 这张表存在的理由：族是**报告的必填披露项**。没有它，族级计数就只能靠调用方
#: 心里记着"哪些字段算一族"，而那正是 `17` §4.3 暴露的问题（`--family-prior-statistics`
#: 是全局参数，`--field all` 一跑就把封成比的门槛算松了：显示 3.023 而应为 3.227）。
FIELD_FAMILIES = {
    "continue_day_cnt": "limit_up_continue_board",
    "seal_money": "limit_up_seal_strength",
    "seal_ratio": "limit_up_seal_strength",
    # 交互族：**这是新族**（先验 0）。但报告会**同时**给出"把两个母族已看过的比较也计入"的
    # 保守门槛（连板族 43 + 现金族 36 = 79 → 族级共 79+48），结论以更严的那个为准。
    "f_score": "limit_up_financial_interaction",
    "f_score_ratio": "limit_up_financial_interaction",
}


def parse_family_prior_statistics(text: str) -> int | dict[str, int]:
    """解析族级先验计数的**规范文本格式**：`"20"` 或 `"字段=条数,字段=条数"`。

    - 裸整数：所有字段共用（向后兼容；多字段同跑时调用方自负其责，报告里会写明）；
    - `字段=N` 列表：**按字段**给，这是修 `17` §4.3 那个口径问题后的推荐用法。

    格式错误一律抛错，绝不静默取 0——静默取 0 会让门槛变松，正是 A.2 要防的事。
    """
    text = (text or "").strip()
    if not text:
        return 0
    if "=" not in text:
        return int(text)
    mapping: dict[str, int] = {}
    for chunk in text.split(","):
        field, separator, value = chunk.partition("=")
        field = field.strip()
        if not separator or not field or not value.strip():
            raise ValueError(f"族级先验计数格式错误：{chunk!r}（应为『字段=条数』）")
        if field not in KNOWN_FIELDS:
            raise ValueError(f"族级先验计数出现未知字段：{field!r}（可选 {sorted(KNOWN_FIELDS)}）")
        mapping[field] = int(value)
    if not mapping:
        raise ValueError(f"族级先验计数为空：{text!r}")
    return mapping


def resolve_family_priors(family_prior_statistics: int | Mapping[str, int],
                          fields: tuple[str, ...]) -> dict[str, int]:
    """把「族级先验计数」解析成**逐字段**的条数，并在不合理时**报错而不是静默兜底**。

    语义（`family_prior_statistics` 的准确含义）：**该字段所属族在本轮该字段之外、已看过的
    全部比较条数**。它包含此前各轮同族的比较，也包含**本轮同族其它字段**的比较——因为
    "同一族的另一个口径也算看过"（`17` §5）。每条比较 = 一个统计量（变体 × 高/低/高−低、
    以及分时段复现），与 `_report()` 里 `statistics_tested` 同一口径。

    两条硬性检查（都是为了"门槛只能算严、不能算松"）：

    1. 给出映射时，**每个要评价的字段都必须列在映射里**——缺项不得当成 0；
    2. **同族字段的先验必须一致**——否则同族两行结果的门槛会不一样，读者无法解释。
    """
    if isinstance(family_prior_statistics, Mapping):
        mapping = family_prior_statistics
        unknown = sorted(set(mapping) - set(KNOWN_FIELDS))
        if unknown:
            raise ValueError(f"族级先验计数出现未知字段：{unknown}（可选 {sorted(KNOWN_FIELDS)}）")
        missing = sorted(set(fields) - set(mapping))
        if missing:
            raise ValueError(
                f"族级先验计数缺少本次要评价的字段：{missing}。按字段给时必须**逐个列全**——"
                "缺项静默当 0 会把门槛算松（`17` §4.3 就是这么错的）。")
        if any(int(value) < 0 for value in mapping.values()):
            raise ValueError("族级先验计数不得为负。")
    else:
        if int(family_prior_statistics) < 0:
            raise ValueError("族级先验计数不得为负。")
        return {field: int(family_prior_statistics) for field in fields}

    resolved = {field: int(mapping[field]) for field in fields}
    by_family: dict[str, set[int]] = {}
    for field, prior in resolved.items():
        by_family.setdefault(FIELD_FAMILIES[field], set()).add(prior)
    inconsistent = {family: sorted(priors) for family, priors in by_family.items()
                    if len(priors) > 1}
    if inconsistent:
        raise ValueError(
            f"同族字段的族级先验不一致：{inconsistent}。同族字段是**一起被看过的**，"
            "先验必须一致，否则同一个族的不同口径会有不同门槛。")
    return resolved


def _next_open_total_return(row: dict) -> float | None:
    """可成交口径：买入价 = **T+1 开盘**，终点 = T+20 收盘，公司行动只算晚于买入日的。"""
    return total_return_between(row.get("next_open"), row.get("horizon_close"),
                                row.get("bonus", 0.0), row.get("dividend", 0.0),
                                row.get("rights", 0.0), row.get("rights_cost", 0.0))


def _next_intraday_return(row: dict) -> float | None:
    """T+1 开盘 → T+1 收盘。窗口 (T+1, T+1] 内不可能有除权事件，故写成裸价格比。"""
    entry, exit_price = row.get("next_open"), row.get("next_close")
    if not entry or entry <= 0 or not exit_price or exit_price <= 0:
        return None
    return exit_price / entry - 1


#: **结果口径（端点）登记表**。换口径＝**新定义 → 必须新开 design reference**（A.1）。
#:
#: 三条口径共用同一套样本域、对照、分层、匹配与置换机制，**只差"从哪个价格算到哪个价格"**，
#: 因此结果可以直接并排比较——这正是回答"扣掉买不到之后还剩多少"所需要的形式。
#:
#: `panel_column` 是面板里的列名；`settlement_rows` 指明 live 通路读哪张结果表
#: （`close_t` → `settlement_returns`；`next_open` → `settlement_returns_next_open`）；
#: `builder` 把 live 的原始行换算成收益率。
OUTCOME_BASES = {
    "close_t": {
        "name": "T 收盘 → T+20 收盘",
        "panel_column": "forward_return",
        "settlement_rows": "close_t",
        "builder": total_return,
        "buy_point": "评估日 T 的**收盘价**",
        "corporate_action_cutoff": "ex_date_ms > T（窗口 (T, T+20]）",
        "tradable": False,
        "note": "既有口径，`16`/`17` 的全部结论都建立在它上面。**封板到收盘的股票当日收盘价"
                "买不到**，所以它隐含『按 T 收盘价成交』这个不可实现的假设（D43/D46）。"
                "本次保留它**只作对照**。",
    },
    "next_open": {
        "name": "T+1 开盘 → T+20 收盘（**可成交口径，本次主检验**）",
        "panel_column": "forward_return_next_open",
        "settlement_rows": "next_open",
        "builder": _next_open_total_return,
        "buy_point": "评估日**次日（T+1）的开盘价**",
        "corporate_action_cutoff": "ex_date_ms > T+1（窗口 (T+1, T+20]）——买入日当天除权的"
                                   "拿不到，因为他买到的已是除权价",
        "tradable": True,
        "note": "把买入点挪到次日开盘后，『封板到收盘买不到』这个缺口就被扣掉了。"
                "它仍然是**超额**（相对同层对照），不是可实现收益：不计冲击成本、"
                "不计滑点，也未验证 T+1 开盘一定有量。",
    },
    "next_intraday": {
        "name": "T+1 开盘 → T+1 收盘（次日日内）",
        "panel_column": "forward_return_next_intraday",
        "settlement_rows": "next_open",
        "builder": _next_intraday_return,
        "buy_point": "T+1 开盘价",
        "corporate_action_cutoff": "窗口 (T+1, T+1] 为空——任何公司行动都不会落在里面",
        "tradable": True,
        "note": "**用来判断缺口是不是就是全部效应**：若 20 日效应在次日日内就基本实现，"
                "那它更像是『次日开盘的溢价/折价』而不是 20 日的持续漂移。",
    },
}

#: 各结果口径对应的**预注册设计 reference**——换口径就是换定义，必须新开 reference（A.1）。
#: 报告里必须写出来，否则读者无法核实"这个门槛是在看到数字之前定的"。
#:
#: 注：`20` 号接力件建议把可成交性轮次登记为 `@v6`，实际登记为 **`@v5`**——因为
#: `@v5` 从未被占用（`@v1`–`@v4` 已用），跳号会让治理台账看起来像缺了一条记录。
OUTCOME_BASIS_DESIGNS = {
    "close_t": "limit_up_event_study_design@v1 / @v2 / @v3 / @v4",
    "next_open": "limit_up_event_study_design@v5",
    "next_intraday": "limit_up_event_study_design@v5",
}
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


def _difference(left: float, right: float) -> float:
    """两个可能为 NaN 的均值之差：任一侧算不出来就返回 NaN，**不拿 0 兜底**。"""
    if left != left or right != right:
        return float("nan")
    return left - right


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

    def __init__(self, repository, *, external=None) -> None:
        """`external` 是**外部字段的取值器**：`external(field, thscode, date_ms) -> float | None`。

        只有评价 `EXTERNAL_FIELDS` 里的字段时才需要它（本轮是财务 F-Score 与涨停池的交互）。
        不注入时这些字段一律取不到值 → 相关评估日自然被跳过，不会产出"看起来有结果"的空报告。
        """
        self._repository = repository
        self._external = external

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
        if field in EXTERNAL_FIELDS:
            # 同问：财务质量的"调节作用"会不会只是连板结构的影子？
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
                      cross_by_symbol: dict[str, dict],
                      external=None) -> dict[str, float]:
        """当日**池内**该字段的取值（派生字段与外部字段都在这里算）。

        派生字段缺当日横截面行或分母 ≤ 0 时**不给值**——绝不能拿 0 或全网均值去凑，
        否则"缺数据"会被当成"低封成比"混进低组，直接把结论做出来。
        外部字段同理：provider 取不到就给不出值（不填补、不用池内均值顶替）。
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
        if field in EXTERNAL_FIELDS:
            if external is None:
                return {}
            values = {}
            for symbol, item in pool.items():
                value = external(field, symbol, item.get("date_ms"))
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
                    pool_union: set[str], return_basis: str = "close_t") -> dict[str, dict]:
        """一个评估日：逐字段 × 逐变体算真实超额 + 当日的 `draws` 次对照置换。

        取数（横截面 + 前向收益）**只做一次**，所有字段与所有变体共用——这是把
        "一个字段一遍"改成"一遍出全部"的原因：两个字段各跑一遍会让取数成本翻倍，
        而分层与匹配变体的边际成本只是记账与重抽。原始收益当天用完就丢。
        """
        cross = self._repository.cross_section(date_ms=date_ms)
        next_rows = self._repository.settlement_returns_next_open(
            date_ms=date_ms, horizon_days=horizon_days)
        gap_by_symbol = {}
        for row in next_rows:
            entry_price, close_t = row.get("next_open"), row.get("close_t")
            gap_by_symbol[row["thscode"]] = (entry_price / close_t - 1
                                             if entry_price and close_t and close_t > 0 else None)
        if return_basis == "close_t":
            outcome = {row["thscode"]: total_return(row)
                       for row in self._repository.settlement_returns(
                           date_ms=date_ms, horizon_days=horizon_days)}
        else:
            builder = OUTCOME_BASES[return_basis]["builder"]
            outcome = {}
            for row in next_rows:
                value = builder(row)
                if value is not None:
                    outcome[row["thscode"]] = value
        if not cross or not outcome:
            return {}
        snapshot = {row["thscode"]: {**row,
                                     "next_open_gap": gap_by_symbol.get(row["thscode"])}
                    for row in cross}
        return self._details(date_ms, pool, snapshot, outcome, horizon_days=horizon_days,
                             fields=fields, draws=draws, rng=rng, pool_union=pool_union)

    def _details(self, date_ms: int, pool: dict[str, dict], snapshot: dict[str, dict],
                 outcome: dict[str, float], *, horizon_days: int, fields: tuple[str, ...],
                 draws: int, rng: random.Random, pool_union: set[str]) -> dict[str, dict]:
        """**单一实现**：给定当日的横截面快照与前向收益，算出全部字段 × 全部变体。

        live 通路（逐日查库）与面板通路（预计算缓存）都调用它——**两条通路共用核心**，
        才谈得上"切换前后结果一致"，也才有一处可写等价比对。
        """
        if not snapshot or not outcome:
            return {}
        turnover = {symbol: row.get("turnover_20") for symbol, row in snapshot.items()
                    if (row.get("turnover_20") or 0) > 0}
        if not turnover:
            return {}
        buckets = self._quantiles(turnover, DECILES)
        prior = {}
        for symbol, row in snapshot.items():
            value = self._prior_momentum(row)
            if value is not None:
                prior[symbol] = value
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
        # 分层要用到**另一个字段**的中位数（例如"封单高"这一层）。原生字段与派生字段
        # 都按同一套 `_field_values` 取（派生字段要读当日横截面行），绝不两处各写一遍。
        field_values = {name: self._field_values(name, pool, snapshot, self._external)
                        for name in KNOWN_FIELDS}
        medians = {name: (sorted(values.values())[len(values) // 2] if values else None)
                   for name, values in field_values.items()}

        for field in fields:
            encoding = KNOWN_FIELDS[field]
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
                    # 「买不到」的量化：次日开盘跳空。**描述量**——不参与分组、不参与重抽、
                    # 不入门槛，只在同一批事件上顺手记下它的分布（见 `_variant_report`）。
                    gaps: dict[str, list[float]] = {"high": [], "low": []}
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
                        side = "high" if flag else "low"
                        events[side].append((key, outcome[symbol]))
                        gap = snapshot[symbol].get("next_open_gap")
                        if isinstance(gap, (int, float)) and gap == gap:
                            gaps[side].append(gap)
                    variants[variant] = {"events": events, "means": means, "grid": grids,
                                         "matched": matched, "gaps": gaps}

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
                entry["next_open_gap"] = {
                    "high_mean": _mean(payload["gaps"]["high"]),
                    "low_mean": _mean(payload["gaps"]["low"]),
                    "high_minus_low": _difference(_mean(payload["gaps"]["high"]),
                                                  _mean(payload["gaps"]["low"])),
                    "high_events": len(payload["gaps"]["high"]),
                    "low_events": len(payload["gaps"]["low"]),
                }
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
        for side, label in zip(("high", "low"), KNOWN_FIELDS[field]["groups"]):
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
        # 「买不到」的量化：高/低两组**次日开盘跳空**的均值与差值。
        # ⚠️ 它是**描述量**，所以**不放进 `results`**（那里只有被检验的三个统计量），
        # 而是单独一块 `descriptive`：不给 t 值、不入门槛计数。给它一个 t 就会把它拖进
        # 多重检验计数、并诱使读者拿它当第 N 个显著性声明（要当检验端点须新开 reference）。
        gap = [entry["next_open_gap"] for entry in entries]
        descriptive = {"next_open_gap": {
            "group": "次日开盘跳空（描述量，**不入门槛**）", "events": None,
            "mean_excess": round(_mean([item["high_minus_low"] for item in gap]), 6),
            "high_mean": round(_mean([item["high_mean"] for item in gap]), 6),
            "low_mean": round(_mean([item["low_mean"] for item in gap]), 6),
            "events_high": sum(item["high_events"] for item in gap),
            "events_low": sum(item["low_events"] for item in gap),
            "t_stat": None,
        }}
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
            "descriptive": descriptive,
            "placebo": {"kind": "对照置换（同层重抽，保样本量与日期分布）",
                        "by_group": by_group},
        }

    def _report(self, field: str, per_date: dict[int, dict], *, horizon_days: int,
                draws: int, seed: int, dates_with_pool: int,
                family_prior_statistics: int = 0, source_info: dict | None = None,
                return_basis: str = "close_t") -> dict:
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
            "field": field, "field_name": KNOWN_FIELDS[field]["name"],
            "groups": KNOWN_FIELDS[field]["groups"],
            "split": KNOWN_FIELDS[field]["split"],
            # 数据通路必须随结果一起披露：面板与 live 已做过逐位等价比对（49,095 个单元
            # 零差异），但读者仍需知道这份数字是从哪条通路来的。
            "data_path": source_info or {"source": "live"},
            # **结果口径**必须与数字一起披露：同一个 t 值在"T 收盘起算"与"T+1 开盘起算"
            # 下含义完全不同（前者不可成交）。换口径＝新定义，须新开 design reference。
            "outcome_basis": {
                "id": return_basis,
                **{key: value for key, value in OUTCOME_BASES[return_basis].items()
                   if key not in ("panel_column", "settlement_rows", "builder")},
            },
            "horizon_days": horizon_days, "draws": draws,
            "dates_with_pool": dates_with_pool, "dates_usable": len(usable),
            "events_total": base.get("events", 0),
            "control_size_median": (sorted(per_date[d]["control_size"] for d in usable)[
                len(usable) // 2] if usable else None),
            "results": base.get("results", {}),
            "descriptive": base.get("descriptive", {}),
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
                "family": FIELD_FAMILIES[field],
                "family_prior_statistics": max(0, int(family_prior_statistics)),
                "family_total_statistics": family_total,
                "bonferroni_family_abs_t": family_threshold,
                "family_counting_note": "族级计数**按字段**给（`17` §4.3 修正）："
                                        "`family_prior_statistics` = 该字段所属族在本轮该字段"
                                        "之外已看过的全部比较条数（含此前轮次与本轮同族其它字段），"
                                        "本轮该字段的 "
                                        f"{statistics_tested} 条另加 → 族级共 {family_total} 条。",
                "note": "按方案 A.2，多重检验计数必须包含**全部尝试过的**比较（含本轮新增的"
                        "稳健性变体、分时段复现，以及**同一族此前已做过的**比较——"
                        "`family_prior_statistics` 就是那部分的条数）；主检验只有 2 条，"
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
            "design": "hypothesis_registry: " + OUTCOME_BASIS_DESIGNS[return_basis],
            "limitations": [
                "涨停池只覆盖已同步的交易日（实测可回溯到 2022-01-05，更早的日子静默返回 0 条），"
                "样本是 2022 年以后的市场状态，不可外推。",
                "对照匹配成交额十分位（精细变体再加事前 40 日动量三分位），"
                "**没有**匹配行业、市值、β——配对超额里仍含这些未控制维度的差异。",
                "本地行情库不含退市标的（幸存者偏差），『涨停后一路走坏直至退市』的情形"
                "不可能出现在样本里，因此负向超额被**低估**、正向超额被高估。",
                "这是**超额**（相对同层对照），不是可实现收益：不计交易成本、不考虑次日"
                "难以成交，也不构成任何可执行的多空策略。"
                + ("**本口径起算于 T 收盘价——封板到收盘的标的当日买不到**，"
                   "因此它隐含『按 close_T 成交』这个不可实现的假设（D43/D46）；"
                   "可成交口径见 `next_open`。"
                   if not OUTCOME_BASES[return_basis]["tradable"] else
                   f"**本口径起算于{OUTCOME_BASES[return_basis]['buy_point']}**"
                   f"（买入日后才计入公司行动：{OUTCOME_BASES[return_basis]['corporate_action_cutoff']}），"
                   "『封板当日买不到』的缺口已被扣掉；但仍未计冲击成本与滑点，"
                   "也未验证 T+1 开盘一定有足够成交量。"),
                "逐日超额存在重叠样本（连板连续上榜）与市场状态持续，t 值假设逐日独立，"
                "因此 |t| 偏乐观；分时段复验与多重检验门槛是为此设的防线。",
                "前向收益含评估日之后的信息，**只用于事后评价**，不得作为信号输入。",
            ],
            "disclaimer": "研究参考，不构成投资建议。",
        }

    # --- 两条数据通路（共用 `_details`，因此可以逐位比对）--------------------
    def _ensure_panel(self, *, horizon_days: int) -> dict:
        """确保面板可用；过期就重建。**重建是安全的**——面板纯属派生物。"""
        status = self._repository.event_panel_status()
        if status["ready"]:
            return {"path": status["path"], "rebuilt": False, "reason": "",
                    "schema_version": status["meta"].get("schema_version")}
        built = self._repository.build_event_panel(horizon_days=horizon_days)
        return {"path": built["path"], "rebuilt": True, "reason": status["reason"],
                "schema_version": built["meta"].get("schema_version")}

    def _date_pairs(self, dates: list[int], by_date: dict[int, dict],
                    *, start_ms: int | None, end_ms: int | None, source: str,
                    horizon_days: int, return_basis: str = "close_t"):
        """产出 `(date_ms, snapshot, outcome)`。

        - `panel`：一次性读出面板里**这些池内日期**的全部输入（不读无关日期）；
        - `live`：逐日查库（慢，但用来证明面板等价）。

        `outcome` 是**该结果口径**下的前向收益。`snapshot` 除了横截面原料，
        还带上 `next_open_gap`（**次日开盘跳空**）——两条通路都必须给，否则
        「买不到」的量化会在面板/live 之间不一致，等价比对就失去意义。
        """
        columns = OUTCOME_BASES[return_basis]["panel_column"]
        if source == "panel":
            wanted = [date_ms for date_ms in dates
                      if (start_ms is None or date_ms >= start_ms)
                      and (end_ms is None or date_ms <= end_ms)]
            for date_ms, panel_rows in self._repository.event_panel_window(
                    dates=wanted, panel_ready_checked=True):
                if not panel_rows:
                    continue
                outcome = {symbol: row[columns]
                           for symbol, row in panel_rows.items()
                           if row[columns] is not None}
                yield date_ms, panel_rows, outcome
            return
        builder = OUTCOME_BASES[return_basis]["builder"]
        for date_ms in dates:
            snapshot = {row["thscode"]: row
                        for row in self._repository.cross_section(date_ms=date_ms)}
            # 次日开盘口径的原始行：既要它算收益（非 close_t 口径），也要它算跳空
            # （所有口径都要——跳空是「买不到」的量化，描述量也得两条通路一致）。
            next_rows = self._repository.settlement_returns_next_open(
                date_ms=date_ms, horizon_days=horizon_days)
            gap_by_symbol = {}
            for row in next_rows:
                entry_price, close_t = row.get("next_open"), row.get("close_t")
                gap = (entry_price / close_t - 1
                       if entry_price and close_t and close_t > 0 else None)
                gap_by_symbol[row["thscode"]] = gap
            for symbol, row in snapshot.items():
                row["next_open_gap"] = gap_by_symbol.get(symbol)
            if return_basis == "close_t":
                outcome = {row["thscode"]: total_return(row)
                           for row in self._repository.settlement_returns(
                               date_ms=date_ms, horizon_days=horizon_days)}
            else:
                outcome = {}
                for row in next_rows:
                    value = builder(row)
                    if value is not None:
                        outcome[row["thscode"]] = value
            yield date_ms, snapshot, outcome

    # --- 主入口 -----------------------------------------------------------
    def run_fields(self, *, fields: tuple[str, ...], start_ms: int | None = None,
                   end_ms: int | None = None, horizon_days: int = 20, draws: int = 200,
                   seed: int = 20260913, progress=None,
                   family_prior_statistics: int | Mapping[str, int] = 0,
                   source: str = "panel", return_basis: str = "close_t") -> dict[str, dict]:
        """**一次遍历**算出多个字段的全部变体：取数只做一遍，字段与变体各自分组与重抽。

        `family_prior_statistics`：族级多重检验计数的**先验部分**，可以是整数（所有字段共用）
        或 `{字段: 条数}`（按字段给，见 `resolve_family_priors` 的语义与两条硬性检查）。

        `return_basis`：结果口径（端点）。`close_t` = 既有口径（T 收盘起算，**不可成交**）；
        `next_open` = 可成交口径（T+1 开盘起算，本次主检验）；`next_intraday` = T+1 日内。
        换口径＝换定义，必须新开 design reference（A.1），见 `OUTCOME_BASES`。

        `source`：
        - `"panel"`（默认）：用**预计算面板**（一次全表扫描物化全部评估日的输入）。
          过期面板会被拒绝并自动重建（面板是派生物，重建永远正确）。
        - `"live"`：逐日调用 `cross_section` + `settlement_returns`。慢得多（每个评估日
          两次全表扫描），保留它是为了**与面板做等价比对**——两条通路共用 `_details`。
        """
        unknown = [item for item in fields if item not in KNOWN_FIELDS]
        if unknown:
            raise ValueError(f"不支持的事件研究字段：{unknown}（可选 {list(KNOWN_FIELDS)}）")
        if not fields:
            raise ValueError("至少要指定一个字段。")
        if draws < 1 or not 1 <= horizon_days <= 250:
            raise ValueError("重抽次数必须为正、前瞻期需在 1–250 个交易日之间。")
        if source not in ("panel", "live"):
            raise ValueError(f"source 只能是 panel 或 live，收到 {source!r}")
        if return_basis not in OUTCOME_BASES:
            raise ValueError(f"return_basis 只能是 {sorted(OUTCOME_BASES)}，收到 {return_basis!r}")
        family_priors = resolve_family_priors(family_prior_statistics, fields)

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

        panel_info: dict = {"source": source}
        if source == "panel" and horizon_days != 20:
            # 面板把 20 日前瞻期写死在 SQL 里；其它前瞻期回落逐日查库，
            # 并**在报告里写明回落原因**，不许悄悄换通路。
            panel_info = {"source": "live",
                          "reason": f"面板只支持 20 日前瞻期，本次为 {horizon_days} 日 → 回落逐日查库"}
            source = "live"
        if source == "panel":
            panel_info = self._ensure_panel(horizon_days=horizon_days)
            panel_info["source"] = "panel"

        per_field: dict[str, dict[int, dict]] = {field: {} for field in fields}
        pairs = self._date_pairs(dates, by_date, start_ms=start_ms, end_ms=end_ms,
                                 source=source, horizon_days=horizon_days,
                                 return_basis=return_basis)
        for index, (date_ms, snapshot, outcome) in enumerate(pairs, 1):
            details = self._details(date_ms, by_date[date_ms], snapshot, outcome,
                                    horizon_days=horizon_days, fields=fields, draws=draws,
                                    rng=rng, pool_union=pool_union)
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
                                          family_prior_statistics=family_priors[field],
                                          source_info=panel_info, return_basis=return_basis)
        return reports

    def run(self, *, start_ms: int | None = None, end_ms: int | None = None,
            horizon_days: int = 20, field: str = "continue_day_cnt",
            draws: int = 200, seed: int = 20260913, progress=None,
            source: str = "panel", return_basis: str = "close_t") -> dict:
        """单字段入口（返回结构保持 `results` = base_all，向后兼容）。"""
        return self.run_fields(fields=(field,), start_ms=start_ms, end_ms=end_ms,
                               horizon_days=horizon_days, draws=draws, seed=seed,
                               progress=progress, source=source,
                               return_basis=return_basis)[field]
