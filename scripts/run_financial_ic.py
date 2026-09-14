"""跑财务因子的横截面 IC 评价（执行预注册设计 `financial_factor_study_design@v1`）。

**只读本地数据，不联网。** 主检验是**可成交口径**（T+1 开盘 → T+20 收盘，D53）；
`close_T` 口径同时跑，作为声明的对照。

    # 全历史（月度采样，约 100+ 个评估日）
    .\\.venv\\Scripts\\python.exe scripts\\run_financial_ic.py

    # 只跑一段（调试；正式结论不要缩小样本）
    .\\.venv\\Scripts\\python.exe scripts\\run_financial_ic.py --from 2024-01-01 --draws 50

结果落盘 `data/exports/financial-ic/<日期>/report.json`。**不产出买入清单。**

⚠️ 样本选择偏置已在登记条目里声明：这 202 只按 **2026-09-04 的成交额前 200** 选出，
用来评价历史 → 结论**不得**外推为全市场。报告里的 `bias` 块给出量化指标。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.market_warehouse.repository import WarehouseRepository  # noqa: E402
from backend.app.screener.financial_ic import FACTOR_DIRECTIONS, FinancialFactorStudy  # noqa: E402
from backend.app.settings import load_settings  # noqa: E402

CST = timezone(timedelta(hours=8))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="财务因子横截面 IC（预注册设计）")
    parser.add_argument("--from", dest="from_text", default="")
    parser.add_argument("--to", dest="to_text", default="")
    parser.add_argument("--bases", default="next_open,close_t",
                        help="前向收益口径；next_open = 可成交（主检验），close_t = 对照")
    parser.add_argument("--draws", type=int, default=200, help="安慰剂重抽次数")
    parser.add_argument("--out", default="")
    parser.add_argument("--json", action="store_true", help="只输出 JSON（给脚本吃）")
    args = parser.parse_args()

    def to_ms(text: str) -> int | None:
        return (int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=CST).timestamp() * 1000)
                if text else None)

    repository = WarehouseRepository(load_settings().market_warehouse_path)
    status = repository.financial_dataset_status()
    if not status["ready"]:
        print(f"财务数据集不可用：{status['reason']}")
        return 1
    study = FinancialFactorStudy(repository)
    started = time.time()
    report = study.report(start_ms=to_ms(args.from_text), end_ms=to_ms(args.to_text),
                          bases=tuple(part.strip() for part in args.bases.split(",")
                                      if part.strip()),
                          draws=args.draws)
    report["dataset"] = {"version": status["version"], "counts": status["counts"]}
    report["elapsed_seconds"] = round(time.time() - started, 1)

    out_dir = (Path(args.out) if args.out
               else ROOT / "data" / "exports" / "financial-ic"
               / datetime.now(CST).strftime("%Y-%m-%d"))
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "report.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
                      encoding="utf-8", newline="\n")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0

    dates = report["evaluation_dates"]
    print(f"财务因子 IC｜数据集 {status['version']}（{status['counts']['symbols']} 只标的）"
          f"｜耗时 {report['elapsed_seconds']:.0f}s")
    print(f"评估日：候选 {dates['candidates']} → 采用 {dates['kept']}"
          f"（因报告期不统一剔除 {len(dates['dropped'])} 个，门槛 {report['modal_share_threshold']}）")
    for basis, payload in report["bases"].items():
        label = "**可成交口径（主检验）**" if basis == "next_open" else "对照：close_T 口径"
        multiplicity = payload["multiplicity"]
        print(f"\n{'=' * 92}\n{basis} —— {label}")
        print(f"采用评估日 {payload['dates_used']}｜样本数中位 {payload['samples_median']}"
              f"｜报告期众数占比中位 {payload['period_mix_median_share']}")
        print(f"多重检验：声明 {multiplicity['declared_comparisons']} 条比较（族 "
              f"{multiplicity['family']}，先验 {multiplicity['family_prior_statistics']}）"
              f"→ 族级门槛 |t| ≥ {multiplicity['bonferroni_family_abs_t']}")
        print(f"\n{'因子':<30}{'方向':>5}{'样本中位':>9}{'均值IC':>9}{'t值':>9}"
              f"{'正IC占比':>9}  判定")
        for factor in FACTOR_DIRECTIONS:
            stat = payload["factors"][factor]
            print(f"{factor:<30}{FACTOR_DIRECTIONS[factor]:>5}{str(stat.get('dates')):>9}"
                  f"{str(stat.get('mean_ic')):>9}{str(stat.get('t_stat')):>9}"
                  f"{str(stat.get('positive_ic_share')):>9}  {stat.get('verdict')}")
        period = payload["by_period"]
        if period:
            print(f"\n分时段复现（均值 IC / t）：")
            for factor in FACTOR_DIRECTIONS:
                cells = []
                for label in ("2016-2020", "2021-"):
                    item = period.get(f"{factor}@{label}")
                    cells.append(f"{label}: {item['mean_ic']} / {item.get('t_stat')}"
                                 if item else f"{label}: —")
                print(f"  {factor:<30} " + "   ".join(cells))
    bias = report["bias"]
    print(f"\n{'=' * 92}\n样本选择偏置（登记时已声明，必须一并阅读）")
    print(f"  {bias['what']}")
    print(f"  {bias['measured']}")
    for basis, payload in (bias["per_basis"] or {}).items():
        if payload:
            print(f"  [{basis}] {payload}")
    print(f"\n  ⚠ {bias['conclusion_limit']}")
    print(f"\n输出：{target}")
    print("\n研究参考，不构成投资建议。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
