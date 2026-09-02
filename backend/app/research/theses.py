"""User-confirmed investment-reason tracking; no automatic monitoring."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field
from ..errors import AppError

class ThesisStatus(StrEnum): ACTIVE="active"; REVOKED="revoked"
class ObservationOutcome(StrEnum): SUPPORTS="supports"; WEAKENS="weakens"; CONFLICT="conflict"; INCONCLUSIVE="inconclusive"
class Thesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str; security_id: str; content: str = Field(min_length=1, max_length=2000)
    required_evidence: list[str] = Field(default_factory=list, max_length=20)
    counter_evidence: list[str] = Field(default_factory=list, max_length=20)
    observation_metrics: list[str] = Field(default_factory=list, max_length=20)
    status: ThesisStatus = ThesisStatus.ACTIVE; confirmed_at: datetime
    source_report_id: str | None = None; created_at: datetime; updated_at: datetime
class ThesisObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str; thesis_id: str; report_id: str; evidence_refs: list[str] = Field(default_factory=list)
    metric_refs: list[str] = Field(default_factory=list); outcome: ObservationOutcome; note: str = Field(default="", max_length=2000); created_at: datetime
class ThesisStore:
    def __init__(self, repository) -> None: self._repository=repository
    def create(self, *, confirmed_by_user: bool, security_id: str, content: str, required_evidence: list[str] | None=None, counter_evidence: list[str] | None=None, observation_metrics: list[str] | None=None, source_report_id: str | None=None) -> Thesis:
        if not confirmed_by_user: raise AppError("validation", "模型候选理由必须由用户确认后才能保存。", 422)
        now=datetime.now(timezone.utc); item=Thesis(id=uuid.uuid4().hex,security_id=security_id,content=content,required_evidence=required_evidence or [],counter_evidence=counter_evidence or [],observation_metrics=observation_metrics or [],confirmed_at=now,source_report_id=source_report_id,created_at=now,updated_at=now)
        self._repository.insert_thesis(item); return item
    def list(self, security_id: str | None=None) -> list[Thesis]: return self._repository.list_theses(security_id)
    def update(self, thesis_id: str, *, content: str, revoked: bool = False) -> Thesis | None:
        return self._repository.update_thesis(thesis_id, content=content, status=ThesisStatus.REVOKED if revoked else ThesisStatus.ACTIVE)
    def observe(self, *, thesis_id: str, report_id: str, evidence_refs: list[str], metric_refs: list[str], outcome: ObservationOutcome, note: str="") -> ThesisObservation:
        if not evidence_refs and not metric_refs: raise AppError("validation", "复核必须引用新报告中的证据或指标，不能编造变化。", 422)
        item=ThesisObservation(id=uuid.uuid4().hex,thesis_id=thesis_id,report_id=report_id,evidence_refs=evidence_refs,metric_refs=metric_refs,outcome=outcome,note=note,created_at=datetime.now(timezone.utc))
        self._repository.insert_thesis_observation(item); return item
