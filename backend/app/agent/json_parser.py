"""Strict JSON parsing for model output (plan 03 section 2.4).

Two entry points with different trust levels:
- parse_tool_arguments: complete text + strict json only. Tool execution never
  runs on repaired or extracted JSON; bad arguments return a structured error
  to the matching call id instead.
- parse_section_object: sections may carry a BOM, one fully surrounding code
  fence, explanatory prose around ONE complete JSON candidate, or one extra
  JSON-string encoding. Every allowed cleanup preserves content semantics and
  is recorded. Nothing here "repairs" values or structure.

Always rejected: duplicate keys, NaN/Infinity constants, non-object roots,
excessive depth/collection size, byte-budget overflow, multiple competing
candidates, unterminated text, and content that is not UTF-8 encodable.
No eval, exec, YAML, or quote replacement exists in this pipeline.
"""

from __future__ import annotations

import json

MAX_DEPTH = 32
MAX_NODES = 20000

REASON_EMPTY = "empty"
REASON_OVERSIZED = "oversized"
REASON_INVALID_UNICODE = "invalid_unicode"
REASON_INVALID_JSON = "invalid_json"
REASON_DUPLICATE_KEYS = "duplicate_keys"
REASON_INVALID_CONSTANT = "invalid_constant"
REASON_NOT_OBJECT = "not_object"
REASON_TOO_DEEP = "too_deep"
REASON_TOO_MANY_NODES = "too_many_nodes"
REASON_MULTIPLE_CANDIDATES = "multiple_candidates"
REASON_NO_CANDIDATE = "no_complete_candidate"
REASON_DOUBLE_ENCODED = "double_encoded_rejected"

CLEANUP_BOM = "bom_stripped"
CLEANUP_FENCE = "code_fence_stripped"
CLEANUP_BOUNDARY = "boundary_extracted"
CLEANUP_DOUBLE_DECODE = "double_decoded_once"
CLEANUP_DAMAGED_TAIL = "damaged_tail_dropped"

_TICK = chr(96)
_FENCE_PLAIN = _TICK * 3
_FENCE_JSON = _FENCE_PLAIN + "json"


