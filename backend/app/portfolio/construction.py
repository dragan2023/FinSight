"""**组合构建层**：把"买哪些"变成"各买多少钱"——机构打法的核心那一步。

## 为什么要有这一层

前面所有轮次里，钱是**等权**分的：入选 10 只就每只 10%，谁波动大谁就实际承担了更多风险。
机构不这么做。机构的做法是**先把风险预算分好，再让钱跟着风险走**——
因为组合的亏损主要由**风险贡献**决定，而不是由**资金占比**决定。

这对散户同样可行：你不需要做空、不需要杠杆、不需要新数据，
只需要在**同样是那几只票**上把金额分配方式换掉。这是"模拟机构打法"里
**唯一一个零新增权限就能抄、且效果可验证**的动作。

## 三种权重方案（都在本模块里，可对比）

| 方案 | 做法 | 它回答什么问题 |
|---|---|---|
| `rank_equal` | 按信号强度排序，每只给固定上限（**旧行为，对照基准**） | "不按风险分配"是什么结果 |
| `inverse_vol` | 权重 ∝ 1/标的波动率（只用对角项） | 只看"各自多颠"，不看"一起颠" |
| `equal_risk` | **等风险贡献（ERC）**：让每只票对组合波动的贡献相同 | 把相关性也算进去 |

`inverse_vol` 与 `equal_risk` 必须一起跑：如果两者结果一样，说明**协方差里的
非对角项没有信息量**，那就没必要为了一个更复杂的算法维护更多代码（A.2 的同理）。

## 三个必须守住的纪律

1. **协方差不许用未来数据**：估计窗口只取**截至决策日**的日收益（见 `trailing_returns`），
   与信号一样遵循"前一交易日收盘决策、次日开盘执行"。
2. **收缩估计**：20 个标的、120 天样本，样本协方差的对角线噪声很大
   （样本数/维数 = 6，勉强够，但非对角项基本是噪声）。所以默认向对角矩阵**收缩**
   （`shrinkage=0.5`），这是机构的标准做法，不是调参。
3. **换手带（no-trade band）**：目标权重与当前权重之差小于 `turnover_band` 就不动。
   这直接对应散户的真实约束——**每次调仓都要付钱**，而按风险微调权重带来的
   预期收益远小于摩擦成本。机构在这一点上和我们一样。

## 约束的施加顺序（写死，避免"约束之间互相打架"）

    原始风险权重 → 单票上限 → 行业上限 → 换手带 → 归一化到目标暴露

顺序写死的原因：先裁上限会改变风险贡献（裁掉一个高风险标的，剩下的风险占比变大），
所以**换手带必须在约束之后**，否则"为了不调仓而保留的权重"会突破上限。
归一化放在最后，保证"约束生效后总仓位不偷偷变化"。
"""
from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Literal

from ..strategy.models import PriceBar

WeightScheme = Literal["rank_equal", "inverse_vol", "equal_risk"]

#: 缺失/占位行业标签。**绝不能**给它们套用行业上限——给一个我们其实不知道的标签施加约束，
#: 等于把「行业上限」偷偷变成「总仓位上限」。2026-09-15 实测踩到：验收脚本把 20 只标的
#: 全部写成 `未分类`，于是 max_industry_weight=0.30 让**总仓位**只剩 30%（3 只 × 10%），
#: 而对照的"等权买入持有"是满仓——那个对比从一开始就不公平，而且报告里看不出来。
UNKNOWN_INDUSTRIES = frozenset({"", "未分类", "unknown", "none", "null"})


def industry_key(industry: object) -> str | None:
    """归一化行业标签；**未知一律返回 None** ＝「不参与行业约束」（而不是"单独一类"）。"""
    text = str(industry if industry is not None else "").strip()
    return None if text.lower() in UNKNOWN_INDUSTRIES else text

#: 估计风险所需的**最少**日收益样本数。低于它就用横截面中位数波动率兜底，
#: 而不是拿两三天数据硬算一个波动率（那比不知道更危险）。
MIN_RETURN_SAMPLES = 30


