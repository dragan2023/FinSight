from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter,Depends,Query,Request
from pydantic import BaseModel,ConfigDict,Field
from ..dependencies import research_repository
from ..errors import AppError
from ..research.library import ReportLibrary
from ..research.notes import NoteStore
from ..research.retention import RetentionService
router=APIRouter(prefix="/api/library",tags=["library"]);Repo=Annotated[object,Depends(research_repository)]
class NoteInput(BaseModel): model_config=ConfigDict(extra="forbid");report_id:str=Field(min_length=1,max_length=64);content:str=Field(min_length=1,max_length=5000)
class DeletionInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    report_ids:list[str]=Field(min_length=1,max_length=20)
    confirmed:bool=False
class LabelsInput(BaseModel):
    model_config=ConfigDict(extra="forbid"); labels:list[str]=Field(default_factory=list,max_length=12)
def _retention(request:Request,repo): return RetentionService(repo,request.app.state.settings.project_root)
@router.get("/reports")
def reports(repo:Repo,q:str|None=Query(default=None,max_length=200),limit:int=Query(default=50,ge=1,le=100)):return {"items":[x.model_dump(mode="json") for x in ReportLibrary(repo).search(q,limit)]}
@router.put("/reports/{report_id}/labels")
def set_labels(report_id: str, data: LabelsInput, repo: Repo):
    try:
        labels = repo.set_report_labels(report_id, data.labels)
    except KeyError:
        raise AppError("not_found", "报告不存在。", 404) from None
    except ValueError as error:
        raise AppError("validation", str(error), 422) from None
    return {"labels": labels}
@router.get("/reports/{report_id}/versions")
def versions(report_id:str,repo:Repo):
    items=ReportLibrary(repo).versions(report_id)
    if not items:raise AppError("not_found","报告不存在。",404)
    return {"items":[x.model_dump(mode="json") for x in items]}
@router.get("/reports/{older_id}/versions/{newer_id}/diff")
def version_diff(older_id:str,newer_id:str,repo:Repo):
    try:return ReportLibrary(repo).diff(older_id,newer_id)
    except KeyError:raise AppError("not_found","报告不存在。",404)
@router.post("/notes",status_code=201)
def add_note(data:NoteInput,repo:Repo):
    if not repo.get_report(data.report_id):raise AppError("not_found","报告不存在。",404)
    return {"item":NoteStore(repo).create(data.report_id,data.content).model_dump(mode="json")}
@router.get("/reports/{report_id}/notes")
def notes(report_id: str, repo: Repo):
    if not repo.get_report(report_id): raise AppError("not_found", "报告不存在。", 404)
    return {"items": [item.model_dump(mode="json") for item in repo.list_notes(report_id)]}
@router.put("/notes/{note_id}")
def edit_note(note_id:str,data:NoteInput,repo:Repo):
    item=NoteStore(repo).update(note_id,data.content)
    if not item:raise AppError("not_found","笔记不存在。",404)
    return {"item":item.model_dump(mode="json")}
@router.post("/deletion-preview")
def deletion_preview(data:DeletionInput,request:Request,repo:Repo):
    return {"items":[item.model_dump(mode="json") for item in _retention(request,repo).preview(data.report_ids)]}
@router.post("/delete")
def delete_reports(data:DeletionInput,request:Request,repo:Repo):
    if not data.confirmed: raise AppError("validation","请先确认本次删除影响。",422)
    return {"items":[item.model_dump(mode="json") for item in _retention(request,repo).delete(data.report_ids)]}
