"""V2-03 私有知识库服务：摄取、重建、删除与混合检索。

全文与分块仅存本地 SQLite；嵌入可选（qwen_embedding 凭据），
未配置时检索走 FTS5 关键词回退，文档内容零外发。
检索与摄取都是用户主动操作；服务绝不后台自动运行。
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from ..errors import AppError
from ..research.attachment_parsers import (
    ALLOWED_EXTENSIONS, AttachmentLimits, AttachmentParseError, parse_attachment)
from ..storage import Storage
from .chunking import ChunkSpec, split_chunks
from .embeddings import (
    QwenEmbeddingClient, cosine_similarity, embedding_config_from_options)
from .models import ALLOWED_TAGS, MAX_TAGS, KbSearchHit, KbSearchResult

_MIME_BY_EXTENSION = {
    "txt": "text/plain", "md": "text/markdown", "markdown": "text/markdown",
    "csv": "text/csv", "tsv": "text/tab-separated-values",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# 去掉 FTS5 查询语法字符，避免用户输入被当作操作符。
_FTS_STRIP = str.maketrans("", "", "'*()[]{}:,^")


def pack_vector(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_vector(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


class KbService:
    def __init__(self, storage: Storage, credentials, settings,
                 http: httpx.AsyncClient | None = None) -> None:
        self._storage = storage
        self._credentials = credentials
        self._settings = settings
        self._http = http

    # -- 状态（数据来源注册表端点） ------------------------------------------
    def source_status(self) -> dict:
        with self._storage.connect() as db:
            docs = db.execute("SELECT COUNT(*) FROM kb_documents").fetchone()[0]
            chunks = db.execute("SELECT COUNT(*) FROM kb_chunks").fetchone()[0]
            embedded = db.execute(
                "SELECT COUNT(*) FROM kb_chunks WHERE vector IS NOT NULL").fetchone()[0]
        if docs:
            if embedded:
                detail = f"本地知识库已就绪，共 {docs} 份文档、{chunks} 个分块（已嵌入 {embedded} 个）。"
            else:
                detail = f"本地知识库已就绪，共 {docs} 份文档、{chunks} 个分块（关键词检索）。"
            return {"enabled": True, "detail": detail}
        return {"enabled": False,
                "detail": "尚未上传文档，可在知识库页上传本地文档（解析只在本机进行）。"}

    # -- 摄取 ----------------------------------------------------------------
    async def ingest(self, filename: str, data: bytes,
                     *, limits: AttachmentLimits | None = None) -> dict:
        limits = limits or AttachmentLimits()
        if len(data) > limits.max_file_bytes:
            raise AppError("limit_exceeded",
                           f"文件超过 {limits.max_file_bytes // (1024 * 1024)} MB 上限。", 422)
        if not data:
            raise AppError("empty_file", "文件为空。", 422)
        safe_name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not safe_name or "/" in safe_name or "\\" in safe_name:
            raise AppError("invalid_filename", "请使用常规文件名。", 422)
        parts = safe_name.rsplit(".", 1)
        if len(parts) != 2:
            raise AppError("unsupported_type", "文件缺少扩展名，无法识别类型。", 422)
        extension = parts[1].lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise AppError("unsupported_type",
                           "仅支持 TXT/Markdown/CSV/TSV/XLSX/文本型 PDF。", 422)
        try:
            parsed = parse_attachment(data, extension=extension,
                                      mime_type=_MIME_BY_EXTENSION[extension],
                                      limits=limits)
        except AttachmentParseError as error:
            raise AppError(error.code, error.message, 422) from None
        digest = hashlib.sha256(data).hexdigest()
        existing = self._find_by_sha(digest)
        if existing is not None:
            return {"duplicate": True, "document": existing}
        if not parsed.text.strip():
            raise AppError("empty_file", "文件未解析到任何文本内容。", 422)
        chunks = split_chunks(parsed.text)
        if not chunks:
            raise AppError("empty_file", "文件未产生任何可检索分块。", 422)
        document_id = uuid4().hex
        now = _now_iso()
        warnings = list(parsed.warnings)
        with self._storage.connect() as db:
            db.execute(
                "INSERT INTO kb_documents(id, filename, kind, parse_method, sha256, byte_size, "
                "char_count, chunk_count, full_text, warnings_json, embedding_state, "
                "embedding_model, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?, 'none', '', ?, ?)",
                (document_id, safe_name, parsed.kind, parsed.parse_method, digest, len(data),
                 len(parsed.text), len(chunks), parsed.text,
                 json.dumps(warnings, ensure_ascii=False), now, now))
            self._insert_chunks(db, document_id, chunks)
        await self._embed_document_if_configured(document_id, warnings)
        return {"duplicate": False, "document": self.get_document(document_id)}

    def _find_by_sha(self, digest: str) -> dict | None:
        with self._storage.connect() as db:
            row = db.execute("SELECT id FROM kb_documents WHERE sha256=?", (digest,)).fetchone()
        return self.get_document(row["id"]) if row is not None else None

    def _insert_chunks(self, db, document_id: str, chunks: list[ChunkSpec]) -> None:
        for position, chunk in enumerate(chunks):
            chunk_id = uuid4().hex
            db.execute(
                "INSERT INTO kb_chunks(id, document_id, position, heading, page, line_start, "
                "line_end, char_start, char_end, content) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (chunk_id, document_id, position, chunk.heading, chunk.page,
                 chunk.line_start, chunk.line_end, chunk.char_start, chunk.char_end,
                 chunk.content))
            db.execute("INSERT INTO kb_fts(content, document_id, chunk_id) VALUES(?,?,?)",
                       (chunk.content, document_id, chunk_id))

    # -- 文档管理 -------------------------------------------------------------
    def list_documents(self) -> list[dict]:
        with self._storage.connect() as db:
            rows = db.execute(
                "SELECT * FROM kb_documents ORDER BY created_at DESC, id").fetchall()
        return [self._document_view(dict(row)) for row in rows]

    def get_document(self, document_id: str) -> dict:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM kb_documents WHERE id=?",
                             (document_id,)).fetchone()
            if row is None:
                raise AppError("not_found", "知识库文档不存在。", 404)
        return self._document_view(dict(row))

    def list_chunks(self, document_id: str, *, page: int | None = None,
                    limit: int = 400) -> dict:
        """只读本机分块原文：让顾问证据可以回溯到知识库的原始定位。

        内容仅从本地库读取并返回给本机界面，绝不外发。
        """
        document = self.get_document(document_id)
        where = "document_id=?"
        params: list = [document_id]
        if page is not None:
            where += " AND page=?"
            params.append(int(page))
        params.append(max(1, min(int(limit), 1000)))
        with self._storage.connect() as db:
            rows = db.execute(
                "SELECT id, position, heading, page, line_start, line_end, content "
                f"FROM kb_chunks WHERE {where} ORDER BY position LIMIT ?",
                params).fetchall()
        return {"document": document,
                "chunks": [{"chunk_id": row["id"], "position": row["position"],
                            "heading": row["heading"] or "", "page": row["page"],
                            "line_start": row["line_start"], "line_end": row["line_end"],
                            "content": row["content"]} for row in rows]}

    def _document_view(self, row: dict) -> dict:
        document_id = row["id"]
        with self._storage.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM kb_chunks WHERE document_id=?",
                               (document_id,)).fetchone()[0]
            embedded = db.execute(
                "SELECT COUNT(*) FROM kb_chunks WHERE document_id=? AND vector IS NOT NULL",
                (document_id,)).fetchone()[0]
        if total and embedded == total:
            state = "full"
        elif embedded:
            state = "partial"
        else:
            state = "none"
        return {
            "id": row["id"],
            "filename": row["filename"],
            "kind": row["kind"],
            "parse_method": row["parse_method"],
            "byte_size": row["byte_size"],
            "char_count": row["char_count"],
            "chunk_count": total,
            "embedded_chunks": embedded,
            "embedding_state": state,
            "embedding_model": row.get("embedding_model") or "",
            "warnings": json.loads(row.get("warnings_json") or "[]"),
            "knowledge_role": row.get("knowledge_role") or "source",
            "tags": json.loads(row.get("tags_json") or "[]"),
            "review_state": row.get("review_state") or "unreviewed",
            "effective_at": row.get("effective_at"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def update_metadata(self, document_id: str, *, knowledge_role: str, tags: list[str], review_state: str, effective_at: str | None) -> dict:
        if knowledge_role == "strategy" and review_state not in {"approved", "retired"}:
            raise AppError("validation", "策略资料必须显式审核通过后才能标记为 strategy。", 422)
        if knowledge_role == "hypothesis" and review_state == "approved":
            raise AppError("validation", "假设资料不能自动升级为已审核策略。", 422)
        cleaned = [item.strip() for item in tags if item.strip()]
        if len(cleaned) != len(set(cleaned)) or any(len(item) > 40 for item in cleaned):
            raise AppError("validation", "标签必须唯一且不超过40字。", 422)
        if len(cleaned) > MAX_TAGS:
            raise AppError("validation", f"标签最多 {MAX_TAGS} 个。", 422)
        unknown = sorted(set(cleaned) - ALLOWED_TAGS)
        if unknown:
            raise AppError("validation", "标签只接受服务端固定词表（资产类型/期限/策略族/市场状态），未知标签：" + "、".join(unknown[:5]) + "。", 422)
        if effective_at is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", effective_at):
            raise AppError("validation", "生效日期必须为 YYYY-MM-DD 或留空。", 422)
        self.get_document(document_id)
        with self._storage.connect() as db:
            db.execute("UPDATE kb_documents SET knowledge_role=?, tags_json=?, review_state=?, effective_at=?, updated_at=? WHERE id=?", (knowledge_role, json.dumps(cleaned, ensure_ascii=False), review_state, effective_at, _now_iso(), document_id))
        return self.get_document(document_id)

    def delete_document(self, document_id: str) -> bool:
        with self._storage.connect() as db:
            row = db.execute("SELECT id FROM kb_documents WHERE id=?",
                             (document_id,)).fetchone()
            if row is None:
                return False
            db.execute("DELETE FROM kb_fts WHERE document_id=?", (document_id,))
            db.execute("DELETE FROM kb_documents WHERE id=?", (document_id,))
        return True

    async def rebuild(self, document_id: str) -> dict:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM kb_documents WHERE id=?",
                             (document_id,)).fetchone()
            if row is None:
                raise AppError("not_found", "知识库文档不存在。", 404)
            full_text = row["full_text"]
            warnings = json.loads(row["warnings_json"] or "[]")
            db.execute("DELETE FROM kb_fts WHERE document_id=?", (document_id,))
            db.execute("DELETE FROM kb_chunks WHERE document_id=?", (document_id,))
            chunks = split_chunks(full_text)
            self._insert_chunks(db, document_id, chunks)
            db.execute("UPDATE kb_documents SET chunk_count=?, updated_at=? WHERE id=?",
                       (len(chunks), _now_iso(), document_id))
        await self._embed_document_if_configured(document_id, warnings)
        return self.get_document(document_id)

    # -- 嵌入 -----------------------------------------------------------------
    def embedding_client(self) -> QwenEmbeddingClient | None:
        if self._http is None:
            return None
        try:
            rows = self._credentials.repository.list()
        except AppError:
            return None
        row = next((item for item in rows if item.get("provider") == "qwen_embedding"), None)
        if row is None or not row.get("enabled"):
            return None
        key_status = self._credentials.root.encryption.key_status(row.get("encrypted_api_key"))
        if key_status != "configured":
            return None
        try:
            options = json.loads(row.get("options_json") or "{}")
            config = embedding_config_from_options(options)
        except (ValueError, AppError):
            return None
        secret = self._credentials.root.encryption.decrypt(row["encrypted_api_key"])
        return QwenEmbeddingClient(self._http, api_key=secret.get_secret_value(), **config)

    async def _embed_document_if_configured(self, document_id: str, warnings: list[str]) -> None:
        client = self.embedding_client()
        if client is None:
            return
        try:
            await self.embed_document(document_id, client)
        except AppError as error:
            warnings.append(f"嵌入未完成：{error.message}；已保留关键词检索能力。")
            with self._storage.connect() as db:
                db.execute("UPDATE kb_documents SET warnings_json=? WHERE id=?",
                           (json.dumps(warnings, ensure_ascii=False), document_id))

    async def embed_document(self, document_id: str, client: QwenEmbeddingClient) -> None:
        with self._storage.connect() as db:
            rows = db.execute(
                "SELECT id, content FROM kb_chunks WHERE document_id=? ORDER BY position",
                (document_id,)).fetchall()
        if not rows:
            return
        vectors = await client.embed([row["content"] for row in rows])
        with self._storage.connect() as db:
            for row, vector in zip(rows, vectors):
                db.execute("UPDATE kb_chunks SET vector=?, embedding_model=? WHERE id=?",
                           (pack_vector(vector), client.model_name, row["id"]))
            db.execute("UPDATE kb_documents SET embedding_model=?, updated_at=? WHERE id=?",
                       (client.model_name, _now_iso(), document_id))

    async def test_capability(self, provider: str, capability: str,
                              credential_version: int | None) -> dict:
        client = self.embedding_client()
        message = ""
        status = "passed"
        if client is None:
            status = "failed"
            message = "未找到已启用的嵌入服务凭据。"
        else:
            try:
                vectors = await client.embed(["连通性测试"])
                if not vectors or len(vectors[0]) != client.dimension:
                    status = "failed"
                    message = "嵌入服务返回的向量维度与配置不一致。"
                else:
                    message = f"向量化正常（模型 {client.model_name}，维度 {client.dimension}）。"
            except AppError as error:
                status = "failed"
                message = error.message
        return {"capability": capability, "status": status, "message": message,
                "tested_at": _now_iso(), "credential_version": credential_version}

    # -- 检索 -----------------------------------------------------------------
    async def search(self, query: str, *, top_k: int = 5, knowledge_roles: list[str] | None = None,
                     review_states: list[str] | None = None, asset_type: str | None = None,
                     horizon: str | None = None, strategy_family: str | None = None,
                     market_regime: str | None = None) -> KbSearchResult:
        query = (query or "").strip()
        if not query:
            raise AppError("validation", "请输入检索词。", 422)
        top_k = max(1, min(int(top_k), 10))
        warnings: list[str] = []
        client = self.embedding_client()
        vector_hits: list[tuple[str, list[float]]] = []
        embedding_used = False
        if client is not None:
            try:
                query_vector = (await client.embed([query]))[0]
                vector_hits = self._vector_candidates(query_vector, client.model_name)
                embedding_used = True
            except AppError as error:
                warnings.append(f"向量检索不可用（{error.message}），已回退关键词检索。")
        fts_hits = self._fts_candidates(query)
        merged = self._rrf_merge(fts_hits, vector_hits)
        # 先按元数据过滤、再做确定性加分；每篇最多两个分块，避免单文档垄断。
        # 显式传入的资产/期限/策略族/状态维度是硬过滤：文档必须带对应标签；
        # 精确标签、标题命中与证券代码再给确定性加分，全程不调用模型。
        candidates = self._hydrate(merged)
        wanted_tags = {item for item in (asset_type, horizon, strategy_family, market_regime) if item}
        documents = {item["id"]: item for item in self.list_documents()}
        code = re.search(r"(?<!\d)[0-9]{6}(?!\d)", query)
        filtered = []
        for hit in candidates:
            doc = documents.get(hit.document_id, {})
            if knowledge_roles and doc.get("knowledge_role") not in knowledge_roles: continue
            if review_states and doc.get("review_state") not in review_states: continue
            tags = set(doc.get("tags") or [])
            if not wanted_tags <= tags: continue
            bonus = len(tags & wanted_tags) * 0.01 + (0.005 if hit.filename.lower() in query.lower() else 0)
            if code and (code.group(0) in tags or code.group(0) in hit.excerpt or code.group(0) in hit.filename):
                bonus += 0.008
            filtered.append(hit.model_copy(update={"score": round(hit.score + bonus, 6)}))
        filtered.sort(key=lambda item: (-item.score, item.document_id, item.chunk_id))
        per_doc: dict[str, int] = {}; hits=[]
        for hit in filtered:
            if per_doc.get(hit.document_id, 0) >= 2: continue
            per_doc[hit.document_id] = per_doc.get(hit.document_id, 0) + 1; hits.append(hit)
            if len(hits) >= top_k: break
        if embedding_used:
            channel_text = "、".join(sorted({c for hit in hits for c in hit.channels})) or "无"
            note = f"混合检索（向量+关键词，通道：{channel_text}）；命中为可核验佐证，引用时注明文档名与定位。"
        else:
            note = "本地关键词检索（未启用嵌入）；命中为可核验佐证，引用时注明文档名与定位。"
        return KbSearchResult(query=query, embedding_used=embedding_used,
                              embedding_model=client.model_name if embedding_used else "",
                              hits=hits, warnings=warnings, note=note)

    def _vector_candidates(self, query_vector: list[float],
                           model: str) -> list[tuple[str, list[float]]]:
        scored: list[tuple[str, list[float]]] = []
        with self._storage.connect() as db:
            rows = db.execute(
                "SELECT id, vector FROM kb_chunks WHERE vector IS NOT NULL AND embedding_model=?",
                (model,)).fetchall()
        for row in rows:
            score = cosine_similarity(query_vector, unpack_vector(row["vector"]))
            scored.append((row["id"], [score]))
        scored.sort(key=lambda item: item[1][0], reverse=True)
        return scored[:40]

    def _fts_candidates(self, query: str) -> list[tuple[str, list[float]]]:
        """FTS5 关键词候选：三字以上词走 MATCH，短词走 LIKE 兜底。

        两类命中不混用原始分值（bm25 与字符计数不可比）：先保留全部
        MATCH 命中（按 bm25 排序），再追加 LIKE 兜底命中；同名分块只保留
        首次出现，保证 RRF 合并前的通道内排序确定。
        """
        matched: list[tuple[str, list[float]]] = []
        fallback: list[tuple[str, list[float]]] = []
        match_terms = [term for term in self._terms(query) if len(term) >= 3]
        like_terms = [term for term in self._terms(query) if 0 < len(term) < 3]
        with self._storage.connect() as db:
            if match_terms:
                clause = " AND ".join(f'"{term}"' for term in match_terms)
                try:
                    rows = db.execute(
                        "SELECT chunk_id, bm25(kb_fts) AS rank FROM kb_fts "
                        "WHERE kb_fts MATCH ? ORDER BY rank LIMIT 40", (clause,)).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                for row in rows:
                    score = -float(row["rank"]) if row["rank"] is not None else 0.0
                    matched.append((row["chunk_id"], [score]))
            if like_terms:
                existing = {chunk_id for chunk_id, _ in matched}
                like_scored: list[tuple[str, float]] = []
                for term in like_terms:
                    rows = db.execute(
                        "SELECT id, content FROM kb_chunks WHERE content LIKE ? LIMIT 400",
                        (f"%{term}%",)).fetchall()
                    for row in rows:
                        if row["id"] in existing:
                            continue
                        like_scored.append((row["id"], float(row["content"].count(term))))
                like_scored.sort(key=lambda item: (-item[1], item[0]))
                for chunk_id, count in like_scored:
                    if chunk_id in existing:
                        continue
                    existing.add(chunk_id)
                    fallback.append((chunk_id, [count]))
        return (matched + fallback)[:40]

    @staticmethod
    def _terms(query: str) -> list[str]:
        cleaned = query.translate(_FTS_STRIP)
        tokens = [term.strip() for term in cleaned.split() if term.strip()]
        return tokens or ([cleaned.strip()] if cleaned.strip() else [])

    @staticmethod
    def _rrf_merge(*ranked_lists, k: int = 60):
        merged: dict = {}
        channel_names = ("fts", "vector")
        for list_index, ranked in enumerate(ranked_lists):
            for rank, (chunk_id, _) in enumerate(ranked, start=1):
                entry = merged.setdefault(chunk_id, {"score": 0.0, "channels": []})
                entry["score"] += 1.0 / (k + rank)
                channel = (channel_names[list_index]
                           if list_index < len(channel_names) else f"list{list_index}")
                if channel not in entry["channels"]:
                    entry["channels"].append(channel)
        ordered = sorted(merged.items(), key=lambda item: item[1]["score"], reverse=True)
        return [(chunk_id, data["score"], data["channels"]) for chunk_id, data in ordered]

    def _hydrate(self, merged) -> list[KbSearchHit]:
        if not merged:
            return []
        hits: list[KbSearchHit] = []
        with self._storage.connect() as db:
            for chunk_id, score, channels in merged:
                row = db.execute(
                    "SELECT c.id, c.heading, c.page, c.line_start, c.line_end, c.content, "
                    "d.id AS document_id, d.filename, d.kind "
                    "FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id "
                    "WHERE c.id=?", (chunk_id,)).fetchone()
                if row is None:
                    continue
                content = row["content"]
                excerpt = content if len(content) <= 400 else content[:400] + "…"
                hits.append(KbSearchHit(
                    chunk_id=row["id"], document_id=row["document_id"],
                    filename=row["filename"], kind=row["kind"],
                    heading=row["heading"] or "",
                    page=row["page"], line_start=row["line_start"], line_end=row["line_end"],
                    score=round(float(score), 6), channels=list(channels), excerpt=excerpt))
        return hits
