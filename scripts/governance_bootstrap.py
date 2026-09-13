"""把 C1 治理层的真实对象一次性登记入册（幂等，可重复运行）。

登记内容（全部来自 `docs/荐股治理方案/07` 的实查结论）：

1. **当前已注册的 4 个策略规格** → `hypothesis_registry`（frozen）；
2. **批次 3 探索性扫描与其安慰剂对照** → 两条 `scan` 假设（`exploratory=1`，
   `trial_count` 记录**真实试验次数**，供多重检验计数用——方案 §A.2）；
3. **两个待执行的规则变更提案** → `kind='rule'` 且 `status='draft'`。
   **刻意不在本脚本里调用 `record_change`**：那是"变更已发生"的动作，会关闭当前批次
   并开启新批次。提案阶段就只登记提案，避免污染批次历史。
4. **开启 C1 治理基线批次**（含方案 02 §5 的 G8 停止条件），并把上述对象绑为批次成员。

用法（会写入正式业务库 `data/db/finsight.db`，建议先复制一份备份）：

    .\\.venv\\Scripts\\python.exe scripts\\governance_bootstrap.py --dry-run
    .\\.venv\\Scripts\\python.exe scripts\\governance_bootstrap.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.governance.service import GovernanceService  # noqa: E402
from backend.app.settings import load_settings  # noqa: E402
from backend.app.storage import Storage  # noqa: E402
from backend.app.strategy.registry import list_specs  # noqa: E402

BATCH_NAME = "C1 治理基线（2026-09-13 起）"

SCAN_HYPOTHESES = (
    {
        "reference": "sweep_20260913@1",
        "name": "批次 3 全市场门禁可行性扫描（探索性）",
        "trial_count": 10572,
        "definition": {
            "universe": "daily_k 中 >=500 根有效日 K 的全部标的（5286 只）",
            "strategies": ["trend_20_60_v1@v2", "reversal_5d_v1@v2"],
            "data_source": "local_duckdb: corporate_action_total_return_v1（as-of 截断）",
            "gate": "strategy/validation.py:grade() 四级门禁，10bps，区间 2016-01-01..2026-09-04",
        },
        "direction": "无方向（筛选率度量）",
        "notes": "结果：candidate 143/10572（全部 trend_20_60_v1）；reversal_5d_v1 0/5286。"
                 "143 中仅 36 个测试段超额为正，另 107 个靠回撤不劣于基准过关。"
                 "按 D19 本扫描为 exploratory，**不构成优势证据、不得用于 C3 判定**。",
    },
    {
        "reference": "placebo_control_20260913@1",
        "name": "安慰剂对照：逐日收益随机重排（探索性）",
        "trial_count": 5286,
        "definition": {
            "treatment": "对每只标的的逐日收益做随机重排后重建价格（seed 20260913）",
            "preserved": ["K 线根数", "日收益全集", "买入持有基准", "无跳空执行价"],
            "destroyed": ["一切序列相关性"],
        },
        "direction": "无方向（零假设）",
        "notes": "结果：安慰剂 candidate 279（真实 143）、跑赢基准 110（真实 36）。"
                 "归因对照 --nogap 与基准逐项相同，证明差异来自序列结构而非执行假设。"
                 "**结论：当前策略族未检出择时优势**（D17）。",
    },
)

PENDING_RULE_CHANGES = (
    {
        "reference": "cost20_test_basis@1",
        "name": "修正 grade() 的 20bps 成本口径（F15）",
        "definition": {"file": "backend/app/strategy/service.py:49",
                       "current": "cost20_test=cost[-1]（run_backtest 的全周期 20bps 指标）",
                       "intended": "测试段的 20bps 指标",
                       "impact": "当测试段为正、全周期为负时会把合格实验误判为不合格"},
        "notes": "待执行：按 D14 必须走 record_change（这会关闭当前批次并开启新批次）。",
    },
    {
        "reference": "backtest_execution_price@1",
        "name": "回测执行价与净值口径不一致（F23）",
        "definition": {"file": "backend/app/strategy/backtest.py:54-86",
                       "finding": "净值曲线只按 close_i/close_{i-1} 累积，开盘价不参与；"
                                  "而 trades[].return_pct 用 open→open，同一笔交易两个收益率",
                       "impact": "hit_rate / average_win_pct / average_loss_pct / profit_loss_ratio "
                                 "与净值曲线口径不一致（顾问使用的 cumulative/excess/drawdown 不受影响）"},
        "notes": "待执行：改变回测语义，必须走 record_change 并重跑受影响实验。",
    },
)

SCAN_EXCLUDED_FROM_VERDICT = [item["reference"] for item in SCAN_HYPOTHESES]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    parser = argparse.ArgumentParser(description="C1 治理层 bootstrap（幂等）")
    parser.add_argument("--dry-run", action="store_true", help="只报告将要写入什么，不落库")
    args = parser.parse_args()

    settings = load_settings()
    if args.dry_run:
        # dry-run 在正式库的**副本**上预演：既让迁移与登记逻辑走一遍真实数据，
        # 又完全不碰业务库。顺带验证 v32 迁移对真实库是安全的。
        import shutil
        import tempfile

        rehearsal = Path(tempfile.mkdtemp(prefix="finsight-governance-dryrun-"))
        for suffix in ("", "-wal", "-shm"):
            source = Path(str(settings.db_path) + suffix)
            if source.exists():
                shutil.copy2(source, rehearsal / (Path(settings.db_path).name + suffix))
        db_path = rehearsal / Path(settings.db_path).name
        print(f"[dry-run] 在副本上预演：{db_path}")
    else:
        db_path = settings.db_path
    storage = Storage(db_path)
    storage.initialize()  # 应用 schema v32（正式库或副本）
    governance = GovernanceService(storage)

    plan: list[str] = []

    # 1) 已注册的策略规格
    registered = {item["reference"] for item in governance.list_hypotheses()}
    for spec in list_specs():
        reference = f"{spec.id}@{spec.version}"
        if reference in registered:
            plan.append(f"skip  hypothesis {reference}（已登记）")
            continue
        plan.append(f"add   hypothesis {reference}（kind=strategy, frozen）")
        governance.register_hypothesis(
            kind="strategy", name=spec.name, reference=reference,
            definition={"id": spec.id, "version": spec.version, "family": spec.family,
                        "horizon": spec.horizon, "signal_rule": spec.signal_rule,
                        "exit_rule": spec.exit_rule, "risk_rule": spec.risk_rule,
                        "parameters": spec.parameters},
            direction=spec.horizon, params=spec.parameters, freeze=True,
            notes="v32 迁移时按当前 strategy/registry.py 现状登记（grandfathered）。")

    # 2) 按 D18 退役结构上不可达的策略
    reversal = governance.find_by_reference("reversal_5d_v1@v2")
    if reversal and reversal["status"] == "frozen":
        plan.append("retire hypothesis reversal_5d_v1@v2（D18：全市场 0/5286 过交易门禁）")
        if not args.dry_run:
            governance.retire(reversal["id"])

    # 3) 探索性扫描 / 安慰剂对照 / 待执行的规则变更提案
    registered = {item["reference"] for item in governance.list_hypotheses()}
    for item in SCAN_HYPOTHESES:
        if item["reference"] in registered:
            plan.append(f"skip  hypothesis {item['reference']}（已登记）")
            continue
        plan.append(f"add   hypothesis {item['reference']}（kind=scan, exploratory）")
        governance.register_hypothesis(
            kind="scan", name=item["name"], reference=item["reference"],
            definition=item["definition"], direction=item["direction"],
            exploratory=True, trial_count=item["trial_count"], notes=item["notes"], freeze=True)
    for item in PENDING_RULE_CHANGES:
        if item["reference"] in registered:
            plan.append(f"skip  hypothesis {item['reference']}（已登记）")
            continue
        plan.append(f"add   hypothesis {item['reference']}（kind=rule, draft）")
        governance.register_hypothesis(
            kind="rule", name=item["name"], reference=item["reference"],
            definition=item["definition"], notes=item["notes"], freeze=False)

    # 4) 开启基线批次并绑定成员
    current = governance.current_batch_id()
    if current is None:
        plan.append(f"add   batch {BATCH_NAME}（open，含 G8 停止条件）")
        batch = governance.open_batch(
            name=BATCH_NAME,
            plan={"purpose": "C1 治理落地基线：预注册 + 批次隔离 + 变更留痕",
                  "exploratory_scans_excluded_from_verdict": SCAN_EXCLUDED_FROM_VERDICT,
                  "note": "探索性扫描按 D19 排除在判定之外；判定样本只能来自本批次内"
                          "正式登记的实验与建议。"})
        current = batch["id"]
    else:
        plan.append(f"skip  batch（已有进行中批次 {current}）")

    if current:
        members = {(item["artifact_kind"], item["artifact_id"])
                   for item in governance.members(current)}
        for hypothesis in governance.list_hypotheses():
            key = ("hypothesis", hypothesis["id"])
            if key in members:
                continue
            plan.append(f"bind  {hypothesis['reference']} -> batch")
            governance.add_member(batch_id=current, artifact_kind="hypothesis",
                                  artifact_id=hypothesis["id"])

    print("\n".join(plan) if plan else "无需变更：治理层已是最新。")
    if args.dry_run:
        print(f"\n[dry-run] 预演完成（副本内已写入并丢弃）：假设 "
              f"{len(governance.list_hypotheses())} 条，批次 {current}。正式库未改动。")
    else:
        print(f"\n完成。假设 {len(governance.list_hypotheses())} 条；当前批次 {current}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
