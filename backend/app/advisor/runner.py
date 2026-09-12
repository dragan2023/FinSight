"""单进程 Advisor 深度任务：立即返回、显式取消与显式恢复。

任务状态落盘（advisor_runs）：进程重启后，上一个进程遗留的在途任务统一
收尾为 interrupted，已完成任务的阶段与证据仍可查看；恢复必须由用户显式
触发，绝不自动重放付费请求。恢复按最近检查点续跑，只补做未完成的付费
阶段（检查点由顾问图逐阶段产出，只含阶段产物，不含模型隐藏推理）。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from ..errors import AppError
from ..storage import Storage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AdvisorRun:
    id: str
    session_id: str
    question: str
    profile: dict
    history: list[dict]
    allow_web_search: bool
    analysis_mode: str = "standard"
    status: str = "queued"
    phases: list[dict] = field(default_factory=list)
    llm_requests: int = 0
    error: str | None = None
    evidence: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    checkpoint: dict | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def view(self) -> dict:
        # question/profile/history 与检查点不进入公开视图：
        # SSE 只暴露可展示的状态、阶段与证据摘要。
        return {"id": self.id, "session_id": self.session_id, "status": self.status,
                "phases": self.phases, "llm_requests": self.llm_requests, "error": self.error,
                "evidence": self.evidence, "analysis_mode": self.analysis_mode,
                "created_at": self.created_at, "updated_at": self.updated_at}


class AdvisorRunStore:
    """advisor_runs 表读写：让任务状态与阶段产物跨进程重启可查、可恢复。"""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def save(self, run: AdvisorRun) -> None:
        with self._storage.connect() as db:
            db.execute(
                "INSERT INTO advisor_runs(id, session_id, question, profile_json, history_json,"
                " allow_web_search, analysis_mode, status, phases_json, evidence_json, checkpoint_json,"
                " llm_requests, error, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET status=excluded.status,"
                " phases_json=excluded.phases_json, evidence_json=excluded.evidence_json,"
                " checkpoint_json=excluded.checkpoint_json, llm_requests=excluded.llm_requests,"
                " error=excluded.error, updated_at=excluded.updated_at",
                (run.id, run.session_id, run.question,
                 json.dumps(run.profile, ensure_ascii=False),
                 json.dumps(run.history, ensure_ascii=False), int(run.allow_web_search),
                 run.analysis_mode, run.status, json.dumps(run.phases, ensure_ascii=False),
                 json.dumps(run.evidence, ensure_ascii=False),
                 json.dumps(run.checkpoint, ensure_ascii=False) if run.checkpoint is not None else None,
                 run.llm_requests, run.error, run.created_at, run.updated_at))

    def get(self, run_id: str) -> AdvisorRun | None:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM advisor_runs WHERE id=?", (run_id,)).fetchone()
        return self._run(dict(row)) if row is not None else None

    def list_by_session(self, session_id: str, *, limit: int = 20) -> list[AdvisorRun]:
        limit = max(1, min(int(limit), 50))
        with self._storage.connect() as db:
            rows = db.execute(
                "SELECT * FROM advisor_runs WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit)).fetchall()
        return [self._run(dict(row)) for row in rows]

    def mark_inflight_interrupted(self) -> int:
        """进程启动时收尾上一个进程遗留的在途任务，绝不自动重放付费请求。"""
        with self._storage.connect() as db:
            cursor = db.execute(
                "UPDATE advisor_runs SET status='interrupted', error=?, updated_at=? "
                "WHERE status IN ('queued','running')",
                ("服务重启导致任务中断；可显式恢复。", _now()))
        return cursor.rowcount

    @staticmethod
    def _run(row: dict) -> AdvisorRun:
        return AdvisorRun(
            id=row["id"], session_id=row["session_id"], question=row["question"],
            profile=json.loads(row["profile_json"] or "{}"),
            history=json.loads(row["history_json"] or "[]"),
            allow_web_search=bool(row["allow_web_search"]), analysis_mode=row.get("analysis_mode", "standard"), status=row["status"],
            phases=json.loads(row["phases_json"] or "[]"),
            llm_requests=row["llm_requests"], error=row["error"],
            evidence=json.loads(row["evidence_json"] or "[]"),
            checkpoint=json.loads(row["checkpoint_json"]) if row["checkpoint_json"] else None,
            created_at=row["created_at"], updated_at=row["updated_at"])


class AdvisorRunManager:
    """不引入队列服务；恢复必须由用户显式触发，绝不自动重放付费请求。"""

    def __init__(self, *, graph=None, graph_factory=None, service,
                 store: AdvisorRunStore | None = None, max_active: int = 1) -> None:
        if graph is None and graph_factory is None:
            raise ValueError("AdvisorRunManager requires graph or graph_factory")
        self._graph, self._graph_factory, self._service = graph, graph_factory, service
        self._store = store
        self._runs: dict[str, AdvisorRun] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(max_active)

    def start(self, *, session_id: str, question: str, profile: dict, history: list[dict], allow_web_search: bool,
              analysis_mode: str = "standard") -> AdvisorRun:
        run = AdvisorRun(id=uuid4().hex, session_id=session_id, question=question, profile=profile,
                         history=history, allow_web_search=allow_web_search, analysis_mode=analysis_mode)
        self._runs[run.id] = run
        self._persist(run)
        self._submit(run)
        return run

    def get(self, run_id: str) -> AdvisorRun:
        run = self._runs.get(run_id)
        if run is None and self._store is not None:
            # 进程重启后从落盘恢复：任务重新进入内存，可被显式恢复。
            restored = self._store.get(run_id)
            if restored is not None:
                run = restored
                self._runs[run.id] = run
        if run is None:
            raise AppError("not_found", "顾问任务不存在。", 404)
        return run

    def list_for_session(self, session_id: str, *, limit: int = 20) -> list[dict]:
        if self._store is None:
            return []
        return [run.view() for run in self._store.list_by_session(session_id, limit=limit)]

    def events_after(self, run_id: str, after: int) -> list[dict]:
        """仅保留公开任务状态；不会把模型隐藏推理写入或推送给浏览器。"""
        return [event for event in self.get(run_id).events if event["seq"] > after]

    def _persist(self, run: AdvisorRun) -> None:
        if self._store is not None:
            self._store.save(run)

    def _event(self, run: AdvisorRun, event_type: str) -> None:
        run.events.append({"seq": len(run.events) + 1, "type": event_type, "run": run.view()})
        run.updated_at = _now()
        self._persist(run)

    def _submit(self, run: AdvisorRun) -> None:
        # 恢复时保留既有阶段、证据与检查点；全新任务的这些字段本就是空值。
        run.status, run.error, run.updated_at = "queued", None, _now()
        self._event(run, "queued")
        self._workers[run.id] = asyncio.create_task(self._execute(run))

    async def cancel(self, run_id: str) -> AdvisorRun:
        run = self.get(run_id)
        worker = self._workers.get(run_id)
        if run.status in {"completed", "cancelled", "failed"}:
            return run
        if worker and not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        else:
            run.status, run.updated_at = "cancelled", _now()
            self._event(run, "cancelled")
        return run

    def resume(self, run_id: str) -> AdvisorRun:
        run = self.get(run_id)
        if run.status not in {"cancelled", "failed", "interrupted"}:
            raise AppError("run_not_resumable", "只有已取消、中断或失败的深度任务可以恢复。", 409)
        self._submit(run)
        return run

    async def shutdown(self) -> None:
        workers = [worker for worker in self._workers.values() if not worker.done()]
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)

    async def _execute(self, run: AdvisorRun) -> None:
        try:
            async with self._semaphore:
                run.status, run.updated_at = "running", _now()
                self._event(run, "running")
                def phase_sink(phase: dict) -> None:
                    run.phases.append(phase)
                    run.updated_at = _now()
                    self._event(run, "phase")

                def checkpoint_sink(state: dict) -> None:
                    run.checkpoint = state
                    run.updated_at = _now()
                    self._persist(run)

                graph = self._graph_factory() if self._graph_factory is not None else self._graph
                outcome = await graph.run(session_id=run.session_id, question=run.question,
                                                profile=run.profile, history=run.history,
                                                allow_web_search=run.allow_web_search,
                                                analysis_mode=run.analysis_mode,
                                                event_sink=phase_sink,
                                                resume_state=run.checkpoint,
                                                checkpoint_sink=checkpoint_sink)
                run.evidence = list(outcome.get("evidence") or [])
                suggestion = outcome["card"]
                if suggestion is not None and run.evidence:
                    suggestion = {**suggestion, "evidence": run.evidence}
                self._service.add_message(run.session_id, "advisor", outcome["reply"], suggestion=suggestion)
                run.status, run.phases, run.llm_requests, run.updated_at = (
                    "completed", outcome["phases"], outcome["llm_requests"], _now())
                self._event(run, "completed")
        except asyncio.CancelledError:
            run.status, run.updated_at = "cancelled", _now()
            self._event(run, "cancelled")
            raise
        except AppError as error:
            run.status, run.error, run.updated_at = "failed", error.message, _now()
            self._event(run, "failed")
        except Exception:
            run.status, run.error, run.updated_at = "failed", "顾问任务执行失败，请显式恢复或新建会话。", _now()
            self._event(run, "failed")
        finally:
            self._workers.pop(run.id, None)
