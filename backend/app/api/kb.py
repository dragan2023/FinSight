"""V2-03 私有知识库端点：上传、列表、删除、重建与检索调试。

- 上传/重建/检索都是用户主动操作；嵌入可选（qwen_embedding 凭据），
  未配置时纯本地 FTS5 关键词检索，文档内容零外发。
- 上传沿用附件研究的 base64 JSON 形态，不引入 multipart 依赖。
"""
from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..errors import AppError
from ..kb.models import KbSearchInput

router = APIRouter(tags=["knowledge"])


class KbUploadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=200)
    data_base64: str = Field(min_length=4, max_length=40_000_000)

class KbMetadataInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    knowledge_role: str = Field(pattern=r"^(source|strategy|hypothesis)$")
    tags: list[str] = Field(default_factory=list, max_length=20)
    review_state: str = Field(pattern=r"^(unreviewed|approved|retired)$")
    effective_at: str | None = Field(default=None, max_length=32)


def _service(request: Request):
    service = getattr(request.app.state, "kb", None)
    if service is None:
        raise AppError("not_configured", "知识库服务不可用。", 409)
    return service


@router.get("/api/kb/documents")
def list_documents(request: Request):
    return {"documents": _service(request).list_documents()}


@router.post("/api/kb/documents", status_code=201)
async def upload_document(data: KbUploadInput, request: Request):
    try:
        raw = base64.b64decode(data.data_base64, validate=True)
    except (binascii.Error, ValueError):
        raise AppError("validation", "文件内容不是有效的 base64 数据。", 422) from None
    result = await _service(request).ingest(data.filename, raw)
    return {"duplicate": result["duplicate"], "document": result["document"]}


@router.delete("/api/kb/documents/{document_id}")
def delete_document(document_id: str, request: Request):
    return {"deleted": _service(request).delete_document(document_id)}


@router.get("/api/kb/documents/{document_id}/chunks")
def list_document_chunks(document_id: str, request: Request,
                         page: int | None = Query(default=None, ge=1)):
    """本机分块原文（按 position 排序），供证据回溯定位。"""
    return _service(request).list_chunks(document_id, page=page)


@router.post("/api/kb/documents/{document_id}/rebuild")
async def rebuild_document(document_id: str, request: Request):
    return {"document": await _service(request).rebuild(document_id)}

@router.patch("/api/kb/documents/{document_id}")
def update_metadata(document_id: str, data: KbMetadataInput, request: Request):
    return {"document": _service(request).update_metadata(document_id, **data.model_dump())}


@router.post("/api/kb/search")
async def search(data: KbSearchInput, request: Request):
    result = await _service(request).search(data.query, **data.model_dump(exclude={"query"}))
    return result.model_dump(mode="json")
