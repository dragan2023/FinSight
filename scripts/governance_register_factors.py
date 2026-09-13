"""把 C2 的因子与其首次评价登记进治理台账（幂等）。

按方案 02 §A.1/§A.2 的要求登记两件事：

1. **每个因子一条 `factor` 假设**——含先验逻辑、方向、口径、数据来源与 as_of 声明；
   「登记后不得改名或改定义，变更视为新因子」。
2. **首次 IC 评价本身**——含**真实试验次数**（多重检验计数必须包含全部尝试过的因子，
   含放弃的），并**如实标注首次评价在登记之前就跑了**（因此其 t 值属探索性，
   不是预注册后的确认性结论）。

用法：

    .\\.venv\\Scripts\\python.exe scripts\\governance_register_factors.py --dry-run
    .\\.venv\\Scripts\\python.exe scripts\\governance_register_factors.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.governance.service import GovernanceService  # noqa: E402
from backend.app.screener import FACTOR_IDS, FACTOR_SET_VERSION, factor_declarations  # noqa: E402
from backend.app.settings import load_settings  # noqa: E402
from backend.app.storage import Storage  # noqa: E402

SCAN_REFERENCE = f"screener_ic_first_run@{FACTOR_SET_VERSION}"
#: 首次评价的实测结果（21 个可用评估日、前瞻 20 个交易日、逐日 200 次安慰剂置乱）。
#: 这里写死数字是刻意的：台账要能回答"当时跑的结论是什么"，而不是只留下一个文件名。
FIRST_RUN_RESULTS = {
    "momentum_60d": {"direction": "high", "mean_ic": -0.0051, "ic_sd": 0.1573, "t_stat": -0.15},
    "bias_20": {"direction": "low", "mean_ic": -0.0012, "ic_sd": 0.0951, "t_stat": -0.056},
    "volatility_20d": {"direction": "low", "mean_ic": 0.0401, "ic_sd": 0.1321, "t_stat": 1.392},
}
DATES_USABLE = 21
DATES_REQUESTED = 24
HORIZON_DAYS = 20


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="登记 C2 因子与首次 IC 评价")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    governance = GovernanceService(Storage(load_settings().db_path))
    plan: list[str] = []
    existing = {item["reference"] for item in governance.list_hypotheses()}

    for declaration in factor_declarations():
        reference = declaration["reference"]
        if reference in existing:
            plan.append(f"skip  {reference}（已登记）")
            continue
        plan.append(f"add   {reference}（kind=factor, exploratory）")
        if args.dry_run:
            continue
        governance.register_hypothesis(
            kind="factor", name=declaration["name"], reference=reference,
            definition=declaration["definition"], direction=declaration["direction"],
            params={"factor_set_version": FACTOR_SET_VERSION}, exploratory=True,
            trial_count=len(FACTOR_IDS),
            notes="第一期只用「量价 + 流动性」因子（决策 D1：财务因子算不出来）。"
                  "**首次 IC 评价已在登记之前跑过**，故本因子的 t 值属探索性，"
                  "不是预注册后的确认性结论。")

    if SCAN_REFERENCE in existing:
        plan.append(f"skip  {SCAN_REFERENCE}（已登记）")
    else:
        plan.append(f"add   {SCAN_REFERENCE}（kind=scan, exploratory）")
        if not args.dry_run:
            governance.register_hypothesis(
                kind="scan", name="C2 因子首次横截面 IC 评价（探索性）",
                reference=SCAN_REFERENCE,
                definition={
                    "factors": list(FACTOR_IDS), "factor_set_version": FACTOR_SET_VERSION,
                    "horizon_days": HORIZON_DAYS, "dates_requested": DATES_REQUESTED,
                    "dates_usable": DATES_USABLE,
                    "sampling": "按交易日等距抽取 24 个评估日（实际可用 21 个），"
                                "间隔远大于前瞻期，IC 之间近似独立",
                    "significance": "IC 时间序列 t 值（不是逐标的置乱安慰剂）",
                    "placebo": "逐标的置乱 200 次，仅作机制自检；其零分布离散度"
                               "仅为真实 IC 离散度的 1/4–1/7，不得用作显著性门槛",
                    "universe_rules": {"min_history_bars": 252, "min_turnover_20d": 2.0e7,
                                       "exclude_suspected_limit_up": True},
                    "first_run_results": FIRST_RUN_RESULTS,
                },
                direction="无方向（横截面预测力度量）", exploratory=True,
                trial_count=len(FACTOR_IDS),
                notes="实测结论：**三个因子全部未通过 |t| >= 1.96**（momentum_60d t=-0.15、"
                      "bias_20 t=-0.06、volatility_20d t=+1.39）。volatility_20d 方向为正、"
                      "最接近门槛，但未显著。**严禁**通过延长观察期或增加评估日直到它显著——"
                      "那正是方案禁止的『延长观察期来修复结论』。若要在更大样本上确认，"
                      "必须作为新假设重新预注册并开新批次。")

    # 绑到当前批次（G4 按批次聚合）
    batch_id = governance.current_batch_id()
    if batch_id:
        members = {(item["artifact_kind"], item["artifact_id"])
                   for item in governance.members(batch_id)}
        for hypothesis in governance.list_hypotheses():
            key = ("hypothesis", hypothesis["id"])
            if key not in members:
                plan.append(f"bind  {hypothesis['reference']} -> batch")
                if not args.dry_run:
                    governance.add_member(batch_id=batch_id, artifact_kind="hypothesis",
                                          artifact_id=hypothesis["id"])

    print("\n".join(plan) if plan else "无需变更。")
    print(f"\n{'（dry-run，未写入）' if args.dry_run else f'完成。当前批次 {batch_id}'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
