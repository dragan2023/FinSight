"""JSON parsing pipeline tests (plan 03 section 4, item 1)."""

from __future__ import annotations

import json

import pytest

from backend.app.agent.json_parser import (
    CLEANUP_BOM,
    CLEANUP_BOUNDARY,
    CLEANUP_DOUBLE_DECODE,
    CLEANUP_DAMAGED_TAIL,
    CLEANUP_FENCE,
    JsonRejected,
    parse_section_object,
    parse_complete_section_objects,
    parse_tool_arguments,
)

REPORT = {"title": "财务质量", "claims": [{"text": "毛利率高", "evidence_refs": ["e1"]}]}
TICK = chr(96)


def fenced(body: str, tag: str = "json") -> str:
    return TICK * 3 + tag + "\n" + body + "\n" + TICK * 3


def test_canonical_json_parses_without_cleanup():
    text = json.dumps(REPORT, ensure_ascii=False)
    value, cleanups = parse_section_object(text)
    assert value == REPORT
    assert cleanups == []


def test_bom_is_stripped_and_recorded():
    value, cleanups = parse_section_object("\ufeff" + json.dumps(REPORT, ensure_ascii=False))
    assert value == REPORT
    assert CLEANUP_BOM in cleanups


def test_surrounding_json_fence_is_stripped_and_recorded():
    value, cleanups = parse_section_object(fenced(json.dumps(REPORT, ensure_ascii=False)))
    assert value == REPORT
    assert cleanups == [CLEANUP_FENCE]


def test_plain_fence_without_language_tag():
    value, cleanups = parse_section_object(fenced(json.dumps(REPORT, ensure_ascii=False), tag=""))
    assert value == REPORT
    assert CLEANUP_FENCE in cleanups


def test_partial_fence_falls_back_to_boundary_extraction():
    text = fenced(json.dumps(REPORT, ensure_ascii=False)) + "\n以上是章节内容。"
    value, cleanups = parse_section_object(text)
    assert value == REPORT
    assert cleanups == [CLEANUP_BOUNDARY]


def test_prose_around_one_candidate_is_extracted():
    text = "以下是章节 JSON：\n" + json.dumps(REPORT, ensure_ascii=False) + "\n以上内容供审阅。"
    value, cleanups = parse_section_object(text)
    assert value == REPORT
    assert cleanups == [CLEANUP_BOUNDARY]


def test_braces_quotes_and_escapes_inside_strings_survive():
    inner = {"summary": "包含 { } 与 \"引号\" 的多行\n文本", "code": "if (a) { return \"{\"; }"}
    text = "报告开始 " + json.dumps(inner, ensure_ascii=False) + " 报告结束"
    value, cleanups = parse_section_object(text)
    assert value == inner
    assert cleanups == [CLEANUP_BOUNDARY]


def test_one_extra_json_string_encoding_is_decoded_once():
    encoded = json.dumps(json.dumps(REPORT, ensure_ascii=False), ensure_ascii=False)
    value, cleanups = parse_section_object(encoded)
    assert value == REPORT
    assert cleanups == [CLEANUP_DOUBLE_DECODE]


def test_double_encoding_requires_the_whole_payload():
    # Double decoding applies when the whole payload is a JSON string;
    # quoted fragments inside prose are never treated as candidates.
    encoded = json.dumps(json.dumps(REPORT, ensure_ascii=False), ensure_ascii=False)
    value, cleanups = parse_section_object(encoded)
    assert value == REPORT
    assert cleanups == [CLEANUP_DOUBLE_DECODE]


def test_triple_encoding_is_rejected():
    triple = json.dumps(json.dumps(json.dumps(REPORT, ensure_ascii=False), ensure_ascii=False), ensure_ascii=False)
    with pytest.raises(JsonRejected) as caught:
        parse_section_object(triple)
    assert caught.value.reason == "double_encoded_rejected"


def test_two_competing_objects_are_rejected():
    text = json.dumps({"a": 1}, ensure_ascii=False) + "\n" + json.dumps({"b": 2}, ensure_ascii=False)
    with pytest.raises(JsonRejected) as caught:
        parse_section_object(text)
    assert caught.value.reason == "multiple_candidates"


def test_duplicate_keys_are_rejected_even_inside_prose():
    text = '说明：{"a": 1, "a": 2} 完'
    with pytest.raises(JsonRejected) as caught:
        parse_section_object(text)
    assert caught.value.reason == "duplicate_keys"


