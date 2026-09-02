"""Translate validated profile options to the vendors' official compatible APIs.

The output budget function here is the single parsing entry for request output
parameters; the frontend and other backend modules read the same numbers via
model_config.limits instead of recomputing them.
"""

import json

from ..errors import AppError
from ..model_config.limits import output_budget
from ..model_config.models import ChatInput, ProfileInput
from ..model_config.providers import model_spec


def completion_options(profile: ProfileInput, messages: list[dict], *, probe: bool = False,
                       extra_chars: int = 0) -> dict:
    options = profile.options
    thinking = options.thinking == "enabled"
    input_chars = len(json.dumps(messages, ensure_ascii=False)) + extra_chars
    budget = output_budget(profile, input_chars)
    limit = budget.effective
    if probe and not thinking:
        # Connectivity probes stay tiny; probe parameters never cap research output.
        limit = min(limit, 256)
    result = {"model": profile.model, "messages": messages, budget.param: limit}
    if not (profile.provider == "deepseek" and thinking):
        result["temperature"] = profile.temperature
    extra = {}
    if profile.provider in {"deepseek", "glm"}:
        extra["thinking"] = {"type": options.thinking}
        if profile.provider == "glm" and thinking:
            extra["thinking"]["clear_thinking"] = False
        if options.reasoning_effort:
            extra["reasoning_effort"] = options.reasoning_effort
    elif profile.provider == "qwen":
        capability = model_spec(profile.provider, profile.model)
        if capability["thinking"]:
            extra["enable_thinking"] = thinking
        # reasoning_effort and thinking_budget are mutually exclusive; never send both.
        if options.thinking_budget is not None:
            if options.reasoning_effort:
                raise AppError("config_conflict", "推理强度与思考预算不能同时发送。", 422)
            extra["thinking_budget"] = options.thinking_budget
        if capability["stream_required"]:
            result.update(stream=True, stream_options={"include_usage": True}, modalities=["text"])
    if extra:
        result["extra_body"] = extra
    result["_output_budget"] = budget
    return result


def chat_messages(profile: ProfileInput, data: ChatInput) -> list[dict]:
    if data.media is None:
        return [{"role": "user", "content": data.prompt}]
    media = data.media
    if media.kind not in profile.options.input_modalities:
        raise AppError("unsupported_modality", "此配置未开启该输入模态，请选择支持的模型并在设置中开启。", 422)
    if media.kind == "audio":
        part = {"type": "input_audio", "input_audio": {"data": media.url, "format": media.audio_format}}
    else:
        kind = f"{media.kind}_url"
        part = {"type": kind, kind: {"url": media.url}}
        if media.kind == "image" and profile.options.image_detail is not None:
            part[kind]["detail"] = profile.options.image_detail
        if media.kind == "video" and profile.options.video_fps is not None:
            part["fps"] = profile.options.video_fps
    return [{"role": "user", "content": [part, {"type": "text", "text": data.prompt}]}]
