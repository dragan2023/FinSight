"""Comparable-window relative performance; never calls the return difference alpha."""
from __future__ import annotations
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field

class RelativeSeries(BaseModel):
    model_config=ConfigDict(extra="forbid")
    id:str; label:str; prices:dict[str,str|None]=Field(default_factory=dict); price_basis:str
class RelativePerformance(BaseModel):
    model_config=ConfigDict(extra="forbid")
    common_dates:list[str]; normalized:dict[str,dict[str,str]]; returns:dict[str,str|None]; max_drawdowns:dict[str,str|None]; return_differences:dict[str,str|None]; limitation:str|None=None

def relative_performance(series:list[RelativeSeries], reference_id:str)->RelativePerformance:
    if len(series)<2: raise ValueError("至少选择两个标的或指数。")
    ids={x.id for x in series}
    if reference_id not in ids: raise ValueError("参考对象不在所选比较组中。")
    common=set.intersection(*[{date for date,value in item.prices.items() if value is not None} for item in series])
    dates=sorted(common)
    if len(dates)<2: return RelativePerformance(common_dates=dates,normalized={},returns={x.id:None for x in series},max_drawdowns={x.id:None for x in series},return_differences={},limitation="没有至少两个共同可比交易日；已拒绝生成相对表现。")
    normalized={}; returns={}; drawdowns={}
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
    reference=Decimal(returns[reference_id])
    differences={key:format(Decimal(value)-reference,"f") for key,value in returns.items() if key!=reference_id and value is not None}
    return RelativePerformance(common_dates=dates,normalized=normalized,returns=returns,max_drawdowns=drawdowns,return_differences=differences,limitation="区间收益差不是风险调整后的 alpha；指数价格/全收益口径须在页面并列说明。")

def breadth(rows:list[dict], *, current_constituents_only:bool=True)->dict:
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
    return {**counts,"covered":len(rows)-counts["unknown"],"total":len(rows),"limitation":"当前成分回看，非历史时点成分。" if current_constituents_only else None}
