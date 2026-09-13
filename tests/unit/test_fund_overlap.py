from backend.app.analysis.fund_overlap import FundDisclosure,FundHolding,visible_overlap
def test_same_disclosure_weighted_overlap_is_visible_lower_bound():
    a=FundDisclosure(fund_id="a",report_end="2024Q4",weight_definition_confirmed=True,holdings=[FundHolding(security_id="600000",market="SH",weight="0.2"),FundHolding(security_id="000001",market="SZ",weight="0.1")])
    b=FundDisclosure(fund_id="b",report_end="2024Q4",weight_definition_confirmed=True,holdings=[FundHolding(security_id="600000",market="SH",weight="0.15")])
    assert visible_overlap(a,b)["overlap_lower_bound"]=="0.15"
def test_different_period_or_market_does_not_fake_overlap():
    a=FundDisclosure(fund_id="a",report_end="2024Q3",holdings=[FundHolding(security_id="000001",market="SZ")]); b=FundDisclosure(fund_id="b",report_end="2024Q4",holdings=[FundHolding(security_id="000001",market="SH")])
    assert visible_overlap(a,b)["status"]=="not_comparable"
