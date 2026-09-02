"""Small shared root-key holder: one KeyEncryption instance for every credential table.

Model profiles and integration profiles must never hold separate, independently
initialized encryption objects: a lost root key must lock both stores together,
and first-save initialization must consider ciphertexts in either table.
"""

from threading import RLock

from .errors import AppError
from .model_config.encryption import KeyEncryption
from .model_config.local_secrets import initialize
from .settings import Settings, load_settings
from .storage import Storage

CREDENTIAL_TABLES = ("model_profiles", "api_profiles")


class CredentialRoot:
    def __init__(self, storage: Storage, settings: Settings):
        self.storage = storage
        self.settings = settings
        self._lock = RLock()
        self.encryption = KeyEncryption(settings.encryption_key)

    def has_ciphertexts(self) -> bool:
        with self.storage.connect() as db:
            for table in CREDENTIAL_TABLES:
                exists = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if exists and db.execute(
                    f"SELECT 1 FROM {table} WHERE encrypted_api_key IS NOT NULL LIMIT 1"
                ).fetchone():
                    return True
        return False

    def can_initialize(self) -> bool:
        return self.encryption.status == "missing" and not self.has_ciphertexts()

    def initialize(self) -> None:
        """Explicit first-save initialization; never called at startup."""
        with self._lock:
            if self.encryption.status == "ready":
                return
            if self.encryption.status != "missing":
                raise AppError("encryption_unavailable", "加密根密钥无效，请恢复原根密钥，不能生成替代密钥。", 409)
            if not self.can_initialize():
                raise AppError("encryption_unavailable", "已有加密记录，请恢复原根密钥，不能生成替代密钥。", 409)
            try:
                initialize(self.settings.project_root)
                self.encryption = KeyEncryption(load_settings(self.settings.project_root).encryption_key)
            except (OSError, RuntimeError):
                raise AppError("encryption_unavailable", "无法安全初始化本机密钥存储，请检查文件权限或恢复原根密钥。", 409) from None
            if self.encryption.status != "ready":
                raise AppError("encryption_unavailable", "本机密钥存储初始化未生效，请检查 .env 后重启后端。", 409)
