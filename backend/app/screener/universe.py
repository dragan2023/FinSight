"""股票池规则：**显式、可复现、每一条都能解释为什么排除**（P0-3 验收第 3 条）。

三条纪律：

1. **规则写死在代码里**，不接受调用方传入表达式——否则"复现同一份排序"就无从谈起；
2. **每条排除都要带上原因**，输出里给出逐条计数（`exclusions`），让人能核对
   "为什么这只没进来"；
3. **能排除什么、不能排除什么都要说清楚**。本机数据**没有** ST 标志、没有退市证券池、
   没有真实停牌/涨跌停标志，所以下面前四条是**代理指标**，绝不假装是交易所口径。
"""
from __future__ import annotations

from dataclasses import dataclass

#: 涨跌停代理：主板 10% 涨停，取 9.8% 容差；再要求收盘等于当日最高价（封板特征）。
LIMIT_UP_PROXY_PCT = 9.8


@dataclass(frozen=True)
class UniverseRules:
    """股票池规则。所有阈值都是显式参数，进 `params_hash`。"""

    min_history_bars: int = 252          # 因子预热（与 registry 的 warmup_bars 一致）
    min_turnover_20d: float = 2.0e7      # 20 日平均成交额下限：2000 万元
    exclude_suspected_limit_up: bool = True

    def as_dict(self) -> dict:
        return {"min_history_bars": self.min_history_bars,
                "min_turnover_20d": self.min_turnover_20d,
                "exclude_suspected_limit_up": self.exclude_suspected_limit_up,
                "limit_up_proxy_pct": LIMIT_UP_PROXY_PCT}


#: 每条规则的说明，供输出与文档引用；**不含**任何"无法实现"的粉饰。
RULE_NOTES = {
    "no_bar_on_date": "评估日当日没有成交（疑似停牌或已退市），不可能按当日价格建仓。",
    "insufficient_history": "回溯交易日不足预热要求，因子无法计算。",
    "below_min_turnover": "20 日平均成交额低于下限：不可执行，而非不看好。",
    "non_positive_price": "收盘价或前收 ≤ 0，属数据异常。",
    "suspected_limit_up": "当日涨幅 ≥ 9.8% 且收盘等于最高价（**涨停封板代理指标**），"
                          "次日难以按回测价成交。",
}

#: 本机数据**做不到**的排除项。必须常驻披露，否则使用者会以为池子已经干净了。
UNSUPPORTED_EXCLUSIONS = [
    "ST / *ST 剔除：本地数据没有 `is_st` 或 `listed_days` 字段，无法识别风险警示股。",
    "退市证券池：本地 `daily_k` 不含任何已退市标的（实测 5286 只全部仍在交易），"
    "因此所有历史统计都是乐观偏置的。",
    "真实停牌 / 涨跌停标志：只能用「当日无成交」与「涨幅+收盘=最高」做代理，"
    "与交易所口径不同。",
    "新股上市不足 60 日的特殊处理：本规则只按 K 线根数过滤，不区分上市时间。",
]


def apply_rules(rows: list[dict], rules: UniverseRules) -> tuple[list[dict], dict]:
    """返回 `(保留的行, 排除计数)`。顺序固定，保证可复现。"""
    kept: list[dict] = []
    exclusions = {key: 0 for key in RULE_NOTES}
    for row in rows:
        close = row.get("close")
        prev = row.get("prev_close")
        if close is None or prev is None or close <= 0 or prev <= 0:
            exclusions["non_positive_price"] += 1
            continue
        if (row.get("bars") or 0) < rules.min_history_bars:
            exclusions["insufficient_history"] += 1
            continue
        if (row.get("turnover_20") or 0.0) < rules.min_turnover_20d:
            exclusions["below_min_turnover"] += 1
            continue
        if rules.exclude_suspected_limit_up:
            change_pct = (close / prev - 1) * 100
            high = row.get("high")
            if change_pct >= LIMIT_UP_PROXY_PCT and high is not None and abs(close - high) < 1e-9:
                exclusions["suspected_limit_up"] += 1
                continue
        kept.append(row)
    return kept, exclusions
