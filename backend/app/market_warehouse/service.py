from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import AppError
from .download import download_parquet
from .repository import WarehouseRepository
from .schema import SCHEMA_VERSION

if TYPE_CHECKING:
    from fastapi import UploadFile

_IMPORT_CHUNK_BYTES = 1024 * 1024


class MarketWarehouseService:
    """Single-process, user-started dump jobs with observable terminal states."""

    def __init__(self, storage, settings, market, http):
        self.storage, self.settings, self.market, self.http = storage, settings, market, http
        self.repository = WarehouseRepository(settings.market_warehouse_path)
        self._jobs: dict[str, asyncio.Task] = {}

    def status(self):
        return self.repository.status()

    def _create_run(self, kind: str) -> str:
        run_id = uuid.uuid4().hex
        with self.storage.connect() as db:
            db.execute("INSERT INTO market_sync_runs(id,kind,status,created_at) VALUES(?,?,?,?)", (
                run_id, kind, "queued", datetime.now(timezone.utc).isoformat()))
        return run_id

    def run_status(self, run_id: str) -> dict:
        with self.storage.connect() as db:
            row = db.execute("SELECT id,kind,status,failure_type,failure_message,created_at,finished_at FROM market_sync_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise AppError("not_found", "全市场同步任务不存在。", 404)
        return dict(row)

    async def start_sync(self, kind: str) -> dict:
        if any(not task.done() for task in self._jobs.values()):
            raise AppError("request_conflict", "已有全市场同步正在进行，请先等待或取消。", 409)
        run_id = self._create_run(kind)
        task = asyncio.create_task(self._run_sync(run_id, kind))
        self._jobs[run_id] = task
        task.add_done_callback(lambda _task: self._jobs.pop(run_id, None))
        return self.run_status(run_id)

    async def sync(self, kind: str) -> dict:
        """Synchronous compatibility entry used by focused service tests only."""
        run_id = self._create_run(kind)
        return await self._run_sync(run_id, kind)

    async def import_daily_parquet(self, upload: "UploadFile") -> dict:
        """Import a user-selected local daily-K Parquet through the same checks.

        This is a local fallback for an unavailable one-time download link.  It
        never sends the file to a third party and does not weaken the schema or
        quality gates used by provider downloads.
        """
        filename = (upload.filename or "").strip()
        if not filename.lower().endswith(".parquet"):
            await upload.close()
            raise AppError("validation", "请选择 .parquet 格式的全市场日 K 文件。", 422)
        run_id = self._create_run("daily_k_import")
        target = self.settings.market_warehouse_path.parent / "manual_daily_k.parquet"
        temp = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
        with self.storage.connect() as db:
            db.execute("UPDATE market_sync_runs SET status='running' WHERE id=?", (run_id,))
        digest = hashlib.sha256()
        total = 0
        try:
            with temp.open("xb") as output:
                while True:
                    chunk = await upload.read(_IMPORT_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.settings.market_import_cap_bytes:
                        raise AppError("payload_too_large", "导入文件超过全市场数据大小上限。", 413)
                    digest.update(chunk)
                    output.write(chunk)
            return self._ingest_file(run_id, "daily_k_import", temp, digest.hexdigest(),
                                           final_target=target)
        except Exception as error:
            failure_type, failure_message = self._public_failure(error)
            with self.storage.connect() as db:
                db.execute("UPDATE market_sync_runs SET status='failed',failure_type=?,failure_message=?,finished_at=? WHERE id=?", (failure_type, failure_message, datetime.now(timezone.utc).isoformat(), run_id))
            raise
        finally:
            await upload.close()
            temp.unlink(missing_ok=True)

    async def cancel_sync(self, run_id: str) -> dict:
        current = self.run_status(run_id)
        task = self._jobs.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.sleep(0)
        return self.run_status(run_id) if current["status"] not in {"completed", "failed", "cancelled"} else current

    async def shutdown(self) -> None:
        tasks = [task for task in self._jobs.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._jobs.clear()

    async def _run_sync(self, run_id: str, kind: str) -> dict:
        created = datetime.now(timezone.utc).isoformat()
        with self.storage.connect() as db:
            db.execute("UPDATE market_sync_runs SET status='running' WHERE id=?", (run_id,))
        try:
            payload = await self.market.fetch_market_dump_link(kind)
            # 同花顺 Financial API Market Dumps 的正式字段是 presigned_url；
            # 保留旧适配字段以兼容已保存的测试夹具。
            url = payload.get("presigned_url") or payload.get("download_url") or payload.get("url")
            if not isinstance(url, str):
                raise AppError("invalid_response", "数据源未返回可下载链接。", 502)
            target = self.settings.market_warehouse_path.parent / f"{kind}.parquet"
            file, digest = await download_parquet(self.http, url, target)
            return self._ingest_file(run_id, kind, file, digest, final_target=file, created=created)
        except asyncio.CancelledError:
            with self.storage.connect() as db:
                db.execute("UPDATE market_sync_runs SET status='cancelled',failure_type='cancelled',finished_at=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), run_id))
            raise
        except Exception as error:
            failure_type, failure_message = self._public_failure(error)
            with self.storage.connect() as db:
                db.execute("UPDATE market_sync_runs SET status='failed',failure_type=?,failure_message=?,finished_at=? WHERE id=?", (failure_type, failure_message, datetime.now(timezone.utc).isoformat(), run_id))
            raise

    @staticmethod
    def _public_failure(error: Exception) -> tuple[str, str]:
        """Return a user-safe failure explanation without persisting upstream data."""
        if isinstance(error, AppError):
            return error.category, error.message[:240]
        if isinstance(error, ValueError):
            return "invalid_response", "下载文件的格式或必要字段不符合全市场数据集契约。"
        return "failed", "本机处理下载数据时出现未分类错误；请重试，仍失败请保留本次任务编号。"

    def _ingest_file(self, run_id: str, kind: str, file: Path, digest: str, *,
                           final_target: Path, created: str | None = None) -> dict:
        """Validate then atomically register one supported official dataset."""
        quality = self.repository.validate_parquet(file, kind=kind)
        daily_quality_failure = kind != "adjustment_factors" and (quality["duplicates"] or quality.get("bad_close", 0))
        if daily_quality_failure or quality.get("invalid_rows", 0):
            raise AppError("invalid_response", "数据质量校验未通过，保留上一版数据。", 422)
        if kind == "adjustment_factors":
            self.repository.upsert_adjustment_factors(file)
        else:
            self.repository.upsert_daily(file)
        if file != final_target:
            os.replace(file, final_target)
        dataset_id = uuid.uuid4().hex
        created = created or datetime.now(timezone.utc).isoformat()
        with self.storage.connect() as db:
            db.execute("INSERT INTO market_datasets(id,kind,version,row_count,min_date_ms,max_date_ms,sha256,schema_version,status,quality_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (dataset_id, kind, "v1", quality["row_count"], quality["min_date_ms"], quality["max_date_ms"], digest, SCHEMA_VERSION, "available", json.dumps(quality, ensure_ascii=False, sort_keys=True), created))
            db.execute("UPDATE market_sync_runs SET dataset_id=?,status='completed',finished_at=? WHERE id=?", (dataset_id, datetime.now(timezone.utc).isoformat(), run_id))
        return {"sync_id": run_id, "status": "completed", "quality": quality}
