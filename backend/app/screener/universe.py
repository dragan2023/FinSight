"""股票池规则：**显式、可复现、每一条都能解释为什么排除**（P0-3 验收第 3 条）。

三条纪律：

1. **规则写死在代码里**，不接受调用方传入表达式——否则"复现同一份排序"就无从谈起；
2. **每条排除都要带上原因**，输出里给出逐条计数（`exclusions`），让人能核对
   "为什么这只没进来"；
3. **能排除什么、不能排除什么都要说清楚**。本机数据**没有** ST 标志、没有退市证券池、
   没有真实停牌标志，所以下面前四条是**代理指标**，绝不假装是交易所口径。

涨停剔除（`exclude_limit_up`）在 2026-09-13 之后有**两种口径**，输出里必须写明用的是哪一种：

- `real_limit_up_pool`：用同花顺**涨停池**（`market_warehouse.limit_pools`，交易所口径）。
  它比价量代理更准：能认 ST 的 5% 涨停（代理按 9.8% 判定会**漏掉**），
  也不会把"收在最高价但没封板"误判成涨停。
- `price_volume_proxy`：涨幅 ≥ 9.8% 且收盘 = 当日最高（**回落口径**，只在当地池子
  没有该评估日的数据时使用；理由逐条写进输出的 `universe.limit_up.reason`）。

**口径必须进 `params_hash`**：同一个评估日在两种口径下是不同的规则，不能被当成同一个结果。
"""
from __future__ import annotations

from dataclasses import dataclass

#: 涨跌停代理：主板 10% 涨停，取 9.8% 容差；再要求收盘等于当日最高价（封板特征）。
LIMIT_UP_PROXY_PCT = 9.8

#: 涨停剔除的口径常量。**随结果一起披露**，不允许只输出"已剔除涨停"。
BASIS_REAL_LIMIT_UP_POOL = "real_limit_up_pool"
BASIS_LIMIT_UP_PROXY = "price_volume_proxy"

#: 规则可选值：优先真实池（默认）、强制代理（**仅用于规则变更的对照证据**）。
LIMIT_UP_SOURCES = ("real_pool_preferred", "proxy_only")


@dataclass(frozen=True)
class LimitUpEvidence:
    """某个评估日的涨停剔除**依据**，由 `ScreenerService` 从本地池子表构造后传入。

    `codes is None` 表示这一天**没有**可用的真实池子，必须回落到代理指标——此时
    `reason` 必须写明为什么（未同步 / 接口失败 / `ok_empty` 可疑），不允许静默回落。
    """

    basis: str
    date_ms: int
    codes: frozenset[str] | None = None
    reason: str = ""
    sync_status: str | None = None
    pool_rows: int = 0

    def as_dict(self) -> dict:
        return {"basis": self.basis, "date_ms": self.date_ms,
                "pool_codes": None if self.codes is None else len(self.codes),
                "pool_rows": self.pool_rows, "sync_status": self.sync_status,
                "reason": self.reason}


@dataclass(frozen=True)
class UniverseRules:
    """股票池规则。所有阈值都是显式参数，进 `params_hash`。"""

    min_history_bars: int = 252          # 因子预热（与 registry 的 warmup_bars 一致）
    min_turnover_20d: float = 2.0e7      # 20 日平均成交额下限：2000 万元
    exclude_suspected_limit_up: bool = True
    #: `real_pool_preferred`（默认）或 `proxy_only`（对照口径，只用来度量规则变更的影响）。
    limit_up_source: str = "real_pool_preferred"

    def __post_init__(self) -> None:
        if self.limit_up_source not in LIMIT_UP_SOURCES:
            raise ValueError(f"limit_up_source 只能是 {LIMIT_UP_SOURCES}，收到 {self.limit_up_source!r}")

    def as_dict(self) -> dict:
        return {"min_history_bars": self.min_history_bars,
                "min_turnover_20d": self.min_turnover_20d,
                "exclude_suspected_limit_up": self.exclude_suspected_limit_up,
                "limit_up_source": self.limit_up_source,
                "limit_up_proxy_pct": LIMIT_UP_PROXY_PCT}


#: 每条规则的说明，供输出与文档引用；**不含**任何"无法实现"的粉饰。
RULE_NOTES = {
    "no_bar_on_date": "评估日当日没有成交（疑似停牌或已退市），不可能按当日价格建仓。",
    "insufficient_history": "回溯交易日不足预热要求，因子无法计算。",
    "below_min_turnover": "20 日平均成交额低于下限：不可执行，而非不看好。",
    "non_positive_price": "收盘价或前收 ≤ 0，属数据异常。",
    "confirmed_limit_up": "当日**在同花顺涨停池中**（交易所口径的封板涨停），"
                          "次日难以按回测价成交。",
    "suspected_limit_up": "涨幅 ≥ 9.8% 且收盘等于最高价（**涨停封板代理指标**，"
                          "回落到代理口径时使用），次日难以按回测价成交。",
}