class JsonRejected(Exception):
    """Deterministic parse failure; reason drives section status, message is safe."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        self.message = message
        super().__init__(message)


def _reject_constant(name: str):
    raise JsonRejected(REASON_INVALID_CONSTANT, "输出包含非标准数值常量。")


def _no_duplicate_pairs(pairs):
    seen = set()
    for key, _ in pairs:
        if key in seen:
            raise JsonRejected(REASON_DUPLICATE_KEYS, "输出包含重复键，已拒绝解析。")
        seen.add(key)
    return dict(pairs)


_DECODER = json.JSONDecoder(object_pairs_hook=_no_duplicate_pairs, parse_constant=_reject_constant)


def _check_structure(value: object) -> None:
    stack = [(value, 1)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES:
            raise JsonRejected(REASON_TOO_MANY_NODES, "输出集合规模超过上限。")
        if isinstance(item, str):
            _encodable(item)
        elif isinstance(item, dict):
            if depth > MAX_DEPTH:
                raise JsonRejected(REASON_TOO_DEEP, "输出嵌套深度超过上限。")
            for child in item.values():
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            if depth > MAX_DEPTH:
                raise JsonRejected(REASON_TOO_DEEP, "输出嵌套深度超过上限。")
            for child in item:
                stack.append((child, depth + 1))


def _encodable(text: str) -> None:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise JsonRejected(REASON_INVALID_UNICODE, "输出包含无法编码的字符。") from None


def _loads_strict(text: str, *, max_bytes: int | None) -> object:
    if not isinstance(text, str) or not text.strip():
        raise JsonRejected(REASON_EMPTY, "模型没有返回可解析内容。")
    _encodable(text)
    if max_bytes is not None and len(text.encode("utf-8")) > max_bytes:
        raise JsonRejected(REASON_OVERSIZED, "输出超过本次长度预算。")
    try:
        value = json.loads(text, object_pairs_hook=_no_duplicate_pairs, parse_constant=_reject_constant)
    except JsonRejected:
        raise
    except RecursionError:
        raise JsonRejected(REASON_TOO_DEEP, "输出嵌套深度超过上限。") from None
    except json.JSONDecodeError:
        raise JsonRejected(REASON_INVALID_JSON, "输出不是合法 JSON。") from None
    _check_structure(value)
    return value


def _strip_surrounding_fence(text: str) -> str | None:
    """One code fence may surround the ENTIRE content; never strip partial fences."""
    stripped = text.strip()
    if not (stripped.startswith(_FENCE_PLAIN) and stripped.endswith(_FENCE_PLAIN)):
        return None
    if len(stripped) < len(_FENCE_PLAIN) * 2:
        return None
    line_break = stripped.find("\n")
    if line_break == -1:
        return None
    opener = stripped[:line_break].strip()
    if opener not in (_FENCE_PLAIN, _FENCE_JSON):
        return None
    body = stripped[line_break + 1: -len(_FENCE_PLAIN)]
    return body if body.strip() else None


def _boundary_candidate(text: str, *, max_bytes: int | None) -> object:
    """Find the ONE complete JSON candidate inside explanatory prose.

    Scanning uses JSONDecoder.raw_decode, so braces and quotes inside strings
    are handled by the real parser, never by bracket-counting regexes. A
    candidate that violates structural rules fails the whole parse instead of
    letting the scanner fish for a smaller valid object.
    """
    i = 0
    candidates = []
    while i < len(text):
        if text[i] in "{[":
            try:
                value, end = _DECODER.raw_decode(text, i)
            except JsonRejected:
                raise
            except RecursionError:
                raise JsonRejected(REASON_TOO_DEEP, "输出嵌套深度超过上限。") from None
            except json.JSONDecodeError:
                i += 1
                continue
            candidates.append((value, i, end))
            i = end
        else:
            i += 1
    if not candidates:
        raise JsonRejected(REASON_NO_CANDIDATE, "输出中没有完整的 JSON 对象。")
    if len(candidates) > 1:
        raise JsonRejected(REASON_MULTIPLE_CANDIDATES, "输出包含多个相互竞争的 JSON 对象，无法选择。")
    value = candidates[0][0]
    if max_bytes is not None and len(text.encode("utf-8")) > max_bytes:
        raise JsonRejected(REASON_OVERSIZED, "输出超过本次长度预算。")
    if not isinstance(value, dict):
        raise JsonRejected(REASON_NOT_OBJECT, "输出的 JSON 根不是对象。")
    _check_structure(value)
    return value


def parse_complete_section_objects(text: str, *, max_bytes: int | None = None) -> tuple[list[dict], list[str]]:
    """Keep complete top-level JSON objects and discard only a damaged tail.

    This recovery path is intentionally *not* used for tool arguments.  It is
    for streamed display content where a provider may append a second object
    and then cut the connection.  Each returned object was parsed intact by
    ``JSONDecoder``; the function never invents brackets, quotes, keys or
    values.  Callers that need exactly one section must still reject competing
    complete objects rather than guessing which one the model meant.
    """
    if not isinstance(text, str) or not text.strip():
        raise JsonRejected(REASON_EMPTY, "模型没有返回可解析内容。")
    _encodable(text)
    if max_bytes is not None and len(text.encode("utf-8")) > max_bytes:
        raise JsonRejected(REASON_OVERSIZED, "输出超过本次长度预算。")
    values: list[dict] = []
    index = 0
    damaged_tail = False
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        try:
            value, end = _DECODER.raw_decode(text, index)
        except JsonRejected:
            raise
        except (json.JSONDecodeError, RecursionError):
            damaged_tail = True
            break
        if not isinstance(value, dict):
            raise JsonRejected(REASON_NOT_OBJECT, "输出的 JSON 根不是对象。")
        _check_structure(value)
        values.append(value)
        index = end
    if not values:
        raise JsonRejected(REASON_NO_CANDIDATE, "输出中没有完整的 JSON 对象。")
    return values, [CLEANUP_DAMAGED_TAIL] if damaged_tail else []


def _maybe_decode_once(value: object, cleanups: list[str], *, max_bytes: int | None) -> dict:
    if not isinstance(value, str):
        return value
    try:
        decoded = _loads_strict(value, max_bytes=max_bytes)
    except JsonRejected:
        raise JsonRejected(REASON_DOUBLE_ENCODED, "输出被额外编码为 JSON 字符串且内层不合法。") from None
    if not isinstance(decoded, dict):
        raise JsonRejected(REASON_DOUBLE_ENCODED, "输出被额外编码为 JSON 字符串且内层不是对象。")
    cleanups.append(CLEANUP_DOUBLE_DECODE)
    return decoded


def parse_section_object(text: str, *, max_bytes: int | None = None) -> tuple[dict, list[str]]:
    """Parse a model-written section into an object plus applied cleanup names."""
    cleanups: list[str] = []
    if not isinstance(text, str) or not text.strip():
        raise JsonRejected(REASON_EMPTY, "模型没有返回可解析内容。")
    _encodable(text)
    if max_bytes is not None and len(text.encode("utf-8")) > max_bytes:
        raise JsonRejected(REASON_OVERSIZED, "输出超过本次长度预算。")
    working = text
    if working.startswith("\ufeff"):
        working = working[1:]
        cleanups.append(CLEANUP_BOM)
    fenced = _strip_surrounding_fence(working)
    if fenced is not None:
        working = fenced
        cleanups.append(CLEANUP_FENCE)
    try:
        value = _loads_strict(working, max_bytes=None)
    except JsonRejected as strict_error:
        if strict_error.reason in (REASON_DUPLICATE_KEYS, REASON_INVALID_CONSTANT, REASON_TOO_DEEP,
                                   REASON_TOO_MANY_NODES, REASON_EMPTY):
            raise
        value = _boundary_candidate(working, max_bytes=None)
        cleanups.append(CLEANUP_BOUNDARY)
    value = _maybe_decode_once(value, cleanups, max_bytes=None)
    if not isinstance(value, dict):
        raise JsonRejected(REASON_NOT_OBJECT, "输出的 JSON 根不是对象。")
    return value, cleanups


def parse_tool_arguments(text: str, *, max_bytes: int = 16384) -> dict:
    """Tool arguments accept strict JSON only; no fences, extraction or decoding."""
    value = _loads_strict(text if isinstance(text, str) else "", max_bytes=max_bytes)
    if not isinstance(value, dict):
        raise JsonRejected(REASON_NOT_OBJECT, "工具参数必须是 JSON 对象。")
    return value
