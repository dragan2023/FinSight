"""V2-04 Advisor 输出契约：SuggestionCard（方案 §3.3 定稿 schema）。

- 三情景概率归一=100 且来源类型不得混写，由程序校验。
- abstain 是合法一等输出：证据不足拒答时 scenarios 可空、附取证路径。
- confidence 分档而非连续百分数：校准回路就位前不给伪精确数字。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DIRECTIONS = ("accumulate", "hold", "wait", "reduce", "avoid", "abstain")
HORIZONS = ("days_5", "days_20", "quarter", "year", "custom")
CONFIDENCES = ("very_low", "low", "medium", "high")
SCHEMA_VERSION = "finsight.suggestion.v2"
RECOMMENDATION_TIERS = (
    "strong_recommended", "recommended", "observe", "trial_buy_signal",
    "short_term_not_considered", "data_insufficient",
)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal["bull", "base", "bear"]
    probability_pct: float = Field(ge=0, le=100)
    probability_source: Literal["base_rate", "subjective"]
    trigger_condition: str = Field(min_length=1, max_length=300)
    expected_range_pct: tuple[float, float] | None = None


class ResearchForecast(BaseModel):
    """与执行建议分离的方向概率声明。

    这不是仓位或推荐档位；它只提供给研究预测轨封存和事后校准。把方向事件
    明确写成上涨/震荡/下跌三类，避免把 bull/base/bear 的幅度情景误当成方向事件。
    """

    model_config = ConfigDict(extra="forbid")

    p_up: float = Field(ge=0, le=100)
    p_flat: float = Field(ge=0, le=100)
    p_down: float = Field(ge=0, le=100)
    return_threshold_pct: float = Field(default=0.2, gt=0, le=50)
    probability_source: Literal["model_subjective", "base_rate"]

    @model_validator(mode="after")
    def _check_distribution(self):
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 100.0) > 0.01:
            raise ValueError(f"研究预测三情景概率之和必须等于 100（当前 {total:.2f}）。")
        values = (self.p_up, self.p_flat, self.p_down)
        if values.count(max(values)) != 1:
            raise ValueError("研究预测必须有唯一主导情景；无法区分方向时应输出观察或拒答。")
        return self


class EvSummary(BaseModel):
    """EV + 安全边际 + 单变量敏感性表（教育性计算，非预测）。"""

    model_config = ConfigDict(extra="forbid")

    expected_return_pct: float
    margin_of_safety_pct: float | None = None
    sensitivity: list[dict] = Field(default_factory=list, max_length=20)
    note: str = Field(min_length=1, max_length=500)


class AdvisorClaim(BaseModel):
    """用户可见陈述；事实与推断必须显式区分，不能混入模型推理。"""
    model_config = ConfigDict(extra="forbid")

    kind: Literal["fact", "derived", "inference", "forecast"]
    text: str = Field(min_length=1, max_length=1000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=12)
    metric_refs: list[str] = Field(default_factory=list, max_length=12)
    strategy_refs: list[str] = Field(default_factory=list, max_length=8)


class SuggestionCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["finsight.suggestion.v2"]
    thscode: str = Field(pattern=r"^[0-9]{6}\.(SH|SZ|BJ)$")
    name: str = Field(min_length=1, max_length=80)
    direction: Literal["accumulate", "hold", "wait", "reduce", "avoid", "abstain"]
    horizon: Literal["days_5", "days_20", "quarter", "year", "custom"]
    scenarios: list[Scenario] = Field(default_factory=list, max_length=3)
    research_prediction: ResearchForecast | None = None
    confidence: Literal["very_low", "low", "medium", "high"]
    claims: list[AdvisorClaim] = Field(default_factory=list, max_length=16)
    key_evidence: list[str] = Field(default_factory=list, max_length=12)
    counter_evidence: list[str] = Field(min_length=1, max_length=12)
    strategy_refs: list[str] = Field(default_factory=list, max_length=8)
    confidence_basis: dict = Field(default_factory=dict, max_length=9)
    invalidations: list[str] = Field(min_length=1, max_length=8)
    watchpoints: list[str] = Field(min_length=1, max_length=8)
    missing_for_decision: list[str] = Field(default_factory=list, max_length=8)
    ev_summary: EvSummary | None = None
    position_thinking: str | None = Field(default=None, max_length=800)
    decision_narrative: str = Field(min_length=1, max_length=2000)
    disclaimer_accepted: Literal[True]

    @model_validator(mode="after")
    def _check_scenarios(self):
        if self.direction == "abstain":
            if self.scenarios:
                raise ValueError("abstain 时不得给出情景概率；请填 missing_for_decision。")
            if not self.missing_for_decision:
                raise ValueError("abstain 必须列出还缺什么证据（missing_for_decision）。")
        else:
            if len(self.scenarios) != 3:
                raise ValueError("非 abstain 结论必须给出 bull/base/bear 三情景。")
            names = [item.name for item in self.scenarios]
            if sorted(names) != ["base", "bear", "bull"]:
                raise ValueError("三情景名必须恰好是 bull/base/bear。")
            total = sum(item.probability_pct for item in self.scenarios)
            if abs(total - 100.0) > 0.01:
                raise ValueError(f"三情景概率之和必须等于 100（当前 {total:.2f}）。")
            sources = {item.probability_source for item in self.scenarios}
            if len(sources) > 1:
                raise ValueError("概率来源类型不得混写（base_rate 或 subjective 二选一）。")
        if self.direction == "wait":
            if not self.watchpoints:
                raise ValueError("wait 必须携带可量化触发条件（watchpoints）。")
        return self


def upgrade_suggestion_view(card: dict) -> dict:
    """为历史 v1 建议提供只读 v2 视图；绝不回写原 JSON。"""
    result = dict(card)
    if result.get("schema_version") == "finsight.suggestion.v1":
        result.setdefault("claims", [])
        result.setdefault("counter_evidence", [])
        result.setdefault("strategy_refs", [])
        result.setdefault("confidence_basis", {})
        result["legacy_unverified"] = True
    elif result.get("schema_version") == SCHEMA_VERSION:
        result.setdefault("legacy_unverified", False)
    result["recommendation_tier"], result["recommendation_tier_reason"] = _recommendation_tier(result)
    return result


def _recommendation_tier(card: dict) -> tuple[str, str]:
    """只读展示分档，永远不放宽已有生产门禁。

    “强烈推荐”必须同时具备当前版本已验证策略、前向校准和组合层验证。LLM
    的措辞不能改变本函数结果。
    """
    direction = str(card.get("direction") or "abstain")
    basis = card.get("confidence_basis") or {}
    if direction == "abstain":
        return "data_insufficient", "证据或可复现策略不足，系统不作方向判断。"
    if direction in {"avoid", "reduce"}:
        return "short_term_not_considered", "方向或风险门禁不支持短期建立仓位。"
    if direction in {"wait", "hold"}:
        return "observe", "暂未满足建立新仓位的条件，保留观察。"
    candidates = basis.get("candidate_experiments") or []
    strategy_validated = basis.get("strategy_validation") == "validated"
    calibration = basis.get("forward_calibration") == "available"
    portfolio_validated = basis.get("portfolio_validation") == "passed"
    final_level = basis.get("final_level")
    if direction == "accumulate" and candidates and strategy_validated and calibration and portfolio_validated and final_level == "high":
        return "strong_recommended", "策略、前向校准与组合层验证均已通过。"
    if direction == "accumulate" and candidates and strategy_validated and calibration:
        return "recommended", "存在已验证策略和前向校准，但组合层尚未达到强烈推荐门槛。"
    if direction == "accumulate" and candidates:
        return "trial_buy_signal", "策略条件触发，但策略验证、前向校准或组合验证不足，只能研究性小仓/虚拟验证。"
    return "observe", "没有足以支持建仓的候选策略与校准证据。"

def tier_gate_snapshot(card: dict) -> dict:
    """登记时刻的档位门禁快照（双轨封存契约：执行建议轨）。

    只冻结 _recommendation_tier 的计算结果与它实际读取到的门禁输入，
    用于事后复盘"当时为什么给这个档位"。它不产生、也不放宽任何档位；
    历史行不回写，档位随时间变化只会出现在新的登记里。
    """
    basis = card.get("confidence_basis") or {}
    candidate_experiments = list(basis.get("candidate_experiments") or [])
    strategy_validation = basis.get("strategy_validation")
    forward_calibration = basis.get("forward_calibration")
    portfolio_validation = basis.get("portfolio_validation")
    final_level = basis.get("final_level")
    tier, tier_reason = _recommendation_tier(card)
    return {
        "tier": tier,
        "tier_reason": tier_reason,
        "gates": {
            "candidate_experiments": candidate_experiments,
            "strategy_validation": strategy_validation,
            "forward_calibration": forward_calibration,
            "portfolio_validation": portfolio_validation,
            "final_level": final_level,
        },
    }
