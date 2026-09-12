"""V2-04 Advisor 会话服务：会话/消息的本地 CRUD 与滚动记忆窗口。

多轮状态=摘要记忆：窗口内最近消息原文 + 更早内容只保留标题级摘要行
（沿用 chat.py 思路放大到会话级；本阶段先做窗口截断，压缩留待校准批次）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from ..errors import AppError
from ..storage import Storage
from .models import MESSAGE_ROLES, RISK_APPETITES, SessionUpdateInput, UserProfileInput
from .output_models import upgrade_suggestion_view

MAX_WINDOW_MESSAGES = 16


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AdvisorService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    # -- sessions ------------------------------------------------------------
    def create_session(self, title: str, profile: UserProfileInput | None) -> dict:
        profile = profile or UserProfileInput()
        session_id = uuid4().hex
        now = _now_iso()
        with self._storage.connect() as db:
            db.execute(
                "INSERT INTO advisor_sessions(id, title, user_profile_json, created_at, updated_at) "
                "VALUES(?,?,?,?,?)",
                (session_id, title or now[:16].replace("T", " "),
                 json.dumps(profile.model_dump(), ensure_ascii=False), now, now))
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM advisor_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise AppError("not_found", "会话不存在。", 404)
        return self._session_view(dict(row))

    def list_sessions(self) -> list[dict]:
        with self._storage.connect() as db:
            rows = db.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM advisor_messages m WHERE m.session_id=s.id) AS message_count "
                "FROM advisor_sessions s ORDER BY s.updated_at DESC").fetchall()
        return [self._session_view(dict(row)) for row in rows]

    def update_session(self, session_id: str, patch: SessionUpdateInput) -> dict:
        self.get_session(session_id)
        sets: list[str] = []
        params: list = []
        if patch.title is not None:
            sets.append("title=?")
            params.append(patch.title)
        if patch.user_profile is not None:
            self._validate_profile(patch.user_profile)
            sets.append("user_profile_json=?")
            params.append(json.dumps(patch.user_profile.model_dump(), ensure_ascii=False))
        if sets:
            sets.append("updated_at=?")
            params.append(_now_iso())
            params.append(session_id)
            with self._storage.connect() as db:
                db.execute(f"UPDATE advisor_sessions SET {', '.join(sets)} WHERE id=?", params)
        return self.get_session(session_id)

    def delete_session(self, session_id: str) -> bool:
        with self._storage.connect() as db:
            cursor = db.execute("DELETE FROM advisor_sessions WHERE id=?", (session_id,))
        return cursor.rowcount > 0

    def _session_view(self, row: dict) -> dict:
        profile_raw = json.loads(row.get("user_profile_json") or "{}")
        return {
            "id": row["id"],
            "title": row.get("title") or "",
            "user_profile": UserProfileInput(**{key: profile_raw.get(key, default)
                                                for key, default in (
                                                    ("risk_appetite", "balanced"),
                                                    ("focus_domains", []),
                                                    ("excluded_thscodes", []))}),
            "message_count": row.get("message_count", 0),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _validate_profile(self, profile: UserProfileInput) -> None:
        if profile.risk_appetite not in RISK_APPETITES:
            raise AppError("invalid_profile", "风险偏好档位不支持。", 422)
        if len(profile.excluded_thscodes) and not all(
                code.isascii() and len(code) == 9 for code in profile.excluded_thscodes):
            raise AppError("invalid_profile", "禁入标的代码格式应为 600519.SH 形式。", 422)

    # -- messages ------------------------------------------------------------
    def add_message(self, session_id: str, role: str, content: str,
                    suggestion: dict | None = None) -> dict:
        if role not in MESSAGE_ROLES:
            raise AppError("invalid_role", "消息角色不支持。", 422)
        message_id = uuid4().hex
        now = _now_iso()
        with self._storage.connect() as db:
            db.execute(
                "INSERT INTO advisor_messages(id, session_id, role, content, suggestion_json, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (message_id, session_id, role, content,
                 json.dumps(suggestion, ensure_ascii=False) if suggestion else None, now))
            db.execute("UPDATE advisor_sessions SET updated_at=? WHERE id=?", (now, session_id))
        return self.get_message(message_id)

    def get_message(self, message_id: str) -> dict:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM advisor_messages WHERE id=?", (message_id,)).fetchone()
        if row is None:
            raise AppError("not_found", "消息不存在。", 404)
        return self._message_view(dict(row))

    def list_messages(self, session_id: str) -> list[dict]:
        with self._storage.connect() as db:
            rows = db.execute(
                "SELECT * FROM advisor_messages WHERE session_id=? ORDER BY created_at",
                (session_id,)).fetchall()
        return [self._message_view(dict(row)) for row in rows]

    def _message_view(self, row: dict) -> dict:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "role": row["role"],
            "content": row["content"],
            "suggestion": (upgrade_suggestion_view(json.loads(row["suggestion_json"]))
                           if row["suggestion_json"] else None),
            "created_at": row["created_at"],
        }

    def window(self, session_id: str) -> list[dict]:
        """滚动窗口：最近 N 条消息（原文），用于多轮上下文。"""
        messages = self.list_messages(session_id)
        return messages[-MAX_WINDOW_MESSAGES:]
