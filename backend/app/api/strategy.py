from fastapi import APIRouter,Query,Request
from ..strategy.models import StrategyExperimentInput,StrategyReviewInput
from ..errors import AppError
router=APIRouter(prefix="/api",tags=["strategy"])
def service(request): return request.app.state.strategy
@router.get("/strategies")
def specs(request:Request): return {"strategies":service(request).specs()}
@router.post("/strategy-experiments",status_code=201)
async def create(data:StrategyExperimentInput,request:Request): return {"experiment":await service(request).run(data)}
@router.get("/strategy-experiments")
def list_items(request:Request): return {"experiments":service(request).list()}
@router.get("/strategy-experiments/{entry_id}")
def get_item(entry_id:str,request:Request): return {"experiment":service(request).get(entry_id)}
@router.delete("/strategy-experiments/{entry_id}")
def delete(entry_id:str,request:Request): return service(request).delete(entry_id)
@router.get("/strategies/{strategy_id}/calibration")
def calibration(strategy_id:str,strategy_version:str,request:Request,horizon_days:int=Query(ge=1,le=365)):
    return {"calibration":service(request).calibration(strategy_id,strategy_version,horizon_days)}
@router.post("/strategies/{strategy_id}/reviews",status_code=201)
def review(strategy_id:str,data:StrategyReviewInput,request:Request): return {"review":service(request).review(strategy_id,data)}
