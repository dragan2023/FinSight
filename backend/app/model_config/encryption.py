from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from ..errors import AppError


class KeyEncryption:
    def __init__(self, key: SecretStr):
        self._fernet: Fernet | None = None
        self.status = "missing"
        if key.get_secret_value():
            try:
                self._fernet = Fernet(key.get_secret_value().encode("ascii"))
                self.status = "ready"
            except (ValueError, UnicodeError):
                self.status = "invalid"

    def require_ready(self) -> Fernet:
        if self._fernet is None:
            raise AppError("encryption_unavailable", "加密根密钥缺失或无效；请在本机配置后重启。", 409)
        return self._fernet

    def encrypt(self, value: SecretStr) -> str:
        return self.require_ready().encrypt(value.get_secret_value().encode()).decode("ascii")

    def decrypt(self, ciphertext: str) -> SecretStr:
        try:
            return SecretStr(self.require_ready().decrypt(ciphertext.encode()).decode())
        except (InvalidToken, ValueError, UnicodeError):
            raise AppError("key_locked", "已有凭据无法解密，请恢复原加密根密钥。", 409) from None

    def key_status(self, ciphertext: str | None) -> str:
        if not ciphertext:
            return "empty"
        try:
            self.decrypt(ciphertext)
        except AppError:
            return "locked"
        return "configured"