#: 本机数据**做不到**的排除项。必须常驻披露，否则使用者会以为池子已经干净了。
UNSUPPORTED_EXCLUSIONS = [
    "ST / *ST 剔除：本地行情库没有 `is_st` 字段。涨停池条目里**有** `is_st`，但同花顺手册"
    "明确写它『仅展示，不作为筛选入参』，且只有涨停当天才存在该字段——**不能**用它做"
    "全样本 ST 剔除。",
    "退市证券池：本地 `daily_k` 不含任何已退市标的（实测 5286 只全部仍在交易），"
    "因此所有历史统计都是乐观偏置的。同花顺手边也没有退市池接口（`stock-basics` 仍在"
    "『正在规划中』）。",
    "真实停牌标志：只能用「当日无成交」做代理，与交易所口径不同。",
    "真实跌停 / 炸板的**历史**：跌停池与炸板池实测只支持近期（2025-06-03 即报 1002），"
    "覆盖率随时间变化，**不得**用于历史横截面评价——覆盖率漂移比单纯缺失更坏。",
    "涨停真实口径的**历史覆盖**：只覆盖已同步的交易日（见 `limit_pools` 台账）；"
    "未同步的日子回落到价量代理，输出里 `universe.limit_up.basis` 写明当天用的是哪一种。",
    "新股上市不足 60 日的特殊处理：本规则只按 K 线根数过滤，不区分上市时间。",
]


def _proxy_limit_up(row: dict) -> bool:
    """价量代理：涨幅 ≥ 9.8% 且收盘等于最高价。**不是**交易所口径。"""
    close, prev = row.get("close"), row.get("prev_close")
    if not close or not prev or prev <= 0:
        return False
    high = row.get("high")
    return (close / prev - 1) * 100 >= LIMIT_UP_PROXY_PCT and high is not None \
        and abs(close - high) < 1e-9


def limit_up_evidence(date_ms: int, *, codes, rules: UniverseRules,
                      sync_status: str | None = None, reason: str = "") -> LimitUpEvidence:
    """把"本地池子有没有这一天"的事实，折算成口径 + 理由。**只做判定，不查库。**"""
    if rules.limit_up_source == "proxy_only":
        return LimitUpEvidence(BASIS_LIMIT_UP_PROXY, date_ms, None, sync_status=sync_status,
                               reason="rules.limit_up_source=proxy_only：本次是对照口径"
                                      "（度量规则变更的影响），刻意不使用真实涨停池。")
    if not codes:
        return LimitUpEvidence(BASIS_LIMIT_UP_PROXY, date_ms, None, sync_status=sync_status,
                               reason=reason or "本地没有该评估日的涨停池数据。")
    return LimitUpEvidence(BASIS_REAL_LIMIT_UP_POOL, date_ms, frozenset(codes),
                           sync_status=sync_status, pool_rows=len(codes),
                           reason=reason)


def apply_rules(rows: list[dict], rules: UniverseRules, *,
                limit_up: LimitUpEvidence | None = None,
                diagnostics: dict | None = None) -> tuple[list[dict], dict]:
    """返回 `(保留的行, 排除计数)`。顺序固定，保证可复现。

    `limit_up` 为**当日涨停剔除依据**（见 `LimitUpEvidence`）。不传就是不传——此时按代理
    口径处理，`diagnostics` 里会如实写明 `basis=price_volume_proxy` 并给出理由，
    **不会**假装用了真实池子。

    `diagnostics`（可选，调用方传入一个 dict 接收）回填本日的口径与**两种口径的分歧计数**：
    `proxy_only`（代理说涨停、池子说不是 → 用真实池时**不再剔除**）与
    `pool_only`（池子里有、代理漏掉 → 典型是 ST 的 5% 涨停）。这两个数字是"池子是否
    真的比代理更好"的唯一直接证据，所以必须输出，不能只报一句"已剔除涨停"。

    注意：分歧计数只统计**通过前面几条规则之后**的行（价格异常/历史不足/成交额过低的
    行在到达涨停检查前就被 `continue` 掉了），与 `exclusions` 的口径一致。
    """
    kept: list[dict] = []
    exclusions = {key: 0 for key in RULE_NOTES}
    use_pool = bool(limit_up is not None and limit_up.codes)
    stats = {
        "basis": BASIS_REAL_LIMIT_UP_POOL if use_pool else BASIS_LIMIT_UP_PROXY,
        "pool_size": len(limit_up.codes) if use_pool else 0,
        "sync_status": limit_up.sync_status if limit_up else None,
        "reason": limit_up.reason if limit_up else "调用方未提供涨停池证据（按代理口径）。",
        "checked_rows": 0, "proxy_hits": 0, "proxy_only": 0, "pool_only": 0,
        "excluded_limit_up": 0,
    }
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
            stats["checked_rows"] += 1
            proxy_hit = _proxy_limit_up(row)
            in_pool = use_pool and row.get("thscode") in limit_up.codes
            if proxy_hit:
                stats["proxy_hits"] += 1
            if in_pool:
                if not proxy_hit:
                    stats["pool_only"] += 1
                exclusions["confirmed_limit_up"] += 1
                stats["excluded_limit_up"] += 1
                continue
            if proxy_hit:
                if use_pool:
                    # 真实池是权威口径：代理说涨停但池子里没有 → **保留**，并记下分歧。
                    stats["proxy_only"] += 1
                else:
                    exclusions["suspected_limit_up"] += 1
                    stats["excluded_limit_up"] += 1
                    continue
        kept.append(row)
    if diagnostics is not None:
        diagnostics.update(stats)
    return kept, exclusions
