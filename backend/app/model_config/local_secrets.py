"""Explicit initialization from CLI or user's first-save action, never startup."""

import os
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import dotenv_values, set_key

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def initialize(root: Path = PROJECT_ROOT) -> str:
    env_path = root / ".env"
    values = dotenv_values(env_path, interpolate=False)
    if os.environ.get("FINSIGHT_ENCRYPTION_KEY") or values.get("FINSIGHT_ENCRYPTION_KEY"):
        return "已有加密根密钥，未修改任何配置。若配置不可解密，请恢复原根密钥。"
    db_path = root / "data" / "db" / "finsight.db"
    if db_path.exists():
        try:
            db = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
            try:
                # Every credential table participates: any ciphertext blocks a new root key.
                for table in ("model_profiles", "api_profiles"):
                    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
                    if exists and db.execute(f"SELECT 1 FROM {table} WHERE encrypted_api_key IS NOT NULL LIMIT 1").fetchone():
                        raise RuntimeError("已有加密记录，拒绝生成替代根密钥。请恢复原 FINSIGHT_ENCRYPTION_KEY。")
            finally:
                db.close()
        except sqlite3.Error:
            raise RuntimeError("无法核实数据库是否含加密记录，未生成根密钥。请先检查本机数据库。") from None
    key = Fernet.generate_key().decode("ascii")
    set_key(str(env_path), "FINSIGHT_ENCRYPTION_KEY", key, quote_mode="always")
    return "已安全写入本机 .env；未显示密钥，未修改其他变量。请重启后端，并私下备份 .env。"


if __name__ == "__main__":
    try:
        print(initialize())
    except (RuntimeError, OSError) as error:
        # Never stringify arbitrary filesystem or dotenv errors with user data.
        print(str(error) if isinstance(error, RuntimeError) else "写入本机 .env 失败，请检查文件权限。")
        raise SystemExit(1) from None
