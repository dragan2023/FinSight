"""Single place that derives an effective output budget from a profile.

The request-parameter entry point lives in agent/provider_requests.py and calls
this module; the settings API reads the same values so the frontend never has to
guess limits itself. There is no global 32K cap anymore.
"""

from dataclasses import dataclass

from ..errors import AppError
from .models import ProfileInput
from .providers import model_spec

# Conservative no-tokenizer input estimate: assume at least one token per two
# characters for mixed Chinese/English plus tool and media overhead.
CHARS_PER_TOKEN = 2
# Reasoning must always leave body room in total-output-style parameters.
BODY_RESERVE = 16384
# Below this the model can barely answer; reduce with an explicit reason instead.
MIN_EFFECTIVE = 1024


@dataclass
class OutputBudget:
    param: str
    requested: int
    effective: int
    policy: str
    estimated_input_tokens: int | None = None
    reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "param": self.param, "value": self.effective, "requested": self.requested,
            "policy": self.policy, "estimated_input_tokens": self.estimated_input_tokens,
            "reason": self.reason,
        }


def documented_limits(profile: ProfileInput) -> tuple[int | None, int | None, str]:
    """Return (context, documented max output, output parameter name)."""
    if profile.provider == "custom":
        return profile.options.context_limit, profile.options.max_output, "max_tokens"
    capability = model_spec(profile.provider, profile.model)
    if not capability:
        return None, None, "max_tokens"
    return capability.get("context"), capability.get("max_output"), capability.get("output_param", "max_tokens")


def maximum_budget(profile: ProfileInput) -> int:
    _, max_output, _ = documented_limits(profile)
    if not max_output:
        raise AppError("output_limit_unknown", "此接口的输出上限未知；请在高级设置中填写服务商明确的上限后再启用最大输出。", 422)
    return max_output


def output_budget(profile: ProfileInput, input_chars: int = 0) -> OutputBudget:
    """requested/effective/reason for one request; never silently clamps to a fixed cap."""
    _, max_output, param = documented_limits(profile)
    if profile.output_policy == "maximum":
        requested = max_output if max_output else profile.max_tokens
        if not max_output:
            raise AppError("output_limit_unknown", "此接口的输出上限未知；请改用自定义输出或填写服务商上限。", 422)
    else:
        requested = profile.max_tokens
    estimated = (input_chars + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN if input_chars else None
    effective = requested
    reason = None
    context, _, _ = documented_limits(profile)
    if estimated is not None and context:
        remaining = context - estimated
        if remaining < effective:
            effective = max(remaining, 0)
            reason = "上下文剩余空间小于请求的输出预算（按每 2 字符约 1 Token 估算）"
    if profile.options.thinking_budget is not None and effective - profile.options.thinking_budget < BODY_RESERVE:
        raise AppError(
            "thinking_budget_conflict",
            "思考预算必须为正文保留至少 16384 Token；请降低思考预算或提高输出上限。",
            422)
    if effective < MIN_EFFECTIVE:
        raise AppError(
            "output_budget_exhausted",
            "输入已占用绝大部分上下文，无法保留最小输出预算；请压缩输入内容。",
            422)
    return OutputBudget(param=param, requested=requested, effective=effective,
                        policy=profile.output_policy, estimated_input_tokens=estimated, reason=reason)


def view_budget(profile: ProfileInput) -> dict:
    """The profile-level effective output shown in API reads (no input pressure yet)."""
    _, max_output, param = documented_limits(profile)
    policy = profile.output_policy
    if policy == "maximum":
        value = max_output if max_output else profile.max_tokens
    else:
        value = profile.max_tokens
    return {"param": param, "value": value, "requested": value, "policy": policy,
            "estimated_input_tokens": None, "reason": None}
