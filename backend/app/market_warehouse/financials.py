"""财务数据的 **point-in-time 口径**：法定披露截止日、PIT 闸门、本地可算比率。

本文档解决一个实测出来的硬问题（`22` 号文档 §1）：**同花顺三表接口返回的
`report_date_ms` 不可信**。对 5 只标的 / 390 条 (报表, 报告期) 记录实测：

| 报表 × 季度 | 滞后天数中位数（披露日 − 报告期末） |
|---|---|
| 资产负债表 Q1 / Q2 / Q3 | **25 / 47 / 25 天（正常）** |
| 资产负债表 Q4 | 445 天 |
| 利润表 & 现金流量表 全部季度 | **386–445 天（≈ 整整晚一年）** |

三表对同一报告期的披露日**有 90/130 组不一致**（69%）。所以它既不能直接当 PIT 闸门
（会让数据白等一年），也不能当"已披露"的依据。**但披露时间本身是有法定上限的**，
于是有了下面这条规则。

## 闸门规则（决策 D59）

《上海证券交易所股票上市规则》5.2.2：年度报告在会计年度结束后 **4 个月**内、
半年度报告在上半年结束后 **2 个月**内、季度报告在前 3 个月/前 9 个月结束后 **1 个月**内披露。
（[证监会江苏局](https://www.csrc.gov.cn/jiangsu/c105409/c5787152/content.shtml)、
[上交所规则 5.2.2 引述](https://stock.stockstar.com/RB2022042200009907.shtml)）

因此每个报告期都有一个**法定披露截止日**，披露必定不晚于它。闸门取：

    available_from = min(接口给的披露日, 法定披露截止日)

- 接口日期**早于**截止日 → 说明它是可信的（例如资产负债表 Q1 的 +25 天），采信它，数据更新鲜；
- 接口日期**晚于**截止日 → 说明它被"漂移"了（利润表那批），回落到法定截止日，
  既不白等一年，也**不会比法定截止日更早**（披露不可能晚于截止日）；
- 两者都落库（`disclosed_at_ms` 与 `available_from_ms`）并在 `available_basis` 里写明采信了哪一个，
  读者可以自己复核——**口径必须自证**（与 D12 同一条纪律）。

## 保守化的两个细节

1. **披露日当天不视为已可得**：`available_from` 一律取「该日期的**次日** 00:00（东八区）」。
   披露可能发生在盘中或盘后，我们无法从日粒度数据判断，所以取最保守的读法。
2. **合法延迟披露仍会带来乐观**：若某公司违规拖延到截止日之后才披露，按截止日开闸会提前若干天。
   本地没有公告日数据可以校验这一点，**如实登记为限制**（`22` 号文档 §5），不假装没有。

**这个模块不联网、不碰数据库**：纯函数 + 常量表，所以可以逐条单测。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))

#: 数据集版本。改了本模块的**闸门语义或比率公式**就必须升版本——否则旧数据会被当成新口径用
#: （与事件研究面板同一条纪律）。
FINANCIAL_PIT_VERSION = "financial_pit_v1"

#: 法定披露截止日规则：报告期末月 → (年偏移, 截止月, 截止日)。
#: 依据《上海证券交易所股票上市规则》5.2.2（年报 4 个月 / 半年报 2 个月 / 季报 1 个月）。
STATUTORY_DEADLINE_RULES = {
    3: (0, 4, 30),     # 一季报 → 当年 4 月 30 日
    6: (0, 8, 31),     # 半年报 → 当年 8 月 31 日
    9: (0, 10, 31),    # 三季报 → 当年 10 月 31 日
    12: (1, 4, 30),    # 年报   → 次年 4 月 30 日
}

#: 闸门来源标签（如实落库）。
BASIS_API_REPORT_DATE = "api_report_date"
BASIS_STATUTORY_DEADLINE = "statutory_deadline"


def _ms(stamp: date) -> int:
    """东八区某日 00:00 的毫秒时间戳（**不** UTC 午夜：报告期与披露日都是中国日历日）。"""
    return int(datetime(stamp.year, stamp.month, stamp.day, tzinfo=CST).timestamp() * 1000)


def period_end_date(period_end_ms: int) -> date:
    return datetime.fromtimestamp(period_end_ms / 1000, CST).date()


def day_text_ms(text: str) -> int:
    """`YYYY-MM-DD` → 东八区当日 00:00 的毫秒（`period_end` 这种日期字符串用）。"""
    return _ms(date.fromisoformat(text[:10]))


def iso_text_ms(text: str | None) -> int | None:
    """接口给的 ISO 披露时刻（带 +08:00）→ 毫秒。空值原样返回 None。"""
    return None if not text else int(datetime.fromisoformat(text).timestamp() * 1000)


def statutory_deadline_date(period_end_ms: int) -> date:
    """报告期的法定披露截止日。报告期末月不在规则表里（非标准财年）时抛错，不猜。"""
    end = period_end_date(period_end_ms)
    rule = STATUTORY_DEADLINE_RULES.get(end.month)
    if rule is None:
        raise ValueError(
            f"报告期末月 {end.month} 不在法定披露规则表里（只支持 3/6/9/12 月末）：{end}。"
            "非标准财年需要单独确认口径，不得臆造截止日。")
    year_offset, month, day = rule
    return date(end.year + year_offset, month, day)


def available_from_ms(*, period_end_ms: int, api_disclosed_ms: int | None) -> tuple[int, str]:
    """PIT 闸门：返回 `(齐备可得的时刻, 采信来源)`。

    `min(api, 法定截止日)`，**且整体推迟到次日 00:00**——披露日当天不视为已可得。
    """
    deadline = statutory_deadline_date(period_end_ms)
    if api_disclosed_ms is None:
        gate, basis = deadline, BASIS_STATUTORY_DEADLINE
    else:
        api_date = datetime.fromtimestamp(api_disclosed_ms / 1000, CST).date()
        if api_date <= deadline:
            gate, basis = api_date, BASIS_API_REPORT_DATE
        else:
            gate, basis = deadline, BASIS_STATUTORY_DEADLINE
    return _ms(gate + timedelta(days=1)), basis


# --- 本地可算的财务比率 -------------------------------------------------------
#: 只列**本模块自己算**的比率，并写明公式与所需原始字段。
#: 为什么要自己算：`indicators` 端点返回的指标**不带披露日**（`normalize_indicators`
#: 里 `disclosed_at=None`），因此无法用于历史 point-in-time；而它的输入本身就在三表里。
#: 这也让"口径自证"成立——报表数据来自哪里、公式是什么，都在这里写死。
FINANCIAL_RATIOS = {
    "roa": {
        "label": "总资产收益率（本地口径 = 净利润 ÷ 资产总计）",
        "inputs": ("net_profit", "assets_total"),
        "formula": "net_profit / assets_total",
    },
    "assets_debt_ratio": {
        "label": "资产负债率（本地口径 = 负债合计 ÷ 资产总计）",
        "inputs": ("total_debt", "assets_total"),
        "formula": "total_debt / assets_total",
    },
    "sale_gross_margin": {
        "label": "销售毛利率（本地口径 = (营业收入 − 营业成本) ÷ 营业收入）",
        "inputs": ("operating_income", "operating_costs"),
        "formula": "(operating_income - operating_costs) / operating_income",
    },
    "total_assets_turnover_ratio": {
        "label": "总资产周转率（本地口径 = 营业收入 ÷ 资产总计）",
        "inputs": ("operating_income", "assets_total"),
        "formula": "operating_income / assets_total",
    },
}

#: **本地口径算不出来**的 F-Score 输入，以及原因。不能算就如实说，不填 0 也不拿近似值顶替。
FSCORE_UNAVAILABLE_LOCALLY = {
    "current_ratio": "需要「流动负债合计」，而三表接口的资产负债表只返回 15 个字段"
                     "（实测字段全集见 `22` §1），其中没有流动负债；"
                     "`indicators` 端点有该指标但**不带披露日**，无法用于历史 PIT。",
    "shares_outstanding": "需要股本变动（增发）信息，三表接口不提供；"
                          "F-Score 第 7 项因此恒为 N/A（既有实现即如此，未改动）。",
}


def _number(facts: dict[str, float], key: str) -> float | None:
    value = facts.get(key)
    return None if value is None else float(value)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    """分母缺失或为 0 时返回 None——**不填 0**（填 0 会把"算不出来"做成"值为 0"）。"""
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def compute_ratios(facts: dict[str, float]) -> dict[str, float | None]:
    """从原始字段算出本地比率。缺输入 → 对应比率为 `None`（不是 0，也不是近似值）。"""
    income = _number(facts, "operating_income")
    costs = _number(facts, "operating_costs")
    return {
        "roa": _ratio(_number(facts, "net_profit"), _number(facts, "assets_total")),
        "assets_debt_ratio": _ratio(_number(facts, "total_debt"), _number(facts, "assets_total")),
        "sale_gross_margin": (_ratio(income - costs, income)
                              if income is not None and costs is not None else None),
        "total_assets_turnover_ratio": _ratio(income, _number(facts, "assets_total")),
    }


#: 一个报告期的全部 F-Score 输入（原始字段 + 本地比率 + 缺口）。
def f_score_inputs(facts: dict[str, float]) -> dict[str, float | None]:
    merged: dict[str, float | None] = dict(facts)
    merged.update(compute_ratios(facts))
    return merged


def group_periods(rows: list[dict]) -> dict[str, dict[int, dict]]:
    """把 `financial_facts` 行按「报表 → 报告期末 → {闸门, 字段}」分组（纯函数，可单测）。"""
    grouped: dict[str, dict[int, dict]] = {}
    for row in rows:
        slot = grouped.setdefault(row["statement"], {}).setdefault(
            row["period_end_ms"],
            {"available_from_ms": row["available_from_ms"],
             "available_basis": row.get("available_basis"),
             "period_key": row.get("period_key"), "facts": {}})
        if row.get("value") is not None:
            slot["facts"][row["field"]] = row["value"]
    return grouped


def select_common_period(grouped: dict[str, dict[int, dict]], as_of_ms: int
                         ) -> tuple[int | None, dict[str, float], dict[str, dict]]:
    """选「**各张报表都已披露**的最近报告期」，并返回该期的合并字段。

    为什么必须"共同期"而不是"各表各自最新"：三表的披露节奏不同（资产负债表 Q4 的接口日期
    实测比利润表晚 4 个月）。若按"全体最大期"取，会造出 **利润表 Q2 + 资产负债表 Q1** 这样的
    组合，那么 `净利润 ÷ 资产总计` 的分子分母就**跨期**了——数字看着正常，却是错的。
    这种错误在汇总层面完全看不出来，所以这里用"共同期"把它从结构上排除。

    返回 `(period_end_ms, facts, per_statement)`；没有任何共同期时返回 `(None, {}, {})`。
    """
    eligible = {statement: {period: item for period, item in periods.items()
                            if item["available_from_ms"] <= as_of_ms}
                for statement, periods in grouped.items()}
    eligible = {statement: periods for statement, periods in eligible.items() if periods}
    if not eligible:
        return None, {}, {}
    common = set.intersection(*[set(periods) for periods in eligible.values()])
    if not common:
        return None, {}, {}
    period = max(common)
    facts: dict[str, float] = {}
    per_statement: dict[str, dict] = {}
    for statement, periods in eligible.items():
        item = periods[period]
        facts.update(item["facts"])
        per_statement[statement] = item
    return period, facts, per_statement


def prior_year_key(period_key: str) -> str | None:
    """`2026-Q2` → `2025-Q2`（Δ 类信号用同季同比：季报累计口径下才是同一口径的比较）。"""
    year, separator, quarter = str(period_key).partition("-")
    if not separator or not year.isdigit():
        return None
    return f"{int(year) - 1}-{quarter}"


def select_prior_year_facts(grouped: dict[str, dict[int, dict]], current_key: str,
                            as_of_ms: int, *, thscode: str = "") -> dict | None:
    """取**上年同季**那一期的字段（同样受闸门约束）；取不到返回 `None`。

    与 `select_common_period` 同一套口径：跨表取"该期各表都有"的字段，缺的就不给。
    """
    prior = prior_year_key(current_key)
    if not prior:
        return None
    period: int | None = None
    facts: dict[str, float] = {}
    info: dict[str, dict] = {}
    for statement, periods in grouped.items():
        for end_ms, item in periods.items():
            if item.get("period_key") != prior or item["available_from_ms"] > as_of_ms:
                continue
            period = end_ms if period is None else max(period, end_ms)
            facts.update(item["facts"])
            info[statement] = item
    if period is None or not facts:
        return None
    return {"thscode": thscode, "period_key": prior, "period_end_ms": period,
            "available_from_ms": max(item["available_from_ms"] for item in info.values()),
            "available_basis": next(iter(info.values()))["available_basis"], "facts": facts}


def f_score_local_coverage() -> dict:
    """如实说明：本地方案能覆盖 9 项里的哪几项、哪些恒为 N/A。"""
    total = 9
    unavailable = len(FSCORE_UNAVAILABLE_LOCALLY)
    return {
        "total_items": total,
        "locally_computable": total - unavailable,
        "unavailable": sorted(FSCORE_UNAVAILABLE_LOCALLY),
        "reasons": dict(FSCORE_UNAVAILABLE_LOCALLY),
        "note": "F-Score 的 7 项可由三表本地算出；第 7 项（未增发）与流动比率改善项无法本地计算，"
                "在报告里恒为 N/A 而**不计分**（分母随之变小，逐项通过率仍可读）。"
                "当前时点评估可另外用 `indicators` 端点补 current_ratio（它不带披露日，"
                "只对『现在』有效，不能用于历史 PIT）。",
    }
