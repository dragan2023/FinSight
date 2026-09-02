"""Task lifecycle service (plan 03 sections 2.1 / 2.6).

Task.status 描述本轮执行如何结束：completed 只说明本轮结束，不代表数据完整。
数据缺失、模型部分失败、输出截断都仍以 completed 结束，原因由报告与缺口表达；
cancelled 来自用户主动取消，interrupted 来自进程重启后的收尾，failed 只保留给
本地执行意外。状态迁移集中在这一层，SQLite 只存业务进度。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .models import ResearchTask, TaskBudget, TaskEvent, TaskStatus, TaskUsage


class TaskStore:
    def __init__(self, repository) -> None:
        self._repository = repository

    def create(self, *, question: str, scope: dict | None = None,
               budget: TaskBudget | None = None, authorization: dict | None = None,
               client_request_id: str | None = None, request_hash: str | None = None,
               model_profile_id: str | None = None) -> ResearchTask:
        task = ResearchTask(
            id=uuid.uuid4().hex, question=question, scope=dict(scope or {}),
            budget=budget or TaskBudget(), authorization=dict(authorization or {}),
            client_request_id=client_request_id, request_hash=request_hash,
            model_profile_id=model_profile_id)
        self._repository.save_task(task)
        self.event(task.id, "task.created", {"status": task.status, "scope": task.scope})
        return task

    def get(self, task_id: str) -> ResearchTask | None:
        return self._repository.get_task(task_id)

    def list_by_status(self, status: TaskStatus) -> list[ResearchTask]:
        return self._repository.list_tasks_by_status(status)

    # -- transitions ---------------------------------------------------------
    def mark_running(self, task_id: str) -> ResearchTask:
        task = self._require(task_id)
        if task.status not in (TaskStatus.QUEUED,):
            raise ValueError(f"任务状态 {task.status} 不能转为 running")
        return self._save(task, TaskStatus.RUNNING)

    def mark_completed(self, task_id: str, *, usage: TaskUsage | None = None,
                       report_id: str | None = None) -> ResearchTask:
        task = self._require(task_id)
        if task.status not in (TaskStatus.RUNNING, TaskStatus.QUEUED):
            raise ValueError(f"任务状态 {task.status} 不能转为 completed")
        updated = self._save(task, TaskStatus.COMPLETED, usage=usage, report_id=report_id,
                             finished=True)
        self.event(task_id, "task.completed", {"report_id": updated.report_id})
        return updated

    def mark_cancelled(self, task_id: str, *, usage: TaskUsage | None = None) -> ResearchTask:
        task = self._require(task_id)
        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            raise ValueError(f"任务状态 {task.status} 不能转为 cancelled")
        updated = self._save(task, TaskStatus.CANCELLED, usage=usage, finished=True)
        self.event(task_id, "task.cancelled", {})
        return updated

    def mark_interrupted(self, task_id: str, *, usage: TaskUsage | None = None) -> ResearchTask:
        """进程重启收尾：running → interrupted，不自动再次付费（计划 §2.6）。"""
        task = self._require(task_id)
        if task.status not in (TaskStatus.RUNNING, TaskStatus.QUEUED):
            raise ValueError(f"任务状态 {task.status} 不能转为 interrupted")
        updated = self._save(task, TaskStatus.INTERRUPTED, usage=usage, finished=True)
        self.event(task_id, "task.interrupted", {})
        return updated

    def mark_failed(self, task_id: str, *, error: str, usage: TaskUsage | None = None) -> ResearchTask:
        task = self._require(task_id)
        updated = self._save(task, TaskStatus.FAILED, usage=usage, error=error, finished=True)
        self.event(task_id, "task.failed", {"message": error})
        return updated

    # -- events --------------------------------------------------------------
    def event(self, task_id: str, type: str, payload: dict | None = None) -> TaskEvent:
        return self._repository.append_event(task_id, type, dict(payload or {}))

    def events_after(self, task_id: str, after_seq: int = 0) -> list[TaskEvent]:
        return self._repository.list_events(task_id, after_seq)

    # -- helpers -------------------------------------------------------------
    def _require(self, task_id: str) -> ResearchTask:
        task = self._repository.get_task(task_id)
        if task is None:
            raise ValueError("研究任务不存在")
        return task

    def _save(self, task: ResearchTask, status: TaskStatus, *, usage: TaskUsage | None = None,
              report_id: str | None = None, error: str | None = None,
              finished: bool = False) -> ResearchTask:
        task.status = status
        if usage is not None:
            task.usage = usage
        if report_id is not None:
            task.report_id = report_id
        if error is not None:
            task.error = error
        task.updated_at = datetime.now(timezone.utc)
        if finished:
            task.finished_at = task.updated_at
        self._repository.save_task(task)
        return task
