"""荐股链守卫测试：候选门禁、降级改写、档位天花板。

这套测试守的是"LLM 不得抬档"这一**有意设计**的门禁（`docs/荐股治理方案`：
04 前向影子验证完成前，历史候选最高只能到 medium / trial_buy_signal）。
它们不是为了让测试通过而放宽门禁的产物——任何一条被改红，都应先假设是
生产门禁被削弱，而不是测试过时。

全部离线：策略服务为脚本假对象，不访问网络、不使用真实凭据。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.advisor.evidence import AdvisorEvidenceItem
from backend.app.advisor.graph import AdvisorGraph
from backend.app.advisor.output_models import (
    SCHEMA_VERSION,
    SuggestionCard,
    _recommendation_tier,
    tier_gate_snapshot,
)
from backend.app.strategy.registry import get_spec

THSCODE = "601799.SH"
STRATEGY_ID = "trend_20_60_v1"
STRATEGY_REF = f"{STRATEGY_ID}@v2#exp-1"


def _today() -> "datetime.date":
    return (datetime.now(timezone.utc) + timedelta(hours=8)).date()


class _FakeStrategy:
    """只实现顾问实际读取的两个窄接口。"""

    def __init__(self, experiments, decisions=None):
        self._experiments = list(experiments)
        self._decisions = dict(decisions or {})

    def list(self):
        return list(self._experiments)

    def review_decision(self, strategy_id, strategy_version, horizon_days):
        return self._decisions.get((strategy_id, strategy_version, horizon_days))


def _graph(experiments=(), decisions=None) -> AdvisorGraph:
    return AdvisorGraph(model=None, market=None, knowledge=None, web_research=None,
                        ledger=object(), strategy=_FakeStrategy(experiments, decisions))


def _experiment(*, as_of: str | None = None, grade: str = "candidate",
                version: str | None = None, strategy_id: str = STRATEGY_ID,
                thscode: str = THSCODE, entry_id: str = "exp-1") -> dict:
    return {
        "id": entry_id, "strategy_id": strategy_id,
        "strategy_version": version or get_spec(strategy_id).version,
        "thscode": thscode, "validation_grade": grade,
        "result": {"signal_snapshot": {"as_of": as_of}, "metrics": {}, "spec_snapshot": {},
                   "cost_basis": "单边成本假设"},
    }


def _days_ago(days: int) -> str:
    return (_today() - timedelta(days=days)).isoformat()


# --- 候选门禁 -------------------------------------------------------------

def test_no_candidate_row_yields_empty_candidate_set():
    graph = _graph([_experiment(as_of=_days_ago(1), grade="experimental"),
                    _experiment(as_of=_days_ago(1), grade="insufficient", entry_id="exp-2")])
    assert graph._candidate_strategy_experiments(THSCODE, "days_20") == []


def test_strategy_version_mismatch_is_excluded():
    """验证口径升级后，旧版本实验不得继续作为当前顾问的候选依据。"""
    graph = _graph([_experiment(as_of=_days_ago(1), version="v1")])
    assert graph._candidate_strategy_experiments(THSCODE, "days_20") == []


def test_horizon_mismatch_is_excluded():
    graph = _graph([_experiment(as_of=_days_ago(1))])
    assert graph._candidate_strategy_experiments(THSCODE, "days_5") == []
    assert graph._candidate_strategy_experiments(THSCODE, None) == []
    assert len(graph._candidate_strategy_experiments(THSCODE, "days_20")) == 1


def test_other_symbol_candidate_is_excluded():
    graph = _graph([_experiment(as_of=_days_ago(1), thscode="600519.SH")])
    assert graph._candidate_strategy_experiments(THSCODE, "days_20") == []


def test_signal_snapshot_age_boundary_day_seven_passes_day_eight_fails():
    graph = _graph([_experiment(as_of=_days_ago(7), entry_id="day-7"),
                    _experiment(as_of=_days_ago(8), entry_id="day-8")])
    kept = [item["id"] for item in graph._candidate_strategy_experiments(THSCODE, "days_20")]
    assert kept == ["day-7"]


def test_future_signal_snapshot_is_excluded():
    graph = _graph([_experiment(as_of=(_today() + timedelta(days=1)).isoformat())])
    assert graph._candidate_strategy_experiments(THSCODE, "days_20") == []


def test_unparsable_signal_snapshot_is_excluded():
    graph = _graph([_experiment(as_of=None), _experiment(as_of="不是日期", entry_id="exp-2")])
    assert graph._candidate_strategy_experiments(THSCODE, "days_20") == []


def test_candidates_carry_the_narrow_review_decision():
    graph = _graph([_experiment(as_of=_days_ago(1))],
                   decisions={(STRATEGY_ID, "v2", 20): "validated"})
    candidates = graph._candidate_strategy_experiments(THSCODE, "days_20")
    assert candidates[0]["review_decision"] == "validated"


# --- 卡片测试夹具 ---------------------------------------------------------

def _card(*, direction: str = "accumulate") -> SuggestionCard:
    scenarios = []
    if direction != "abstain":
        for name, probability in (("bull", 30.0), ("base", 40.0), ("bear", 30.0)):
            scenarios.append({"name": name, "probability_pct": probability,
                              "probability_source": "subjective",
                              "trigger_condition": f"{name} 情景触发条件",
                              "expected_range_pct": [-5.0, 5.0]})
    return SuggestionCard.model_validate({
        "schema_version": SCHEMA_VERSION, "thscode": THSCODE, "name": "星宇股份",
        "direction": direction, "horizon": "days_20", "scenarios": scenarios,
        "confidence": "low", "claims": [], "key_evidence": [],
        "counter_evidence": ["A1"], "strategy_refs": [],
        "invalidations": ["实验失效即作废。"],
        "watchpoints": ["收盘价重新站上 MA20。"],
        "missing_for_decision": ["通过候选门禁的策略实验。"] if direction == "abstain" else [],
        "decision_narrative": "守门测试用的建议卡叙述。", "disclaimer_accepted": True,
    })


def _a1_item(*, available: bool = False) -> AdvisorEvidenceItem:
    closes = [100.0 + index * 0.1 for index in range(90)]
    return AdvisorEvidenceItem.model_validate({
        "id": "A1", "dimension": "algorithm", "title": "历史状态基率",
        "text": "历史状态基率（测试用）。", "thscode": THSCODE,
        "base_rate_available": available,
        "algorithm_data": {"closes": closes, "return_closes": closes},
        "sources": [{"kind": "market", "label": "本机行情仓库"}]})


def _s1_item(*, validated: bool = False) -> AdvisorEvidenceItem:
    return AdvisorEvidenceItem.model_validate({
        "id": "S1", "dimension": "strategy", "title": "候选策略实验",
        "text": f"{STRATEGY_REF}：候选策略（测试用）。", "thscode": THSCODE,
        "sources": [{"kind": "strategy", "label": f"本地策略实验：{STRATEGY_REF}",
                     "ref": STRATEGY_REF,
                     "strategy_review_decision": "validated" if validated else None}]})


# --- 方向降级改写 ---------------------------------------------------------

def test_missing_candidates_force_wait_for_directional_card():
    graph = _graph()
    card = graph._enrich_card(_card(direction="accumulate"), [_a1_item()])

    assert card.direction == "wait"
    assert card.confidence == "very_low"
    assert card.strategy_refs == []
    assert card.confidence_basis["strategy_grade"] == "none"


def test_abstain_is_never_rewritten_into_wait():
    """拒答不是等待：缺候选时保持 abstain，不凭空造出方向。"""
    graph = _graph()
    card = graph._enrich_card(_card(direction="abstain"), [_a1_item()])

    assert card.direction == "abstain"
    assert card.confidence == "very_low"
    assert card.scenarios == []
    assert _recommendation_tier(card.model_dump())[0] == "data_insufficient"


def test_abstain_is_not_rewritten_by_unresolved_conflicts_either():
    graph = _graph()
    card = graph._enrich_card(_card(direction="abstain"), [_a1_item()],
                              conflicts=["某文档宣称策略有效，但本机没有通过候选门禁的实验"])

    assert card.direction == "abstain"
    assert card.confidence == "very_low"


def test_unresolved_conflicts_downgrade_directional_card_to_wait():
    graph = _graph()
    card = graph._enrich_card(_card(direction="accumulate"),
                              [_a1_item(), _s1_item()], conflicts=["冲突示例"])

    assert card.direction == "wait"
    assert card.confidence == "very_low"
    assert card.confidence_basis["unresolved_conflicts"] is True


def test_candidates_present_keeps_direction_but_caps_confidence_at_medium():
    graph = _graph()
    card = graph._enrich_card(_card(direction="accumulate"),
                              [_a1_item(available=True), _s1_item(validated=True)])

    assert card.direction == "accumulate"
    assert card.confidence == "medium"
    assert card.confidence_basis["strategy_grade"] == "candidate"


# --- 档位天花板 -----------------------------------------------------------

@pytest.mark.parametrize("validated", [False, True])
@pytest.mark.parametrize("available", [False, True])
def test_enriched_card_never_reaches_recommended(validated: bool, available: bool):
    """最高档位守卫：`_enrich_card()` 从不写 forward_calibration/portfolio_validation。

    因此无论输入多有利，本机产出的建议卡都不可能到 recommended 或
    strong_recommended——这是 F1 档位天花板的可执行形式。
    """
    graph = _graph()
    card = graph._enrich_card(_card(direction="accumulate"),
                              [_a1_item(available=available), _s1_item(validated=validated)])

    basis = card.confidence_basis
    assert basis["forward_calibration"] == "not_available"
    assert basis.get("portfolio_validation") != "passed"

    tier, _ = _recommendation_tier(card.model_dump())
    assert tier not in {"recommended", "strong_recommended"}
    assert tier == "trial_buy_signal"

    snapshot = tier_gate_snapshot(card.model_dump())
    assert snapshot["tier"] == tier
    assert snapshot["gates"]["forward_calibration"] == "not_available"


@pytest.mark.parametrize("basis,expected", [
    ({}, "observe"),
    ({"candidate_experiments": [STRATEGY_REF]}, "trial_buy_signal"),
    ({"candidate_experiments": [STRATEGY_REF], "strategy_validation": "validated"},
     "trial_buy_signal"),
    ({"candidate_experiments": [STRATEGY_REF], "strategy_validation": "validated",
      "forward_calibration": "not_available"}, "trial_buy_signal"),
])
def test_tier_ceiling_without_forward_calibration(basis: dict, expected: str):
    tier, _ = _recommendation_tier({"direction": "accumulate", "confidence_basis": basis})
    assert tier == expected
    assert tier not in {"recommended", "strong_recommended"}


def test_strong_recommended_requires_portfolio_validation():
    """即使前向校准齐备，缺组合层验证也只能到 recommended。"""
    full = {"candidate_experiments": [STRATEGY_REF], "strategy_validation": "validated",
            "forward_calibration": "available", "final_level": "high"}
    assert _recommendation_tier({"direction": "accumulate", "confidence_basis": full})[0] == "recommended"
    assert _recommendation_tier({"direction": "accumulate", "confidence_basis": {
        **full, "portfolio_validation": "passed"}})[0] == "strong_recommended"


@pytest.mark.parametrize("direction,expected", [
    ("abstain", "data_insufficient"),
    ("avoid", "short_term_not_considered"),
    ("reduce", "short_term_not_considered"),
    ("wait", "observe"),
    ("hold", "observe"),
])
def test_tier_never_escalates_for_non_directional_cards(direction: str, expected: str):
    card = _card(direction=direction)
    tier, _ = _recommendation_tier({**card.model_dump(),
                                    "confidence_basis": {"candidate_experiments": [STRATEGY_REF],
                                                         "strategy_validation": "validated",
                                                         "forward_calibration": "available",
                                                         "portfolio_validation": "passed",
                                                         "final_level": "high"}})
    assert tier == expected
