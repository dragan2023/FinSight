from backend.app.agent.output_models import Coverage,OutputIntegrity,ResearchReport,ReportSection,SectionStatus
from backend.app.analysis.models import MetricValue
from backend.app.research.exporting import ExportService
from backend.app.research.evidence_store import EvidenceStore
from backend.app.market_data.normalization import NormalizedRecord
from backend.app.research.reports import ReportStore
from backend.app.research.repository import ResearchRepository
from backend.app.research.tasks import TaskStore
from backend.app.storage import Storage

def test_export_is_safe_and_keeps_partial_markers(tmp_path):
    storage=Storage(tmp_path/"db.sqlite");storage.initialize();repo=ResearchRepository(storage)
    task=TaskStore(repo).create(question="<script>测试</script>")
    report=ResearchReport(id="r",version=1,task_id=task.id,question="<script>测试</script>",coverage=Coverage.PARTIAL,output_integrity=OutputIntegrity.TRUNCATED,metrics=[MetricValue(metric="x",value="1",unit="CNY",basis="annual",formula="x",evidence_ids=["e"],note="=unsafe")],sections=[ReportSection(id="facts",title="事实",status=SectionStatus.CONDITIONAL,summary="<b>文本</b>")])
    service=ExportService(repo,tmp_path/"exports")
    evidence=EvidenceStore(repo).save_fetch(provider="hexin",profile_id=None,capability="financials",endpoint_path="/income",params={"thscode":"600519.SH"},basis="annual_report",source_time="2025-FY",raw_payload={"revenue":123},records=[NormalizedRecord(thscode="600519.SH",period_key="2025-FY",period_end="2025-12-31",disclosed_at=None,basis="annual_report",currency="CNY",fields={"revenue": {"source_field":"revenue","label":"营收","kind":"amount","unit":"CNY","raw_value":"123","value":"123"}})],gaps=[])
    report=report.model_copy(update={"evidence_ids":[evidence.id]})
    stored=ReportStore(repo).save(report)
    html=service.create(stored.id,"html");csv=service.create(stored.id,"csv");json_export=service.create(stored.id,"json");markdown=service.create(stored.id,"markdown");sources=service.create(stored.id,"source_markdown")
    assert "<script>" not in service.path(html["id"]).read_text(encoding="utf-8")
    assert "'=unsafe" in service.path(csv["id"]).read_text(encoding="utf-8-sig")
    assert "truncated" in service.path(json_export["id"]).read_text(encoding="utf-8")
    assert "证据：" not in service.path(markdown["id"]).read_text(encoding="utf-8")
    source_text=service.path(sources["id"]).read_text(encoding="utf-8")
    assert "核验材料" in source_text and '"revenue"' in source_text
    assert '"revenue": 123' in source_text
