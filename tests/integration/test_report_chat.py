"""Local report-conversation boundaries; no model or search request is allowed here."""

from __future__ import annotations

import uuid

from backend.app.agent.output_models import Coverage, OutputIntegrity, ResearchReport
from backend.app.research.reports import ReportStore
from backend.app.research.repository import ResearchRepository
from backend.app.research.models import ResearchTask


def _report(client):
    repository = ResearchRepository(client.app.state.storage)
    task = ResearchTask(id=uuid.uuid4().hex, question="现金流需要核验什么？")
    repository.save_task(task)
    report = ResearchReport(id=uuid.uuid4().hex, version=1, task_id=task.id,
                            question=task.question, coverage=Coverage.PARTIAL,
                            output_integrity=OutputIntegrity.VALIDATED)
    return ReportStore(repository).save(report)


def test_conversation_is_local_memory_and_unconfigured_model_never_calls_out(client):
    report = _report(client)
    # 页面拿到的 report.id 必须就是路由可查询的持久化 id；否则追问会误报“报告不存在”。
    report_view = client.get(f"/api/research/reports/{report.id}")
    assert report_view.status_code == 200
    assert report_view.json()["report"]["id"] == report.id
    history = client.get(f"/api/research/reports/{report.id}/conversation")
    assert history.status_code == 200
    assert history.json()["messages"] == []
    # The offline fixture has no active model.  The endpoint must fail before
    # an LLM or search request is attempted, and it must not store a partial turn.
    ask = client.post(f"/api/research/reports/{report.id}/conversation/messages", json={
        "question": "请结合这份报告解释现金流风险", "use_web_search": True,
    })
    assert ask.status_code == 409
    after = client.get(f"/api/research/reports/{report.id}/conversation")
    assert after.status_code == 200
    assert after.json()["messages"] == []
