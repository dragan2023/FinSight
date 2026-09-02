"""User supplement service (plan 03 section 2.7).

补充资料只保存、不外发、不触发任何自动再分析；kind 与报告 ClaimKind 对齐
（user_provided / user_assumption），不自动改写官方证据。保存后状态就是
"已保存，尚未再分析"——referenced_by_report_id 仅在再分析显式引用后写入。
"""

from __future__ import annotations

import uuid

from ..errors import AppError
from .models import MAX_SUPPLEMENT_TEXT, ResearchSupplement, SupplementKind

ALLOWED_FIELD_KEYS = frozenset({"dimension", "period", "unit", "source_note", "value_hint"})


class SupplementStore:
    def __init__(self, repository) -> None:
        self._repository = repository

    def add(self, *, report_id: str, kind: SupplementKind, content: str,
            fields: dict | None = None, gap_ref: str | None = None) -> ResearchSupplement:
        stored = self._repository.get_report(report_id)
        if stored is None:
            raise AppError("not_found", "报告不存在，无法保存补充资料。", 404)
        clean_fields = {key: value for key, value in dict(fields or {}).items()
                        if key in ALLOWED_FIELD_KEYS}
        supplement = ResearchSupplement(
            id=uuid.uuid4().hex, report_id=report_id, task_id=stored.task_id,
            kind=kind, content=content[:MAX_SUPPLEMENT_TEXT],
            gap_ref=gap_ref, fields=clean_fields)
        self._repository.insert_supplement(supplement)
        return supplement

    def list(self, report_id: str) -> list[ResearchSupplement]:
        return self._repository.list_supplements(report_id)

    def mark_referenced(self, supplement_ids: list[str], new_report_id: str) -> None:
        """再分析创建子版本时显式引用；不改变原报告。"""
        if supplement_ids:
            self._repository.mark_supplements_referenced(supplement_ids, new_report_id)
