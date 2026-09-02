"""Mapping-candidate extraction unit tests (plan 04 §2.5, batch 7).

确定性规则提取：零模型零网络；宁缺毋滥（裸 6 位数字、无后缀代码不作候选）。
"""

from __future__ import annotations

from backend.app.research.mapping_candidates import MappingCandidates, extract_mapping_candidates


def test_extracts_all_fields_from_typical_report_text():
    text = "贵州茅台（600519.SH）2024年第一季度报告：营业收入按人民币万元列示。"
    result = extract_mapping_candidates(text)
    assert result.security_id == "600519.SH"
    assert result.period == "2024Q1"
    assert result.unit == "CNY"
    assert result.note == "金额以万元计"


def test_prefix_style_and_lowercase_suffix_are_canonicalized():
    assert extract_mapping_candidates("见 sh600519 的披露。").security_id == "600519.SH"
    assert extract_mapping_candidates("见 000001.sz 的披露。").security_id == "000001.SZ"
    assert extract_mapping_candidates("见 bj835185 公告。").security_id == "835185.BJ"


def test_period_variants_canonicalized():
    cases = {
        "2024Q1 数据": "2024Q1",
        "2024 q3": "2024Q3",
        "2024年第三季度": "2024Q3",
        "2024年三季报": "2024Q3",
        "2025年中报": "2025H1",
        "2024年半年度报告": "2024H1",
        "2024年年报": "2024FY",
        "2024年度审计": "2024FY",
    }
    for text, expected in cases.items():
        assert extract_mapping_candidates(text).period == expected, text


def test_bare_six_digits_and_suffixless_codes_are_not_candidates():
    assert extract_mapping_candidates("编号 600519 的档案").security_id is None
    assert extract_mapping_candidates("金额 600519 元").security_id is None
    assert extract_mapping_candidates("600519 未标注交易所").security_id is None


def test_no_matches_returns_all_none():
    result = extract_mapping_candidates("会议要点：资本开支维持克制。")
    assert result == MappingCandidates()


def test_empty_text_is_safe():
    assert extract_mapping_candidates("").model_dump() == {
        "security_id": None, "period": None, "unit": None, "note": None}


def test_first_match_wins_deterministically():
    text = "先提 000001.SZ，后提 600519.SH；2024年报在前，2023年报在后。"
    result = extract_mapping_candidates(text)
    assert result.security_id == "000001.SZ"
    assert result.period == "2024FY"


def test_currency_variants():
    assert extract_mapping_candidates("以美元计价").unit == "USD"
    assert extract_mapping_candidates("denominated in HKD").unit == "HKD"
    assert extract_mapping_candidates("单位：RMB").unit == "CNY"
    assert extract_mapping_candidates("以欧元计价").unit is None


def test_candidates_model_forbids_extra_fields():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        MappingCandidates(security_id="600519.SH", unknown="x")
