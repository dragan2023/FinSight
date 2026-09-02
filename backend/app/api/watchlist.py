"""Local watchlist and user-confirmed thesis endpoints; no external calls."""
from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from ..dependencies import research_repository
from ..errors import AppError
from ..research.theses import ObservationOutcome, ThesisStore
from ..research.watchlist import WatchlistStore

router=APIRouter(tags=["company-research"])
Repo=Annotated[object,Depends(research_repository)]
class WatchlistInput(BaseModel):
    model_config=ConfigDict(extra="forbid"); security_id:str; thscode:str=Field(pattern=r"^[0-9]{6}\.(SH|SZ|BJ)$"); name:str=Field(min_length=1,max_length=100); tags:list[str]=Field(default_factory=list,max_length=12)
class TagsInput(BaseModel): model_config=ConfigDict(extra="forbid"); tags:list[str]=Field(max_length=12)
class ThesisInput(BaseModel):
    model_config=ConfigDict(extra="forbid"); security_id:str; content:str=Field(min_length=1,max_length=2000); confirmed_by_user:bool; required_evidence:list[str]=Field(default_factory=list,max_length=20); counter_evidence:list[str]=Field(default_factory=list,max_length=20); observation_metrics:list[str]=Field(default_factory=list,max_length=20); source_report_id:str|None=None
class ThesisUpdateInput(BaseModel):
    model_config=ConfigDict(extra="forbid"); content:str=Field(min_length=1,max_length=2000); revoked:bool=False
class ObservationInput(BaseModel):
    model_config=ConfigDict(extra="forbid"); report_id:str; evidence_refs:list[str]=Field(default_factory=list); metric_refs:list[str]=Field(default_factory=list); outcome:ObservationOutcome; note:str=Field(default="",max_length=2000)
@router.get("/api/watchlist")
def list_watchlist(repo:Repo): return {"items":[x.model_dump(mode="json") for x in WatchlistStore(repo).list()]}
@router.post("/api/watchlist",status_code=201)
def add_watchlist(data:WatchlistInput,repo:Repo): return {"item":WatchlistStore(repo).add(**data.model_dump()).model_dump(mode="json")}
@router.put("/api/watchlist/{item_id}")
def update_watchlist(item_id:str,data:TagsInput,repo:Repo):
    item=WatchlistStore(repo).update(item_id,data.tags)
    if not item: raise AppError("not_found","关注标的不存在。",404)
    return {"item":item.model_dump(mode="json")}
@router.delete("/api/watchlist/{item_id}")
def delete_watchlist(item_id:str,repo:Repo):
    if not WatchlistStore(repo).remove(item_id): raise AppError("not_found","关注标的不存在。",404)
    return {"deleted":True}
@router.get("/api/theses")
def list_theses(repo:Repo,security_id:str|None=None): return {"items":[x.model_dump(mode="json") for x in ThesisStore(repo).list(security_id)]}
@router.post("/api/theses",status_code=201)
def create_thesis(data:ThesisInput,repo:Repo): return {"item":ThesisStore(repo).create(**data.model_dump()).model_dump(mode="json")}
@router.put("/api/theses/{thesis_id}")
def update_thesis(thesis_id:str,data:ThesisUpdateInput,repo:Repo):
    item=ThesisStore(repo).update(thesis_id,content=data.content,revoked=data.revoked)
    if not item: raise AppError("not_found","理由不存在。",404)
    return {"item":item.model_dump(mode="json")}
@router.post("/api/theses/{thesis_id}/observations",status_code=201)
def observe_thesis(thesis_id:str,data:ObservationInput,repo:Repo): return {"item":ThesisStore(repo).observe(thesis_id=thesis_id,**data.model_dump()).model_dump(mode="json")}
