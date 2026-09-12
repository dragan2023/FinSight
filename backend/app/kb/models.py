"""V2-03 私有知识库：文档、分块与检索结果的数据模型。

全文与分块只存本地 SQLite；嵌入向量随提供方维度存 BLOB，
未配置嵌入服务时检索走 FTS5 关键词回退，完全不外发。
"""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

# 服务端固定标签词表（阶段03 §3.1）：标签只接受以下词表，
# 覆盖资产类型、期限、策略族与市场状态四类维度，避免自由文本标签污染检索过滤。
TAG_VOCABULARY: dict[str, frozenset[str]] = {
    "asset_type": frozenset({"a-share", "index", "fund"}),
    "horizon": frozenset({"days_5", "days_20", "quarter", "year"}),
    "strategy_family": frozenset({"trend", "reversal"}),
    "market_regime": frozenset({"trend_up", "trend_down", "range", "low_vol", "high_vol"}),
}
ALLOWED_TAGS = frozenset().union(*TAG_VOCABULARY.values())
MAX_TAGS = 20


class KbDocumentView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    filename: str
    kind: str
    parse_method: str
    byte_size: int
    char_count: int
    chunk_count: int
    embedded_chunks: int
    embedding_state: str
    embedding_model: str = ""
    warnings: list[str] = Field(default_factory=list)
    knowledge_role: Literal["source", "strategy", "hypothesis"] = "source"
    tags: list[str] = Field(default_factory=list)
    review_state: Literal["unreviewed", "approved", "retired"] = "unreviewed"
    effective_at: str | None = None
    created_at: str
    updated_at: str


class KbSearchInput(BaseModel):
    """检索调试输入；检索是用户主动操作（嵌入调用可能经外部服务）。"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200)
    top_k: int = Field(default=5, ge=1, le=10)
    knowledge_roles: list[Literal["source", "strategy", "hypothesis"]] = Field(default_factory=list)
    review_states: list[Literal["unreviewed", "approved", "retired"]] = Field(default_factory=list)
    asset_type: str | None = Field(default=None, max_length=40)
    horizon: str | None = Field(default=None, max_length=40)
    strategy_family: str | None = Field(default=None, max_length=40)
    market_regime: str | None = Field(default=None, max_length=40)


class KbSearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    document_id: str
    filename: str
    kind: str
    heading: str = ""
    page: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    score: float
    channels: list[str] = Field(default_factory=list)
    excerpt: str


class KbSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    embedding_used: bool
    embedding_model: str = ""
    hits: list[KbSearchHit] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    note: str
