"""Supplement upload and management endpoints (plan 04 sections 2.5 / 2.6).

上传只在本机解析：不访问网络、不调用模型、绝不自动触发再分析。上传按计划
§2.5 执行可见限额：白名单 MIME + 扩展名双检、单文件 25 MiB / 一次 5 个 /
合计 100 MiB，会话保护与受限 multipart 由 LocalSecurityMiddleware 统一处理，
请求体按实际流字节计数。失败文件返回结构化错误与 DataGap 候选，不影响其他
文件；保存资料不等于同意外发。

批次5（§2.6）：GET 预览解析结果（元数据 + 摘录，绝不提供原始文件）、
PUT mapping 确认候选字段（只保存，不触发计算）、DELETE 删除未引用资料
（已引用按保留规则拒绝）、报告清单只读查询。真正再分析沿用 03 的
/reports/{id}/reanalyze，需用户显式发起。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import attachments_store
from ..errors import AppError
from ..research.attachments import (
    AttachmentEntry,
    AttachmentStore,
    is_attachment_id,
)
from ..security import UPLOAD_REPLAY_CHUNK_BYTES

router = APIRouter(prefix="/api/supplements", tags=["research-supplements"])
Store = Annotated[AttachmentStore, Depends(attachments_store)]


class MappingInput(BaseModel):
    """候选字段映射确认；全部可选但至少一项，Pydantic extra=forbid。"""

    model_config = ConfigDict(extra="forbid")

    security_id: str | None = Field(default=None, pattern=r"^[0-9]{6}\.(SH|SZ|BJ)$")
    period: str | None = Field(default=None, min_length=1, max_length=32)
    unit: str | None = Field(default=None, min_length=1, max_length=16)
    note: str | None = Field(default=None, min_length=1, max_length=200)


async def _read_entry(upload: UploadFile) -> AttachmentEntry:
    """按块读取 spool 中的上传文件：计的是实际流过的字节，不信任声明长度。"""
    chunks: list[bytes] = []
    while True:
        chunk = await upload.read(UPLOAD_REPLAY_CHUNK_BYTES)
        if not chunk:
            break
        chunks.append(chunk)
    await upload.close()
    return AttachmentEntry(display_name=upload.filename or "",
                           mime_type=upload.content_type or "",
                           data=b"".join(chunks))


@router.post("/files")
async def upload_files(request: Request, store: Store,
                       files: list[UploadFile] = File(...),
                       report_id: str = Form(...),
                       retention: str = Form("reference_only")):
    if not is_attachment_id(report_id.strip()):
        raise AppError("validation", "报告标识格式不正确。", 422)
    entries = []
    for upload in files:
        entries.append(await _read_entry(upload))
    outcome = store.save_upload(report_id=report_id.strip(), retention=retention,
                                entries=entries)
    return outcome.model_dump(mode="json")


@router.get("/attachments")
def list_report_attachments(store: Store, report_id: str = Query(...)):
    """报告附件与补充清单（只读）；供报告页在刷新后恢复资料面板状态。"""
    view = store.list_for_report(report_id.strip())
    return view.model_dump(mode="json")


@router.get("/{attachment_id}")
def preview_attachment(attachment_id: str, store: Store):
    preview = store.preview(attachment_id.strip())
    return preview.model_dump(mode="json")


@router.put("/{attachment_id}/mapping")
def confirm_attachment_mapping(attachment_id: str, data: MappingInput, store: Store):
    provided = data.model_dump(exclude_none=True)
    if not provided:
        raise AppError("validation", "请至少确认一项映射（标的/期间/单位/备注）。", 422)
    record = store.confirm_mapping(attachment_id.strip(), provided)
    return {"attachment": record.model_dump(mode="json")}


@router.delete("/{attachment_id}")
def delete_attachment(attachment_id: str, store: Store) -> dict:
    deletion = store.delete(attachment_id.strip())
    return deletion.model_dump(mode="json")