@dataclass(frozen=True)
class ConstructionPolicy:
    """组合构建的完整规则（声明式，可复算）。"""

    scheme: WeightScheme = "rank_equal"
    #: 估计风险用的日收益窗口（交易日）。120 天 ≈ 半年，是机构常用的量级。
    lookback_days: int = 120
    #: 向对角矩阵收缩的强度：0 = 用样本协方差，1 = 只用各自方差（忽略相关性）。
    shrinkage: float = 0.5
    #: 换手带：目标与当前权重之差小于它就维持当前权重。
    turnover_band: float = 0.0
    max_positions: int = 10
    max_single_weight: float = 0.10
    max_industry_weight: float = 0.30
    #: **显式的总仓位上限**（默认 0.95，留 5% 现金覆盖换手成本）。
    #: 见 `portfolio/backtest.py` 的同名参数：在它有名字之前，"投多少"是被行业上限
    #: 隐式决定的，行业标签缺失时更会退化成"总仓位 = 行业上限"。
    max_total_exposure: float = 0.95
    #: ERC 迭代上限与收敛判据（相对权重变化）。
    erc_iterations: int = 200
    erc_tolerance: float = 1e-8

    def __post_init__(self) -> None:
        if not 0.0 <= self.shrinkage <= 1.0:
            raise ValueError("收缩强度必须落在 [0, 1]。")
        if self.lookback_days < MIN_RETURN_SAMPLES:
            raise ValueError(f"风险估计窗口至少需要 {MIN_RETURN_SAMPLES} 个交易日。")
        if self.turnover_band < 0:
            raise ValueError("换手带不能为负。")
        if not 0 < self.max_single_weight <= 1 or not 0 < self.max_industry_weight <= 1:
            raise ValueError("组合上限参数无效。")
        if not 0 < self.max_total_exposure <= 1:
            raise ValueError("总仓位上限必须落在 (0, 1]。")
        if self.max_positions < 1:
            raise ValueError("持仓数上限必须为正。")


@dataclass(frozen=True)
class ReturnSeries:
    """一只标的的日收益序列（`dates[i]` 的收益是 `returns[i]`）。

    预计算一次、按窗口切片，避免在逐日循环里反复重建日期列表——
    组合回放要跑 2000+ 个交易日，这里的常数因子是实打实的。
    """

    dates: list[str]
    returns: list[float]

    def window(self, day: str, lookback_days: int) -> list[float]:
        """**严格早于 `day`** 的最近 `lookback_days` 个日收益。

        不含 `day` 当天：当天收盘才能算完当天收益，而交易发生在此之前。
        少算一天，换"绝不前视"，这个交换不做。
        """
        end = bisect_left(self.dates, day)
        start = max(0, end - lookback_days)
        return self.returns[start:end]


def build_return_series(bars: list[PriceBar]) -> ReturnSeries:
    dates: list[str] = []
    returns: list[float] = []
    for index in range(1, len(bars)):
        previous = bars[index - 1].close
        current = bars[index].close
        if previous > 0 and current > 0:
            dates.append(bars[index].date)
            returns.append(current / previous - 1)
    return ReturnSeries(dates=dates, returns=returns)


def trailing_returns(bars: list[PriceBar], day: str, *, lookback_days: int) -> list[float]:
    """单次调用的便利函数（与 `ReturnSeries.window` 同口径）。"""
    return build_return_series(bars).window(day, lookback_days)


def shrink_covariance(returns_by_code: dict[str, list[float]], *,
                      shrinkage: float) -> tuple[list[str], list[list[float]]]:
    """样本协方差 → 向对角矩阵收缩（Ledoit-Wolf 的简化常数版本）。

    只有**两家都有样本**的标的对才计入；样本长度按最短的那只对齐到尾部
    （尾部比头部更接近当前的波动水平，这也是机构常用的做法）。
    返回 `(codes, matrix)`，矩阵按 `codes` 的顺序排列。
    """
    codes = sorted(returns_by_code)
    usable = [code for code in codes if len(returns_by_code[code]) >= MIN_RETURN_SAMPLES]
    if not usable:
        return [], []
    length = min(len(returns_by_code[code]) for code in usable)
    if length < 2:
        return [], []
    windows = {code: returns_by_code[code][-length:] for code in usable}
    n = len(usable)
    means = {code: sum(windows[code]) / length for code in usable}
    matrix: list[list[float]] = []
    for i, code_i in enumerate(usable):
        row: list[float] = []
        for j, code_j in enumerate(usable):
            if i == j:
                variance = sum((value - means[code_i]) ** 2 for value in windows[code_i])
                variance /= (length - 1)
                row.append(variance)
            else:
                covariance = sum((windows[code_i][k] - means[code_i])
                                 * (windows[code_j][k] - means[code_j])
                                 for k in range(length)) / (length - 1)
                row.append(covariance)
        matrix.append(row)
    if shrinkage > 0:
        for i in range(n):
            diagonal = matrix[i][i]
            for j in range(n):
                target = diagonal if i == j else 0.0
                matrix[i][j] = (1 - shrinkage) * matrix[i][j] + shrinkage * target
    # 数值保护：协方差矩阵必须对称正定，浮点误差可能破坏对称性
    for i in range(n):
        for j in range(i + 1, n):
            symmetric = (matrix[i][j] + matrix[j][i]) / 2
            matrix[i][j] = matrix[j][i] = symmetric
    for i in range(n):
        if matrix[i][i] <= 0:
            matrix[i][i] = 1e-12
    return usable, matrix


