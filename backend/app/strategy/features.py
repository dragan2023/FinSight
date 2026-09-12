from __future__ import annotations
import math
from .models import PriceBar


def _std_annualized(values: list[float]) -> float:
    mean = sum(values) / len(values)
    return math.sqrt(252) * math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def feature_rows(bars: list[PriceBar], *, vol_window: int = 20, vol_history: int = 252,
                 vol_quantile: float = 0.8) -> list[dict]:
    """只用当日及以前数据的特征；滚动集合避免 3000 根 K 的二次重算。

    波动率闸门参数来自 volatility_gate_v1 注册表（单一来源），默认值仅作兜底。
    """
    closes = [bar.close for bar in bars]
    prefix = [0.0]
    for close in closes:
        prefix.append(prefix[-1] + close)
    daily = [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes))]
    rows: list[dict] = []
    prior_vols: list[float] = []
    prior_returns: list[float] = []
    for index, close in enumerate(closes):
        ma20 = (prefix[index + 1] - prefix[index - 19]) / 20 if index >= 19 else None
        ma60 = (prefix[index + 1] - prefix[index - 59]) / 60 if index >= 59 else None
        ret5 = close / closes[index - 5] - 1 if index >= 5 else None
        returns_to_today = daily[max(0, index - vol_window):index]
        vol = _std_annualized(returns_to_today) if len(returns_to_today) == vol_window else None
        gate = (sorted(prior_vols[-vol_history:])[int(vol_quantile * (min(vol_history, len(prior_vols)) - 1))]
                if len(prior_vols) >= vol_history else None)
        q = (sorted(prior_returns[-120:])[int(.1 * (min(120, len(prior_returns)) - 1))]
             if len(prior_returns) >= 120 else None)
        rows.append({"ma20": ma20, "ma60": ma60, "ret5": ret5, "ret5_q10": q,
                     "vol": vol, "vol_gate": gate})
        if vol is not None:
            prior_vols.append(vol)
        if ret5 is not None:
            prior_returns.append(ret5)
    return rows
