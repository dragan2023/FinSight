from threading import RLock

from ..credentials import CredentialRoot
from ..errors import AppError
from ..settings import Settings
from .limits import view_budget
from .models import EffectiveOutput, ModelOptions, ProfileInput, ProfileView
from .repository import ModelRepository


class ModelService:
    def __init__(self, repository: ModelRepository, root: CredentialRoot, settings: Settings):
        self.repository = repository
        self.root = root
        self.settings = settings
        # Serialize the read/modify/write sequence so a blank-key edit cannot
        # restore an older credential during a concurrent key replacement.
        self._write_lock = RLock()

    @property
    def encryption(self):
        # Always reached through the shared root: one instance for every credential store.
        return self.root.encryption

    def view(self, row: dict) -> ProfileView:
        status = self.root.encryption.key_status(row["encrypted_api_key"])
        try:
            profile = ProfileInput(**{field: row[field] for field in (
                "name", "base_url", "model", "temperature", "max_tokens", "timeout", "provider",
                "output_policy",
            )}, options=ModelOptions.model_validate_json(row["options_json"]), active=False)
            effective = EffectiveOutput(**view_budget(profile))
        except Exception:
            effective = EffectiveOutput(param="max_tokens", value=row["max_tokens"],
                                        policy=row.get("output_policy", "custom"), requested=row["max_tokens"],
                                        estimated_input_tokens=None, reason="配置无效，按原值显示")
        return ProfileView(**{field: row[field] for field in (
            "id", "name", "base_url", "model", "temperature", "max_tokens", "timeout",
            "active", "created_at", "updated_at", "provider", "output_policy", "legacy_max_tokens",
        )}, options=ModelOptions.model_validate_json(row["options_json"]),
            effective_output=effective,
            key_status=status, masked_api_key="••••••••" if status != "empty" else None)

    def can_initialize(self) -> bool:
        return self.root.can_initialize()

    def list(self) -> list[ProfileView]:
        return [self.view(row) for row in self.repository.list()]

    def save(self, data: ProfileInput, profile_id: str | None = None) -> ProfileView:
        with self._write_lock:
            return self._save(data, profile_id)

    def _save(self, data: ProfileInput, profile_id: str | None = None) -> ProfileView:
        if data.initialize_encryption and self.root.encryption.status == "missing":
            self.root.initialize()
        self.root.encryption.require_ready()
        previous = self.repository.get(profile_id) if profile_id else None
        ciphertext = previous["encrypted_api_key"] if previous else None
        if (previous and ciphertext and previous["base_url"] != data.base_url
                and not data.api_key and not data.clear_api_key):
            raise AppError("endpoint_changed", "更改模型地址时必须重新填写或显式清除凭据。", 409)
        if data.clear_api_key:
            ciphertext = None
        elif data.api_key:
            ciphertext = self.root.encryption.encrypt(data.api_key)
        if data.active:
            if not ciphertext:
                raise AppError("missing_key", "请先填写凭据再激活此配置。", 409)
            self.root.encryption.decrypt(ciphertext)
        values = data.model_dump(exclude={"api_key", "clear_api_key", "options", "initialize_encryption"})
        values["options_json"] = data.options.model_dump_json()
        values["encrypted_api_key"] = ciphertext
        # Keep the last custom value viewable/restorable when the maximum policy is active.
        values["legacy_max_tokens"] = (previous["max_tokens"] if previous else data.max_tokens) \
            if data.output_policy == "maximum" else data.max_tokens
        return self.view(self.repository.save(values, profile_id))

    def activate(self, profile_id: str) -> ProfileView:
        with self._write_lock:
            row = self.repository.get(profile_id)
            return self._save(ProfileInput(**{field: row[field] for field in (
                "name", "base_url", "model", "temperature", "max_tokens", "timeout", "provider", "output_policy",
            )}, options=ModelOptions.model_validate_json(row["options_json"]), active=True), profile_id)

    def resolve(self, profile_id: str | None = None) -> ProfileInput:
        """Business calls never fall back to hidden environment credentials.

        Environment variables are imported explicitly from the settings page and
        stored as an encrypted profile; until then there is no usable model.
        """
        rows = self.repository.list()
        if profile_id is not None:
            row = self.repository.get(profile_id)
        elif rows:
            row = next((row for row in rows if row["active"]), None)
            if row is None:
                raise AppError("no_active_model", "已有配置但没有活动模型，请先激活一个配置。", 409)
        else:
            raise AppError("model_unconfigured", "尚未配置模型。请在设置中保存并激活一个模型配置。", 409)
        if not row["encrypted_api_key"]:
            raise AppError("missing_key", "此模型配置尚未设置凭据。", 409)
        return ProfileInput(**{field: row[field] for field in (
            "name", "base_url", "model", "temperature", "max_tokens", "timeout", "provider", "output_policy",
        )}, options=ModelOptions.model_validate_json(row["options_json"]),
            api_key=self.root.encryption.decrypt(row["encrypted_api_key"]))

    def status(self) -> dict:
        profiles = self.list()
        active = next((profile for profile in profiles if profile.active), None)
        try:
            resolved = self.resolve()
            state, label = "ready", resolved.model
        except AppError as error:
            state, label = error.category, active.model if active else None
        return {"source": "profile" if profiles else "none", "status": state, "model_label": label,
                "active_profile_id": active.id if active else None, "profile_count": len(profiles)}
