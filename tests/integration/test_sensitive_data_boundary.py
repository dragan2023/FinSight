"""Local exports are scoped report artefacts, never a database or credential dump."""
from __future__ import annotations

import uuid

from backend.app.agent.output_models import Coverage, OutputIntegrity, ResearchReport
from backend.app.research.models import ResearchTask
from backend.app.research.repository import ResearchRepository
from backend.app.research.reports import ReportStore


def test_export_manifest_contains_no_attachment_or_secret_fields(client):
    repository = ResearchRepository(client.app.state.storage)
    task = ResearchTask(id=uuid.uuid4().hex, question="边界检查")
    repository.save_task(task)
    report = ReportStore(repository).save(ResearchReport(
        id=uuid.uuid4().hex, version=1, task_id=task.id, question=task.question,
        coverage=Coverage.PARTIAL, output_integrity=OutputIntegrity.VALIDATED))
    created = client.post("/api/exports", json={"report_id": report.id, "format": "json"})
    assert created.status_code == 200
    content = client.get(f"/api/exports/{created.json()['export']['id']}/download").content.decode("utf-8")
    assert "encrypted_api_key" not in content
    assert "attachment" not in content
