"""治理层 API：预注册台账、评估批次、批次隔离与规则变更（方案 02 §5 G1–G4）。

这里刻意**不提供**改写已冻结规则、改写已关闭批次、或跨批次合并统计的"方便接口"：
前者由 SQLite 触发器拒绝，后者要求显式确认并标记 `usable_for_verdict=false`。
治理的价值就在这些拒绝里，加一个绕过它的端点等于把治理作废。
"""
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..governance.models import (BatchInput, BatchVerdictInput, ChangeInput, CombinedStatisticsInput,
                                 HypothesisInput, MemberInput)
from ..governance.test_run_export import DailyExportService

router = APIRouter(prefix="/api/governance", tags=["governance"])


class TestRunExportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    batch_id: str | None = Field(default=None, max_length=64)


def service(request: Request):
    return request.app.state.governance


@router.get("/hypotheses")
def list_hypotheses(request: Request, status: str | None = Query(default=None)):
    return {"hypotheses": service(request).list_hypotheses(status=status)}


@router.post("/hypotheses", status_code=201)
def register_hypothesis(data: HypothesisInput, request: Request):
    return {"hypothesis": service(request).register_hypothesis(
        kind=data.kind, name=data.name, reference=data.reference, definition=data.definition,
        direction=data.direction, params=data.params, exploratory=data.exploratory,
        trial_count=data.trial_count, notes=data.notes)}


@router.post("/hypotheses/{hypothesis_id}/freeze")
def freeze_hypothesis(hypothesis_id: str, request: Request):
    return {"hypothesis": service(request).freeze(hypothesis_id)}


@router.post("/hypotheses/{hypothesis_id}/retire")
def retire_hypothesis(hypothesis_id: str, request: Request):
    return {"hypothesis": service(request).retire(hypothesis_id)}


@router.get("/batches")
def list_batches(request: Request, status: str | None = Query(default=None)):
    return {"batches": service(request).list_batches(status=status)}


@router.get("/batches/current")
def current_batch(request: Request):
    batch_id = service(request).current_batch_id()
    return {"batch_id": batch_id,
            "batch": service(request).get_batch(batch_id) if batch_id else None}


@router.post("/batches", status_code=201)
def open_batch(data: BatchInput, request: Request):
    return {"batch": service(request).open_batch(name=data.name, plan=data.plan,
                                                stop_criteria=data.stop_criteria,
                                                exploratory=data.exploratory)}


@router.post("/batches/{batch_id}/freeze")
def freeze_batch(batch_id: str, request: Request):
    return {"batch": service(request).freeze_batch(batch_id)}


@router.post("/batches/{batch_id}/close")
def close_batch(batch_id: str, data: BatchVerdictInput, request: Request):
    return {"batch": service(request).close_batch(batch_id, verdict=data.verdict)}


@router.get("/batches/{batch_id}/members")
def list_members(batch_id: str, request: Request, artifact_kind: str | None = Query(default=None)):
    return {"members": service(request).members(batch_id, artifact_kind=artifact_kind)}


@router.post("/members", status_code=201)
def add_member(data: MemberInput, request: Request):
    return {"member": service(request).add_member(batch_id=data.batch_id,
                                                 artifact_kind=data.artifact_kind,
                                                 artifact_id=data.artifact_id)}


@router.get("/batches/{batch_id}/statistics")
def batch_statistics(batch_id: str, request: Request):
    return {"statistics": service(request).batch_statistics(batch_id)}


@router.post("/statistics/combined")
def combined_statistics(data: CombinedStatisticsInput, request: Request):
    return {"statistics": service(request).combined_statistics(
        data.batch_ids, acknowledge_cross_batch=data.acknowledge_cross_batch)}


@router.get("/changes")
def list_changes(request: Request, batch_id: str | None = Query(default=None)):
    return {"changes": service(request).list_changes(batch_id=batch_id)}


@router.post("/changes", status_code=201)
def record_change(data: ChangeInput, request: Request):
    return service(request).record_change(
        field=data.field, reason=data.reason, old_hash=data.old_hash, new_hash=data.new_hash,
        hypothesis_id=data.hypothesis_id, replacement_id=data.replacement_id,
        new_batch_name=data.new_batch_name, plan=data.plan, stop_criteria=data.stop_criteria)


# -- 当日测试数据落盘（方案 00 §5）------------------------------------------
def export_service(request: Request) -> DailyExportService:
    """**只能由用户点击触发。** 这里不注册任何定时任务，导出也不在取数路径里被调用。"""
    return DailyExportService(request.app.state.storage, request.app.state.settings.exports_path)


@router.get("/test-run-exports")
def list_test_run_exports(request: Request):
    return {"runs": export_service(request).list_runs(),
            "note": "导出由用户在界面点击触发；本接口也是只读。"}


@router.post("/test-run-export", status_code=201)
def create_test_run_export(data: TestRunExportInput, request: Request):
    """把当日（或指定批次）的测试数据落盘为 daily.md / daily.jsonl / batch.json。"""
    return export_service(request).export(date=data.date, batch_id=data.batch_id)
