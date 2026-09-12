"""Stage 03 research endpoints (plan 03 section 2.6).

任务启动：保存 client_request_id、请求哈希、范围/授权/预算后立即返回 202，
生成在后台单进程任务集合中进行。同一 client_request_id + 同一请求内容复用
原任务（不二次收费）；同 ID 不同内容返回 409。SSE 只传公开进度与章节预览，
reasoning 只以字符计数出现；浏览器断线后以 after={seq} 续读，不重放生成 POST。
补充资料只保存，不自动外发或再分析；再分析创建子版本任务，旧报告保持可读。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import research_manager
from ..errors import AppError
from ..research.events import (
    SSE_HEARTBEAT_SECONDS,
    SSE_POLL_SECONDS,
    TERMINAL_STATUSES,
    sse_event,
    sse_heartbeat,
    task_snapshot,
)
from ..research.models import SupplementKind, TaskBudget
from ..research.chat import OpenChatService, ReportChatService
from ..research.reports import ReportStore
from ..research.repository import ResearchRepository
from ..research.runner import ResearchTaskManager
from ..research.supplements import SupplementStore
from ..research.scopes import validate_scope

router = APIRouter(prefix="/api/research", tags=["research"])
Manager = Annotated[ResearchTaskManager, Depends(research_manager)]

THSCODE_FIELD = Field(default=None, pattern=r"^[0-9]{6}\.(SH|SZ|BJ)$")
PROFILE_ID_FIELD = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class BudgetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_llm_requests: int | None = Field(default=None, ge=0, le=200)
    max_fetches: int | None = Field(default=None, ge=0, le=200)
    max_tool_rounds: int | None = Field(default=None, ge=0, le=64)


ResearchKind = Literal["general", "company_quality", "company_comparison", "thesis_review", "sector_relative", "fund_overlap", "fund_quality", "market_review"]


class ResearchTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    thscode: str | None = THSCODE_FIELD
    type: ResearchKind = "general"
    scope: dict | None = None
    budget: BudgetInput | None = None
    model_profile_id: str | None = PROFILE_ID_FIELD
    client_request_id: str | None = Field(default=None, min_length=8, max_length=128)


class SupplementInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["user_provided", "user_assumption"]
    content: str = Field(min_length=1, max_length=8000)
    fields: dict = Field(default_factory=dict)
    gap_ref: str | None = None


class ReanalyzeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplement_ids: list[str] = Field(default_factory=list, max_length=32)
    model_profile_id: str | None = PROFILE_ID_FIELD
    budget: BudgetInput | None = None
    client_request_id: str | None = Field(default=None, min_length=8, max_length=128)


class ChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=6000)
    # Every web request requires the user's affirmative choice on this turn.
    use_web_search: bool = False
    search_provider: Literal["bocha"] = "bocha"
    model_profile_id: str | None = PROFILE_ID_FIELD


class OpenChatTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class OpenChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=6000)
    thscode: str | None = THSCODE_FIELD
    history: list[OpenChatTurn] = Field(default_factory=list, max_length=12)
    use_web_search: bool = False
    search_provider: Literal["bocha"] = "bocha"
    model_profile_id: str | None = PROFILE_ID_FIELD


class SourceFlowPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flow: str
    description: str
    enabled: bool
    budget_note: str | None = None
    requires_confirmation: bool = False


class SourcePreviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    task_status: str
    flows: list[SourceFlowPreview]
    budget: dict
    usage: dict
    supplements: dict
    notice: str


def _budget(input_model: BudgetInput | None) -> TaskBudget:
    budget = TaskBudget()
    if input_model is not None:
        provided = input_model.model_dump(exclude_none=True)
        if provided:
            budget = budget.model_copy(update=provided)
    return budget


def _request_hash(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _repository(request: Request) -> ResearchRepository:
    return ResearchRepository(request.app.state.storage)


def _chat(request: Request) -> ReportChatService:
    return ReportChatService(repository=_repository(request), models=request.app.state.models,
                             model_factory=request.app.state.model_factory,
                             web_research=request.app.state.web_research)


def _open_chat(request: Request) -> OpenChatService:
    return OpenChatService(models=request.app.state.models,
                           model_factory=request.app.state.model_factory,
                           market=request.app.state.market,
                           web_research=request.app.state.web_research,
                           news=getattr(request.app.state, "rss", None))


def _apply_idempotency(repository: ResearchRepository, client_request_id: str | None,
                       request_hash: str):
    """同 ID 同内容返回原任务；同 ID 不同内容 409。返回 (task, replayed)。"""
    if not client_request_id:
        return None, False
    existing = repository.get_task_by_client_request_id(client_request_id)
    if existing is None:
        return None, False
    if existing.request_hash != request_hash:
        raise AppError("request_conflict", "同一 client_request_id 已用于不同请求内容，请更换标识后重试。", 409)
    return existing, True


@router.post("/tasks")
async def create_task(data: ResearchTaskCreate, manager: Manager, request: Request):
    if data.scope is not None and data.thscode is not None:
        raise AppError("validation", "新 scope 不能与旧 thscode 参数同时提供。", 422)
    try:
        # Legacy requests retain their persisted type verbatim so old reports and
        # clients remain stable; tool routing maps it to the corresponding scope.
        scope = validate_scope(data.scope) if data.scope is not None else {"type": data.type}
    except ValueError as error:
        raise AppError("validation", str(error), 422) from error
    if data.thscode:
        scope["thscode"] = data.thscode
        scope["thscodes"] = [data.thscode]
    budget = _budget(data.budget)
    request_hash = _request_hash({"question": data.question, "scope": scope,
                                  "budget": budget.model_dump(),
                                  "model_profile_id": data.model_profile_id})
    repository = _repository(request)
    existing, replayed = _apply_idempotency(repository, data.client_request_id, request_hash)
    if replayed:
        return JSONResponse({"task": task_snapshot(existing)}, status_code=200)
    task = manager.tasks.create(
        question=data.question, scope=scope, budget=budget,
        authorization={"sources": ["hexin_structured"], "web_search": False},
        client_request_id=data.client_request_id, request_hash=request_hash,
        model_profile_id=data.model_profile_id)
    manager.submit(task.id)
    return JSONResponse({"task": task_snapshot(task)}, status_code=202)


@router.get("/tasks/{task_id}")
def get_task(task_id: str, manager: Manager):
    task = manager.tasks.get(task_id)
    if task is None:
        raise AppError("not_found", "研究任务不存在。", 404)
    events = manager.tasks.events_after(task_id, 0)
    return {"task": task_snapshot(task),
            "last_seq": events[-1].seq if events else 0,
            "events": events[-20:]}


@router.get("/tasks/{task_id}/events")
async def stream_events(task_id: str, manager: Manager, after: int = Query(default=0, ge=0)):
    task = manager.tasks.get(task_id)
    if task is None:
        raise AppError("not_found", "研究任务不存在。", 404)

    async def generator():
        seq = after
        yield sse_event("snapshot", task_snapshot(task))
        last_output = time.monotonic()
        while True:
            events = manager.tasks.events_after(task_id, seq)
            for event in events:
                yield sse_event(event.type, {**event.payload, "seq": event.seq})
                seq = event.seq
                last_output = time.monotonic()
            current = manager.tasks.get(task_id)
            if current is None:
                break
            if current.status in TERMINAL_STATUSES and not events:
                yield sse_event("end", {"status": current.status})
                break
            if time.monotonic() - last_output > SSE_HEARTBEAT_SECONDS:
                yield sse_heartbeat()
                last_output = time.monotonic()
            await asyncio.sleep(SSE_POLL_SECONDS)

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store"})


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, manager: Manager):
    await manager.cancel(task_id)
    return {"task": task_snapshot(manager.tasks.get(task_id))}


@router.get("/reports/{report_id}")
def get_report(report_id: str, manager: Manager, request: Request):
    stored = ReportStore(_repository(request)).get(report_id)
    if stored is None:
        raise AppError("not_found", "报告不存在。", 404)
    return {"report": stored.report.model_dump(mode="json"),
            "raw_sections": [item.model_dump(mode="json") for item in stored.raw_sections]}


@router.get("/reports/{report_id}/conversation")
def conversation(report_id: str, request: Request):
    item, messages = _chat(request).history(report_id)
    return {"conversation": item.model_dump(mode="json"),
            "messages": [message.model_dump(mode="json") for message in messages]}


@router.post("/reports/{report_id}/conversation/messages", status_code=201)
async def ask_report(report_id: str, data: ChatInput, request: Request):
    question = data.question.strip()
    if not question:
        raise AppError("validation", "请输入要追问的问题。", 422)
    conversation, message = await _chat(request).ask(
        report_id=report_id, question=question, use_web_search=data.use_web_search,
        search_provider=data.search_provider, model_profile_id=data.model_profile_id)
    return {"conversation": conversation.model_dump(mode="json"),
            "message": message.model_dump(mode="json")}


@router.post("/ai-chat/messages")
async def ask_open_chat(data: OpenChatInput, request: Request):
    result = await _open_chat(request).ask(
        question=data.question.strip(), thscode=data.thscode,
        history=[item.model_dump() for item in data.history],
        use_web_search=data.use_web_search, search_provider=data.search_provider,
        model_profile_id=data.model_profile_id)
    return result


@router.post("/reports/{report_id}/supplements", status_code=201)
def add_supplement(report_id: str, data: SupplementInput, manager: Manager, request: Request):
    store = SupplementStore(_repository(request))
    item = store.add(report_id=report_id, kind=SupplementKind(data.kind),
                     content=data.content, fields=data.fields, gap_ref=data.gap_ref)
    return {"supplement": item.model_dump(mode="json")}


@router.post("/reports/{report_id}/reanalyze")
async def reanalyze(report_id: str, data: ReanalyzeInput, manager: Manager, request: Request):
    repository = _repository(request)
    stored = ReportStore(repository).get(report_id)
    if stored is None:
        raise AppError("not_found", "报告不存在，无法再分析。", 404)
    supplements = {item.id: item for item in SupplementStore(repository).list(report_id)}
    unknown = [item for item in data.supplement_ids if item not in supplements]
    if unknown:
        raise AppError("validation", "所选补充资料不属于该报告，已拒绝本次再分析。", 422)
    budget = _budget(data.budget)
    request_hash = _request_hash({"parent_report_id": stored.id, "question": stored.question,
                                  "scope": stored.scope, "budget": budget.model_dump(),
                                  "model_profile_id": data.model_profile_id,
                                  "supplement_ids": data.supplement_ids})
    existing, replayed = _apply_idempotency(repository, data.client_request_id, request_hash)
    if replayed:
        return JSONResponse({"task": task_snapshot(existing)}, status_code=200)
    task = manager.tasks.create(
        question=stored.question, scope=dict(stored.scope), budget=budget,
        authorization={"sources": ["hexin_structured"], "web_search": False},
        client_request_id=data.client_request_id, request_hash=request_hash,
        model_profile_id=data.model_profile_id)
    selected = [supplements[item].model_dump(mode="json") for item in data.supplement_ids]
    manager.submit(task.id, run_kwargs={"parent_report_id": stored.id,
                                        "supplement_ids": list(data.supplement_ids),
                                        "supplements": selected})
    return JSONResponse({"task": task_snapshot(task)}, status_code=202)


@router.post("/tasks/{task_id}/source-preview")
def source_preview(task_id: str, manager: Manager, request: Request) -> dict:
    """只预览本任务拟定的数据流向与预算（计划 04 §2.6）：纯本地计算，
    不发起任何外部请求、模型调用或预算扣减；真正取数仍由用户显式启动任务。"""
    task = manager.tasks.get(task_id)
    if task is None:
        raise AppError("not_found", "研究任务不存在。", 404)
    repository = _repository(request)
    report = ReportStore(repository).get(task.report_id) if task.report_id else None
    supplements = SupplementStore(repository).list(report.id) if report else []
    attachments = repository.list_attachments(report.id) if report else []
    web_search_enabled = bool(task.authorization.get("web_search"))
    referenced = sum(1 for item in supplements if item.referenced_by_report_id)
    flows = [
        SourceFlowPreview(
            flow="hexin_structured", enabled=True,
            description="同花顺结构化行情/财报数据（本机官方接口，缺数据转程序缺口）",
            budget_note=f"预算 ≤ {task.budget.max_fetches} 次取数"),
        SourceFlowPreview(
            flow="model_llm", enabled=True,
            description="所选模型分析与章节生成（reasoning 只记字符数）",
            budget_note=f"预算 ≤ {task.budget.max_llm_requests} 次请求"),
        SourceFlowPreview(
            flow="web_search", enabled=web_search_enabled,
            description="联网搜索（博查付费 API；搜索摘要只作线索，不充当财报）",
            budget_note="本任务未授权联网搜索。" if not web_search_enabled
            else "已授权：在任务预算内执行。",
            requires_confirmation=web_search_enabled),
        SourceFlowPreview(
            flow="local_attachment", enabled=True,
            description="本地附件解析（只在本机，不外发；截图配合已配置视觉模型需另行确认）",
            budget_note=f"该报告已有 {len(attachments)} 个附件资料、{len(supplements)} 条用户补充。"),
    ]
    result = SourcePreviewResult(
        task_id=task.id, task_status=task.status, flows=flows,
        budget=task.budget.model_dump(), usage=task.usage.model_dump(),
        supplements={"total": len(supplements), "referenced": referenced},
        notice="本次预览只在本地计算，未发起任何外部请求、模型调用或预算扣减。")
    return result.model_dump(mode="json")
