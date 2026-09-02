"""SQLite persistence for identities, evidence, gaps and the data cache."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from ..storage import Storage
from ..market_data.identity import SecurityIdentity
from ..market_data.normalization import NormalizedRecord
from ..research.attachments import AttachmentRecord
from ..research.evidence_models import DataGap, EvidenceMeta, StoragePolicy
from ..research.models import (
    ChatRole,
    RawSectionText,
    ReportChatMessage,
    ReportConversation,
    ResearchSupplement,
    ResearchTask,
    StoredReport,
    SupplementKind,
    TaskBudget,
    TaskEvent,
    TaskStatus,
    TaskUsage,
)


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(raw: str | None) -> object:
    if not raw:
        return None
    return json.loads(raw)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResearchRepository:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    # --- identities ---------------------------------------------------------
    def upsert_identity(self, identity: SecurityIdentity) -> None:
        with self._storage.connect() as db:
            db.execute(
                "INSERT INTO security_identities (thscode, provider, ticker, name, exchange,"
                " asset_type, currency, confirmed_source, confirmed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(thscode) DO UPDATE SET provider=excluded.provider,"
                " ticker=excluded.ticker, name=excluded.name, exchange=excluded.exchange,"
                " asset_type=excluded.asset_type, currency=excluded.currency,"
                " confirmed_source=excluded.confirmed_source, confirmed_at=excluded.confirmed_at",
                (identity.thscode, identity.provider, identity.ticker, identity.name,
                 identity.exchange, identity.asset_type, identity.currency,
                 identity.confirmed_source, identity.confirmed_at.isoformat()))

    def list_identities(self) -> list[SecurityIdentity]:
        with self._storage.connect() as db:
            rows = db.execute("SELECT * FROM security_identities ORDER BY confirmed_at DESC").fetchall()
        return [SecurityIdentity.model_validate(dict(row)) for row in rows]

    def get_identity(self, thscode: str) -> SecurityIdentity | None:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM security_identities WHERE thscode = ?", (thscode,)).fetchone()
        return SecurityIdentity.model_validate(dict(row)) if row else None

    # --- evidence -----------------------------------------------------------
    def insert_evidence(self, meta: EvidenceMeta, records: list[NormalizedRecord],
                        gaps: list[DataGap], duplicate_versions: int,
                        raw_payload: object | None = None) -> None:
        with self._storage.connect() as db:
            db.execute(
                "INSERT INTO evidence (id, provider, profile_id, capability, endpoint_path,"
                " params_json, security_id, thscode, basis, source_time, fetched_at, content_hash,"
                " record_count, schema_version, storage_policy, license_note, records_json,"
                " gaps_json, duplicate_versions, raw_payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (meta.id, meta.provider, meta.profile_id, meta.capability, meta.endpoint_path,
                 _dump(meta.params), meta.security_id, meta.thscode, meta.basis, meta.source_time,
                 meta.fetched_at.isoformat(), meta.content_hash, meta.record_count,
                 meta.schema_version, meta.storage_policy, meta.license_note,
                 _dump([record.model_dump() for record in records]),
                 _dump([gap.model_dump() for gap in gaps]), duplicate_versions,
                 _dump(raw_payload) if raw_payload is not None else None))
            for gap in gaps:
                db.execute(
                    "INSERT INTO data_gaps (id, evidence_id, dimension, fields_json, security_id,"
                    " period, reason, impact, blocking_for_json, remedies_json, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (gap.id, meta.id, gap.dimension, _dump(gap.fields), gap.security_id,
                     gap.period, gap.reason, gap.impact, _dump(gap.blocking_for),
                     _dump(gap.remedies), _now()))

    def get_evidence(self, evidence_id: str) -> tuple[EvidenceMeta, list, list[DataGap]] | None:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
        if row is None:
            return None
        meta = EvidenceMeta(
            id=row["id"], provider=row["provider"], profile_id=row["profile_id"],
            capability=row["capability"], endpoint_path=row["endpoint_path"],
            params=_load(row["params_json"]), security_id=row["security_id"],
            thscode=row["thscode"], basis=row["basis"], source_time=row["source_time"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]), content_hash=row["content_hash"],
            record_count=row["record_count"], schema_version=row["schema_version"],
            storage_policy=StoragePolicy(row["storage_policy"]), license_note=row["license_note"])
        gaps = [DataGap.model_validate(item) for item in _load(row["gaps_json"]) or []]
        return meta, _load(row["records_json"]) or [], gaps

    def get_evidence_raw(self, evidence_id: str) -> object | None:
        with self._storage.connect() as db:
            row = db.execute("SELECT raw_payload_json FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
        return _load(row["raw_payload_json"]) if row and row["raw_payload_json"] else None

    def list_evidence(self, *, thscode: str | None = None, capability: str | None = None,
                      limit: int = 50) -> list[EvidenceMeta]:
        query = "SELECT * FROM evidence"
        conditions, args = [], []
        if thscode:
            conditions.append("thscode = ?")
            args.append(thscode)
        if capability:
            conditions.append("capability = ?")
            args.append(capability)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY fetched_at DESC LIMIT ?"
        args.append(limit)
        with self._storage.connect() as db:
            rows = db.execute(query, args).fetchall()
        return [EvidenceMeta(
            id=row["id"], provider=row["provider"], profile_id=row["profile_id"],
            capability=row["capability"], endpoint_path=row["endpoint_path"],
            params=_load(row["params_json"]), security_id=row["security_id"],
            thscode=row["thscode"], basis=row["basis"], source_time=row["source_time"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]), content_hash=row["content_hash"],
            record_count=row["record_count"], schema_version=row["schema_version"],
            storage_policy=StoragePolicy(row["storage_policy"]), license_note=row["license_note"])
            for row in rows]

    # --- data cache ---------------------------------------------------------
    def cache_get(self, cache_key: str) -> dict | None:
        with self._storage.connect() as db:
            row = db.execute("SELECT payload_json, expires_at FROM data_cache WHERE cache_key = ?",
                             (cache_key,)).fetchone()
        if row is None:
            return None
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
            # Expired entries stay as history; evidence rows are unaffected.
            return None
        return _load(row["payload_json"])

    def cache_put(self, cache_key: str, *, provider: str, profile_id: str | None,
                  credential_version: int, endpoint_path: str, schema_version: str,
                  payload: object, ttl_seconds: int) -> None:
        now = datetime.now(timezone.utc)
        with self._storage.connect() as db:
            db.execute(
                "INSERT INTO data_cache (cache_key, provider, profile_id, credential_version,"
                " endpoint_path, schema_version, payload_json, created_at, expires_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(cache_key) DO UPDATE SET payload_json=excluded.payload_json,"
                " credential_version=excluded.credential_version, created_at=excluded.created_at,"
                " expires_at=excluded.expires_at",
                (cache_key, provider, profile_id, credential_version, endpoint_path,
                 schema_version, _dump(payload), now.isoformat(),
                 datetime.fromtimestamp(now.timestamp() + ttl_seconds, tz=timezone.utc).isoformat()))


    # --- research tasks -----------------------------------------------------
    def save_task(self, task: ResearchTask) -> None:
        with self._storage.connect() as db:
            db.execute(
                "INSERT INTO research_tasks (id, question, scope_json, status, budget_json,"
                " usage_json, authorization_json, client_request_id, request_hash,"
                " model_profile_id, report_id, error, created_at, updated_at, finished_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET question=excluded.question,"
                " scope_json=excluded.scope_json, status=excluded.status,"
                " budget_json=excluded.budget_json, usage_json=excluded.usage_json,"
                " authorization_json=excluded.authorization_json,"
                " client_request_id=excluded.client_request_id,"
                " request_hash=excluded.request_hash,"
                " model_profile_id=excluded.model_profile_id,"
                " report_id=excluded.report_id, error=excluded.error,"
                " updated_at=excluded.updated_at, finished_at=excluded.finished_at",
                (task.id, task.question, _dump(task.scope), task.status,
                 task.budget.model_dump_json(), task.usage.model_dump_json(),
                 _dump(task.authorization), task.client_request_id, task.request_hash,
                 task.model_profile_id, task.report_id, task.error,
                 task.created_at.isoformat(), task.updated_at.isoformat(),
                 task.finished_at.isoformat() if task.finished_at else None))

    def get_task(self, task_id: str) -> ResearchTask | None:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM research_tasks WHERE id = ?", (task_id,)).fetchone()
        return self._task_from_row(row) if row else None

    def list_tasks_by_status(self, status: TaskStatus) -> list[ResearchTask]:
        with self._storage.connect() as db:
            rows = db.execute("SELECT * FROM research_tasks WHERE status = ? ORDER BY created_at",
                              (status,)).fetchall()
        return [self._task_from_row(row) for row in rows]

    def get_task_by_client_request_id(self, client_request_id: str) -> ResearchTask | None:
        """client_request_id 幂等：同 ID 同内容复用原任务，不二次收费。"""
        with self._storage.connect() as db:
            row = db.execute(
                "SELECT * FROM research_tasks WHERE client_request_id = ?"
                " ORDER BY created_at DESC LIMIT 1", (client_request_id,)).fetchone()
        return self._task_from_row(row) if row else None

    def _task_from_row(self, row) -> ResearchTask:
        return ResearchTask(
            id=row["id"], question=row["question"], scope=_load(row["scope_json"]),
            status=TaskStatus(row["status"]),
            budget=TaskBudget.model_validate_json(row["budget_json"]),
            usage=TaskUsage.model_validate_json(row["usage_json"]),
            authorization=_load(row["authorization_json"]),
            client_request_id=row["client_request_id"], request_hash=row["request_hash"],
            model_profile_id=row["model_profile_id"], report_id=row["report_id"],
            error=row["error"], created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None)

    # --- task events --------------------------------------------------------
    def append_event(self, task_id: str, type: str, payload: dict) -> TaskEvent:
        now = datetime.now(timezone.utc)
        with self._storage.connect() as db:
            row = db.execute("SELECT COALESCE(MAX(seq), 0) AS top FROM research_events"
                             " WHERE task_id = ?", (task_id,)).fetchone()
            seq = int(row["top"]) + 1
            db.execute(
                "INSERT INTO research_events (task_id, seq, type, payload_json, created_at)"
                " VALUES (?,?,?,?,?)", (task_id, seq, type, _dump(payload), now.isoformat()))
        return TaskEvent(task_id=task_id, seq=seq, type=type, payload=payload, created_at=now)

    def list_events(self, task_id: str, after_seq: int = 0) -> list[TaskEvent]:
        with self._storage.connect() as db:
            rows = db.execute(
                "SELECT * FROM research_events WHERE task_id = ? AND seq > ? ORDER BY seq",
                (task_id, after_seq)).fetchall()
        return [TaskEvent(task_id=row["task_id"], seq=row["seq"], type=row["type"],
                          payload=_load(row["payload_json"]),
                          created_at=datetime.fromisoformat(row["created_at"]))
                for row in rows]

    # --- reports ------------------------------------------------------------
    def count_reports(self, task_id: str) -> int:
        with self._storage.connect() as db:
            row = db.execute("SELECT COUNT(*) AS total FROM research_reports WHERE task_id = ?",
                             (task_id,)).fetchone()
        return int(row["total"])

    def insert_report(self, stored: StoredReport) -> None:
        report = stored.report
        with self._storage.connect() as db:
            db.execute(
                "INSERT INTO research_reports (id, task_id, version, parent_report_id, question,"
                " scope_json, model_json, report_json, raw_sections_json, coverage,"
                " output_integrity, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (stored.id, stored.task_id, stored.version, stored.parent_report_id,
                 stored.question, _dump(stored.scope),
                 _dump(report.model_snapshot.model_dump()) if report.model_snapshot else None,
                 report.model_dump_json(),
                 _dump([item.model_dump() for item in stored.raw_sections]),
                 report.coverage, report.output_integrity, stored.created_at.isoformat()))

    def get_report(self, report_id: str) -> StoredReport | None:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM research_reports WHERE id = ?", (report_id,)).fetchone()
        return self._report_from_row(row) if row else None

    def list_reports(self, task_id: str) -> list[StoredReport]:
        with self._storage.connect() as db:
            rows = db.execute("SELECT * FROM research_reports WHERE task_id = ?"
                              " ORDER BY version", (task_id,)).fetchall()
        return [self._report_from_row(row) for row in rows]

    def _report_from_row(self, row) -> StoredReport:
        from ..agent.output_models import ResearchReport
        # 兼容早期版本：当时报告 JSON 内的 id 与表主键分别生成，导致用户
        # 从页面拿到的 id 不能用于追问和下载。读取时统一以持久化主键为准。
        report = ResearchReport.model_validate_json(row["report_json"]).model_copy(
            update={"id": row["id"], "version": row["version"],
                    "parent_report_id": row["parent_report_id"]})
        return StoredReport(
            id=row["id"], task_id=row["task_id"], version=row["version"],
            parent_report_id=row["parent_report_id"], question=row["question"],
            scope=_load(row["scope_json"]),
            report=report,
            raw_sections=[RawSectionText.model_validate(item)
                          for item in _load(row["raw_sections_json"]) or []],
            created_at=datetime.fromisoformat(row["created_at"]))

    # --- report conversations ------------------------------------------------
    def get_conversation(self, report_id: str) -> ReportConversation | None:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM report_conversations WHERE report_id = ?", (report_id,)).fetchone()
        return self._conversation_from_row(row) if row else None

    def get_or_create_conversation(self, report_id: str, model_profile_id: str | None = None) -> ReportConversation:
        existing = self.get_conversation(report_id)
        if existing is not None:
            return existing
        now = datetime.now(timezone.utc)
        conversation = ReportConversation(id=uuid.uuid4().hex, report_id=report_id,
                                          model_profile_id=model_profile_id,
                                          created_at=now, updated_at=now)
        with self._storage.connect() as db:
            db.execute("INSERT INTO report_conversations (id, report_id, model_profile_id, created_at, updated_at) VALUES (?,?,?,?,?)",
                       (conversation.id, conversation.report_id, conversation.model_profile_id,
                        conversation.created_at.isoformat(), conversation.updated_at.isoformat()))
        return conversation

    def list_chat_messages(self, conversation_id: str, limit: int = 24) -> list[ReportChatMessage]:
        with self._storage.connect() as db:
            rows = db.execute("SELECT * FROM report_chat_messages WHERE conversation_id = ? ORDER BY created_at DESC LIMIT ?",
                              (conversation_id, min(max(limit, 1), 100))).fetchall()
        return [self._chat_message_from_row(row) for row in reversed(rows)]

    def add_chat_message(self, conversation_id: str, role: ChatRole, content: str,
                         sources: list[dict] | None = None) -> ReportChatMessage:
        now = datetime.now(timezone.utc)
        message = ReportChatMessage(id=uuid.uuid4().hex, conversation_id=conversation_id,
                                    role=role, content=content, sources=list(sources or []), created_at=now)
        with self._storage.connect() as db:
            db.execute("INSERT INTO report_chat_messages (id, conversation_id, role, content, sources_json, created_at) VALUES (?,?,?,?,?,?)",
                       (message.id, message.conversation_id, message.role, message.content,
                        _dump(message.sources), message.created_at.isoformat()))
            db.execute("UPDATE report_conversations SET updated_at = ? WHERE id = ?", (now.isoformat(), conversation_id))
        return message

    @staticmethod
    def _conversation_from_row(row) -> ReportConversation:
        return ReportConversation(id=row["id"], report_id=row["report_id"], model_profile_id=row["model_profile_id"],
                                  created_at=datetime.fromisoformat(row["created_at"]),
                                  updated_at=datetime.fromisoformat(row["updated_at"]))

    @staticmethod
    def _chat_message_from_row(row) -> ReportChatMessage:
        return ReportChatMessage(id=row["id"], conversation_id=row["conversation_id"], role=ChatRole(row["role"]),
                                 content=row["content"], sources=_load(row["sources_json"]) or [],
                                 created_at=datetime.fromisoformat(row["created_at"]))

    # --- supplements --------------------------------------------------------
    def insert_supplement(self, supplement: ResearchSupplement) -> None:
        with self._storage.connect() as db:
            db.execute(
                "INSERT INTO research_supplements (id, report_id, task_id, kind, gap_ref,"
                " content, fields_json, referenced_by_report_id, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (supplement.id, supplement.report_id, supplement.task_id, supplement.kind,
                 supplement.gap_ref, supplement.content, _dump(supplement.fields),
                 supplement.referenced_by_report_id, supplement.created_at.isoformat()))

    def list_supplements(self, report_id: str) -> list[ResearchSupplement]:
        with self._storage.connect() as db:
            rows = db.execute("SELECT * FROM research_supplements WHERE report_id = ?"
                              " ORDER BY created_at", (report_id,)).fetchall()
        return [self._supplement_from_row(row) for row in rows]

    def mark_supplements_referenced(self, supplement_ids: list[str], new_report_id: str) -> None:
        with self._storage.connect() as db:
            for supplement_id in supplement_ids:
                db.execute(
                    "UPDATE research_supplements SET referenced_by_report_id = ? WHERE id = ?",
                    (new_report_id, supplement_id))

    def _supplement_from_row(self, row) -> ResearchSupplement:
        return ResearchSupplement(
            id=row["id"], report_id=row["report_id"], task_id=row["task_id"],
            kind=SupplementKind(row["kind"]),
            gap_ref=row["gap_ref"], content=row["content"],
            fields=_load(row["fields_json"]),
            referenced_by_report_id=row["referenced_by_report_id"],
            created_at=datetime.fromisoformat(row["created_at"]))

    # --- attachments (stage 04 batch 4) -------------------------------------
    def insert_attachment(self, record: AttachmentRecord) -> None:
        with self._storage.connect() as db:
            db.execute(
                "INSERT INTO research_attachments (id, report_id, task_id, display_name,"
                " extension, mime_type, size_bytes, sha256, kind, parse_method,"
                " storage_policy, raw_file_retained, excerpt_path, truncated,"
                " warnings_json, stats_json, supplement_id, created_at,"
                " mapping_candidates_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (record.id, record.report_id, record.task_id, record.display_name,
                 record.extension, record.mime_type, record.size_bytes, record.sha256,
                 record.kind, record.parse_method, record.storage_policy,
                 1 if record.raw_file_retained else 0, record.excerpt_path,
                 1 if record.truncated else 0, _dump(record.warnings),
                 _dump(record.stats), record.supplement_id, record.created_at,
                 _dump(record.mapping_candidates)))

    def get_attachment(self, attachment_id: str) -> AttachmentRecord | None:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM research_attachments WHERE id = ?",
                             (attachment_id,)).fetchone()
        return self._attachment_from_row(row) if row else None

    def list_attachments(self, report_id: str) -> list[AttachmentRecord]:
        with self._storage.connect() as db:
            rows = db.execute("SELECT * FROM research_attachments WHERE report_id = ?"
                              " ORDER BY created_at", (report_id,)).fetchall()
        return [self._attachment_from_row(row) for row in rows]

    def _attachment_from_row(self, row) -> AttachmentRecord:
        return AttachmentRecord(
            id=row["id"], report_id=row["report_id"], task_id=row["task_id"],
            display_name=row["display_name"], extension=row["extension"],
            mime_type=row["mime_type"], size_bytes=row["size_bytes"],
            sha256=row["sha256"], kind=row["kind"], parse_method=row["parse_method"],
            storage_policy=StoragePolicy(row["storage_policy"]),
            raw_file_retained=bool(row["raw_file_retained"]),
            excerpt_path=row["excerpt_path"], truncated=bool(row["truncated"]),
            warnings=_load(row["warnings_json"]) or [],
            stats=_load(row["stats_json"]) or {},
            supplement_id=row["supplement_id"],
            mapping=_load(row["mapping_json"]) or {},
            mapping_confirmed_at=row["mapping_confirmed_at"],
            mapping_candidates=_load(row["mapping_candidates_json"]) or {},
            created_at=row["created_at"])

    def update_attachment_mapping(self, attachment_id: str, mapping: dict,
                                  confirmed_at: str) -> None:
        with self._storage.connect() as db:
            db.execute(
                "UPDATE research_attachments SET mapping_json = ?, mapping_confirmed_at = ?"
                " WHERE id = ?", (_dump(mapping), confirmed_at, attachment_id))

    def delete_attachment(self, attachment_id: str) -> None:
        with self._storage.connect() as db:
            db.execute("DELETE FROM research_attachments WHERE id = ?", (attachment_id,))

    def get_supplement(self, supplement_id: str) -> ResearchSupplement | None:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM research_supplements WHERE id = ?",
                             (supplement_id,)).fetchone()
        return self._supplement_from_row(row) if row else None

    def delete_supplement(self, supplement_id: str) -> None:
        """只允许删除未被任何报告引用的用户补充（调用方负责校验引用状态）。"""
        with self._storage.connect() as db:
            db.execute("DELETE FROM research_supplements WHERE id = ?", (supplement_id,))

    # --- stage 05 watchlist and user-confirmed theses ----------------------
    def upsert_watchlist(self, item):
        from .watchlist import WatchlistEntry
        with self._storage.connect() as db:
            existing = db.execute("SELECT * FROM watchlist_entries WHERE security_id = ?", (item.security_id,)).fetchone()
            if existing:
                db.execute("UPDATE watchlist_entries SET thscode=?, name=?, tags_json=?, updated_at=? WHERE id=?",
                           (item.thscode, item.name, _dump(item.tags), item.updated_at.isoformat(), existing["id"]))
                item = item.model_copy(update={"id": existing["id"], "created_at": datetime.fromisoformat(existing["created_at"])})
            else:
                db.execute("INSERT INTO watchlist_entries VALUES (?,?,?,?,?,?,?)", (item.id, item.security_id, item.thscode, item.name, _dump(item.tags), item.created_at.isoformat(), item.updated_at.isoformat()))
        return item

    def list_watchlist(self):
        from .watchlist import WatchlistEntry
        with self._storage.connect() as db: rows = db.execute("SELECT * FROM watchlist_entries ORDER BY updated_at DESC").fetchall()
        return [WatchlistEntry(id=row["id"], security_id=row["security_id"], thscode=row["thscode"], name=row["name"], tags=_load(row["tags_json"]) or [], created_at=datetime.fromisoformat(row["created_at"]), updated_at=datetime.fromisoformat(row["updated_at"])) for row in rows]

    def update_watchlist_tags(self, item_id: str, tags: list[str]):
        from .watchlist import WatchlistEntry
        now = _now()
        with self._storage.connect() as db:
            db.execute("UPDATE watchlist_entries SET tags_json=?, updated_at=? WHERE id=?", (_dump(tags), now, item_id))
            row=db.execute("SELECT * FROM watchlist_entries WHERE id=?", (item_id,)).fetchone()
        return WatchlistEntry(id=row["id"], security_id=row["security_id"], thscode=row["thscode"], name=row["name"], tags=_load(row["tags_json"]) or [], created_at=datetime.fromisoformat(row["created_at"]), updated_at=datetime.fromisoformat(row["updated_at"])) if row else None

    def delete_watchlist(self, item_id: str) -> bool:
        with self._storage.connect() as db: return db.execute("DELETE FROM watchlist_entries WHERE id=?", (item_id,)).rowcount > 0

    def insert_thesis(self, item) -> None:
        with self._storage.connect() as db:
            db.execute("INSERT INTO theses VALUES (?,?,?,?,?,?,?,?,?,?,?)", (item.id,item.security_id,item.content,_dump(item.required_evidence),_dump(item.counter_evidence),_dump(item.observation_metrics),item.status,item.confirmed_at.isoformat(),item.source_report_id,item.created_at.isoformat(),item.updated_at.isoformat()))

    def list_theses(self, security_id: str | None = None):
        from .theses import Thesis, ThesisStatus
        sql="SELECT * FROM theses"; args=[]
        if security_id: sql += " WHERE security_id=?"; args.append(security_id)
        sql += " ORDER BY updated_at DESC"
        with self._storage.connect() as db: rows=db.execute(sql,args).fetchall()
        return [Thesis(id=r["id"],security_id=r["security_id"],content=r["content"],required_evidence=_load(r["required_evidence_json"]) or [],counter_evidence=_load(r["counter_evidence_json"]) or [],observation_metrics=_load(r["observation_metrics_json"]) or [],status=ThesisStatus(r["status"]),confirmed_at=datetime.fromisoformat(r["confirmed_at"]),source_report_id=r["source_report_id"],created_at=datetime.fromisoformat(r["created_at"]),updated_at=datetime.fromisoformat(r["updated_at"])) for r in rows]

    def insert_thesis_observation(self, item) -> None:
        with self._storage.connect() as db:
            db.execute("INSERT INTO thesis_observations VALUES (?,?,?,?,?,?,?,?)", (item.id,item.thesis_id,item.report_id,_dump(item.evidence_refs),_dump(item.metric_refs),item.outcome,item.note,item.created_at.isoformat()))

    def update_thesis(self, thesis_id: str, *, content: str, status: str):
        from .theses import Thesis, ThesisStatus
        now = _now()
        with self._storage.connect() as db:
            db.execute("UPDATE theses SET content=?, status=?, updated_at=? WHERE id=?", (content, status, now, thesis_id))
            row = db.execute("SELECT * FROM theses WHERE id=?", (thesis_id,)).fetchone()
        if not row: return None
        return Thesis(id=row["id"], security_id=row["security_id"], content=row["content"], required_evidence=_load(row["required_evidence_json"]) or [], counter_evidence=_load(row["counter_evidence_json"]) or [], observation_metrics=_load(row["observation_metrics_json"]) or [], status=ThesisStatus(row["status"]), confirmed_at=datetime.fromisoformat(row["confirmed_at"]), source_report_id=row["source_report_id"], created_at=datetime.fromisoformat(row["created_at"]), updated_at=datetime.fromisoformat(row["updated_at"]))

    # --- stage 07 local report library, notes and export manifest ----------
    def _library_item(self, row):
        from .library import LibraryItem
        scope = _load(row["scope_json"]) or {}
        return LibraryItem(id=row["id"], task_id=row["task_id"], version=row["version"],
                           parent_report_id=row["parent_report_id"], question=row["question"],
                           coverage=row["coverage"], output_integrity=row["output_integrity"],
                           created_at=datetime.fromisoformat(row["created_at"]), thscode=scope.get("thscode"),
                           labels=self.report_labels(row["id"]))

    def search_reports(self, query: str | None, limit: int):
        needle = (query or "").strip()
        sql = "SELECT * FROM research_reports"; args=[]
        if needle:
            sql += " WHERE question LIKE ?"; args.append(f"%{needle}%")
        sql += " ORDER BY created_at DESC LIMIT ?"; args.append(min(max(limit, 1), 100))
        with self._storage.connect() as db: rows = db.execute(sql,args).fetchall()
        return [self._library_item(row) for row in rows]

    def reports_for_scope_type(self, kind: str, *, target: str | None = None, limit: int = 100):
        """Filter reports after decoding local JSON, avoiding a JSON-extension dependency."""
        with self._storage.connect() as db:
            rows = db.execute("SELECT * FROM research_reports ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 500),)).fetchall()
        selected = []
        for row in rows:
            scope = _load(row["scope_json"]) or {}
            if scope.get("type") != kind:
                continue
            if target and target not in {str(value) for value in scope.values()}:
                continue
            selected.append(self._library_item(row))
        return selected

    def report_versions(self, report_id: str):
        with self._storage.connect() as db:
            root = db.execute("SELECT COALESCE(parent_report_id,id) AS root FROM research_reports WHERE id=?",(report_id,)).fetchone()
            if not root: return []
            rows=db.execute("SELECT * FROM research_reports WHERE id=? OR parent_report_id=? ORDER BY version",(root["root"],root["root"])).fetchall()
        return [self._library_item(row) for row in rows]

    def insert_note(self,item)->None:
        with self._storage.connect() as db: db.execute("INSERT INTO report_notes VALUES (?,?,?,?,?)",(item.id,item.report_id,item.content,item.created_at.isoformat(),item.updated_at.isoformat()))

    def update_note(self,note_id:str,content:str):
        from .notes import ReportNote
        now=_now()
        with self._storage.connect() as db:
            db.execute("UPDATE report_notes SET content=?, updated_at=? WHERE id=?",(content,now,note_id));row=db.execute("SELECT * FROM report_notes WHERE id=?",(note_id,)).fetchone()
        return ReportNote(id=row["id"],report_id=row["report_id"],content=row["content"],created_at=datetime.fromisoformat(row["created_at"]),updated_at=datetime.fromisoformat(row["updated_at"])) if row else None

    def list_notes(self, report_id: str):
        from .notes import ReportNote
        with self._storage.connect() as db:
            rows = db.execute("SELECT * FROM report_notes WHERE report_id=? ORDER BY updated_at DESC", (report_id,)).fetchall()
        return [ReportNote(id=row["id"], report_id=row["report_id"], content=row["content"],
                           created_at=datetime.fromisoformat(row["created_at"]),
                           updated_at=datetime.fromisoformat(row["updated_at"])) for row in rows]

    def report_labels(self, report_id: str) -> list[str]:
        with self._storage.connect() as db:
            rows = db.execute("SELECT label FROM report_labels WHERE report_id=? ORDER BY label", (report_id,)).fetchall()
        return [row["label"] for row in rows]

    def set_report_labels(self, report_id: str, labels: list[str]) -> list[str]:
        cleaned = sorted({label.strip() for label in labels if label.strip()})
        if len(cleaned) > 12 or any(len(label) > 48 for label in cleaned):
            raise ValueError("标签最多 12 个、每个不超过 48 个字符。")
        with self._storage.connect() as db:
            if not db.execute("SELECT 1 FROM research_reports WHERE id=?", (report_id,)).fetchone():
                raise KeyError(report_id)
            db.execute("DELETE FROM report_labels WHERE report_id=?", (report_id,))
            db.executemany("INSERT INTO report_labels (report_id,label) VALUES (?,?)", [(report_id, label) for label in cleaned])
        return cleaned

    def insert_export(self, export_id:str, report_id:str, format:str, filename:str)->None:
        with self._storage.connect() as db: db.execute("INSERT INTO report_exports VALUES (?,?,?,?,?)",(export_id,report_id,format,filename,_now()))

    def get_export(self, export_id:str):
        with self._storage.connect() as db: return db.execute("SELECT * FROM report_exports WHERE id=?",(export_id,)).fetchone()

    # --- stage 07 deletion preview and explicit retention -----------------
    def report_deletion_counts(self, report_id: str):
        with self._storage.connect() as db:
            if not db.execute("SELECT 1 FROM research_reports WHERE id=?", (report_id,)).fetchone():
                return None

            def count(sql: str) -> int:
                return int(db.execute(sql, (report_id,)).fetchone()[0])

            return {
                "attachments": count("SELECT COUNT(*) FROM research_attachments WHERE report_id=?"),
                "excerpts": count("SELECT COUNT(*) FROM research_attachments WHERE report_id=? AND excerpt_path IS NOT NULL"),
                "notes": count("SELECT COUNT(*) FROM report_notes WHERE report_id=?"),
                "exports": count("SELECT COUNT(*) FROM report_exports WHERE report_id=?"),
                "thesis_observations": count("SELECT COUNT(*) FROM thesis_observations WHERE report_id=?"),
                "supplement_references": count("SELECT COUNT(*) FROM research_supplements WHERE referenced_by_report_id=?"),
            }

    def report_excerpt_paths(self, report_id: str) -> list[str]:
        with self._storage.connect() as db:
            rows = db.execute("SELECT excerpt_path FROM research_attachments WHERE report_id=? AND excerpt_path IS NOT NULL", (report_id,)).fetchall()
        return [row["excerpt_path"] for row in rows]

    def report_export_paths(self, report_id: str) -> list[str]:
        with self._storage.connect() as db:
            rows = db.execute("SELECT filename FROM report_exports WHERE report_id=?", (report_id,)).fetchall()
        return [row["filename"] for row in rows]

    def delete_reports(self, report_ids: list[str]) -> None:
        with self._storage.connect() as db:
            for report_id in report_ids:
                db.execute("DELETE FROM research_reports WHERE id=?", (report_id,))

    def record_cleanup_failure(self, report_id: str, relative_path: str, reason: str) -> None:
        with self._storage.connect() as db:
            db.execute("INSERT INTO cleanup_failures VALUES (?,?,?,?,?)",
                       (uuid.uuid4().hex, report_id, relative_path, reason, _now()))

    def cleanup_failures(self, report_id: str) -> list[str]:
        with self._storage.connect() as db:
            rows = db.execute("SELECT relative_path FROM cleanup_failures WHERE report_id=?", (report_id,)).fetchall()
        return [row["relative_path"] for row in rows]

    def clear_cleanup_failure(self, report_id: str, relative_path: str) -> None:
        with self._storage.connect() as db:
            db.execute("DELETE FROM cleanup_failures WHERE report_id=? AND relative_path=?", (report_id, relative_path))
