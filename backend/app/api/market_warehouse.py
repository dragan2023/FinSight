from typing import Annotated, Literal
from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from ..errors import AppError
from ..dependencies import market_warehouse_service

router = APIRouter(prefix="/api/market-warehouse", tags=["market-warehouse"])
Service = Annotated[object, Depends(market_warehouse_service)]

class SyncInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["daily_k", "daily_k_10d", "adjustment_factors"]
class QueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date_ms: int = Field(gt=0)
    metric: Literal["breadth", "liquidity", "sample"]
    min_turnover: float = Field(default=0, ge=0)
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=100, ge=1, le=500)

class ClearDerivedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]

@router.get("/status")
def status(service: Service): return service.status()
@router.post("/sync")
async def sync(data: SyncInput, service: Service): return await service.start_sync(data.kind)
@router.post("/import")
async def import_daily_k(service: Service, file: UploadFile = File(...)):
    return await service.import_daily_parquet(file)
@router.get("/sync/{sync_id}")
def sync_status(sync_id: str, service: Service): return service.run_status(sync_id)
@router.post("/sync/{sync_id}/cancel")
async def cancel_sync(sync_id: str, service: Service): return await service.cancel_sync(sync_id)
@router.post("/query")
def query(data: QueryInput, service: Service):
    try: return service.repository.query(**data.model_dump())
    except ValueError as error: raise AppError("validation", str(error), 422) from error
@router.post("/clear-derived")
def clear_derived(data: ClearDerivedInput, service: Service):
    service.repository.clear_derived(); return {"status": "completed", "notice": "未删除原始下载、SQLite 证据或研究报告。"}
