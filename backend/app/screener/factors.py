"""因子声明表（方案 02 §A.1：先验逻辑 / 数据可用性 / 方向 / 登记入册）。

**第一期只用「量价 + 流动性」因子**（决策 D1）。财务因子不做，原因不是"不重要"，
而是**算不出来**：`financial_statements` 缺 Z-Score 的 `retained_earnings` /
`market_value_equity`，也缺 F-Score 的 ROA（见 `01` 号文档 F11 与 `10` 号文档 D9）。

每个因子必须写清三件事，缺一不进研究池：

1. **先验逻辑**：一句话说明经济/行为学机制，不接受纯数据挖掘产物；
2. **方向**：`high` 表示"数值越大越看多"，`low` 反之——方向写错会让 IC 符号解释完全反过来；
3. **口径**：它到底怎么算、数据来自哪一列。

**流动性不是 alpha 因子，而是股票池过滤条件**（`turnover_20d` 只进 universe，
不进因子集）。把它混进打分会让"好公司"与"能买"两件事互相污染，也让 IC 无法解释。
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: 因子版本号。**任何口径变更都必须同时改它**——它进 params_hash，
#: 也是治理层 `hypothesis_registry.reference` 的一部分（方案 A.1：变更视为新因子）。
FACTOR_SET_VERSION = "v1"


@dataclass(frozen=True)
class Factor:
    id: str
    name: str
    prior: str
    direction: str          # "high" | "low"
    window: str
    note: str = ""
    #: 取原始值的函数名（在 service.py 里实现），避免因子定义里塞进计算逻辑。
    compute: str = field(default="")


FACTORS: tuple[Factor, ...] = (
    Factor(
        id="momentum_60d", name="60 日总收益（中期动量）",
        prior="趋势延续：中期相对强势的标的在随后一段时间的表现优于弱势标的。",
        direction="high", window="60 个交易日",
        note="公司行动总收益口径；窗口内除权除息已折算，见 repository.cross_section。",
        compute="momentum_60d",
    ),
    Factor(
        id="bias_20", name="20 日乖离率（收盘 / MA20 − 1）",
        prior="短期均值回归：价格显著偏离自身 20 日均线后，短期倾向于向均线回归。",
        direction="low", window="20 个交易日",
        note="与 momentum_60d 方向在短周期上可能冲突——两者是**两个独立的假设**，"
             "不是同一个故事的两种说法。",
        compute="bias_20",
    ),
    Factor(
        id="volatility_20d", name="20 日年化波动率",
        prior="低波动异象：在同等收益下，历史波动更低的标的具有更好的风险调整后表现。",
        direction="low", window="20 个交易日",
        note="年化用 sqrt(252)；仅用评估日及之前的日收益。",
        compute="volatility_20d",
    ),
)

FACTOR_BY_ID = {factor.id: factor for factor in FACTORS}
FACTOR_IDS = tuple(factor.id for factor in FACTORS)

#: 股票池/流动性过滤用的列（**不是** alpha 因子）。
LIQUIDITY_COLUMN = {
    "id": "turnover_20d", "name": "20 日平均成交额",
    "use": "股票池过滤（可交易性），不进入打分",
    "prior": "成交额过低的标的冲击成本高、次日难以按回测价成交，属于不可执行而非 alpha。",
}


def factor_declarations() -> list[dict]:
    """供治理层登记：每个因子一条 `hypothesis_registry` 候选记录。"""
    return [{
        "reference": f"factor:{factor.id}@{FACTOR_SET_VERSION}",
        "kind": "factor", "name": factor.name, "direction": factor.direction,
        "definition": {"id": factor.id, "prior": factor.prior, "window": factor.window,
                       "note": factor.note, "compute": factor.compute,
                       "data_source": "local_duckdb:daily_k + adjustment_factors",
                       "as_of": "只使用评估日及之前的信息；公司行动按 ex_date_ms <= as_of 截断"},
    } for factor in FACTORS]
