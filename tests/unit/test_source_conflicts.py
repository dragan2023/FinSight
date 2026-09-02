"""Cross-source conflict rules (plan 04 section 2.4 / section 4)."""

from __future__ import annotations

import asyncio

import pytest

from backend.app.errors import AppError
from backend.app.market_data.source_conflicts import (
    MergeDecision,
    comparability_key,
    compare_supplement,
    resolve_by_choice,
    valuation_percentile,
)

run = asyncio.run


def key(**overrides):
    base = dict(entity="600519.SH", period="2023-12-31", currency="CNY", unit="CNY",
                consolidation="consolidated", period_basis="cumulative",
                definition="net_profit", version="2024-04-02")
    base.update(overrides)
    return comparability_key(**base)


class FakeField:
    def __init__(self, value):
        self.value = value


PRIMARY = {"net_profit": FakeField("74700000000")}
SUPPLEMENT = {"net_profit": FakeField("74750000000")}


class TestConflictRules:
    def test_same_metric_comparable_and_primary_missing_fills_with_source(self):
        outcomes = compare_supplement(
            {"net_profit": FakeField(None)}, {"net_profit": FakeField("74750000000")},
            primary_key=key(), supplement_key=key(),
            primary_source="hexin", supplement_source="tushare",
            labels={"net_profit": "净利润"})
        assert outcomes[0].decision == MergeDecision.FILL
        assert outcomes[0].supplement_value == "74750000000"
        assert outcomes[0].supplement_source == "tushare"
        assert "替代来源" in outcomes[0].note

    def test_comparable_conflict_shown_side_by_side_never_merged(self):
        outcomes = compare_supplement(
            PRIMARY, SUPPLEMENT, primary_key=key(), supplement_key=key(),
            primary_source="hexin", supplement_source="tushare")
        assert outcomes[0].decision == MergeDecision.CONFLICT
        assert outcomes[0].primary_value == "74700000000"
        assert outcomes[0].supplement_value == "74750000000"
        assert "并列" in outcomes[0].note and "平均" in outcomes[0].note

    def test_equal_values_consistent(self):
        outcomes = compare_supplement(
            PRIMARY, {"net_profit": FakeField("74700000000")},
            primary_key=key(), supplement_key=key(),
            primary_source="hexin", supplement_source="tushare")
        assert outcomes[0].decision == MergeDecision.CONSISTENT

    @pytest.mark.parametrize("override", [
        {"period": "2024-12-31"},          # 不同期间不自动合并
        {"currency": "USD"},               # 不同币种不比
        {"unit": "x"},                     # 不同单位不比
        {"consolidation": "parent"},       # 合并/母公司口径不同
        {"period_basis": "single_quarter"},  # 累计/单季不同
        {"version": "2024-04-20"},         # 披露版本不同不强并
        {"definition": "parent_holder_net_profit"},  # 定义不同
    ])
    def test_incomparable_is_parallel(self, override):
        outcomes = compare_supplement(
            PRIMARY, SUPPLEMENT, primary_key=key(),
            supplement_key=key(**override),
            primary_source="hexin", supplement_source="tushare")
        assert outcomes[0].decision == MergeDecision.PARALLEL
        assert "不合并" in outcomes[0].note

    def test_supplement_definition_absent_in_primary_is_skipped(self):
        outcomes = compare_supplement(
            PRIMARY, {"unknown_metric": FakeField("1")}, primary_key=key(),
            supplement_key=key(), primary_source="hexin", supplement_source="tushare")
        assert outcomes == []

    def test_choice_affects_only_new_report_and_inputs_untouched(self):
        outcomes = compare_supplement(
            PRIMARY, SUPPLEMENT, primary_key=key(), supplement_key=key(),
            primary_source="hexin", supplement_source="tushare")
        resolved = resolve_by_choice(outcomes, {"net_profit": "supplement"})
        assert resolved == {"net_profit": "74750000000"}
        # 原输入（历史证据代理）不被修改：primary 仍是原值
        assert PRIMARY["net_profit"].value == "74700000000"
        # 默认（未选择）保留主源
        assert resolve_by_choice(outcomes) == {"net_profit": "74700000000"}


class TestPercentileHonesty:
    def test_short_window_labelled_by_actual_days(self):
        meta = valuation_percentile(list(range(1, 251)), 200, indicator="pe_ttm",
                                    span_days=250)
        assert "250个交易日" in meta.window_label
        assert "十年" not in meta.window_label
        assert meta.valid_days == 250
        assert meta.formula and meta.negative_handling

    def test_full_window_may_claim_ten_years(self):
        meta = valuation_percentile(list(range(1, 2600)), 1300, indicator="pe_ttm",
                                    span_days=3800)
        assert "近十年及以上" in meta.window_label

    def test_missing_ratio_and_math(self):
        series = [10, 20, None, 30, 40, "bad"]
        meta = valuation_percentile(series, 30, indicator="pb_mrq", span_days=300)
        assert meta.valid_days == 4
        assert meta.percentile == "75"
        # 缺失比例：(6-4)/6 = 0.333...
        assert meta.missing_ratio.startswith("0.3")

    def test_negative_values_participate(self):
        meta = valuation_percentile([-5, 10, 20, 30], -2, indicator="pe_ttm", span_days=100)
        assert meta.percentile == "25"
        assert meta.negative_handling == "负值参与排名"

    def test_insufficient_samples_reports_unknown(self):
        meta = valuation_percentile([None, None], None, indicator="pe_ttm", span_days=30)
        assert meta.percentile == "unknown" and "不足" in meta.note
