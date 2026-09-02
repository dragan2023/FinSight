"""Disclosure-date bounded fund-holding overlap, never a live-portfolio claim."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from pydantic import BaseModel, ConfigDict, Field

class FundHolding(BaseModel):
    model_config=ConfigDict(extra="forbid")
    security_id:str; market:str; weight:str|None=None
class FundDisclosure(BaseModel):
    model_config=ConfigDict(extra="forbid")
    fund_id:str; report_end:str; disclosed_weight_total:str|None=None; weight_definition_confirmed:bool=False; holdings:list[FundHolding]=Field(default_factory=list)
def visible_overlap(left:FundDisclosure,right:FundDisclosure)->dict:
    if left.report_end!=right.report_end: return {"status":"not_comparable","common":[],"overlap_lower_bound":None,"limitation":"披露期不同，不计算同步重叠率。"}
    a=_merge(left.holdings); b=_merge(right.holdings); common=sorted(set(a)&set(b))
    result={"status":"partial","common":[{"market":m,"security_id":s,"left_weight":a[(m,s)].weight,"right_weight":b[(m,s)].weight} for m,s in common],"overlap_lower_bound":None,"left_disclosed_weight_total":left.disclosed_weight_total,"right_disclosed_weight_total":right.disclosed_weight_total,"limitation":"仅为同披露期、已披露证券的可见重叠下限；未披露部分不补零、不重新归一。"}
    if left.weight_definition_confirmed and right.weight_definition_confirmed and common and all(a[key].weight is not None and b[key].weight is not None for key in common): result["overlap_lower_bound"]=format(sum(min(Decimal(a[key].weight),Decimal(b[key].weight)) for key in common),"f")
    return result

def _merge(holdings:list[FundHolding])->dict[tuple[str,str],FundHolding]:
    """Only merge exact market/security pairs; never collapse cross-market tickers."""
    merged:dict[tuple[str,str],FundHolding]={}
    for holding in holdings:
        key=(holding.market,holding.security_id)
        if key not in merged:
            merged[key]=holding
            continue
        first=merged[key]
        if first.weight is None or holding.weight is None:
            merged[key]=FundHolding(security_id=holding.security_id,market=holding.market,weight=None)
            continue
        try:
            weight=Decimal(first.weight)+Decimal(holding.weight)
        except InvalidOperation as error:
            raise ValueError("披露持仓权重无法解释。") from error
        merged[key]=FundHolding(security_id=holding.security_id,market=holding.market,weight=format(weight,"f"))
    return merged
