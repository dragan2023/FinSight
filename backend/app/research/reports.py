"""Report persistence service (plan 03 sections 2.1 / 2.3).

已通过校验的章节进入 ResearchReport 契约；未通过结构校验的模型原始文本只放
raw_sections（仅用于下载核验材料），原始文本永不进入已验证业务对象或用户页面。
原报告不可变：同一任务每次生成产生新版本行，旧版本保持可读。
"""

from __future__ import annotations

import uuid

from ..agent.output_models import ResearchReport
from .models import RawSectionText, StoredReport


class ReportStore:
    def __init__(self, repository) -> None:
        self._repository = repository

    def save(self, report: ResearchReport, *,
             raw_sections: list[RawSectionText] | None = None) -> StoredReport:
        """保存一版报告；version 按任务内递增，不覆盖既有行。"""
        version = self._repository.count_reports(report.task_id) + 1
        report_id = uuid.uuid4().hex
        # 对外传递的 report.id 必须与 research_reports 主键一致。否则前端用
        # report.id 发起追问、下载或补充资料请求时会找不到已保存的那一行。
        saved_report = report.model_copy(update={"id": report_id, "version": version})
        stored = StoredReport(
            id=report_id, task_id=saved_report.task_id, version=version,
            parent_report_id=report.parent_report_id, question=report.question,
            scope=dict(saved_report.scope), report=saved_report,
            raw_sections=list(raw_sections or []))
        self._repository.insert_report(stored)
        return stored

    def get(self, report_id: str) -> StoredReport | None:
        return self._repository.get_report(report_id)

    def list_for_task(self, task_id: str) -> list[StoredReport]:
        return self._repository.list_reports(task_id)
