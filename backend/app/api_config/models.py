from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from ..errors import AppError
from .catalog import integration_spec

OPTION_LIMIT = 4096
MAX_MASK = "••••••••"


def _clean_key(value: object) -> object:
    if isinstance(value, str):
        value = value.strip()
        if any(ord(c) < 32 or ord(c) > 126 for c in value):
            raise ValueError("凭据包含非法字符")
        return value or None
    return value


class ApiProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    api_key: SecretStr | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    enabled: bool = True
    options: dict[str, object] = Field(default_factory=dict)
    credential_version: int | None = Field(default=None, ge=1)
    initialize_encryption: bool = False

    _trim_key = field_validator("api_key", mode="before")(_clean_key)

    @model_validator(mode="after")
    def validate_provider(self) -> "ApiProfileInput":
        spec = integration_spec(self.provider)
        if spec is None:
            raise ValueError("此服务不在已选接入目录中")
        if spec["credential"]["kind"] == "none":
            raise ValueError("此服务无需保存凭据")
        if self.clear_api_key and self.api_key is not None:
            raise ValueError("替换和清除凭据不能同时使用")
        if len(_options_json(self.options)) > OPTION_LIMIT:
            raise ValueError("服务选项过大")
        return self


def _options_json(options: dict[str, object]) -> str:
    import json

    return json.dumps(options, ensure_ascii=False, sort_keys=True)


class ApiTestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: str = Field(min_length=1, max_length=60)
    confirm_external: bool = False


class ImportEnvironmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: Literal["finance", "llm"]


class ApiTestView(BaseModel):
    capability: str
    status: Literal["ok", "permission_denied", "quota_limited", "failed", "stale"]
    message: str
    tested_at: str
    credential_version: int


class ApiProfileView(BaseModel):
    id: str
    provider: str
    name: str
    enabled: bool
    options: dict[str, object]
    credential_version: int
    key_status: Literal["configured", "empty", "locked"]
    masked_api_key: str | None
    status: Literal[
        "unconfigured", "saved_unverified", "available", "permission_denied",
        "quota_limited", "expired", "locked", "not_implemented",
    ]
    implementation: Literal["planned", "available"]
    tests: list[ApiTestView]
    created_at: str
    updated_at: str


class ImportedView(BaseModel):
    """Environment import result: identifiers and masked state only, no secrets."""

    target: Literal["integration", "model"]
    id: str
    name: str
    key_status: str
    imported_from: str


def provider_must_allow_key(provider: str) -> None:
    if not has_credential(provider):
        raise AppError("validation", "此服务无需保存凭据。", 422)
