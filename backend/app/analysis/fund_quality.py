"""Net-asset-value diagnostics with explicit sample and basis guards."""
from __future__ import annotations
from decimal import Decimal
from math import sqrt

def nav_quality(points:list[tuple[str,str|None]], *, adjusted:bool, days_per_year:int=252)->dict:
    valid=[(date,Decimal(value)) for date,value in points if value is not None and Decimal(value)>0]
    if len(valid)<2:return {"return":None,"max_drawdown":None,"distance_to_high":None,"recovery_status":"insufficient_sample","annualized_return":None,"volatility":None,"limitation":"可用净值点不足两个。"}
    values=[value for _,value in valid]; total=values[-1]/values[0]-1; peak=values[0]; drawdown=Decimal(0)
    for value in values: peak=max(peak,value); drawdown=min(drawdown,value/peak-1)
    annual=None
    if len(valid)>=days_per_year: annual=format((float(values[-1]/values[0])**(days_per_year/(len(valid)-1))-1),".12f")
    returns=[float(values[i]/values[i-1]-1) for i in range(1,len(values))]
    volatility=None
    if len(returns)>=60:
        mean=sum(returns)/len(returns); variance=sum((value-mean)**2 for value in returns)/(len(returns)-1); volatility=format(sqrt(variance)*sqrt(days_per_year),".12f")
    distance = values[-1] / peak - 1
    recovery = "at_high" if distance == 0 else "recovering" if values[-1] > values[0] else "in_drawdown"
    return {"return":format(total,"f"),"max_drawdown":format(drawdown,"f"),"distance_to_high":format(distance,"f"),"recovery_status":recovery,"annualized_return":annual,"volatility":volatility,"limitation":None if adjusted else "使用单位净值；除息造成的变化不能直接称投资损失。"}
