from __future__ import annotations
from typing import Annotated,Literal
from fastapi import APIRouter,Depends,Request
from fastapi.responses import FileResponse
from pydantic import BaseModel,ConfigDict
from ..dependencies import research_repository
from ..errors import AppError
from ..research.exporting import ExportService
router=APIRouter(prefix="/api/exports",tags=["exports"]);Repo=Annotated[object,Depends(research_repository)]
class ExportInput(BaseModel):model_config=ConfigDict(extra="forbid");report_id:str;format:Literal["markdown","html","json","csv","source_markdown"]
def _service(request:Request,repo):return ExportService(repo,request.app.state.settings.exports_path)
@router.post("")
def create(data:ExportInput,request:Request,repo:Repo):
    try:return {"export":_service(request,repo).create(data.report_id,data.format)}
    except KeyError:raise AppError("not_found","报告不存在。",404)
    except ValueError as error:raise AppError("validation",str(error),422)
@router.get("/{export_id}/download")
def download(export_id:str,request:Request,repo:Repo):
    path=_service(request,repo).path(export_id)
    if not path:raise AppError("not_found","导出文件不存在或已清理。",404)
    return FileResponse(path,filename=path.name,media_type="application/octet-stream")
