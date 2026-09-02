"""Deterministic mapping-candidate extraction from attachment text (plan 04 §2.5).

候选字段映射的"生成半环"（批次7）：纯本地规则提取，零模型、零网络、零额外
依赖；候选只用于预填确认表单，**确认仍必须走 PUT /api/supplements/{id}/mapping**
——未经用户确认的候选绝不进入确定性计算。

规则口径（宁缺毋滥，确定性取首个命中）：
- 标的：仅接受带显式交易所后缀的 6 位代码（600519.SH / 000001.sz / sh600519），
  后缀规范化为大写；裸 6 位数字一律不作为标的候选（可能是日期/金额片段）。
- 期间：2024Q1 / 2024年第一季度 / 2024年一季报 / 2024年中报(半年报) /
  2024年报(年度/全年) → 规范化为 2024Qn / 2024H1 / 2024FY。
- 单位：人民币/CNY/RMB → CNY；美元/USD → USD；港元/港币/HKD → HKD。
- 备注：出现 万元/亿元 时提示计量口径（只提示，不改写任何数值）。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

_QUARTER_BY_WORD = {"一": "Q1", "1": "Q1", "二": "Q2", "2": "Q2",
                    "三": "Q3", "3": "Q3", "四": "Q4", "4": "Q4"}

# 600519.SH / 000001.sz；前后不能紧邻字母数字，避免撞上更长数字串。
_SECURITY_ID_RE = re.compile(
    r"(?<![0-9A-Za-z])(\d{6})\.(SH|SZ|BJ)(?![0-9A-Za-z])", re.IGNORECASE)
# sh600519 / sz000001 / bj835185 前缀式；同样要求边界干净。
_PREFIX_SECURITY_RE = re.compile(
    r"(?<![0-9A-Za-z])(SH|SZ|BJ)(\d{6})(?![0-9A-Za-z])", re.IGNORECASE)

_PERIOD_PATTERNS = (
    # 2024Q1 / 2024 Q1
    (re.compile(r"(?<![0-9A-Za-z])(20\d{2})\s?([Qq][1-4])(?![0-9A-Za-z])"),
     lambda m: m.group(1) + m.group(2).upper()),
    # 2024年第一季度 / 2024第3季度 / 2024一季度
    (re.compile(r"(20\d{2})\s*年?\s*第?\s*([一二三四1-4])\s*季度"),
     lambda m: m.group(1) + _QUARTER_BY_WORD.get(m.group(2), "")),
    # 2024年一季报 / 2024三季报
    (re.compile(r"(20\d{2})\s*年?\s*(一|二|三|四)季报"),
     lambda m: m.group(1) + _QUARTER_BY_WORD[m.group(2)]),
    # 2024年中报 / 2024中报 / 2024年半年报 / 半年度
    (re.compile(r"(20\d{2})\s*年?\s*(?:中报|半年报|半年度)"),
     lambda m: m.group(1) + "H1"),
    # 2024年报 / 2024年年度 / 2024全年
    (re.compile(r"(20\d{2})\s*年?\s*(?:年报|年度|全年)"),
     lambda m: m.group(1) + "FY"),
)

_UNIT_CANDIDATES = (
    (re.compile(r"人民币|(?<![A-Za-z])CNY(?![A-Za-z])|RMB", re.IGNORECASE), "CNY"),
    (re.compile(r"(?<![A-Za-z])USD(?![A-Za-z])|美元"), "USD"),
    (re.compile(r"(?<![A-Za-z])HKD(?![A-Za-z])|港元|港币"), "HKD"),
)

_SCALE_HINTS = ((re.compile(r"亿元"), "金额以亿元计"), (re.compile(r"万元"), "金额以万元计"))


class MappingCandidates(BaseModel):
    """确定性候选提取结果；四项均可为 None（宁缺毋滥，不猜测）。"""

    model_config = ConfigDict(extra="forbid")

    security_id: str | None = None
    period: str | None = None
    unit: str | None = None
    note: str | None = None


def extract_mapping_candidates(text: str) -> MappingCandidates:
    """从附件解析文本确定性提取候选值；取每类首个命中，无命中即为 None。"""
    if not text:
        return MappingCandidates()
    security_id = None
    match = _SECURITY_ID_RE.search(text)
    if match:
        security_id = f"{match.group(1)}.{match.group(2).upper()}"
    else:
        match = _PREFIX_SECURITY_RE.search(text)
        if match:
            security_id = f"{match.group(2)}.{match.group(1).upper()}"
    period = None
    for pattern, render in _PERIOD_PATTERNS:
        match = pattern.search(text)
        if match:
            rendered = render(match)
            if rendered.endswith(("Q1", "Q2", "Q3", "Q4", "H1", "FY")):
                period = rendered
                break
    unit = None
    for pattern, code in _UNIT_CANDIDATES:
        if pattern.search(text):
            unit = code
            break
    note = None
    for pattern, hint in _SCALE_HINTS:
        if pattern.search(text):
            note = hint
            break
    return MappingCandidates(security_id=security_id, period=period,
                             unit=unit, note=note)
