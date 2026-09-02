"""Single-process asyncio research task collection (plan 03 section 2.6).

单实例默认最多 1 个活跃生成任务（asyncio.Semaphore），其余排队；不引入
Celery/Redis/第二数据库。取消幂等：排队任务直接取消，运行中任务关闭在途
模型请求（CancelledError 传播进 SDK 流），已持久化的事件与证据保持可读。
进程退出时统一取消并收尾；重启后由 lifespan 把 RUNNING/QUEUED 收尾为
interrupted，绝不自动再次付费（计划 §2.6）。
"""

from __future__ import annotations

import asyncio
import logging

from ..errors import AppError
from .events import TERMINAL_STATUSES as TERMINAL
from .models import TaskStatus
from .tasks import TaskStore

logger = logging.getLogger("finsight")


class ResearchTaskManager:
    def __init__(self, *, graph_factory, tasks: TaskStore, max_active: int = 1) -> None:
        # graph_factory(task) -> ResearchGraph：按任务选择模型/预算/事件落库。
        self._graph_factory = graph_factory
        self._tasks = tasks
        self._semaphore = asyncio.Semaphore(max_active)
        self._workers: dict[str, asyncio.Task] = {}
        self._cancel_requested: set[str] = set()

    @property
    def tasks(self) -> TaskStore:
        return self._tasks

    def submit(self, task_id: str, run_kwargs: dict | None = None) -> None:
        """排队一个已创建（queued）的任务；不等待完成。"""
        if task_id in self._workers:
            raise AppError("validation", "该任务已在执行队列中。", 409)
        self._workers[task_id] = asyncio.create_task(self._run(task_id, dict(run_kwargs or {})))

    def has_worker(self, task_id: str) -> bool:
        return task_id in self._workers

    async def cancel(self, task_id: str) -> TaskStatus:
        """幂等取消：终态原样返回；排队即取消，运行中关闭在途请求。"""
        task = self._tasks.get(task_id)
        if task is None:
            raise AppError("not_found", "研究任务不存在。", 404)
        if task.status in TERMINAL:
            return task.status
        self._cancel_requested.add(task_id)
        worker = self._workers.get(task_id)
        if worker is not None and not worker.done():
            worker.cancel()
            await asyncio.wait({worker}, timeout=10)
        else:
            self._settle_cancelled(task_id)
        updated = self._tasks.get(task_id)
        return updated.status if updated else task.status

    async def shutdown(self) -> None:
        """退出收尾：取消在途任务，剩余 RUNNING/QUEUED 全部标 interrupted。"""
        workers = [worker for worker in self._workers.values() if not worker.done()]
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        for status in (TaskStatus.RUNNING, TaskStatus.QUEUED):
            for task in self._tasks.list_by_status(status):
                try:
                    self._tasks.mark_interrupted(task.id)
                except ValueError:
                    pass
        self._workers.clear()
        self._cancel_requested.clear()

    # -- internals -----------------------------------------------------------
    async def _run(self, task_id: str, run_kwargs: dict) -> None:
        try:
            await self._semaphore.acquire()
        except asyncio.CancelledError:
            self._settle_cancelled(task_id)
            return
        try:
            task = self._tasks.get(task_id)
            if task is None or task.status != TaskStatus.QUEUED or task_id in self._cancel_requested:
                self._settle_cancelled(task_id)
                return
            self._tasks.mark_running(task_id)
            graph = self._graph_factory(task)
            await graph.run(task_id=task_id, question=task.question,
                            scope=task.scope, **run_kwargs)
        except asyncio.CancelledError:
            self._settle_cancelled(task_id)
        except Exception as error:  # noqa: BLE001 - worker 内不允许未捕获异常
            logger.error("research_task_failed task_id=%s error=%s", task_id, type(error).__name__)
            try:
                current = self._tasks.get(task_id)
                if current is not None and current.status == TaskStatus.RUNNING:
                    self._tasks.mark_failed(task_id, error="研究任务执行失败，请稍后重试或新建任务。")
            except ValueError:
                pass
        finally:
            self._semaphore.release()
            self._workers.pop(task_id, None)
            self._cancel_requested.discard(task_id)

    def _settle_cancelled(self, task_id: str) -> None:
        try:
            self._tasks.mark_cancelled(task_id)
        except ValueError:
            pass  # 已是终态（完成/失败）：幂等取消不覆盖
