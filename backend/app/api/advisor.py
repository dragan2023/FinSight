"""V2-04 Advisor API：会话 CRUD、消息、发送（同步等待）、建议台账与结算。"""
from __future__ import annotations

import asyncio
import time
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..advisor.dual_track import DualTrackLedger
from ..advisor.graph import AdvisorGraph
from ..advisor.ledger import AdvisorLedger
from ..advisor.research_track import ResearchPredictionInput, ResearchPredictionLedger
from ..advisor.models import SessionCreateInput, SessionUpdateInput
from ..advisor.service import AdvisorService
from ..errors import AppError
from ..research.events import SSE_HEARTBEAT_SECONDS, SSE_POLL_SECONDS, sse_event, sse_heartbeat

router = APIRouter(prefix="/api/advisor", tags=["advisor"])


class AdvisorSendInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000)
    allow_web_search: bool = False
    analysis_mode: Literal["standard", "deep"] = "standard"


class LedgerSettleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh: bool = False


class LedgerSettleEligibleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool = False


def _service(request: Request) -> AdvisorService:
    return request.app.state.advisor


def _ledger(request: Request) -> AdvisorLedger:
    return request.app.state.ledger


def _graph(request: Request) -> AdvisorGraph:
    factory = getattr(request.app.state, "advisor_graph_factory", None)
    return factory() if factory is not None else request.app.state.advisor_graph


def _runs(request: Request):
    return request.app.state.advisor_runs


# -- sessions ---------------------------------------------------------------
@router.get("/sessions")
async def list_sessions(request: Request):
    return {"sessions": _service(request).list_sessions()}


@router.post("/sessions", status_code=201)
async def create_session(request: Request, payload: SessionCreateInput):
    session = _service(request).create_session(payload.title, payload.user_profile)
    return {"duplicate": False, "session": session}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    return {"session": _service(request).get_session(session_id)}


@router.patch("/sessions/{session_id}")
async def update_session(session_id: str, payload: SessionUpdateInput, request: Request):
    return {"session": _service(request).update_session(session_id, payload)}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    return {"deleted": _service(request).delete_session(session_id)}


@router.get("/sessions/{session_id}/messages")
async def list_messages(session_id: str, request: Request):
    _service(request).get_session(session_id)
    return {"messages": _service(request).list_messages(session_id)}


# -- send -------------------------------------------------------------------
@router.post("/sessions/{session_id}/send")
async def send(session_id: str, payload: AdvisorSendInput, request: Request):
    service = _service(request)
    session = service.get_session(session_id)
    excluded = session["user_profile"].excluded_thscodes
    for code in excluded:
        if code in payload.content:
            # 与 /runs 同序：先校验后落库，被拒请求不得在会话里留下消息。
            raise AppError("excluded_target", f"标的 {code} 在你的禁入清单中，不予分析。", 422)
    service.add_message(session_id, "user", payload.content)
    history = service.window(session_id)
    graph = _graph(request)
    try:
        outcome = await graph.run(session_id=session_id, question=payload.content,
                                  profile=session["user_profile"].model_dump(),
                                  history=history[:-1],
                                  allow_web_search=payload.allow_web_search,
                                  analysis_mode=payload.analysis_mode)
    except AppError:
        # 失败也回写 advisor 侧提示，保持会话可读；再抛给统一错误处理。
        raise
    suggestion = outcome["card"]
    if suggestion is not None and outcome.get("evidence"):
        suggestion = {**suggestion, "evidence": outcome["evidence"]}
    reply_message = service.add_message(session_id, "advisor", outcome["reply"], suggestion=suggestion)
    return {"needs_clarification": outcome["needs_clarification"],
            "message": reply_message, "phases": outcome["phases"],
            "llm_requests": outcome["llm_requests"], "advice_id": outcome["advice_id"]}


@router.post("/sessions/{session_id}/runs", status_code=202)
async def start_run(session_id: str, payload: AdvisorSendInput, request: Request):
    service = _service(request)
    session = service.get_session(session_id)
    excluded = session["user_profile"].excluded_thscodes
    if excluded:
        for code in excluded:
            if code in payload.content:
                raise AppError("excluded_target", f"标的 {code} 在你的禁入清单中，不予分析。", 422)
    service.add_message(session_id, "user", payload.content)
    run = _runs(request).start(session_id=session_id, question=payload.content,
                               profile=session["user_profile"].model_dump(),
                               history=service.window(session_id)[:-1],
                               allow_web_search=payload.allow_web_search,
                               analysis_mode=payload.analysis_mode)
    return {"run": run.view()}


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request):
    return {"run": _runs(request).get(run_id).view()}


@router.get("/sessions/{session_id}/runs")
def list_session_runs(session_id: str, request: Request, limit: int = Query(default=10, ge=1, le=50)):
    """会话最近任务（公开视图，不含问题原文）；重启后仍可查、可恢复。"""
    _service(request).get_session(session_id)
    return {"runs": _runs(request).list_for_session(session_id, limit=limit)}


