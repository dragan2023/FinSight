import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from ..errors import AppError
from ..storage import Storage


class ModelRepository:
    def __init__(self, storage: Storage):
        self.storage = storage

    def list(self) -> list[dict]:
        with self.storage.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM model_profiles ORDER BY created_at, id")]

    def get(self, profile_id: str) -> dict:
        with self.storage.connect() as db:
            row = db.execute("SELECT * FROM model_profiles WHERE id = ?", (profile_id,)).fetchone()
        if row is None:
            raise AppError("not_found", "模型配置不存在。", 404)
        return dict(row)

    def save(self, values: dict, profile_id: str | None = None) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self.storage.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if profile_id:
                    if db.execute("SELECT 1 FROM model_profiles WHERE id = ?", (profile_id,)).fetchone() is None:
                        raise AppError("not_found", "模型配置不存在。", 404)
                elif db.execute("SELECT COUNT(*) FROM model_profiles").fetchone()[0] >= 12:
                    raise AppError("profile_limit", "最多保存 12 个模型配置。", 409)
                if values["active"]:
                    db.execute("UPDATE model_profiles SET active = 0, updated_at = ? WHERE active = 1", (now,))
                fields = ("name", "base_url", "model", "encrypted_api_key", "temperature", "max_tokens", "timeout", "active", "provider", "options_json", "output_policy", "legacy_max_tokens")
                params = [values[field] for field in fields]
                if profile_id:
                    db.execute(
                        "UPDATE model_profiles SET name=?, base_url=?, model=?, encrypted_api_key=?, "
                        "temperature=?, max_tokens=?, timeout=?, active=?, provider=?, options_json=?, "
                        "output_policy=?, legacy_max_tokens=?, updated_at=? WHERE id=?",
                        [*params, now, profile_id],
                    )
                else:
                    profile_id = str(uuid4())
                    db.execute("INSERT INTO model_profiles (id, name, base_url, model, encrypted_api_key, temperature, "
                               "max_tokens, timeout, active, provider, options_json, output_policy, legacy_max_tokens, "
                               "created_at, updated_at) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               [profile_id, *params, now, now])
                # Read inside this transaction so responses describe this operation's result.
                return dict(db.execute("SELECT * FROM model_profiles WHERE id = ?", (profile_id,)).fetchone())
        except sqlite3.IntegrityError:
            raise AppError("profile_conflict", "配置名称已存在，请使用其他名称。", 409) from None

    def delete(self, profile_id: str) -> None:
        with self.storage.connect() as db:
            result = db.execute("DELETE FROM model_profiles WHERE id = ?", (profile_id,))
            if result.rowcount == 0:
                raise AppError("not_found", "模型配置不存在。", 404)
