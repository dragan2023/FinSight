from threading import RLock

from pydantic import SecretStr, ValidationError

from ..errors import AppError
from ..model_config.models import ModelOptions, ProfileInput
from ..settings import Settings
from .catalog import integration_spec
from .models import (
    MAX_MASK, ApiProfileInput, ApiProfileView, ApiTestInput, ApiTestView, ImportedView,
    _options_json,
)
from .repository import ApiRepository


class ApiConfigService:
    """Owns integration credentials in api_profiles; adapters consume them later.

    Credentials are write-only toward clients. Capability test results are stored
    separately and bound to credential_version, so replacing a key invalidates them.
    """

    def __init__(self, root, repository: ApiRepository, settings: Settings, models):
        self.root = root
        self.repository = repository
        self.settings = settings
        self.models = models
        # Stage 02 adapter registry; main.py binds the market service after build.
        self.market_service = None
        # V2-03: embedding provider tests run through the knowledge-base service.
        self.embedding_service = None
        # Serialize read/modify/write so concurrent saves cannot resurrect a cleared key.
        self._write_lock = RLock()

    # -- views ---------------------------------------------------------------

    def view(self, row: dict) -> ApiProfileView:
        spec = integration_spec(row["provider"])
        key_status = self.root.encryption.key_status(row["encrypted_api_key"])
        return ApiProfileView(
            id=row["id"], provider=row["provider"], name=row["name"],
            enabled=bool(row["enabled"]), options=self.repository.decode_options(row),
            credential_version=row["credential_version"], key_status=key_status,
            masked_api_key=MAX_MASK if key_status == "configured" else None,
            status=self._status(row, spec, key_status),
            implementation=spec["implementation"] if spec else "planned",
            tests=[ApiTestView(**test) for test in row["tests"]],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _status(row: dict, spec: dict | None, key_status: str) -> str:
        if spec is None or spec["implementation"] != "available":
            return "not_implemented"
        if key_status == "locked":
            return "locked"
        if key_status == "empty":
            return "unconfigured"
        current = [t for t in row["tests"] if t["credential_version"] == row["credential_version"]]
        if current:
            statuses = {t["status"] for t in current}
            if "permission_denied" in statuses:
                return "permission_denied"
            if "quota_limited" in statuses:
                return "quota_limited"
            if statuses == {"ok"}:
                return "available"
        return "saved_unverified"

    def list(self) -> list[ApiProfileView]:
        return [self.view(row) for row in self.repository.list()]

    def environment(self) -> dict:
        llm = all((
            self.settings.llm_base_url, self.settings.llm_model,
            self.settings.llm_api_key.get_secret_value(),
        ))
        return {"finance": bool(self.settings.finance_api_key.get_secret_value()), "llm": bool(llm)}

    # -- writes --------------------------------------------------------------

    def save(self, data: ApiProfileInput, profile_id: str | None = None) -> ApiProfileView:
        with self._write_lock:
            if data.initialize_encryption and self.root.encryption.status == "missing":
                self.root.initialize()
            self.root.encryption.require_ready()
            previous = self.repository.get(profile_id) if profile_id else None
            if previous:
                if data.credential_version is not None and data.credential_version != previous["credential_version"]:
                    raise AppError("version_conflict", "该配置已被其他修改更新，请刷新后重试。", 409)
                if previous["provider"] != data.provider and data.api_key is None and not data.clear_api_key:
                    raise AppError("provider_changed", "更换服务商必须重新填写或显式清除凭据。", 409)
            ciphertext = previous["encrypted_api_key"] if previous else None
            version = previous["credential_version"] if previous else 1
            key_changed = False
            if data.clear_api_key:
                key_changed = bool(ciphertext)
                ciphertext = None
            elif data.api_key is not None:
                ciphertext = self.root.encryption.encrypt(data.api_key)
                key_changed = previous is not None
            values = {
                "provider": data.provider, "name": data.name, "encrypted_api_key": ciphertext,
                "enabled": data.enabled, "options_json": _options_json(data.options),
                "credential_version": version + 1 if key_changed else version,
                "key_changed": key_changed,
            }
            return self.view(self.repository.save(values, profile_id))

    def delete(self, profile_id: str, credential_version: int | None = None) -> None:
        with self._write_lock:
            row = self.repository.get(profile_id)
            if credential_version is not None and credential_version != row["credential_version"]:
                raise AppError("version_conflict", "该配置已被其他修改更新，请刷新后重试。", 409)
            # Deleted or disabled credentials must not feed any later external request;
            # adapters read profiles per request, so nothing keeps using them.
            self.repository.delete(profile_id)

    async def test(self, profile_id: str, data: ApiTestInput) -> ApiTestView:
        row = self.repository.get(profile_id)
        spec = integration_spec(row["provider"])
        capability_specs = {c["id"]: c for c in (spec or {}).get("capabilities", [])}
        if data.capability not in capability_specs:
            raise AppError("unknown_capability", "此服务没有所请求的能力项。", 422)
        # Stage 01 ships no adapters: never fake a capability result or go online.
        if not spec or spec["implementation"] != "available":
            raise AppError("not_implemented", "此服务的适配器尚未实现，暂无可执行的能力测试；本次未发起外部请求。", 501)
        if capability_specs[data.capability].get("implementation", "available") != "available":
            raise AppError("not_implemented", "此能力尚未实现，暂无可执行的能力测试；本次未发起外部请求。", 501)
        if not data.confirm_external:
            raise AppError("live_tests_disabled", "请先确认本次测试将发送至该服务且可能计费。", 409)
        if (spec or {}).get("category") == "embedding":
            if self.embedding_service is None:
                raise AppError("not_implemented", "此服务的能力测试尚未实现。", 501)
            result = await self.embedding_service.test_capability(
                row["provider"], data.capability, row["credential_version"])
            saved = self.repository.save_tests(str(profile_id), [result])
            stored_test = next(item for item in saved["tests"]
                               if item["capability"] == data.capability)
            return ApiTestView(**stored_test)
        if (spec or {}).get("category") == "web_search":
            if self.web_service is None:
                raise AppError("not_implemented", "此服务的能力测试尚未实现。", 501)
            result = await self.web_service.test_capability(
                row["provider"], data.capability, row["credential_version"])
            saved = self.repository.save_tests(str(profile_id), [result])
            # save_tests returns the persisted profile row (including its tests),
            # not a list of rows.  Read back the matching stored result so the
            # response exactly reflects what the UI will display after refresh.
            stored_test = next(item for item in saved["tests"]
                               if item["capability"] == data.capability)
            return ApiTestView(**stored_test)
        if self.market_service is None:
            raise AppError("not_implemented", "此服务的能力测试尚未实现。", 501)
        result = await self.market_service.test_capability(
            str(profile_id), data.capability, row["credential_version"])
        saved = self.repository.save_tests(str(profile_id), [result])
        stored_test = next(item for item in saved["tests"]
                           if item["capability"] == data.capability)
        return ApiTestView(**stored_test)

    # -- explicit environment import ------------------------------------------

    def import_environment(self, service: str) -> ImportedView:
        if service == "finance":
            return self._import_finance()
        return self._import_llm()

    def _require_ready(self) -> None:
        if self.root.encryption.status != "ready":
            raise AppError("encryption_unavailable", "尚未初始化本机加密存储；请先在设置中保存任一配置完成初始化。", 409)

    def _import_finance(self) -> ImportedView:
        key = self.settings.finance_api_key.get_secret_value().strip()
        if not key:
            raise AppError("environment_missing", "未检测到可导入的同花顺环境配置（HITHINK_FINANCE_API_KEY）。", 409)
        self._require_ready()
        existing = next((row for row in self.repository.list()
                         if row["provider"] == "hexin_finance"
                         and self.repository.decode_options(row).get("imported_from") == "environment"), None)
        if existing and existing["encrypted_api_key"]:
            view = self.view(existing)
            return ImportedView(target="integration", id=view.id, name=view.name,
                                key_status=view.key_status, imported_from="environment")
        data = ApiProfileInput(provider="hexin_finance", name=existing["name"] if existing else "同花顺 Financial-API（环境导入）",
                               api_key=SecretStr(key), enabled=True,
                               options={"imported_from": "environment", "source_variable": "HITHINK_FINANCE_API_KEY"})
        view = self.save(data, existing["id"] if existing else None)
        return ImportedView(target="integration", id=view.id, name=view.name,
                            key_status=view.key_status, imported_from="environment")

    def _import_llm(self) -> ImportedView:
        base_url, model = self.settings.llm_base_url, self.settings.llm_model
        key = self.settings.llm_api_key.get_secret_value().strip()
        if not (base_url and model and key):
            raise AppError("environment_missing", "未检测到完整的模型环境配置（LLM_BASE_URL、LLM_MODEL、LLM_API_KEY）。", 409)
        self._require_ready()
        try:
            data = ProfileInput(provider="custom", name="环境导入模型", base_url=base_url, model=model,
                                api_key=SecretStr(key), options=ModelOptions(), active=False)
        except ValidationError:
            raise AppError("invalid_environment", "模型环境配置无效，请检查地址、模型名称和凭据。", 409) from None
        existing = next((row for row in self.models.repository.list()
                         if row["name"] == "环境导入模型" and row["base_url"] == base_url and row["model"] == model), None)
        if existing and existing["encrypted_api_key"]:
            view = self.models.view(existing)
            return ImportedView(target="model", id=view.id, name=view.name,
                                key_status=view.key_status, imported_from="environment")
        view = self.models.save(data, existing["id"] if existing else None)
        return ImportedView(target="model", id=view.id, name=view.name,
                            key_status=view.key_status, imported_from="environment")
