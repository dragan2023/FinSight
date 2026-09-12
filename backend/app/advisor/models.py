"""V2-04 Advisor Agent：会话、消息、用户画像与建议台账的数据模型。

多轮状态=摘要记忆（滚动窗口+要点压缩）；建议落台账可跟踪可复盘。
枚举值由 advisor/labels.py 统一映射中文，模型不输出中文标签本身。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

RISK_APPETITES = ("conservative", "balanced", "aggressive")
MESSAGE_ROLES = ("user", "advisor")
LEDGER_STATUSES = ("tracking", "settled", "void")


class UserProfileInput(BaseModel):
    """用户画像：只存风险偏好档位与关注域，不采集财务状况（合规 §6.2）。"""

    model_config = ConfigDict(extra="forbid")

    risk_appetite: str = Field(default="balanced")
    focus_domains: list[str] = Field(default_factory=list, max_length=10)
    excluded_thscodes: list[str] = Field(default_factory=list, max_length=50)


class SessionCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=120)
    user_profile: UserProfileInput = Field(default_factory=UserProfileInput)


class SessionUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=120)
    user_profile: UserProfileInput | None = None


class SessionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    user_profile: UserProfileInput
    message_count: int
    created_at: str
    updated_at: str


class MessageView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    session_id: str
    role: str
    content: str
    suggestion: dict | None = None
    created_at: str


class LedgerEntryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    session_id: str | None = None
    thscode: str
    name: str
    direction: str
    confidence: str
    horizon: str
    card: dict
    params_hash: str
    status: str
    settled_at: str | None = None
    settlement: dict | None = None
    created_at: str
