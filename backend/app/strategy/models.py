from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class StrategySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9_]+_v[0-9]+$")
    version: str
    name: str
    family: str
    asset_types: list[Literal["a-share"]]
    horizon: str
    required_fields: list[str]
    warmup_bars: int = Field(ge=1, le=300)
    parameters: dict[str, int | float | str]
    signal_rule: str
    exit_rule: str
    risk_rule: str
    cost_basis: str
    benchmark_id: Literal["buy_hold_v1"]
    applicability: str
    contraindications: str
    research_sources: list[str]
    lifecycle: Literal["experimental", "candidate", "validated", "retired"] = "experimental"

class StrategyExperimentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_id: Literal["trend_20_60_v1", "reversal_5d_v1"]
    thscode: str = Field(pattern=r"^[0-9]{6}\.(SH|SZ|BJ)$")
    start_ms: int = Field(gt=0)
    end_ms: int = Field(gt=0)
    one_way_cost_bps: int = Field(default=10, ge=0, le=100)
    refresh: bool = False

    @model_validator(mode="after")
    def _ordered_range(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("结束日期必须晚于开始日期。")
        if self.end_ms - self.start_ms > 15 * 366 * 86_400_000:
            raise ValueError("单次实验日期范围不得超过15年。")
        return self

class PriceBar(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str
    open: float | None = None
    close: float
    #: 下面三项是**风控层需要的**（原先的日 K 只有开/收，做不了止损与流动性门槛）。
    #: 都是可选：既有调用方只给 date/open/close 时行为完全不变。
    #: `low`/`high` 必须与 `close` 处于**同一复权空间**（由 `local_bars` 负责缩放），
    #: 否则除权日附近的止损判断会被机械跳空骗到。
    low: float | None = None
    high: float | None = None
    #: 当日成交额（元）。流动性门槛用它；成交额不随复权缩放（它是金额）。
    turnover: float | None = None


class RiskProfile(BaseModel):
    """**风控档位**：一组显式的、可复算的规则（不是拍脑袋的参数）。

    为什么单列成模型而不是塞进策略参数：风控应当**独立于信号**存在——同一个信号配不同风控，
    结果差异往往比换信号还大；而用户的目标里"保本/少亏"正是靠这一层。每条规则都写明
    它拦的是什么，避免出现"调了个参数不知道在调什么"的情况。
    """

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9_]+_v[0-9]+$")
    name: str
    #: 单笔止损：相对**入场价**下跌多少就离场（0.08 = 8%）。`None` = 不止损。
    stop_loss_pct: float | None = Field(default=None, ge=0, le=0.5)
    #: 回撤熔断：**组合**净值从峰值回撤超过该比例时暂停开新仓（已持仓不动），
    #: **暂停 `breaker_cooldown_days` 个交易日后自动恢复**。
    #: ⚠️ 用"时间界"而不是"回撤收敛到一半"恢复：后者会**死锁**——空仓时净值不动、
    #: 回撤永远不收敛，策略从此再不交易（2026-09-15 实测踩到，见 25 号文档）。
    drawdown_breaker_pct: float | None = Field(default=None, ge=0.02, le=0.9)
    #: 熔断后的暂停时长（交易日）。只在熔断触发时生效。
    breaker_cooldown_days: int = Field(default=20, ge=1, le=250)
    #: 流动性门槛：**入场当日**成交额低于它就不开仓（避免买到撮合不了的票）。
    min_turnover: float | None = Field(default=None, ge=0)
    #: 涨停不可买 / 跌停不可卖：开盘相对前收的跳空超过该比例时，视为**无法按开盘价成交**。
    #: 0.095 对应主板 10% 涨跌停（留一点余量给四舍五入）。`None` = 不考虑这个约束。
    limit_move_pct: float | None = Field(default=None, ge=0.03, le=0.25)
    #: 波动率闸门是否启用（复用既有 `volatility_gate_v1` 的 vol/vol_gate 特征）。
    volatility_gate: bool = False
    #: 止损后**同一标的**的再入场冷静期（交易日）。0 = 允许次日立刻重进。
    #: 存在理由：止损当天信号往往还没转向，没有冷静期就会被同样的条件反复进出（来回挨打）。
    reentry_cooldown_days: int = Field(default=0, ge=0, le=60)

    @model_validator(mode="after")
    def _coherent(self):
        if self.stop_loss_pct is not None and self.stop_loss_pct == 0:
            raise ValueError("止损幅度不能为 0（要止损就给一个正数，不要就给 None）。")
        return self

class StrategyExperimentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec_snapshot: dict
    data_snapshot: dict
    metrics: dict
    segment_metrics: list[dict]
    cost_sensitivity: list[dict]
    validation_grade: Literal["rejected", "insufficient", "experimental", "candidate"]
    validation_reasons: list[str]
    curve: list[dict]
    trades: list[dict]
    year_metrics: list[dict] = Field(default_factory=list)
    signal_snapshot: dict = Field(default_factory=dict)


class StrategyReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_version: str = Field(min_length=1, max_length=40)
    horizon_days: int = Field(ge=1, le=365)
    decision: Literal["validated", "keep_candidate", "retire"]
