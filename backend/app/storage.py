import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class Storage:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 15:
                raise RuntimeError("数据库版本比当前应用新，请使用匹配的应用版本。")
            if version == 0:
                db.execute("""
                    CREATE TABLE model_profiles (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL UNIQUE,
                        base_url TEXT NOT NULL,
                        model TEXT NOT NULL,
                        encrypted_api_key TEXT,
                        temperature REAL NOT NULL,
                        max_tokens INTEGER NOT NULL,
                        timeout REAL NOT NULL,
                        active INTEGER NOT NULL CHECK(active IN (0, 1)),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                db.execute("CREATE UNIQUE INDEX one_active_profile ON model_profiles(active) WHERE active = 1")
                db.execute("PRAGMA user_version = 1")
            if version < 2:
                db.execute("ALTER TABLE model_profiles ADD COLUMN provider TEXT NOT NULL DEFAULT 'custom'")
                db.execute("ALTER TABLE model_profiles ADD COLUMN options_json TEXT NOT NULL DEFAULT '{}'")
                db.execute("PRAGMA user_version = 2")
            if version < 3:
                db.execute("""
                    CREATE TABLE api_profiles (
                        id TEXT PRIMARY KEY,
                        provider TEXT NOT NULL,
                        name TEXT NOT NULL UNIQUE,
                        encrypted_api_key TEXT,
                        enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                        options_json TEXT NOT NULL DEFAULT '{}',
                        credential_version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                db.execute("""
                    CREATE TABLE api_capability_tests (
                        profile_id TEXT NOT NULL REFERENCES api_profiles(id) ON DELETE CASCADE,
                        capability TEXT NOT NULL,
                        status TEXT NOT NULL,
                        message TEXT NOT NULL DEFAULT '',
                        credential_version INTEGER NOT NULL,
                        tested_at TEXT NOT NULL,
                        PRIMARY KEY (profile_id, capability)
                    )
                """)
                db.execute("PRAGMA user_version = 3")
            if version < 4:
                # Maximum-output migration: official models default to the documented
                # catalog maximum with the previous value kept as legacy_max_tokens;
                # custom interfaces keep their value and show "unknown limit".
                db.execute("ALTER TABLE model_profiles ADD COLUMN output_policy TEXT NOT NULL DEFAULT 'custom'")
                db.execute("ALTER TABLE model_profiles ADD COLUMN legacy_max_tokens INTEGER")
                db.execute("UPDATE model_profiles SET output_policy = CASE WHEN provider = 'custom' THEN 'custom' ELSE 'maximum' END, "
                           "legacy_max_tokens = max_tokens")
                db.execute("PRAGMA user_version = 4")
            if version < 5:
                # Stage 02: confirmed identities, evidence ledger, gap ledger and
                # the data cache. Evidence rows keep normalized records only;
                # raw vendor payloads are hashed, not stored, by default.
                db.execute("""
                    CREATE TABLE security_identities (
                        thscode TEXT PRIMARY KEY,
                        provider TEXT NOT NULL,
                        ticker TEXT NOT NULL,
                        name TEXT NOT NULL,
                        exchange TEXT,
                        asset_type TEXT NOT NULL,
                        currency TEXT NOT NULL,
                        confirmed_source TEXT NOT NULL,
                        confirmed_at TEXT NOT NULL
                    )
                """)
                db.execute("""
                    CREATE TABLE evidence (
                        id TEXT PRIMARY KEY,
                        provider TEXT NOT NULL,
                        profile_id TEXT,
                        capability TEXT NOT NULL,
                        endpoint_path TEXT NOT NULL,
                        params_json TEXT NOT NULL,
                        security_id TEXT,
                        thscode TEXT,
                        basis TEXT NOT NULL,
                        source_time TEXT,
                        fetched_at TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        record_count INTEGER NOT NULL,
                        schema_version TEXT NOT NULL,
                        storage_policy TEXT NOT NULL,
                        license_note TEXT NOT NULL DEFAULT '',
                        records_json TEXT NOT NULL,
                        gaps_json TEXT NOT NULL DEFAULT '[]',
                        duplicate_versions INTEGER NOT NULL DEFAULT 0
                    )
                """)
                db.execute("CREATE INDEX evidence_security_idx ON evidence(thscode, capability, fetched_at)")
                db.execute("""
                    CREATE TABLE data_gaps (
                        id TEXT PRIMARY KEY,
                        evidence_id TEXT REFERENCES evidence(id) ON DELETE CASCADE,
                        dimension TEXT NOT NULL,
                        fields_json TEXT NOT NULL,
                        security_id TEXT,
                        period TEXT,
                        reason TEXT NOT NULL,
                        impact TEXT NOT NULL,
                        blocking_for_json TEXT NOT NULL DEFAULT '[]',
                        remedies_json TEXT NOT NULL DEFAULT '[]',
                        created_at TEXT NOT NULL
                    )
                """)
                db.execute("""
                    CREATE TABLE data_cache (
                        cache_key TEXT PRIMARY KEY,
                        provider TEXT NOT NULL,
                        profile_id TEXT,
                        credential_version INTEGER NOT NULL,
                        endpoint_path TEXT NOT NULL,
                        schema_version TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    )
                """)
                db.execute("PRAGMA user_version = 5")
            if version < 6:
                # Stage 03: research tasks, public progress events, report
                # versions and user supplements. Task status stays separate
                # from report coverage/integrity; reasoning content and hidden
                # model state are never persisted. Raw section text stored on
                # the report row is public model output for plain-text display.
                db.execute("""
                    CREATE TABLE research_tasks (
                        id TEXT PRIMARY KEY,
                        question TEXT NOT NULL,
                        scope_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        budget_json TEXT NOT NULL,
                        usage_json TEXT NOT NULL,
                        authorization_json TEXT NOT NULL DEFAULT '{}',
                        client_request_id TEXT,
                        request_hash TEXT,
                        model_profile_id TEXT,
                        report_id TEXT,
                        error TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        finished_at TEXT
                    )
                """)
                db.execute("CREATE INDEX research_tasks_status_idx ON research_tasks(status, created_at)")
                db.execute("""
                    CREATE TABLE research_events (
                        task_id TEXT NOT NULL REFERENCES research_tasks(id) ON DELETE CASCADE,
                        seq INTEGER NOT NULL,
                        type TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (task_id, seq)
                    )
                """)
                db.execute("""
                    CREATE TABLE research_reports (
                        id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL REFERENCES research_tasks(id) ON DELETE CASCADE,
                        version INTEGER NOT NULL,
                        parent_report_id TEXT,
                        question TEXT NOT NULL,
                        scope_json TEXT NOT NULL,
                        model_json TEXT,
                        report_json TEXT NOT NULL,
                        raw_sections_json TEXT NOT NULL DEFAULT '[]',
                        coverage TEXT NOT NULL,
                        output_integrity TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)
                db.execute("CREATE INDEX research_reports_task_idx ON research_reports(task_id, version)")
                db.execute("""
                    CREATE TABLE research_supplements (
                        id TEXT PRIMARY KEY,
                        report_id TEXT NOT NULL REFERENCES research_reports(id) ON DELETE CASCADE,
                        task_id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        gap_ref TEXT,
                        content TEXT NOT NULL,
                        fields_json TEXT NOT NULL DEFAULT '{}',
                        referenced_by_report_id TEXT,
                        created_at TEXT NOT NULL
                    )
                """)
                db.execute("CREATE INDEX research_supplements_report_idx ON research_supplements(report_id, created_at)")
                db.execute("PRAGMA user_version = 6")
            if version < 7:
                # Stage 04 batch 4: local attachment ledger. Metadata only —
                # original files are never persisted; excerpt retention stores a
                # bounded parsed-text excerpt file outside the database.
                db.execute("""
                    CREATE TABLE research_attachments (
                        id TEXT PRIMARY KEY,
                        report_id TEXT NOT NULL REFERENCES research_reports(id) ON DELETE CASCADE,
                        task_id TEXT NOT NULL,
                        display_name TEXT NOT NULL,
                        extension TEXT NOT NULL,
                        mime_type TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL,
                        sha256 TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        parse_method TEXT NOT NULL,
                        storage_policy TEXT NOT NULL,
                        raw_file_retained INTEGER NOT NULL DEFAULT 0,
                        excerpt_path TEXT,
                        truncated INTEGER NOT NULL DEFAULT 0,
                        warnings_json TEXT NOT NULL DEFAULT '[]',
                        stats_json TEXT NOT NULL DEFAULT '{}',
                        supplement_id TEXT,
                        created_at TEXT NOT NULL
                    )
                """)
                db.execute("CREATE INDEX research_attachments_report_idx ON research_attachments(report_id, created_at)")
                db.execute("PRAGMA user_version = 7")
            if version < 8:
                # Stage 04 batch 5: user-confirmed field mapping for attachment
                # supplements (标的/期间/单位/备注)。确认只保存，不自动触发计算。
                db.execute("ALTER TABLE research_attachments ADD COLUMN mapping_json TEXT NOT NULL DEFAULT '{}'")
                db.execute("ALTER TABLE research_attachments ADD COLUMN mapping_confirmed_at TEXT")
                db.execute("PRAGMA user_version = 8")
            if version < 9:
                # Stage 04 batch 7: deterministic mapping-candidate suggestions
                # (标的/期间/单位/备注)。候选仅用于预填表单，确认仍走 mapping_json。
                db.execute("ALTER TABLE research_attachments ADD COLUMN mapping_candidates_json TEXT NOT NULL DEFAULT '{}'")
                db.execute("PRAGMA user_version = 9")
            if version < 10:
                db.execute("""
                    CREATE TABLE watchlist_entries (
                        id TEXT PRIMARY KEY,
                        security_id TEXT NOT NULL UNIQUE,
                        thscode TEXT NOT NULL,
                        name TEXT NOT NULL,
                        tags_json TEXT NOT NULL DEFAULT '[]',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                db.execute("""
                    CREATE TABLE theses (
                        id TEXT PRIMARY KEY,
                        security_id TEXT NOT NULL,
                        content TEXT NOT NULL,
                        required_evidence_json TEXT NOT NULL DEFAULT '[]',
                        counter_evidence_json TEXT NOT NULL DEFAULT '[]',
                        observation_metrics_json TEXT NOT NULL DEFAULT '[]',
                        status TEXT NOT NULL,
                        confirmed_at TEXT NOT NULL,
                        source_report_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                db.execute("CREATE INDEX theses_security_idx ON theses(security_id, updated_at)")
                db.execute("""
                    CREATE TABLE thesis_observations (
                        id TEXT PRIMARY KEY,
                        thesis_id TEXT NOT NULL REFERENCES theses(id) ON DELETE CASCADE,
                        report_id TEXT NOT NULL REFERENCES research_reports(id) ON DELETE RESTRICT,
                        evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                        metric_refs_json TEXT NOT NULL DEFAULT '[]',
                        outcome TEXT NOT NULL,
                        note TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    )
                """)
                db.execute("CREATE INDEX thesis_observations_thesis_idx ON thesis_observations(thesis_id, created_at)")
                db.execute("PRAGMA user_version = 10")
            if version < 11:
                db.execute("""
                    CREATE TABLE report_notes (
                        id TEXT PRIMARY KEY,
                        report_id TEXT NOT NULL REFERENCES research_reports(id) ON DELETE CASCADE,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                db.execute("CREATE INDEX report_notes_report_idx ON report_notes(report_id, updated_at)")
                db.execute("""
                    CREATE TABLE report_exports (
                        id TEXT PRIMARY KEY,
                        report_id TEXT NOT NULL REFERENCES research_reports(id) ON DELETE CASCADE,
                        format TEXT NOT NULL,
                        filename TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)
                db.execute("PRAGMA user_version = 11")
            if version < 12:
                # A failed local file cleanup must remain visible and retryable;
                # never claim success before the file is removed.
                db.execute("""
                    CREATE TABLE cleanup_failures (
                        id TEXT PRIMARY KEY,
                        report_id TEXT NOT NULL,
                        relative_path TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)
                db.execute("CREATE INDEX cleanup_failures_report_idx ON cleanup_failures(report_id)")
                db.execute("PRAGMA user_version = 12")
            if version < 13:
                # Editable organisation metadata is deliberately separate from
                # immutable report content and model conclusions.
                db.execute("""
                    CREATE TABLE report_labels (
                        report_id TEXT NOT NULL REFERENCES research_reports(id) ON DELETE CASCADE,
                        label TEXT NOT NULL,
                        PRIMARY KEY (report_id, label)
                    )
                """)
                db.execute("CREATE INDEX report_labels_label_idx ON report_labels(label, report_id)")
                db.execute("PRAGMA user_version = 13")
            if version < 14:
                # A report conversation is local working memory.  The immutable
                # report remains the source of truth; chat turns are separate so
                # asking a follow-up can never rewrite a saved conclusion.
                db.execute("""
                    CREATE TABLE report_conversations (
                        id TEXT PRIMARY KEY,
                        report_id TEXT NOT NULL UNIQUE REFERENCES research_reports(id) ON DELETE CASCADE,
                        model_profile_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                db.execute("""
                    CREATE TABLE report_chat_messages (
                        id TEXT PRIMARY KEY,
                        conversation_id TEXT NOT NULL REFERENCES report_conversations(id) ON DELETE CASCADE,
                        role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                        content TEXT NOT NULL,
                        sources_json TEXT NOT NULL DEFAULT '[]',
                        created_at TEXT NOT NULL
                    )
                """)
                db.execute("CREATE INDEX report_chat_messages_conversation_idx ON report_chat_messages(conversation_id, created_at)")
                db.execute("PRAGMA user_version = 14")
            if version < 15:
                # User-approved local audit export: keep the sanitized response
                # only in SQLite.  It is never rendered in the product UI.
                db.execute("ALTER TABLE evidence ADD COLUMN raw_payload_json TEXT")
                db.execute("PRAGMA user_version = 15")
