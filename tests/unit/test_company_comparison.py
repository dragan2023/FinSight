"""Stage 05 batch 1: comparison group and accounting-boundary rules."""

import pytest

from backend.app.analysis.company_comparison import ComparisonCompany, build_comparison, validate_group
from backend.app.research.comparison import collect_selected_peers


def company(security_id: str, **overrides) -> ComparisonCompany:
    data = {
        "security_id": security_id, "thscode": f"00000{security_id}.SZ", "name": f"公司{security_id}",
        "period": "2024-FY", "currency": "CNY", "unit": "CNY", "consolidation": "consolidated",
        "period_basis": "annual_report", "values": {"revenue_yoy": "0.1", "pe_ttm": "12", "pb_mrq": "2"},
    }
    data.update(overrides)
    return ComparisonCompany.model_validate(data)


def test_group_must_be_explicit_two_to_ten_unique_companies():
    with pytest.raises(ValueError, match="2 至 10"):
        validate_group([company("1")])
    with pytest.raises(ValueError, match="重复"):
        validate_group([company("1"), company("1")])
    validate_group([company("1"), company("2")])
    with pytest.raises(ValueError, match="2 至 10"):
        validate_group([company(str(index)) for index in range(11)])


@pytest.mark.parametrize("key,value", [
    ("period", "2023-FY"), ("currency", "USD"), ("unit", "USD"),
    ("consolidation", "parent"), ("period_basis", "quarterly_cumulative_unknown"),
])
def test_mismatched_accounting_keys_stay_parallel(key, value):
    table = build_comparison([company("1"), company("2", **{key: value})], ["revenue_yoy"])
    assert table.common_period is None
    assert table.comparable_count == 0
    assert table.rankings == {}
    assert "不一致" in table.gaps[0]


def test_loss_pe_and_negative_equity_pb_are_not_ranked_as_cheap():
    loss = company("1", values={"pe_ttm": "-8", "pb_mrq": "3", "parent_holder_net_profit": "-1",
                                  "holder_equity_total": "5"})
    negative_equity = company("2", values={"pe_ttm": "10", "pb_mrq": "1", "parent_holder_net_profit": "2",
                                             "holder_equity_total": "-1"})
    table = build_comparison([loss, negative_equity], ["pe_ttm", "pb_mrq"])
    assert table.rows[0].cells["pe_ttm"].status == "not_applicable"
    assert table.rows[1].cells["pb_mrq"].status == "not_applicable"
    assert table.rankings == {"pe_ttm": ["2"], "pb_mrq": ["1"]}


def test_partial_sample_keeps_missing_company_and_labels_ranking_as_group_only():
    table = build_comparison([company("1"), company("2", values={"revenue_yoy": None})], ["revenue_yoy"])
    assert table.rows[1].cells["revenue_yoy"].status == "no_data"
    assert table.rankings["revenue_yoy"] == ["1"]
    assert table.selected_count == 2 and table.comparable_count == 2


class FakeResult:
    def __init__(self, name: str):
        self.name = name
        self.gaps = []

    def model_dump(self):
        return {"result": self.name}


class FakeMarketData:
    def __init__(self):
        self.requested = []

    def require_identity(self, thscode: str):
        self.requested.append(("identity", thscode))

    async def fetch_all_statements(self, thscode: str, **kwargs):
        self.requested.append(("statements", thscode, kwargs["security_id"]))
        return [FakeResult("income"), FakeResult("balance"), FakeResult("cashflow")]

    async def fetch_valuations(self, thscodes: list[str], **kwargs):
        self.requested.append(("valuation", thscodes[0], kwargs["security_id"]))
        return FakeResult("valuation")


@pytest.mark.asyncio
async def test_collection_fetches_only_explicit_peers_and_uses_their_security_ids():
    service = FakeMarketData()
    rows = await collect_selected_peers(service, [company("1"), company("2")])
    assert [item.company.security_id for item in rows] == ["1", "2"]
    assert service.requested == [
        ("identity", "000001.SZ"), ("statements", "000001.SZ", "1"), ("valuation", "000001.SZ", "1"),
        ("identity", "000002.SZ"), ("statements", "000002.SZ", "2"), ("valuation", "000002.SZ", "2"),
    ]
