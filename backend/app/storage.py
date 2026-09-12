import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# 当前 Schema 版本：迁移链的最后一级与版本守卫共用此常量，
# 新增迁移时只改这里与对应的 `if version < N` 块。
CURRENT_SCHEMA_VERSION = 31


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
            if version > CURRENT_SCHEMA_VERSION:
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
            if version < 16:
                # Explicit watchlist refresh summaries.  They only retain the
                # small UI-facing result and evidence ids, never raw responses.
                db.execute("""
                    CREATE TABLE watchlist_observations (
                        id TEXT PRIMARY KEY,
                        watchlist_item_id TEXT NOT NULL REFERENCES watchlist_entries(id) ON DELETE CASCADE,
                        thscode TEXT NOT NULL,
                        snapshot_json TEXT NOT NULL DEFAULT '{}',
                        anomalies_json TEXT NOT NULL DEFAULT '[]',
                        price_evidence_id TEXT,
                        anomaly_evidence_id TEXT,
                        observed_at TEXT NOT NULL
                    )
                """)
                db.execute("CREATE INDEX watchlist_observations_latest_idx ON watchlist_observations(watchlist_item_id, observed_at DESC)")
                db.execute("PRAGMA user_version = 16")
            if version < 17:
                db.execute("""
                    CREATE TABLE market_observations (
                        id TEXT PRIMARY KEY,
                        observed_date TEXT NOT NULL,
                        modules_json TEXT NOT NULL,
                        summary_json TEXT NOT NULL,
                        evidence_ids_json TEXT NOT NULL DEFAULT '[]',
                        created_at TEXT NOT NULL
                    )
                """)
                db.execute("CREATE INDEX market_observations_date_idx ON market_observations(observed_date, created_at DESC)")
                db.execute("PRAGMA user_version = 17")
            if version < 18:
                # Metadata only: market facts remain in local DuckDB, never SQLite.
                db.execute("""
                    CREATE TABLE market_datasets (
                        id TEXT PRIMARY KEY, kind TEXT NOT NULL, version TEXT NOT NULL,
                        row_count INTEGER NOT NULL, min_date_ms INTEGER, max_date_ms INTEGER,
                        sha256 TEXT NOT NULL, schema_version TEXT NOT NULL, status TEXT NOT NULL,
                        quality_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                    )
                """)
                db.execute("""
                    CREATE TABLE market_sync_runs (
                        id TEXT PRIMARY KEY, dataset_id TEXT REFERENCES market_datasets(id),
                        kind TEXT NOT NULL, status TEXT NOT NULL, failure_type TEXT,
                        request_id TEXT, created_at TEXT NOT NULL, finished_at TEXT
                    )
                """)
                db.execute("CREATE INDEX market_sync_runs_status_idx ON market_sync_runs(status, created_at DESC)")
                db.execute("PRAGMA user_version = 18")
            if version < 19:
                # Derived, reader-facing local calculations for a market
                # observation. Raw supplier evidence remains in ``evidence``.
                db.execute("ALTER TABLE market_observations ADD COLUMN dashboard_json TEXT NOT NULL DEFAULT '{}'")
                db.execute("PRAGMA user_version = 19")
            if version < 20:
                # V2-02: RSS subscriptions.  Copyright boundary: only title,
                # summary, link and publish time are stored — never full text.
                # Poll switch lives in rss_settings and defaults to off.
                db.execute("""
                    CREATE TABLE IF NOT EXISTS rss_feeds (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        url TEXT NOT NULL UNIQUE,
                        enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                        last_fetch_at TEXT,
                        last_success_at TEXT,
                        last_error TEXT NOT NULL DEFAULT '',
                        consecutive_failures INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL
                    )
                """)
                db.execute("""
                    CREATE TABLE IF NOT EXISTS rss_items (
                        id TEXT PRIMARY KEY,
                        feed_id TEXT NOT NULL REFERENCES rss_feeds(id) ON DELETE CASCADE,
                        guid TEXT NOT NULL DEFAULT '',
                        dedup_key TEXT NOT NULL,
                        title TEXT NOT NULL,
                        summary TEXT NOT NULL DEFAULT '',
                        link TEXT NOT NULL DEFAULT '',
                        published_ms INTEGER,
                        fetched_at TEXT NOT NULL,
                        tickers_json TEXT NOT NULL DEFAULT '[]',
                        UNIQUE(feed_id, dedup_key)
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS rss_items_feed_idx ON rss_items(feed_id, published_ms DESC)")
                db.execute("CREATE INDEX IF NOT EXISTS rss_items_published_idx ON rss_items(published_ms DESC)")
                db.execute("""
                    CREATE TABLE IF NOT EXISTS rss_settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                """)
                db.execute("PRAGMA user_version = 20")
            if version < 21:
                # V2-03: private knowledge base. Full text and chunks stay in
                # the local database only; vectors are optional (NULL until
                # embedded) so retrieval degrades to FTS5 keyword search.
                db.execute("""
                    CREATE TABLE IF NOT EXISTS kb_documents (
                        id TEXT PRIMARY KEY,
                        filename TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        parse_method TEXT NOT NULL,
                        sha256 TEXT NOT NULL UNIQUE,
                        byte_size INTEGER NOT NULL,
                        char_count INTEGER NOT NULL,
                        chunk_count INTEGER NOT NULL DEFAULT 0,
                        full_text TEXT NOT NULL,
                        warnings_json TEXT NOT NULL DEFAULT '[]',
                        embedding_state TEXT NOT NULL DEFAULT 'none',
                        embedding_model TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                db.execute("""
                    CREATE TABLE IF NOT EXISTS kb_chunks (
                        id TEXT PRIMARY KEY,
                        document_id TEXT NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
                        position INTEGER NOT NULL,
                        heading TEXT NOT NULL DEFAULT '',
                        page INTEGER,
                        line_start INTEGER,
                        line_end INTEGER,
                        char_start INTEGER NOT NULL,
                        char_end INTEGER NOT NULL,
                        content TEXT NOT NULL,
                        vector BLOB,
                        embedding_model TEXT,
                        UNIQUE(document_id, position)
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS kb_chunks_document_idx ON kb_chunks(document_id, position)")
                # FTS5 trigram 切词可按三字滑窗匹配中文；旧 SQLite 回退默认切词。
                try:
                    db.execute(
                        "CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5("
                        "content, document_id UNINDEXED, chunk_id UNINDEXED, tokenize='trigram')")
                except sqlite3.OperationalError:
                    db.execute(
                        "CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5("
                        "content, document_id UNINDEXED, chunk_id UNINDEXED)")
                db.execute("PRAGMA user_version = 21")
            if version < 22:
                # V2-04: Advisor sessions, rolling summary memory and the advice
                # ledger. A suggestion card snapshot is stored as JSON together
                # with its params hash so settled entries can be replayed.
                db.execute("""
                    CREATE TABLE IF NOT EXISTS advisor_sessions (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL DEFAULT '',
                        user_profile_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                db.execute("""
                    CREATE TABLE IF NOT EXISTS advisor_messages (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL REFERENCES advisor_sessions(id) ON DELETE CASCADE,
                        role TEXT NOT NULL CHECK(role IN ('user', 'advisor')),
                        content TEXT NOT NULL,
                        suggestion_json TEXT,
                        created_at TEXT NOT NULL
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS advisor_messages_session_idx ON advisor_messages(session_id, created_at)")
                db.execute("""
                    CREATE TABLE IF NOT EXISTS advice_ledger (
                        id TEXT PRIMARY KEY,
                        session_id TEXT,
                        thscode TEXT NOT NULL,
                        name TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        confidence TEXT NOT NULL,
                        horizon TEXT NOT NULL,
                        card_json TEXT NOT NULL,
                        params_hash TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'tracking' CHECK(status IN ('tracking', 'settled', 'void')),
                        settled_at TEXT,
                        settlement_json TEXT,
                        created_at TEXT NOT NULL
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS advice_ledger_status_idx ON advice_ledger(status, created_at DESC)")
                db.execute("PRAGMA user_version = 22")
            if version < 23:
                # Advisor 建议的来源摘要单独保存，避免把可追溯材料伪装成建议卡字段。
                # 仅保存已裁剪的公开证据摘要，绝不写入密钥、请求头或模型隐藏推理。
                columns = {row["name"] for row in db.execute("PRAGMA table_info(advice_ledger)").fetchall()}
                if "evidence_json" not in columns:
                    db.execute("ALTER TABLE advice_ledger ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '[]'")
                db.execute("PRAGMA user_version = 23")
            if version < 24:
                # V2-04b：顾问深度任务落盘。任务状态、阶段与证据在进程重启后
                # 仍可查看与显式恢复；在途任务启动时统一收尾为 interrupted，
                # 绝不自动重放付费请求（计划 §2.6）。检查点只存阶段产物，
                # 不存模型隐藏推理。
                db.execute("""
                    CREATE TABLE IF NOT EXISTS advisor_runs (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        profile_json TEXT NOT NULL DEFAULT '{}',
                        history_json TEXT NOT NULL DEFAULT '[]',
                        allow_web_search INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'queued',
                        phases_json TEXT NOT NULL DEFAULT '[]',
                        evidence_json TEXT NOT NULL DEFAULT '[]',
                        checkpoint_json TEXT,
                        llm_requests INTEGER NOT NULL DEFAULT 0,
                        error TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                db.execute(
                    "CREATE INDEX IF NOT EXISTS advisor_runs_session_idx "
                    "ON advisor_runs(session_id, created_at DESC)")
                db.execute("PRAGMA user_version = 24")
            if version < 25:
                db.execute("""CREATE TABLE IF NOT EXISTS strategy_experiments (id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL, thscode TEXT NOT NULL, input_json TEXT NOT NULL, spec_json TEXT NOT NULL, data_snapshot_json TEXT NOT NULL, result_json TEXT NOT NULL, validation_grade TEXT NOT NULL, params_hash TEXT NOT NULL, created_at TEXT NOT NULL)""")
                db.execute("CREATE INDEX IF NOT EXISTS strategy_experiments_created_idx ON strategy_experiments(created_at DESC)")
                db.execute("PRAGMA user_version = 25")
            if version < 26:
                columns = {row["name"] for row in db.execute("PRAGMA table_info(kb_documents)").fetchall()}
                for name, definition in (("knowledge_role", "TEXT NOT NULL DEFAULT 'source'"), ("tags_json", "TEXT NOT NULL DEFAULT '[]'"), ("review_state", "TEXT NOT NULL DEFAULT 'unreviewed'"), ("effective_at", "TEXT")):
                    if name not in columns: db.execute(f"ALTER TABLE kb_documents ADD COLUMN {name} {definition}")
                db.execute("PRAGMA user_version = 26")
            if version < 27:
                columns = {row["name"] for row in db.execute("PRAGMA table_info(advisor_runs)").fetchall()}
                if "analysis_mode" not in columns:
                    db.execute("ALTER TABLE advisor_runs ADD COLUMN analysis_mode TEXT NOT NULL DEFAULT 'standard'")
                db.execute("PRAGMA user_version = 27")
            if version < 28:
                columns = {row["name"] for row in db.execute("PRAGMA table_info(advice_ledger)").fetchall()}
                for name, definition in (
                    ("strategy_ref_json", "TEXT NOT NULL DEFAULT '[]'"),
                    ("forecast_event_json", "TEXT NOT NULL DEFAULT '{}'"),
                    ("confidence_basis_json", "TEXT NOT NULL DEFAULT '{}'"),
                    ("evidence_hash", "TEXT NOT NULL DEFAULT ''"),
                    ("prompt_version", "TEXT NOT NULL DEFAULT 'advisor-v2'"),
                    ("model_profile_id", "TEXT"),
                ):
                    if name not in columns:
                        db.execute(f"ALTER TABLE advice_ledger ADD COLUMN {name} {definition}")
                db.execute("""CREATE TABLE IF NOT EXISTS strategy_reviews (
                    id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL,
                    horizon_days INTEGER NOT NULL, decision TEXT NOT NULL CHECK(decision IN ('validated','keep_candidate','retire')),
                    calibration_snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL)""")
                db.execute("CREATE INDEX IF NOT EXISTS strategy_reviews_lookup_idx ON strategy_reviews(strategy_id, strategy_version, horizon_days, created_at DESC)")
                db.execute("PRAGMA user_version = 28")
            if version < 29:
                columns = {row["name"] for row in db.execute("PRAGMA table_info(advice_ledger)").fetchall()}
                if "strategy_forecast_json" not in columns:
                    db.execute("ALTER TABLE advice_ledger ADD COLUMN strategy_forecast_json TEXT NOT NULL DEFAULT '[]'")
                db.execute("PRAGMA user_version = 29")
            if version < 30:
                # 双轨封存数据契约（交接 §8.2）：研究预测轨独立成表，登记即封存、
                # 只读可验证、事后可结算；执行建议轨在登记时冻结档位门禁快照。
                # 两条轨道各自统计，绝不与历史回放、覆盖度回放或人工前向验证混合。
                db.execute("""
                    CREATE TABLE IF NOT EXISTS research_predictions (
                        id TEXT PRIMARY KEY,
                        source TEXT NOT NULL DEFAULT 'manual',
                        thscode TEXT NOT NULL,
                        name TEXT NOT NULL DEFAULT '',
                        as_of_date TEXT NOT NULL,
                        horizon TEXT NOT NULL,
                        p_up REAL NOT NULL,
                        p_flat REAL NOT NULL,
                        p_down REAL NOT NULL,
                        dominant_scenario TEXT NOT NULL,
                        event TEXT NOT NULL,
                        return_threshold_pct REAL NOT NULL,
                        probability_source TEXT NOT NULL,
                        rationale TEXT NOT NULL DEFAULT '',
                        counter_evidence_json TEXT NOT NULL DEFAULT '[]',
                        invalidations_json TEXT NOT NULL DEFAULT '[]',
                        evidence_snapshot_json TEXT NOT NULL DEFAULT '[]',
                        evidence_hash TEXT NOT NULL DEFAULT '',
                        record_hash TEXT NOT NULL,
                        model_profile_id TEXT,
                        prompt_version TEXT NOT NULL DEFAULT '',
                        linked_advice_id TEXT,
                        status TEXT NOT NULL DEFAULT 'sealed' CHECK(status IN ('sealed','settled','void')),
                        settlement_json TEXT,
                        settled_at TEXT,
                        created_at TEXT NOT NULL
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS research_predictions_status_idx ON research_predictions(status, created_at DESC)")
                db.execute("CREATE INDEX IF NOT EXISTS research_predictions_code_idx ON research_predictions(thscode, created_at DESC)")
                columns = {row["name"] for row in db.execute("PRAGMA table_info(advice_ledger)").fetchall()}
                if "tier_snapshot_json" not in columns:
                    db.execute("ALTER TABLE advice_ledger ADD COLUMN tier_snapshot_json TEXT NOT NULL DEFAULT '{}'")
                db.execute("PRAGMA user_version = 30")
            if version < 31:
                # 同步失败需要给用户可操作的公开原因；只保存 AppError 的安全文案，
                # 不保存供应商响应、下载链接、请求头或密钥。
                columns = {row["name"] for row in db.execute("PRAGMA table_info(market_sync_runs)").fetchall()}
                if "failure_message" not in columns:
                    db.execute("ALTER TABLE market_sync_runs ADD COLUMN failure_message TEXT")
                db.execute("PRAGMA user_version = 31")
