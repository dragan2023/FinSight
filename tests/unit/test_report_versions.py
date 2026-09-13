import uuid

from backend.app.agent.output_models import Coverage, MetricValue, OutputIntegrity, ResearchReport
from backend.app.research.library import ReportLibrary
from backend.app.research.models import ResearchTask
from backend.app.research.repository import ResearchRepository
from backend.app.research.reports import ReportStore
from backend.app.settings import Settings
from backend.app.storage import Storage


def test_version_diff_only_calls_structured_evidence_metrics_and_gaps(tmp_path):
    settings = Settings(project_root=tmp_path)
    storage = Storage(settings.db_path)
    settings.create_directories(); storage.initialize()
    repository = ResearchRepository(storage)
    task = ResearchTask(id=uuid.uuid4().hex, question="版本对比")
    repository.save_task(task)
    first = ResearchReport(id=uuid.uuid4().hex, version=1, task_id=task.id, question=task.question,
                           evidence_ids=["e1"], metrics=[MetricValue(metric="营收", value="1", unit="CNY", basis="annual", formula="来源", evidence_ids=["e1"])],
                           coverage=Coverage.PARTIAL, output_integrity=OutputIntegrity.VALIDATED)
    second = ResearchReport(id=uuid.uuid4().hex, version=2, task_id=task.id, question=task.question,
                            evidence_ids=["e2"], metrics=[MetricValue(metric="现金流", value="2", unit="CNY", basis="annual", formula="来源", evidence_ids=["e2"])],
                            coverage=Coverage.PARTIAL, output_integrity=OutputIntegrity.VALIDATED)
    old = ReportStore(repository).save(first)
    new = ReportStore(repository).save(second)
    diff = ReportLibrary(repository).diff(old.id, new.id)
    assert diff["evidence"] == {"added": ["e2"], "removed": ["e1"]}
    assert diff["metrics"] == {"added": ["现金流"], "removed": ["营收"]}
    assert "章节文字" in diff["notice"]
