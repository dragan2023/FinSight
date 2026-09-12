"""顾问到知识库的可审计查询构造器，不把证券代码当作唯一检索词。"""
from __future__ import annotations

import re


def build_strategy_query(*, question: str, thscode: str, horizon: str | None,
                         strategy_ids: list[str], market_regime: str | None = None) -> dict:
    """从用户原话和程序上下文生成检索文本与固定元数据过滤条件。"""
    family = next(("trend" for item in strategy_ids if item.startswith("trend_")), None)
    if family is None:
        family = next(("reversal" for item in strategy_ids if item.startswith("reversal_")), None)
    clean_question = re.sub(r"\s+", " ", question).strip()[:160]
    terms = [thscode, clean_question, "策略适用条件", "风险失效条件"]
    if horizon:
        terms.append(horizon)
    if family:
        terms.append(family)
    if market_regime:
        terms.append(market_regime)
    return {"query": " ".join(item for item in terms if item),
            "horizon": horizon, "strategy_family": family, "market_regime": market_regime}


def infer_horizon(question: str) -> str | None:
    """从用户原话确定性地推断期限标签；只认固定词，不做模型猜测。

    词表与 KbSearchInput/标签词表一致：days_5 / days_20 / quarter / year。
    """
    text = (question or "").lower()
    if any(word in text for word in ("季度", "一季", "三个月", "90 天", "90天")):
        return "quarter"
    if any(word in text for word in ("一年", "年度", "长期", "365 天", "365天")):
        return "year"
    if "月" in text:
        return "days_20"
    if "周" in text:
        return "days_5"
    if any(word in text for word in ("短线", "日内", "次日", "几天")):
        return "days_5"
    return None
