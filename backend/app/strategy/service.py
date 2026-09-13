from __future__ import annotations
import hashlib,json
from datetime import datetime,timezone
from uuid import uuid4
from ..errors import AppError
from ..governance.service import GovernanceService
from ..storage import Storage
from ..advisor.ledger import AdvisorLedger
from .backtest import current_signal_snapshot,metrics,run_backtest,segments,year_segments
from .models import PriceBar,StrategyExperimentInput,StrategyExperimentResult
from .registry import get_spec,list_specs
from .validation import grade

class StrategyService:
 def __init__(self,storage:Storage,market):
  self.storage=storage; self.market=market; self._governance=GovernanceService(storage)
 def specs(self):
  items=[x.model_dump() for x in list_specs()]
  with self.storage.connect() as db:
   rows=db.execute("SELECT strategy_id,strategy_version,decision,created_at FROM strategy_reviews ORDER BY created_at DESC").fetchall()
  latest={}
  for row in rows: latest.setdefault((row["strategy_id"],row["strategy_version"]),row["decision"])
  for item in items:
   decision=latest.get((item["id"],item["version"]))
   if decision:
    item["review_decision"]=decision
    item["lifecycle"]={"validated":"validated","retire":"retired","keep_candidate":"candidate"}[decision]
  return items
 async def run(self,data:StrategyExperimentInput):
  # G1 预注册门禁：未登记或未冻结的策略不得进入候选生成。放在取数之前，
  # 既避免无谓的外部调用，也让"没登记就跑不了"成为一条硬规则而不是提醒。
  spec=get_spec(data.strategy_id)
  self._governance.assert_runnable(f"{spec.id}@{spec.version}")
  self.market.require_stock_identity(data.thscode); result=await self.market.fetch_price_history(data.thscode,start_ms=data.start_ms,end_ms=data.end_ms,adjust="forward",refresh=data.refresh)
  bars=[]
  for record in result.records:
   fields=record.get("fields",{}); close=(fields.get("close_price") or {}).get("value"); opening=(fields.get("open_price") or {}).get("value")
   if close is not None: bars.append(PriceBar(date=str(record["period_key"])[:10],open=float(opening) if opening is not None else None,close=float(close)))
  if not bars: raise AppError("data_unavailable","没有可用的前复权日K收盘价，无法生成实验。",409)
  if spec.family=="risk_overlay": raise AppError("strategy_not_runnable","风险覆盖层只作为其他策略的风险规则，不能单独运行实验。",422)
  if len(bars)>3000: raise AppError("strategy_invalid","有效日K超过3000根上限，拒绝保存。",422)
  structural=self._structural_errors(bars)
  snapshot={"evidence_ids":result.evidence_ids,"adjust":"forward","bars":len(bars),"missing_open_bars":sum(1 for x in bars if x.open is None),"start":bars[0].date if bars else None,"end":bars[-1].date if bars else None,"sha256":hashlib.sha256(json.dumps([x.model_dump() for x in bars],sort_keys=True).encode()).hexdigest()}
  entry_id=uuid4().hex; now=datetime.now(timezone.utc).isoformat(); params_hash=hashlib.sha256(json.dumps({"input":data.model_dump(),"spec":spec.model_dump()},sort_keys=True).encode()).hexdigest()[:16]
  if structural:
   # 结构性错误（重复日期、无效价格等）按门禁落为 rejected 记录，而不是抛错不留痕。
   payload=StrategyExperimentResult(spec_snapshot=spec.model_dump(),data_snapshot=snapshot,metrics={},segment_metrics=[],cost_sensitivity=[],validation_grade="rejected",validation_reasons=structural,curve=[],trades=[]).model_dump()
   with self.storage.connect() as db: db.execute("INSERT INTO strategy_experiments VALUES(?,?,?,?,?,?,?,?,?,?,?)",(entry_id,spec.id,spec.version,data.thscode,json.dumps(data.model_dump()),json.dumps(spec.model_dump()),json.dumps(snapshot),json.dumps(payload),"rejected",params_hash,now))
   return self.get(entry_id)
  gate=dict(get_spec("volatility_gate_v1").parameters)
  raw=run_backtest(data.strategy_id,bars,data.one_way_cost_bps,gate=gate); main=metrics(raw["curve"],raw["trades"]); seg=segments(data.strategy_id,bars,data.one_way_cost_bps,gate=gate); cost=[]
  for bps in (5,10,20):
   item=run_backtest(data.strategy_id,bars,bps,gate=gate); cost.append({"one_way_cost_bps":bps,**metrics(item["curve"],item["trades"])})
  # F15 修正（已走 record_change）：门禁的「20bps 下不亏」必须看**测试段**，不是全周期。
  # 参数名 cost20_test 本就写明了该意图；此前传的是 cost[-1]（全周期指标），
  # 于是「测试段为正、全周期为负」的好实验会被误判为不合格。
  cost20_test=segments(data.strategy_id,bars,20,gate=gate)[-1]
  grade_value,reasons=grade(bars=len(bars),segments=seg,cost20_test=cost20_test,test=seg[-1])
  output=StrategyExperimentResult(spec_snapshot=spec.model_dump(),data_snapshot=snapshot,metrics=main,segment_metrics=seg,cost_sensitivity=cost,validation_grade=grade_value,validation_reasons=reasons,curve=raw["curve"],trades=raw["trades"],year_metrics=year_segments(raw["curve"],raw["trades"]),signal_snapshot=current_signal_snapshot(data.strategy_id,bars,gate=gate))
  if len(raw["trades"])>1000: raise AppError("strategy_invalid","交易数超过上限，拒绝保存。",422)
  payload=output.model_dump()
  with self.storage.connect() as db: db.execute("INSERT INTO strategy_experiments VALUES(?,?,?,?,?,?,?,?,?,?,?)",(entry_id,spec.id,spec.version,data.thscode,json.dumps(data.model_dump()),json.dumps(spec.model_dump()),json.dumps(snapshot),json.dumps(payload),grade_value,params_hash,now))
  return self.get(entry_id)
 @staticmethod
 def _structural_errors(bars:list[PriceBar]) -> list[str]:
  errors=[]; dates=[x.date for x in bars]
  if len(set(dates))!=len(dates): errors.append("价格日期存在重复，属于数据完整性错误。")
  elif dates!=sorted(dates): errors.append("价格日期非单调递增，属于数据完整性错误。")
  invalid=sum(1 for x in bars if x.close<=0 or (x.open is not None and x.open<=0))
  if invalid: errors.append(f"存在 {invalid} 根无效价格（收盘或开盘<=0）。")
  return errors
 def get(self,entry_id):
  with self.storage.connect() as db: row=db.execute("SELECT * FROM strategy_experiments WHERE id=?",(entry_id,)).fetchone()
  if not row: raise AppError("not_found","策略实验不存在。",404)
  return {"id":row["id"],"strategy_id":row["strategy_id"],"strategy_version":row["strategy_version"],"thscode":row["thscode"],"input":json.loads(row["input_json"]),"result":json.loads(row["result_json"]),"validation_grade":row["validation_grade"],"params_hash":row["params_hash"],"created_at":row["created_at"]}
 def list(self):
  with self.storage.connect() as db: rows=db.execute("SELECT id FROM strategy_experiments ORDER BY created_at DESC").fetchall()
  return [self.get(row["id"]) for row in rows]
 def delete(self,entry_id):
  with self.storage.connect() as db: return {"deleted":db.execute("DELETE FROM strategy_experiments WHERE id=?",(entry_id,)).rowcount>0}
 def calibration(self,strategy_id:str,strategy_version:str,horizon_days:int):
  """前向校准快照（只读）：供评审界面判断是否允许升级为已验证。"""
  return AdvisorLedger(self.storage).strategy_calibration(strategy_id,strategy_version,horizon_days)
 def review_decision(self,strategy_id:str,strategy_version:str,horizon_days:int) -> str | None:
  """返回当前版本、当前预测窗口最后一次人工评审决定。

  顾问只读取这个窄口径结果，不能把其他窗口或旧版本的“已验证”借来抬高
  当前建议档位。
  """
  with self.storage.connect() as db:
   row=db.execute("SELECT decision FROM strategy_reviews WHERE strategy_id=? AND strategy_version=? AND horizon_days=? ORDER BY created_at DESC LIMIT 1",(strategy_id,strategy_version,horizon_days)).fetchone()
  return str(row["decision"]) if row else None
 def review(self,strategy_id,data):
  spec=get_spec(strategy_id)
  if data.strategy_version != spec.version: raise AppError("strategy_version_mismatch","策略版本与注册表不一致。",422)
  snapshot=AdvisorLedger(self.storage).strategy_calibration(strategy_id,data.strategy_version,data.horizon_days)
  if data.decision=="validated" and not snapshot["eligible"]: raise AppError("strategy_not_eligible",snapshot["reason"],422)
  review_id=uuid4().hex; now=datetime.now(timezone.utc).isoformat()
  with self.storage.connect() as db: db.execute("INSERT INTO strategy_reviews VALUES(?,?,?,?,?,?,?)",(review_id,strategy_id,data.strategy_version,data.horizon_days,data.decision,json.dumps(snapshot,ensure_ascii=False),now))
  return {"id":review_id,"strategy_id":strategy_id,"strategy_version":data.strategy_version,"horizon_days":data.horizon_days,"decision":data.decision,"calibration":snapshot,"created_at":now}