def inverse_vol_weights(codes: list[str], matrix: list[list[float]]) -> dict[str, float]:
    """权重 ∝ 1/波动率（只用对角线）。这是"只看各自多颠、不看一起颠"的对照。"""
    raw: dict[str, float] = {}
    for index, code in enumerate(codes):
        sigma = math.sqrt(max(matrix[index][index], 1e-18))
        raw[code] = 1.0 / sigma
    return _normalize(raw)


def equal_risk_weights(codes: list[str], matrix: list[list[float]], *,
                       iterations: int = 200, tolerance: float = 1e-8) -> dict[str, float]:
    """**等风险贡献（ERC）**：让每只标的对组合波动的边际贡献相同。

    做法是循环坐标下降。第 i 只的**风险贡献**是 `x_i · (Σx)_i`，
    要求它们全部相等（都等于 1 即可，方向唯一、尺度无关）：

        σ_ii · x_i² + x_i · Σ_{j≠i} σ_ij · x_j − 1 = 0

    这是关于 `x_i` 的一元二次方程，取正根即可，逐个坐标更新直到收敛。
    长期多约束（`x_i > 0`）**自动满足**——正根必为正，所以不需要额外投影。

    为什么用 ERC 而不是均值-方差优化：均值-方差需要**预期收益**输入，
    而我们没有任何经过验证的预期收益（前面几轮的全部努力都没有找到）。
    硬套一个拍脑袋的预期收益，得到的会是"伪精确"的权重——这在本仓库里
    已被明确禁止（见 `allocate_candidates` 的原始说明）。风险层面则不需要预测：
    协方差可以从历史稳健估计，这是唯一诚实的优化目标。
    """
    n = len(codes)
    if n == 0:
        return {}
    if n == 1:
        return {codes[0]: 1.0}
    x = [1.0 / math.sqrt(max(matrix[i][i], 1e-18)) for i in range(n)]
    scale = sum(x) or 1.0
    x = [value / scale * n for value in x]          # 起点：逆波动率
    for _ in range(iterations):
        largest_change = 0.0
        for i in range(n):
            off_diagonal = sum(matrix[i][j] * x[j] for j in range(n) if j != i)
            a = matrix[i][i]
            b = off_diagonal
            # a·x² + b·x − 1 = 0 → 取正根
            discriminant = b * b + 4 * a
            updated = (-b + math.sqrt(discriminant)) / (2 * a) if a > 0 else x[i]
            if not math.isfinite(updated) or updated <= 0:
                updated = x[i]
            largest_change = max(largest_change, abs(updated - x[i]) / max(x[i], 1e-18))
            x[i] = updated
        if largest_change < tolerance:
            break
    return _normalize({code: x[index] for index, code in enumerate(codes)})


def risk_contributions(weights: dict[str, float], returns_by_code: dict[str, list[float]], *,
                       shrinkage: float = 0.5) -> dict[str, float]:
    """各标的**实际**占组合方差的比例（审计用）。

    这是"ERC 到底有没有把风险摊平"的唯一硬证据：权重相等 ≠ 风险相等，
    所以不能拿权重当结果。返回的比例之和为 1；无法估计的标的不出现。
    """
    codes = sorted(code for code, value in weights.items() if value > 0)
    if not codes:
        return {}
    usable, matrix = shrink_covariance({code: returns_by_code.get(code, []) for code in codes},
                                       shrinkage=shrinkage)
    if len(usable) < 2:
        return {}
    vector = [weights[code] for code in usable]
    n = len(usable)
    variance = sum(vector[i] * matrix[i][j] * vector[j] for i in range(n) for j in range(n))
    if variance <= 0:
        return {}
    return {code: vector[i] * sum(matrix[i][j] * vector[j] for j in range(n)) / variance
            for i, code in enumerate(usable)}


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total <= 0:
        return {code: 0.0 for code in weights}
    return {code: value / total for code, value in weights.items()}


