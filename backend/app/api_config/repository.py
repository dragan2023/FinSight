from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from ..errors import AppError
from ..storage import Storage


class ApiRepository:
    def __init__(self, storage: Storage):
        self.storage = storage

    def list(self) -> list[dict]:
        with self.storage.connect() as db:
            profiles = [dict(row) for row in db.execute("SELECT * FROM api_profiles ORDER BY created_at, id")]
            return [self._with_tests(db, row) for row in profiles]

    def get(self, profile_id: str) -> dict:
        with self.storage.connect() as db:
            row = db.execute("SELECT * FROM api_profiles WHERE id = ?", (profile_id,)).fetchone()
            if row is None:
                raise AppError("not_found", "服务配置不存在。", 404)
            return self._with_tests(db, dict(row))

    def _with_tests(self, db: sqlite3.Connection, row: dict) -> dict:
        row["tests"] = [
            dict(test) for test in db.execute(
                "SELECT capability, status, message, credential_version, tested_at "
                "FROM api_capability_tests WHERE profile_id = ? ORDER BY capability", (row["id"],)
            )
        ]
        return row

    def save(self, values: dict, profile_id: str | None = None) -> dict:
        """Persist the profile exactly as computed by the service (version included)."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self.storage.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if profile_id is None:
                    profile_id = str(uuid4())
                elif db.execute("SELECT 1 FROM api_profiles WHERE id = ?", (profile_id,)).fetchone() is None:
                    raise AppError("not_found", "服务配置不存在。", 404)
                db.execute(
                    "INSERT INTO api_profiles (id, provider, name, encrypted_api_key, enabled, options_json, "
                    "credential_version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET provider=excluded.provider, name=excluded.name, "
                    "encrypted_api_key=excluded.encrypted_api_key, enabled=excluded.enabled, "
                    "options_json=excluded.options_json, credential_version=excluded.credential_version, "
                    "updated_at=excluded.updated_at",
                    (profile_id, values["provider"], values["name"], values["encrypted_api_key"],
                     values["enabled"], values["options_json"], values["credential_version"], now, now),
                )
                if values.get("key_changed"):
                    # A new credential invalidates capability tests bound to the old version.
                    db.execute("DELETE FROM api_capability_tests WHERE profile_id = ?", (profile_id,))
                return self._with_tests(db, dict(
                    db.execute("SELECT * FROM api_profiles WHERE id = ?", (profile_id,)).fetchone()
                ))
        except sqlite3.IntegrityError:
            raise AppError("profile_conflict", "配置名称已存在，请使用其他名称。", 409) from None

    def delete(self, profile_id: str) -> None:
        with self.storage.connect() as db:
            result = db.execute("DELETE FROM api_profiles WHERE id = ?", (profile_id,))
            if result.rowcount == 0:
                raise AppError("not_found", "服务配置不存在。", 404)

    def save_tests(self, profile_id: str, results: list[dict]) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        with self.storage.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT credential_version FROM api_profiles WHERE id = ?", (profile_id,)).fetchone()
            if row is None:
                raise AppError("not_found", "服务配置不存在。", 404)
            db.execute("DELETE FROM api_capability_tests WHERE profile_id = ?", (profile_id,))
            for result in results:
                db.execute(
                    "INSERT INTO api_capability_tests (profile_id, capability, status, message, credential_version, tested_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (profile_id, result["capability"], result["status"], result["message"], row["credential_version"], now),
                )
            db.execute("UPDATE api_profiles SET updated_at = ? WHERE id = ?", (now, profile_id))
            return self._with_tests(db, dict(db.execute("SELECT * FROM api_profiles WHERE id = ?", (profile_id,)).fetchone()))

    @staticmethod
    def decode_options(row: dict) -> dict:
        try:
            options = json.loads(row["options_json"] or "{}")
        except ValueError:
            options = {}
        return options if isinstance(options, dict) else {}
