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


def pool_counts(*, limit_up: list[dict], limit_down: list[dict], limit_break: list[dict], totals: dict[str, int | None]) -> dict:
    """Each pool has its own supplier denominator; none is inferred from another."""
    def count(rows: list[dict], key: str) -> dict:
        unique = {str(row.get("thscode") or row.get("code") or index) for index, row in enumerate(rows)}
        return {"unique_records": len(unique), "supplier_total": totals.get(key), "coverage": len(unique) if totals.get(key) is None else f"{len(unique)} / {totals[key]}"}
    return {"limit_up": count(limit_up, "limit_up"), "limit_down": count(limit_down, "limit_down"), "limit_break": count(limit_break, "limit_break")}


def ladder_coverage(rows: list[dict], cap: int = 4) -> dict:
    samples = limit_ladder(rows, cap=cap)
    returned = {level: sum(1 for row in rows if row.get("streak") == level) for level in samples}
    return {"samples": samples, "returned_per_level": returned, "limitation": "每个梯队仅展示供应商返回样本，不代表全市场数量。"}


def reason_distribution(rows: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get("limit_up_reason") or row.get("reason")
        if value is not None and str(value).strip(): counts[str(value).strip()] = counts.get(str(value).strip(), 0) + 1
    return {"counts": counts, "limitation": "这是供应商原因字段，不等同于行业分类。"}


def concept_split(rows: list[dict]) -> dict:
    """Optional equal split; report conservation instead of implying fund flow."""
    allocated: dict[str, float] = {}; source = 0.0
    for row in rows:
        amount = float(row.get("amount") or 0); concepts = [str(item) for item in row.get("concepts") or [] if str(item)]
        if not concepts: continue
        source += amount
        for concept in concepts: allocated[concept] = allocated.get(concept, 0.0) + amount / len(concepts)
    return {"allocated": allocated, "source_total": source, "allocated_total": sum(allocated.values()), "conserved": abs(sum(allocated.values()) - source) < 1e-9, "limitation": "一股多概念按概念数等分，仅为展示分摊，不代表资金流。"}
