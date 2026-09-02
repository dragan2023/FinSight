"""Market-review aggregation with day/window keys kept separate."""
from __future__ import annotations

from ..market_data.market_events import dragon_tiger_key, event_date_supported

def dedupe_dragon_tiger(rows:list[dict])->list[dict]:
    seen=set(); output=[]
    for row in rows:
        key=dragon_tiger_key(row)
        if key not in seen: seen.add(key); output.append(row)
    return output
def limit_ladder(rows:list[dict], cap:int=4)->dict:
    groups={}
    for row in rows:
        level=row.get("streak")
        if isinstance(level,int) and level>0: groups.setdefault(level,[]).append(row)
    return {level:items[:cap] for level,items in groups.items()}
def historical_anomaly_context(*, requested_date:str, supported_date:str|None, explanation:str|None)->dict:
    if not event_date_supported(requested_date=requested_date,returned_date=supported_date):return {"available":False,"explanation":None,"limitation":"该异动解释不支持历史回查，未将当日解释移植到过去。"}
    return {"available":bool(explanation),"explanation":explanation,"limitation":None}