@router.get("/runs/{run_id}/events")
async def stream_run_events(run_id: str, request: Request, after: int = Query(default=0, ge=0)):
    """SSE 只发送可展示的任务阶段，不传递模型推理或原始外部响应。"""
    manager = _runs(request)
    manager.get(run_id)

    async def generator():
        seq = after
        last_output = time.monotonic()
        while True:
            events = manager.events_after(run_id, seq)
            for event in events:
                yield sse_event(event["type"], event)
                seq = event["seq"]
                last_output = time.monotonic()
            run = manager.get(run_id)
            if run.status in {"completed", "cancelled", "failed", "interrupted"} and not events:
                yield sse_event("end", {"type": "end", "run": run.view(), "seq": seq})
                break
            if time.monotonic() - last_output > SSE_HEARTBEAT_SECONDS:
                yield sse_heartbeat()
                last_output = time.monotonic()
            await asyncio.sleep(SSE_POLL_SECONDS)

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store"})


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request):
    return {"run": (await _runs(request).cancel(run_id)).view()}


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str, request: Request):
    # resume 会创建后台 asyncio 任务，必须运行在 ASGI 事件循环内；同步路由会
    # 把状态先写成 queued、随后因没有 running loop 而报错，留下永远不执行的任务。
    return {"run": _runs(request).resume(run_id).view()}


# -- ledger -----------------------------------------------------------------
@router.get("/ledger")
async def ledger_list(request: Request, status: str | None = None, limit: int = 50, offset: int = 0):
    if status is not None and status not in ("tracking", "settled", "void"):
        raise AppError("invalid_status", "台账状态过滤值不支持。", 422)
    return {"entries": _ledger(request).list(status=status, limit=limit, offset=offset),
            "calibration": _ledger(request).calibration()}


@router.get("/ledger/{entry_id}")
async def ledger_entry(entry_id: str, request: Request):
    return {"entry": _ledger(request).get(entry_id)}


@router.post("/ledger/{entry_id}/settle")
async def ledger_settle(entry_id: str, payload: LedgerSettleInput, request: Request):
    market = request.app.state.market
    return {"entry": await _ledger(request).settle(entry_id, market, refresh=payload.refresh)}


@router.post("/ledger/settle-eligible")
async def ledger_settle_eligible(payload: LedgerSettleEligibleInput, request: Request):
    return await _ledger(request).settle_eligible(request.app.state.market, confirmed=payload.confirmed)

def _research_track(request: Request) -> ResearchPredictionLedger:
    return request.app.state.research_predictions


# -- 双轨独立结算（方案 02 §4）------------------------------------------------
def _dual_track(request: Request) -> DualTrackLedger:
    return DualTrackLedger(request.app.state.storage)


def _warehouse_repository(request: Request):
    return request.app.state.market_warehouse.repository


@router.get("/dual-track/records")
async def dual_track_records(request: Request, track: str | None = None,
                             status: str | None = None, batch_id: str | None = None,
                             limit: int = Query(default=200, ge=1, le=2000)):
    if track is not None and track not in ("strategy", "ai"):
        raise AppError("invalid_track", "轨道只支持 strategy 或 ai。", 422)
    if status is not None and status not in ("sealed", "settled", "void"):
        raise AppError("invalid_status", "双轨状态过滤值不支持。", 422)
    return {"records": _dual_track(request).list(track=track, status=status,
                                                 batch_id=batch_id, limit=limit)}


@router.get("/dual-track/advice/{advice_id}")
async def dual_track_for_advice(advice_id: str, request: Request):
    return {"records": _dual_track(request).for_advice(advice_id)}


@router.get("/dual-track/due")
async def dual_track_due(request: Request):
    return {"due": _dual_track(request).due()}


@router.post("/dual-track/{settlement_id}/settle")
async def dual_track_settle(settlement_id: str, request: Request):
    """结算**一条**轨道。两轨各自独立，互不依赖——这是双轨设计的要点。"""
    return {"record": _dual_track(request).settle(settlement_id, _warehouse_repository(request))}


@router.post("/dual-track/settle-due")
async def dual_track_settle_due(payload: LedgerSettleEligibleInput, request: Request):
    return _dual_track(request).settle_due(_warehouse_repository(request),
                                           confirmed=payload.confirmed)


@router.get("/dual-track/margin")
async def dual_track_margin(request: Request, batch_id: str | None = None):
    """两轨对比表 + LLM 的边际贡献（= 两轨差值）+ 预注册停止条件判定。"""
    return {"report": _dual_track(request).evaluate_stop_criteria(batch_id=batch_id)}


# -- research predictions（双轨封存：研究预测轨，登记即封存） ------------------
@router.get("/research-predictions")
async def research_prediction_list(request: Request, status: str | None = None,
                                   thscode: str | None = None, limit: int = 50, offset: int = 0):
    if status is not None and status not in ("sealed", "settled", "void"):
        raise AppError("invalid_status", "研究预测状态过滤值不支持。", 422)
    track = _research_track(request)
    return {"predictions": track.list(status=status, thscode=thscode, limit=limit, offset=offset),
            "summary": track.summary()}


@router.post("/research-predictions", status_code=201)
async def research_prediction_seal(payload: ResearchPredictionInput, request: Request):
    """封存一条研究预测；缺反证/失效条件或概率不归一时拒绝，不做默认补齐。"""
    return {"prediction": _research_track(request).seal(payload.model_dump())}


@router.get("/research-predictions/verify-all")
async def research_prediction_verify_all(request: Request):
    return _research_track(request).verify_all()


@router.get("/research-predictions/{prediction_id}")
async def research_prediction_get(prediction_id: str, request: Request):
    return {"prediction": _research_track(request).get(prediction_id)}


@router.get("/research-predictions/{prediction_id}/verify")
async def research_prediction_verify(prediction_id: str, request: Request):
    return _research_track(request).verify(prediction_id)


@router.post("/research-predictions/{prediction_id}/settle")
async def research_prediction_settle(prediction_id: str, payload: LedgerSettleInput, request: Request):
    """结算由用户显式触发；行情为免费数据，但绝不自动后台结算。"""
    market = request.app.state.market
    return {"prediction": await _research_track(request).settle(prediction_id, market)}
