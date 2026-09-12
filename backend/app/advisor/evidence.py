"""顾问运行内的可公开证据契约。

该对象只保存能在建议卡中展示、回链或核验的材料；分析师简报、辩论和
风控备忘虽可供本次综合使用，但显式标为不可发布，不能变成事实来源。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AdvisorEvidenceSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=32)
    label: str = Field(min_length=1, max_length=240)
    ref: str | None = Field(default=None, max_length=2000)
    page: int | None = Field(default=None, ge=1)
    # 仅策略实验来源使用；让档位门禁可核验当前版本、当前期限的人工评审，
    # 不把模型对“已验证”的自述当成事实。
    strategy_review_decision: str | None = Field(default=None, max_length=32)


class AdvisorMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,95}$")
    value: str | None = Field(default=None, max_length=120)
    unit: str = Field(min_length=1, max_length=32)
    evidence_refs: list[str] = Field(default_factory=list, min_length=1, max_length=8)


class AdvisorEvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Z][0-9]{1,3}$")
    dimension: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=6000)
    thscode: str | None = Field(default=None, pattern=r"^[0-9]{6}\.(SH|SZ|BJ)$")
    as_of: str | None = Field(default=None, max_length=64)
    sources: list[AdvisorEvidenceSource] = Field(default_factory=list, max_length=16)
    metrics: list[AdvisorMetric] = Field(default_factory=list, max_length=64)
    publishable: bool = True
    base_rate_available: bool = False
    algorithm_data: dict = Field(default_factory=dict)

    # 过渡期兼容旧编排代码的只读访问方式；所有实例仍经过 Pydantic 校验。
    @property
    def dim(self) -> str:
        return self.dimension

    def get(self, name: str, default=None):
        aliases = {"dim": "dimension"}
        return getattr(self, aliases.get(name, name), default)

    def __getitem__(self, name: str):
        value = self.get(name, _MISSING)
        if value is _MISSING:
            raise KeyError(name)
        return value


_MISSING = object()


def evidence_index(items: list[AdvisorEvidenceItem], *, thscode: str | None) -> dict[str, AdvisorEvidenceItem]:
    """构建本轮可公开、同标的证据索引；重复编号一律拒绝。"""
    index: dict[str, AdvisorEvidenceItem] = {}
    for item in items:
        if item.id in index:
            raise ValueError(f"证据编号重复：{item.id}")
        if item.publishable and (not thscode or not item.thscode or item.thscode == thscode):
            index[item.id] = item
    return index


def metric_index(items: list[AdvisorEvidenceItem]) -> dict[str, AdvisorMetric]:
    index: dict[str, AdvisorMetric] = {}
    for item in items:
        if not item.publishable:
            continue
        for metric in item.metrics:
            if metric.name in index:
                raise ValueError(f"指标编号重复：{metric.name}")
            index[metric.name] = metric
    return index
