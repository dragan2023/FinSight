"""Offline checks for report deletion preview and confirmed local cleanup."""
from __future__ import annotations

import uuid

from backend.app.agent.output_models import Coverage, OutputIntegrity, ResearchReport
from backend.app.research.models import ResearchTask
from backend.app.research.repository import ResearchRepository
from backend.app.research.reports import ReportStore
from backend.app.research.theses import ObservationOutcome, ThesisStore


def _report(client, question: str = "本地报告"):
    repository = ResearchRepository(client.app.state.storage)
    task = ResearchTask(id=uuid.uuid4().hex, question=question)
    repository.save_task(task)
    report = ResearchReport(id=uuid.uuid4().hex, version=1, task_id=task.id,
                            question=question, coverage=Coverage.PARTIAL,
                            output_integrity=OutputIntegrity.VALIDATED)
    return repository, ReportStore(repository).save(report)


def test_preview_is_read_only_and_confirmed_delete_removes_only_selected_report(client):
    repository, first = _report(client, "保留测试 A")
    _, second = _report(client, "保留测试 B")
    preview = client.post("/api/library/deletion-preview", json={"report_ids": [first.id]})
    assert preview.status_code == 200
    assert preview.json()["items"][0]["exists"] is True
    assert repository.get_report(first.id) is not None

    rejected = client.post("/api/library/delete", json={"report_ids": [first.id]})
    assert rejected.status_code == 422
    removed = client.post("/api/library/delete", json={"report_ids": [first.id], "confirmed": True})
    assert removed.status_code == 200
    assert repository.get_report(first.id) is None
    assert repository.get_report(second.id) is not None


def test_observed_thesis_blocks_report_deletion(client):
    repository, report = _report(client)
    thesis = ThesisStore(repository).create(
        security_id="600519.SH", content="需要继续验证现金流",
        required_evidence=["经营现金流"], counter_evidence=["回款恶化"],
        observation_metrics=["经营现金流"], confirmed_by_user=True)
    ThesisStore(repository).observe(
        thesis_id=thesis.id, report_id=report.id, evidence_refs=["evidence-local"], metric_refs=["metric-local"],
        outcome=ObservationOutcome.INCONCLUSIVE, note="本地材料不足")
    response = client.post("/api/library/delete", json={"report_ids": [report.id], "confirmed": True})
    assert response.status_code == 409
    assert response.json()["error"]["category"] == "request_conflict"
    assert repository.get_report(report.id) is not None


def test_task_type_is_persisted_in_local_scope_without_external_call(client):
    response = client.post("/api/research/tasks", json={
        "question": "仅建立公司体检任务", "type": "company_quality",
        "client_request_id": "stage05-company-quality-001",
    })
    assert response.status_code == 202
    assert response.json()["task"]["scope"] == {"type": "company_quality"}