def apply_caps(weights: dict[str, float], industries: dict[str, str], *,
               max_single_weight: float, max_industry_weight: float,
               iterations: int = 40) -> dict[str, float]:
    """单票上限 + 行业上限，用**注水法**把超额部分再分配给还有空间的标的。

    三条纪律，每条都是踩过坑换来的：

    1. **不许只截断**：截断会让总仓位凭空变小、钱变现金，那是"约束偷偷改了暴露度"。
    2. **不许向上归一化**：如果约束本身不可行（例如 6 只票、单票 20%、行业上限把总量压到
       90%），正确结果是**只投 90%**，不是硬拉到 100% 把上限冲掉。
       （第一版就是这么错的：末尾 `_normalize` 一拉，单票上限直接变成 33%。）
    3. **未知行业不参与行业约束**：给一个我们不知道的标签施加约束，等于把"行业上限"
       变成"总仓位上限"——这正是 `portfolio/backtest.py` 里那个已修 bug 的成因。

    允许的收尾操作只有**向下缩放**——向下缩放不会破坏任何上限约束。
    """
    target_total = sum(weights.values())
    if target_total <= 0:
        return {}
    current = {code: max(0.0, value) for code, value in weights.items()}
    group_of = {code: industry_key(industries.get(code)) for code in current}

    for _ in range(iterations):
        for code in current:
            current[code] = min(current[code], max_single_weight)
        by_group: dict[str, list[str]] = {}
        for code, group in group_of.items():
            if group is not None:
                by_group.setdefault(group, []).append(code)
        for members in by_group.values():
            used = sum(current[code] for code in members)
            if used > max_industry_weight:
                scale = max_industry_weight / used
                for code in members:
                    current[code] *= scale
        excess = target_total - sum(current.values())
        if excess <= 1e-12:
            break
        group_used: dict[str, float] = {}
        for code, group in group_of.items():
            if group is not None:
                group_used[group] = group_used.get(group, 0.0) + current[code]
        room: dict[str, float] = {}
        for code, group in group_of.items():
            single_room = max(0.0, max_single_weight - current[code])
            if group is None:
                room[code] = single_room
            else:
                room[code] = min(single_room, max(0.0, max_industry_weight - group_used[group]))
        total_room = sum(room.values())
        if total_room <= 1e-12:
            break                       # 约束不可行 → 投不满是**正确**结果，不硬凑
        for code in current:
            current[code] += excess * room[code] / total_room

    total = sum(current.values())
    if total > target_total:            # 只允许向下缩放
        scale = target_total / total
        current = {code: value * scale for code, value in current.items()}
    return {code: value for code, value in current.items() if value > 1e-12}


def target_weights(
    candidates: list[dict],
    *,
    policy: ConstructionPolicy,
    returns_by_code: dict[str, list[float]],
    industries: dict[str, str],
    current_weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """把候选集变成目标权重（**全部约束都在这里施加完**）。

    `candidates` 需要带 `thscode` 与 `score`（信号强度，用于排序与同分时的稳定顺序）。
    `returns_by_code` 是各标的**截至决策日**的日收益（由 `trailing_returns` 生成）。
    `current_weights` 是当前权重（换手带用；传 None 表示视为空仓）。
    """
    ranked = sorted(candidates, key=lambda item: (-float(item["score"]), str(item["thscode"])))
    chosen = [str(item["thscode"]) for item in ranked[:policy.max_positions]]
    if not chosen:
        return {}

    if policy.scheme == "rank_equal":
        raw = {code: 1.0 for code in chosen}
    else:
        usable, matrix = shrink_covariance(
            {code: returns_by_code.get(code, []) for code in chosen},
            shrinkage=policy.shrinkage)
        if len(usable) < 2:
            # 风险估计不可用（历史太短）→ **退化为等权**，而不是硬算。
            # 诚实说明：这一天的结果与 `rank_equal` 相同，会体现在统计里。
            raw = {code: 1.0 for code in chosen}
        else:
            if policy.scheme == "inverse_vol":
                estimated = inverse_vol_weights(usable, matrix)
            else:
                estimated = equal_risk_weights(usable, matrix, iterations=policy.erc_iterations,
                                               tolerance=policy.erc_tolerance)
            # 未参与估计的标的（历史不足）按已估计标的的平均权重给一个中性值
            average = (sum(estimated.values()) / len(estimated)) if estimated else 0.0
            raw = {code: estimated.get(code, average) for code in chosen}

    weights = _normalize({code: value for code, value in raw.items() if value > 0})
    # 总暴露 = min(显式总仓位上限, 单票上限 × 实际持仓数)。上限很小时（如 6 只 × 15%）
    # 也要满仓，而不是每只给上限后剩一堆现金——那会让"仓位上限"意外变成"仓位目标"。
    target_exposure = min(policy.max_total_exposure,
                          policy.max_single_weight * len(weights))
    weights = {code: value * target_exposure for code, value in weights.items()}
    weights = apply_caps(weights, industries,
                         max_single_weight=policy.max_single_weight,
                         max_industry_weight=policy.max_industry_weight)

    if policy.turnover_band > 0 and current_weights:
        banded = dict(weights)
        for code in set(weights) | set(current_weights):
            target = weights.get(code, 0.0)
            held = current_weights.get(code, 0.0)
            if abs(target - held) < policy.turnover_band:
                banded[code] = held
        banded = {code: value for code, value in banded.items() if value > 0}
        # 归一化到"目标暴露"，保证换手带不会偷偷改变总仓位
        total = sum(banded.values())
        if total > 0:
            weights = {code: value / total * target_exposure for code, value in banded.items()}
    return {code: round(value, 10) for code, value in weights.items() if value > 1e-12}
