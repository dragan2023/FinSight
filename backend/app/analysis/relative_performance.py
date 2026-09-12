"""Comparable-window relative performance; never calls the return difference alpha."""
from __future__ import annotations
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field

class RelativeSeries(BaseModel):
    model_config=ConfigDict(extra="forbid")
    id:str; label:str; prices:dict[str,str|None]=Field(default_factory=dict); turnovers:dict[str,str|None]=Field(default_factory=dict); price_basis:str
class RelativePerformance(BaseModel):
    model_config=ConfigDict(extra="forbid")
    common_dates:list[str]; normalized:dict[str,dict[str,str]]; returns:dict[str,str|None]; max_drawdowns:dict[str,str|None]; return_differences:dict[str,str|None]; period_returns:dict[str,dict[str,str|None]]=Field(default_factory=dict); turnover_pulses:dict[str,str|None]=Field(default_factory=dict); selected_ranks:dict[str,int|None]=Field(default_factory=dict); rank_changes:dict[str,int|None]=Field(default_factory=dict); limitation:str|None=None

def relative_performance(series:list[RelativeSeries], reference_id:str)->RelativePerformance:
    if len(series)<2: raise ValueError("至少选择两个标的或指数。")
    ids={x.id for x in series}
    if reference_id not in ids: raise ValueError("参考对象不在所选比较组中。")
    common=set.intersection(*[{date for date,value in item.prices.items() if value is not None} for item in series])
    dates=sorted(common)
    if len(dates)<2: return RelativePerformance(common_dates=dates,normalized={},returns={x.id:None for x in series},max_drawdowns={x.id:None for x in series},return_differences={},limitation="没有至少两个共同可比交易日；已拒绝生成相对表现。")
    normalized={}; returns={}; drawdowns={}; period_returns={}; turnover_pulses={}
    for item in series:
        values=[Decimal(item.prices[date]) for date in dates]
        if any(value <= 0 for value in values):
            raise ValueError("共同窗口内的价格必须全部为正数。")
        base=values[0]
        if base<=0: raise ValueError("价格基数必须为正数。")
        norm=[value/base*Decimal(100) for value in values]
        normalized[item.id]={date:format(value,"f") for date,value in zip(dates,norm)}
        returns[item.id]=format(values[-1]/base-1,"f")
        peak=norm[0]; worst=Decimal(0)
        for value in norm:
            peak=max(peak,value); worst=min(worst,value/peak-1)
        drawdowns[item.id]=format(worst,"f")
        period_returns[item.id] = {}
        for window in (5, 20, 60):
            period_returns[item.id][f"{window}d"] = (format(values[-1] / values[-window - 1] - 1, "f")
                                                       if len(values) > window else None)
        turnover_values = [Decimal(item.turnovers[date]) for date in dates if item.turnovers.get(date) not in (None, "")]
        turnover_pulses[item.id] = (format(turnover_values[-1] / (sum(turnover_values[-21:-1]) / Decimal(20)) - 1, "f")
                                    if len(turnover_values) >= 21 and sum(turnover_values[-21:-1]) > 0 else None)
    reference=Decimal(returns[reference_id])
    differences={key:format(Decimal(value)-reference,"f") for key,value in returns.items() if key!=reference_id and value is not None}
    ranked = sorted(((Decimal(value), code) for code, value in returns.items() if value is not None), reverse=True)
    ranks = {code: position + 1 for position, (_value, code) in enumerate(ranked)}
    recent = sorted(((Decimal(values["20d"]), code) for code, values in period_returns.items() if values.get("20d") is not None), reverse=True)
    recent_ranks = {code: position + 1 for position, (_value, code) in enumerate(recent)}
    changes = {code: (recent_ranks[code] - rank if code in recent_ranks else None) for code, rank in ranks.items()}
    return RelativePerformance(common_dates=dates,normalized=normalized,returns=returns,max_drawdowns=drawdowns,return_differences=differences,period_returns=period_returns,turnover_pulses=turnover_pulses,selected_ranks=ranks,rank_changes=changes,limitation="区间收益差不是风险调整后的 alpha；排名仅在本次所选指数内；成交额脉冲分母不足时留空；指数价格/全收益口径须在页面并列说明。")

def breadth(rows:list[dict], *, current_constituents_only:bool=True, total_constituents:int|None=None)->dict:
    counts={"up":0,"down":0,"flat":0,"unknown":0}
    for row in rows:
        value=row.get("change")
        if value is None:
            counts["unknown"]+=1
            continue
        try:
            change=Decimal(str(value))
        except Exception:
            counts["unknown"]+=1
            continue
        if change>0: counts["up"]+=1
        elif change<0: counts["down"]+=1
        else: counts["flat"]+=1
    total = total_constituents if total_constituents is not None else len(rows)
    return {**counts,"covered":len(rows)-counts["unknown"],"total":total,"limitation":"当前成分回看，非历史时点成分；仅为等权涨跌代理，不是指数贡献。" if current_constituents_only else None}
