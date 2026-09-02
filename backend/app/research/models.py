"""Research task, progress and supplement contracts (plan 03 section 2.1).

任务执行状态与报告数据覆盖程度分开保存：Task.status 只描述本轮执行如何结束，
数据缺失、部分章节未通过或输出截断都不是任务失败，它们由报告侧的
coverage / section status / output integrity 表达（契约在 agent/output_models.py，
不在此重复定义）。真正 failed 只保留给本地执行意外。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..agent.output_models import ResearchReport

MAX_SUPPLEMENT_TEXT = 8000
MAX_RAW_SECTION_TEXT = 200000
MAX_CHAT_MESSAGE_TEXT = 6000


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TaskBudget(BaseModel):
    """可在启动前调整的产品约束；不是虚构的费用保证。"""

    model_config = ConfigDict(extra="forbid")

    max_llm_requests: int = Field(default=24, ge=0, le=200)
    max_fetches: int = Field(default=24, ge=0, le=200)
    max_tool_rounds: int = Field(default=8, ge=0, le=64)
    # 搜索/正文提取尚未接入且默认未经授权：固定为 0（计划 §2.2）。
    max_searches: int = Field(default=0, ge=0, le=0)
    max_extractions: int = Field(default=0, ge=0, le=0)


class TaskUsage(BaseModel):
    """实际用量计数；reasoning 只记字符数，内容永不落盘。"""

    model_config = ConfigDict(extra="forbid")

    llm_requests: int = Field(default=0, ge=0)
    fetches: int = Field(default=0, ge=0)
    tool_rounds: int = Field(default=0, ge=0)
    searches: int = Field(default=0, ge=0)
    extractions: int = Field(default=0, ge=0)
    reasoning_chars: int = Field(default=0, ge=0)


class ResearchTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str = Field(min_length=1, max_length=2000)
    scope: dict = Field(default_factory=dict)
    status: TaskStatus = TaskStatus.QUEUED
    budget: TaskBudget = Field(default_factory=TaskBudget)
    usage: TaskUsage = Field(default_factory=TaskUsage)
    authorization: dict = Field(default_factory=dict)
    client_request_id: str | None = None
    request_hash: str | None = None
    model_profile_id: str | None = None
    report_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None


class TaskEvent(BaseModel):
    """公开业务进度事件；seq 供批次 4 的 SSE 增量读取使用。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    seq: int = Field(ge=1)
    type: str = Field(min_length=1, max_length=64)
    payload: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SupplementKind(StrEnum):
    USER_PROVIDED = "user_provided"
    USER_ASSUMPTION = "user_assumption"


class ResearchSupplement(BaseModel):
    """用户补充只保存，不自动外发或再分析（计划 §2.7）。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    report_id: str
    task_id: str
    kind: SupplementKind
    content: str = Field(min_length=1, max_length=MAX_SUPPLEMENT_TEXT)
    gap_ref: str | None = None
    # 结构化说明（期间/单位/来源等）由程序读取；自由文本进 content。
    fields: dict = Field(default_factory=dict)
    referenced_by_report_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RawSectionText(BaseModel):
    """未通过结构校验的模型原始文本：公开、可展开、只按纯文本渲染。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    reason: str
    text: str = Field(min_length=1, max_length=MAX_RAW_SECTION_TEXT)


class StoredReport(BaseModel):
    """持久化报告行：已验证契约 + 原始文本分开保存。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    task_id: str
    version: int = Field(ge=1)
    parent_report_id: str | None = None
    question: str
    scope: dict = Field(default_factory=dict)
    report: ResearchReport
    raw_sections: list[RawSectionText] = Field(default_factory=list, max_length=16)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ReportConversation(BaseModel):
    """本地报告对话的轻量工作记忆，不改变不可变报告正文。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    report_id: str
    model_profile_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ReportChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    conversation_id: str
    role: ChatRole
    content: str = Field(min_length=1, max_length=MAX_CHAT_MESSAGE_TEXT)
    # 仅保存检索结果的标题、域名和链接，绝不把 Key 或外部原文写入本地记忆。
    sources: list[dict] = Field(default_factory=list, max_length=8)
    created_at: datetime
