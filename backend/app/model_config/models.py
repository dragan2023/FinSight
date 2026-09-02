from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from .providers import model_spec, official_url, provider_spec


def validate_base_url(value: str) -> str:
    value = value.strip()
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        raise ValueError("模型地址格式不正确") from None
    if (not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or "\\" in value
            or any(c.isspace() or ord(c) < 32 for c in value)):
        raise ValueError("模型地址不能含凭据、查询参数或片段")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        raise ValueError("远端模型必须使用 HTTPS；仅本机模型允许 HTTP")
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))


class ModelOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thinking: Literal["enabled", "disabled"] = "disabled"
    reasoning_effort: Literal["low", "high", "max"] | None = None
    thinking_budget: int | None = Field(default=None, ge=1, le=114688, strict=True)
    input_modalities: list[Literal["image", "video", "audio"]] = Field(default_factory=list, max_length=3)
    region: Literal["beijing", "singapore"] = "beijing"
    workspace_id: str = Field(default="", max_length=63, pattern=r"^([a-z0-9][a-z0-9-]{0,61}[a-z0-9]|[a-z0-9])?$")
    video_fps: float | None = Field(default=None, ge=0.1, le=10, allow_inf_nan=False)
    image_detail: Literal["low", "high", "original", "auto"] | None = None
    # Custom interfaces only: the user declares the vendor's documented limits,
    # enabling the maximum policy. Official models take limits from the catalog.
    context_limit: int | None = Field(default=None, ge=1, le=2097152, strict=True)
    max_output: int | None = Field(default=None, ge=1, le=393216, strict=True)


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: Literal["custom", "deepseek", "glm", "qwen"] = "custom"
    name: str = Field(default="", min_length=1, max_length=80)
    base_url: str = Field(default="", min_length=1, max_length=500)
    model: str = Field(default="", min_length=1, max_length=150)
    options: ModelOptions = Field(default_factory=ModelOptions)
    initialize_encryption: bool = False
    api_key: SecretStr | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    temperature: float = Field(default=0.2, ge=0, le=2, allow_inf_nan=False)
    max_tokens: int = Field(default=2048, ge=64, le=393216, strict=True)
    output_policy: Literal["maximum", "custom"] = "custom"
    timeout: float = Field(default=30, ge=5, le=120, allow_inf_nan=False)
    active: bool = False

    _base_url = field_validator("base_url")(validate_base_url)

    @model_validator(mode="before")
    @classmethod
    def official_defaults(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values
        data = dict(values)
        spec = provider_spec(data.get("provider", "custom"))
        if spec:
            data.setdefault("model", spec["default_model"])
            data.setdefault("name", spec["name"])
            capability = model_spec(spec["id"], data["model"])
            if "options" not in data and capability:
                data["options"] = {"thinking": "enabled" if capability["thinking"] else "disabled",
                                   "input_modalities": capability["modalities"]}
            options = ModelOptions.model_validate(data.get("options", {}))
            data.setdefault("base_url", official_url(spec["id"], options.region, options.workspace_id))
            data.setdefault("max_tokens", 8192)
            data.setdefault("output_policy", "maximum")
            data.setdefault("timeout", 120)
            if spec["id"] == "glm":
                data.setdefault("temperature", 1)
        else:
            data.setdefault("output_policy", "custom")
        return data

    @field_validator("api_key", mode="before")
    @classmethod
    def trim_key(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if any(ord(c) < 32 or ord(c) > 126 for c in value):
                raise ValueError("凭据包含非法字符")
            return value or None
        return value

    @model_validator(mode="after")
    def validate_clear(self) -> "ProfileInput":
        if self.clear_api_key and self.api_key is not None:
            raise ValueError("替换和清除凭据不能同时使用")
        if not self.name or not self.base_url or not self.model:
            raise ValueError("自定义配置必须填写名称、地址和模型")
        options = self.options
        if self.provider == "custom":
            allowed = ModelOptions(context_limit=options.context_limit, max_output=options.max_output)
            if options != allowed:
                raise ValueError("自定义兼容接口不支持官方专用参数")
            if self.output_policy == "maximum" and not options.max_output:
                raise ValueError("此接口输出上限未知；请填写服务商明确的输出上限后再启用最大输出")
            if options.max_output is not None and self.max_tokens > options.max_output:
                raise ValueError("自定义输出不能超过已填写的输出上限")
            return self
        capability = model_spec(self.provider, self.model)
        if not capability:
            raise ValueError("此模型不在已核验的官方预设中")
        if options.context_limit is not None or options.max_output is not None:
            raise ValueError("官方模型以上限目录为准，不能自行填写上限")
        if self.output_policy == "custom" and self.max_tokens > capability["max_output"]:
            raise ValueError("自定义输出不能超过该模型的官方输出上限")
        if options.thinking_budget is not None:
            limit = capability["max_output"] if self.output_policy == "maximum" else self.max_tokens
            if options.thinking_budget > limit - 16384:
                raise ValueError("思考预算必须为正文保留至少 16384 Token，请降低思考预算或提高输出上限")
        if self.base_url != official_url(self.provider, options.region, options.workspace_id):
            raise ValueError("官方预设只能使用对应官方地址")
        if self.provider != "qwen" and (options.region != "beijing" or options.workspace_id):
            raise ValueError("地域与业务空间仅适用于 Qwen")
        if options.thinking == "enabled" and not capability["thinking"]:
            raise ValueError("此模型不支持思考模式")
        if capability.get("thinking_required") and options.thinking != "enabled":
            raise ValueError("此模型仅支持开启思考模式")
        if options.reasoning_effort and (options.thinking != "enabled" or options.reasoning_effort not in capability["efforts"]):
            raise ValueError("此配置不支持指定的推理强度")
        if options.thinking_budget is not None and (options.thinking != "enabled" or not capability["budget"]):
            raise ValueError("此配置不支持思考预算")
        if not set(options.input_modalities).issubset(capability["modalities"]) or len(set(options.input_modalities)) != len(options.input_modalities):
            raise ValueError("此模型不支持指定的输入模态")
        if options.video_fps is not None and (self.model not in {"qwen3.8-flash", "qwen3.8-max"} or "video" not in options.input_modalities):
            raise ValueError("抽帧频率仅适用于已开启视频输入的 Qwen3.8")
        if options.image_detail is not None and (self.provider != "deepseek" or "image" not in options.input_modalities):
            raise ValueError("图像精度仅适用于已开启图像输入的 DeepSeek Vision")
        if self.provider == "glm" and self.temperature > 1:
            raise ValueError("GLM Temperature 不能超过 1")
        return self


class EffectiveOutput(BaseModel):
    param: Literal["max_tokens", "max_completion_tokens"]
    value: int
    policy: Literal["maximum", "custom"]
    requested: int
    estimated_input_tokens: int | None
    reason: str | None


class ProfileView(BaseModel):
    id: str
    name: str
    base_url: str
    model: str
    provider: Literal["custom", "deepseek", "glm", "qwen"]
    options: ModelOptions
    temperature: float
    max_tokens: int
    output_policy: Literal["maximum", "custom"]
    legacy_max_tokens: int | None
    effective_output: EffectiveOutput
    timeout: float
    active: bool
    key_status: Literal["configured", "empty", "locked"]
    masked_api_key: str | None
    created_at: str
    updated_at: str


class ProbeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["connectivity", "chat", "tools"]
    confirm_external: bool = False


class MediaInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["image", "video", "audio"]
    url: str = Field(min_length=1, max_length=4096)
    audio_format: Literal["wav", "mp3"] = "wav"

    @field_validator("url")
    @classmethod
    def public_url(cls, value: str) -> str:
        # The provider fetches the URL; FinSight never downloads media or previews it.
        from ipaddress import ip_address
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.fragment or "\\" in value or any(c.isspace() or ord(c) < 32 for c in value)):
            raise ValueError("素材须为不含凭据的 HTTPS URL")
        _ = parsed.port
        host = parsed.hostname.lower()
        if host == "localhost" or host.endswith((".localhost", ".local")) or "." not in host:
            raise ValueError("素材须为公网 URL")
        try:
            address = ip_address(host)
        except ValueError:
            address = None
        if address and not address.is_global:
            raise ValueError("素材须为公网 URL")
        return value


class ChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    prompt: str = Field(min_length=1, max_length=4000)
    media: MediaInput | None = None
    confirm_external: bool = False


class ChatResult(BaseModel):
    content: str
    model_label: str
    truncated: bool


class ProbeResult(BaseModel):
    mode: Literal["connectivity", "chat", "tools"]
    supported: bool
    message: str
    model_label: str
