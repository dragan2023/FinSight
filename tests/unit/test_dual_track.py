"""C1 双轨独立结算守卫测试（方案 02 §4 的验收标准）。

守的核心是**一条**：`advisor/graph.py:295` 的 `_enrich_card()` 会把"候选为空/存在冲突"
改写成 `wait`，于是"模型原本说了什么"在登记时就没了。本测试确保：

1. AI 轨记的是**门禁改写之前**的原始裁决，绝不拿被修剪过的卡片冒充；
2. 策略轨与 AI 轨是**两条独立记录**，可各自结算、互不依赖；
3. 弃权（wait/hold/abstain）**不计入方向命中率**，只计入等待/拒答比例；
4. 结算只用**本地总收益序列**，不联网、口径自证；
5. 已结算的轨道**不可改写**（方案 02 §10：不得为更好统计而修改已封存预测）；
6. 边际贡献 = 两轨差值；**样本不足时明确拒绝下结论**（G8 的 ≥30 条）。

全部离线：临时 SQLite + 假行情仓库，不访问网络。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.advisor.dual_track import DualTrackLedger, wilson_interval
from backend.app.advisor.ledger import AdvisorLedger
from backend.app.errors import AppError
from backend.app.governance.service import GovernanceService
from backend.app.storage import Storage
from backend.app.strategy.registry import get_spec

THSCODE = "601799.SH"
DAY_MS = 86_400_000
CST = timezone(timedelta(hours=8))
#: 信号新鲜度门禁要求 as_of 距登记日 ≤7 天，所以候选信号必须用"最近"的日期。
TODAY = datetime.now(CST).strftime("%Y-%m-%d")


@pytest.fixture
def storage(tmp_path):
    storage = Storage(tmp_path / "dual.db")
    storage.initialize()
    return storage


@pytest.fixture
def batch(storage):
    return GovernanceService(storage).open_batch(name="双轨测试批次")


class _FakeRepository:
    """只实现双轨结算用到的那一个方法：本地总收益序列。"""

    def __init__(self, *, daily_return_pct: float = 0.0, start: str = "2025-01-01",
                 count: int = 900) -> None:
        self._start = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
        level = 100.0
        self._closes: list[tuple[int, float]] = []
        for index in range(count):
            date_ms = int((self._start + timedelta(days=index)).timestamp() * 1000)
            self._closes.append((date_ms, level))
            level *= (1 + daily_return_pct / 100.0)

    def corporate_action_total_return_history(self, thscode: str, start_ms: int, end_ms: int) -> dict:
        items, previous = [], None
        for date_ms, close in self._closes:
            if date_ms > end_ms:
                break
            factor = 1.0 if previous is None else close / previous
            items.append({"date_ms": date_ms, "open_price": close, "close_price": close,
                          "previous_close": previous, "event_count": 0,
                          "cash_dividend_per_share": 0.0, "bonus_share_per_share": 0.0,
                          "rights_ratio": 0.0, "rights_cash_cost_per_share": 0.0,
                          "raw_close_return_factor": factor, "total_return_factor": factor})
            previous = close
        return {"basis": "corporate_action_total_return_v1", "items": items,
                "applied_event_count": 0, "unmatched_event_count": 0,
                "unmatched_event_dates": [], "invalid_factor_count": 0}


def _card(*, direction="wait", strategy_refs=(), horizon="days_20") -> dict:
    return {"schema_version": "finsight.suggestion.v2", "thscode": THSCODE, "name": "星宇股份",
            "direction": direction, "horizon": horizon, "scenarios": [], "confidence": "very_low",
            "claims": [], "key_evidence": [], "counter_evidence": ["A1"],
            "strategy_refs": list(strategy_refs), "confidence_basis": {},
            "invalidations": ["实验失效即作废。"], "watchpoints": ["收盘价重新站上 MA20。"],
            "missing_for_decision": [], "decision_narrative": "双轨测试用卡。",
            "disclaimer_accepted": True}


def _verdict(**overrides) -> dict:
    verdict = {"direction": "accumulate", "confidence": "medium",
               "research_prediction": {"p_up": 60.0, "p_flat": 25.0, "p_down": 15.0,
                                       "return_threshold_pct": 0.2}}
    verdict.update(overrides)
    return verdict


def _candidate_ref() -> str:
    return f"trend_20_60_v1@{get_spec('trend_20_60_v1').version}#exp-1"


def _insert_candidate_experiment(storage: Storage, *, as_of: str = TODAY,
                                 windows: int = 40, horizon: int = 20) -> str:
    """一条通过候选门禁的实验，带确定性信号快照与足够多的非重叠窗口。"""
    curve = [{"period": f"bar-{index}", "net_value": round(1 + 0.001 * index, 6),
              "benchmark_net_value": 1.0, "drawdown_pct": 0.0, "position": 1}
             for index in range(windows * horizon + 1)]
    result = {"curve": curve, "trades": [],
              "signal_snapshot": {"as_of": as_of, "action": "enter", "current_position": 0,
                                  "next_position": 1, "volatility_gate_passed": True}}
    with storage.connect() as db:
        db.execute("INSERT INTO strategy_experiments VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   ("exp-1", "trend_20_60_v1", get_spec("trend_20_60_v1").version, THSCODE,
                    json.dumps({"one_way_cost_bps": 10, "thscode": THSCODE,
                                "strategy_id": "trend_20_60_v1", "start_ms": 1, "end_ms": 2}),
                    json.dumps({}), json.dumps({}), json.dumps(result), "candidate", "hash",
                    "2026-01-01T00:00:00+00:00"))
    return _candidate_ref()


# --- 封存 -----------------------------------------------------------------

def test_register_seals_two_tracks_sharing_one_window(storage, batch):
    entry = AdvisorLedger(storage).register(_card(), raw_verdict=_verdict())
    records = DualTrackLedger(storage).for_advice(entry["id"])

    assert [item["track"] for item in records] == ["ai", "strategy"]
    assert len({item["as_of_date"] for item in records}) == 1
    assert all(item["batch_id"] == batch["id"] for item in records)
    assert all(item["status"] == "sealed" for item in records)


def test_ai_track_records_the_pre_gate_verdict_not_the_gated_card(storage, batch):
    """**本文件最重要的一条**：卡片已被门禁改写成 wait，AI 轨仍必须记 accumulate。"""
    entry = AdvisorLedger(storage).register(_card(direction="wait"),
                                            raw_verdict=_verdict(direction="accumulate"))
    ai = next(item for item in DualTrackLedger(storage).for_advice(entry["id"])
              if item["track"] == "ai")

    assert ai["direction"] == "accumulate"      # 原始裁决
    assert entry["direction"] == "wait"          # 被门禁改写后的卡片
    assert ai["probability"]["p_up"] == 60.0
    assert ai["eligible"] is True


def test_ai_track_is_ineligible_without_a_raw_verdict(storage, batch):
    """拿不到原始裁决时如实记为不可结算，绝不用被改写的卡片充当 AI 轨。"""
    entry = AdvisorLedger(storage).register(_card(direction="wait"))
    ai = next(item for item in DualTrackLedger(storage).for_advice(entry["id"])
              if item["track"] == "ai")

    assert ai["eligible"] is False
    assert ai["direction"] == "abstain"
    assert "原始 LLM 裁决" in ai["ineligible_reason"]


def test_strategy_track_records_candidate_signal(storage, batch):
    ref = _insert_candidate_experiment(storage)
    entry = AdvisorLedger(storage).register(_card(strategy_refs=[ref]), raw_verdict=_verdict())
    strategy = next(item for item in DualTrackLedger(storage).for_advice(entry["id"])
                    if item["track"] == "strategy")

    assert strategy["direction"] == "accumulate"
    assert strategy["eligible"] is True
    assert strategy["probability"]["probability"] == 1.0  # 单调上升曲线 → 窗口全命中
    assert strategy["as_of_basis"] == "candidate_signal_snapshot"
    assert strategy["as_of_date"] == TODAY


def test_strategy_track_abstains_without_candidates(storage, batch):
    entry = AdvisorLedger(storage).register(_card(), raw_verdict=_verdict())
    strategy = next(item for item in DualTrackLedger(storage).for_advice(entry["id"])
                    if item["track"] == "strategy")

    assert strategy["eligible"] is False
    assert strategy["direction"] == "abstain"
    assert "没有通过候选门禁" in strategy["ineligible_reason"]


# --- 独立结算 -------------------------------------------------------------

def test_tracks_settle_independently(storage, batch):
    ref = _insert_candidate_experiment(storage)
    entry = AdvisorLedger(storage).register(_card(strategy_refs=[ref]), raw_verdict=_verdict())
    ledger = DualTrackLedger(storage)
    records = {item["track"]: item for item in ledger.for_advice(entry["id"])}
    repository = _FakeRepository(daily_return_pct=0.3)  # 20 日约 +6.2%

    settled = ledger.settle(records["strategy"]["id"], repository)

    assert settled["status"] == "settled"
    assert settled["settlement"]["hit"] is True
    assert settled["settlement"]["outcome"] == "up"
    assert settled["settlement"]["basis"] == "local_total_return_close"
    # AI 轨完全不受影响，仍是 sealed —— 两轨互不依赖。
    remaining = {item["track"]: item for item in ledger.for_advice(entry["id"])}
    assert remaining["ai"]["status"] == "sealed"
    assert remaining["ai"]["settlement"] is None


def test_ai_track_brier_uses_the_binary_event_for_comparability(storage, batch):
    entry = AdvisorLedger(storage).register(_card(), raw_verdict=_verdict())
    ledger = DualTrackLedger(storage)
    ai = next(item for item in ledger.for_advice(entry["id"]) if item["track"] == "ai")

    settled = ledger.settle(ai["id"], _FakeRepository(daily_return_pct=0.3))

    # p_up=60% 且实际上涨 → (0.6-1)^2 = 0.16
    assert settled["settlement"]["binary_brier"] == pytest.approx(0.16)
    # 三分类 Brier 同时给出：(0.6-1)^2+(0.25-0)^2+(0.15-0)^2 = 0.245
    assert settled["settlement"]["three_class_brier"] == pytest.approx(0.245)


def test_abstention_is_excluded_from_hit_rate_but_counted_separately(storage, batch):
    ref = _insert_candidate_experiment(storage)
    entry = AdvisorLedger(storage).register(_card(strategy_refs=[ref]),
                                            raw_verdict=_verdict(direction="wait"))
    ledger = DualTrackLedger(storage)
    repository = _FakeRepository(daily_return_pct=0.3)
    for record in ledger.for_advice(entry["id"]):
        ledger.settle(record["id"], repository)

    report = ledger.margin_report()
    ai = report["tracks"]["ai"]
    assert ai["directional_samples"] == 0     # wait 是弃权
    assert ai["hit_rate"] is None
    assert ai["abstained"] == 1
    assert ai["abstention_rate"] == 1.0
    # 策略轨有方向，正常计入
    assert report["tracks"]["strategy"]["directional_samples"] == 1


def test_settlement_refuses_when_the_horizon_window_is_incomplete(storage, batch):
    entry = AdvisorLedger(storage).register(_card(), raw_verdict=_verdict())
    ledger = DualTrackLedger(storage)
    ai = next(item for item in ledger.for_advice(entry["id"]) if item["track"] == "ai")

    with pytest.raises(AppError) as excinfo:
        ledger.settle(ai["id"], _FakeRepository(daily_return_pct=0.3, count=10))
    assert excinfo.value.category == "data_unavailable"
    assert "尚未齐备" in excinfo.value.message


def test_settled_track_cannot_be_rewritten(storage, batch):
    entry = AdvisorLedger(storage).register(_card(), raw_verdict=_verdict())
    ledger = DualTrackLedger(storage)
    ai = next(item for item in ledger.for_advice(entry["id"]) if item["track"] == "ai")
    ledger.settle(ai["id"], _FakeRepository(daily_return_pct=0.3))

    with pytest.raises(sqlite3.IntegrityError, match="track_settlement_immutable"):
        with storage.connect() as db:
            db.execute("UPDATE track_settlements SET direction='avoid', settlement_json=? "
                       "WHERE id=?", (json.dumps({"hit": False}), ai["id"]))


def test_settled_track_cannot_be_deleted(storage, batch):
    entry = AdvisorLedger(storage).register(_card(), raw_verdict=_verdict())
    ledger = DualTrackLedger(storage)
    ai = next(item for item in ledger.for_advice(entry["id"]) if item["track"] == "ai")
    ledger.settle(ai["id"], _FakeRepository(daily_return_pct=0.3))

    with pytest.raises(sqlite3.IntegrityError, match="track_settlement_no_delete"):
        with storage.connect() as db:
            db.execute("DELETE FROM track_settlements WHERE id=?", (ai["id"],))


def test_settle_due_requires_explicit_confirmation(storage, batch):
    entry = AdvisorLedger(storage).register(_card(), raw_verdict=_verdict())
    ledger = DualTrackLedger(storage)
    with pytest.raises(AppError) as excinfo:
        ledger.settle_due(_FakeRepository(), confirmed=False)
    assert excinfo.value.category == "confirmation_required"
    assert entry["id"]


# --- 边际贡献报告 ---------------------------------------------------------

def _insert_settled(storage: Storage, *, track: str, hits: int, total: int,
                    brier: float = 0.25, batch_id: str | None = None) -> None:
    """直接写入已结算轨道，用于构造样本量条件（报告只读这些行）。"""
    with storage.connect() as db:
        for index in range(total):
            settled = index < hits
            db.execute(
                "INSERT INTO track_settlements(id, advice_id, batch_id, track, thscode, "
                "horizon_days, as_of_date, as_of_basis, due_date, direction, confidence, "
                "probability_json, return_threshold_pct, eligible, ineligible_reason, status, "
                "settled_at, settlement_json, created_at) "
                "VALUES(?,?,?,?,?,20,'2026-01-01','registration_date','2026-03-01','accumulate',"
                "'medium','{}',0.2,1,'','settled','2026-03-01T00:00:00+00:00',?,"
                "'2026-01-01T00:00:00+00:00')",
                (f"{track}-{index}", f"advice-{track}-{index}", batch_id, track, THSCODE,
                 json.dumps({"kind": "dual_track", "hit": settled, "abstained": False,
                             "binary_brier": brier})))


def test_margin_report_delta_is_ai_minus_strategy(storage):
    _insert_settled(storage, track="strategy", hits=20, total=40, brier=0.20)
    _insert_settled(storage, track="ai", hits=24, total=40, brier=0.24)

    report = DualTrackLedger(storage).margin_report()

    assert report["sufficient"] is True
    assert report["tracks"]["strategy"]["hit_rate"] == 0.5
    assert report["tracks"]["ai"]["hit_rate"] == 0.6
    delta = report["marginal_contribution"]
    assert delta["hit_rate"] == pytest.approx(0.1)     # AI 高 10 个百分点
    assert delta["binary_brier"] == pytest.approx(0.04)  # AI 的 Brier 更差


def test_margin_report_refuses_to_conclude_on_a_small_sample(storage):
    _insert_settled(storage, track="strategy", hits=3, total=5)
    _insert_settled(storage, track="ai", hits=4, total=5)

    report = DualTrackLedger(storage).margin_report()

    assert report["sufficient"] is False
    assert "不得据此判断" in report["verdict_note"]
    assert report["tracks"]["ai"]["hit_rate_ci95"] is not None


def test_stop_criterion_settled_30_triggers_when_interval_contains_half(storage):
    """命中率 50%、30 条样本 → 95% 区间包含 50% → 触发预注册的「无方向优势」。"""
    _insert_settled(storage, track="strategy", hits=15, total=30)

    report = DualTrackLedger(storage).evaluate_stop_criteria()

    triggered = {item["criterion"] for item in report["triggered_stop_criteria"]}
    assert "settled_30" in triggered
    assert report["tracks"]["strategy"]["directional_samples"] == 30


def test_stop_criterion_settled_30_does_not_trigger_on_an_obvious_edge(storage):
    _insert_settled(storage, track="strategy", hits=27, total=30)

    report = DualTrackLedger(storage).evaluate_stop_criteria()

    assert all(item["criterion"] != "settled_30" for item in report["triggered_stop_criteria"])
    # 不是纯统计量的条件不假装能自动判
    assert "drift" in report["not_evaluated"]


def test_wilson_interval_is_sane():
    assert wilson_interval(0, 0) is None
    low, high = wilson_interval(15, 30)
    assert low < 0.5 < high
    low, high = wilson_interval(30, 30)
    assert low > 0.85 and high <= 1.0
