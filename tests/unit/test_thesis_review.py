from __future__ import annotations
import pytest
from backend.app.errors import AppError
from backend.app.research.repository import ResearchRepository
from backend.app.research.theses import ObservationOutcome, ThesisStore
from backend.app.research.watchlist import WatchlistStore
from backend.app.storage import Storage

def repo(tmp_path):
    storage=Storage(tmp_path / "finsight.db"); storage.initialize(); return ResearchRepository(storage)

def test_watchlist_is_local_and_updates_without_deleting_theses(tmp_path):
    store=WatchlistStore(repo(tmp_path)); item=store.add(security_id="600519.SH",thscode="600519.SH",name="贵州茅台",tags=["长期"])
    assert store.update(item.id,["复核"]).tags == ["复核"]
    assert store.remove(item.id) is True and store.list() == []

def test_thesis_requires_user_confirmation_and_observation_requires_refs(tmp_path):
    store=ThesisStore(repo(tmp_path))
    with pytest.raises(AppError): store.create(confirmed_by_user=False,security_id="600519.SH",content="现金流改善")
    thesis=store.create(confirmed_by_user=True,security_id="600519.SH",content="现金流改善",required_evidence=["收入与现金流年报"])
    assert store.list("600519.SH")[0].id == thesis.id
    with pytest.raises(AppError): store.observe(thesis_id=thesis.id,report_id="a"*32,evidence_refs=[],metric_refs=[],outcome=ObservationOutcome.INCONCLUSIVE)