def test_nan_and_infinity_are_rejected():
    with pytest.raises(JsonRejected) as nan:
        parse_section_object("{\"value\": NaN}")
    assert nan.value.reason == "invalid_constant"
    with pytest.raises(JsonRejected) as inf:
        parse_section_object(json.dumps({"a": 1})[:-1] + ", \"b\": Infinity}")
    assert inf.value.reason == "invalid_constant"


def test_excessive_depth_is_rejected():
    value = 1
    for _ in range(40):
        value = {"nested": value}
    with pytest.raises(JsonRejected) as caught:
        parse_section_object(json.dumps(value))
    assert caught.value.reason == "too_deep"


def test_oversized_content_is_rejected():
    text = json.dumps({"body": "x" * 3000}, ensure_ascii=False)
    with pytest.raises(JsonRejected) as caught:
        parse_section_object(text, max_bytes=1024)
    assert caught.value.reason == "oversized"


def test_unterminated_string_and_missing_bracket_fail_without_guessing():
    with pytest.raises(JsonRejected) as unterminated:
        parse_section_object("{\"summary\": \"未闭合的字符串")
    assert unterminated.value.reason == "no_complete_candidate"
    with pytest.raises(JsonRejected) as missing:
        parse_section_object("{\"a\": {\"b\": 1")
    assert missing.value.reason == "no_complete_candidate"


def test_truncated_stream_never_parses_as_partial():
    full = json.dumps(REPORT, ensure_ascii=False)
    with pytest.raises(JsonRejected) as caught:
        parse_section_object(full[: len(full) // 2])
    assert caught.value.reason == "no_complete_candidate"


def test_lone_surrogate_in_value_is_rejected():
    text = '{"summary": "\\ud800 独立代理项"}'
    with pytest.raises(JsonRejected) as caught:
        parse_section_object(text)
    assert caught.value.reason == "invalid_unicode"


def test_non_object_roots_are_rejected():
    for text in ("[1, 2, 3]", "42", "null"):
        with pytest.raises(JsonRejected) as caught:
            parse_section_object(text)
        assert caught.value.reason == "not_object"
    # A JSON string whose inner payload is not an object reports the double
    # encoding failure, which is the more precise diagnosis.
    with pytest.raises(JsonRejected) as string_root:
        parse_section_object('"一个字符串"')
    assert string_root.value.reason == "double_encoded_rejected"


def test_empty_content_is_rejected():
    with pytest.raises(JsonRejected) as caught:
        parse_section_object("   \n  ")
    assert caught.value.reason == "empty"


def test_tool_arguments_strict_path_rejects_cleanups():
    assert parse_tool_arguments(json.dumps({"path": "e1", "segment": 2})) == {"path": "e1", "segment": 2}
    with pytest.raises(JsonRejected):
        parse_tool_arguments(fenced(json.dumps({"path": "e1"})))
    with pytest.raises(JsonRejected):
        parse_tool_arguments("参数：" + json.dumps({"path": "e1"}))
    with pytest.raises(JsonRejected) as encoded:
        parse_tool_arguments(json.dumps(json.dumps({"path": "e1"}, ensure_ascii=False), ensure_ascii=False))
    assert encoded.value.reason == "not_object"
    with pytest.raises(JsonRejected) as dup:
        parse_tool_arguments("{\"path\": \"a\", \"path\": \"b\"}")
    assert dup.value.reason == "duplicate_keys"


def test_cleanup_never_changes_content_meaning():
    text = json.dumps(REPORT, ensure_ascii=False)
    direct, direct_cleanups = parse_section_object(text)
    wrapped, wrapped_cleanups = parse_section_object("前文 " + text + " 后文")
    assert direct == wrapped == REPORT
    assert direct_cleanups == [] and wrapped_cleanups == [CLEANUP_BOUNDARY]


def test_complete_objects_before_a_damaged_tail_are_preserved_without_repairing_values():
    first = {"id": "facts", "summary": "完整章节"}
    second = '{"id":"risk","summary":"连接中断'
    values, cleanups = parse_complete_section_objects(json.dumps(first, ensure_ascii=False) + "\n" + second)
    assert values == [first]
    assert cleanups == [CLEANUP_DAMAGED_TAIL]


def test_complete_object_recovery_never_accepts_a_broken_object_as_a_partial_value():
    with pytest.raises(JsonRejected) as caught:
        parse_complete_section_objects('{"id":"facts","claims":[{"text":"未闭合"')
    assert caught.value.reason == "no_complete_candidate"
