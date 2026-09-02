"""Public research event stream helpers (plan 03 section 2.6).

SSE 只携带公开进度：任务状态、阶段、章节预览、校验问题、缺口与用量计数。
reasoning 只以字符计数出现；Key、完整 SDK 异常与隐藏推理永不进入事件。
连接建立时先发一帧 snapshot，旧事件被清理后客户端也能恢复任务全貌；
seq 单调递增，客户端按 seq 续读、去重。
"""

from __future__ import annotations

import json

from .models import TaskStatus, ResearchTask

SSE_POLL_SECONDS = 0.2
SSE_HEARTBEAT_SECONDS = 15.0

TERMINAL_STATUSES = frozenset({
    TaskStatus.COMPLETED, TaskStatus.INTERRUPTED, TaskStatus.CANCELLED, TaskStatus.FAILED,
})


def task_snapshot(task: ResearchTask) -> dict:
    """公开任务快照：不含 client 凭据、不含模型隐藏状态。"""
    return {
        "id": task.id,
        "question": task.question,
        "scope": task.scope,
        "status": task.status,
        "budget": task.budget.model_dump(),
        "usage": task.usage.model_dump(),
        "report_id": task.report_id,
        "error": task.error,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }


def sse_event(event_type: str, payload: dict) -> str:
    body = json.dumps({"type": event_type, **payload}, ensure_ascii=False)
    return f"event: {event_type}\ndata: {body}\n\n"


def sse_heartbeat() -> str:
    # SSE 注释行：任何代理/客户端都会忽略，仅保活连接。
    return ": ping\n\n"
