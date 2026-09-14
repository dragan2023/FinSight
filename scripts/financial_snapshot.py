"""看一眼某只标的在某个时点的财务快照与 F-Score（**只读本地数据，不联网**）。

    # 最新时点
    .\\.venv\\Scripts\\python.exe scripts\\financial_snapshot.py --symbol 600519.SH

    # 历史时点（point-in-time：只能看到那一刻**已经披露**的报告期）
    .\\.venv\\Scripts\\python.exe scripts\\financial_snapshot.py --symbol 600519.SH --as-of 2024-06-30

    # 给脚本/程序吃的 JSON
    .\\.venv\\Scripts\\python.exe scripts\\financial_snapshot.py --symbol 600519.SH --json

它刻意把三件事摆在一起，因为它们**必须一起读**：

1. 可见的报告期与**闸门**（`available_from` / 采信了接口日期还是法定截止日）——
   同一份报表在"当时"和"现在"能不能看到，答案可能不同；
2. 原始字段与**本地算出的比率**（公式写在 `market_warehouse/financials.py`，可自证）；
3. F-Score 的逐项判定，**包括算不出来的项**（流动比率、未增发恒为 N/A，不计分）。

数据从哪来：`scripts/sync_financials.py`（人工触发）。没同步过它会拒绝运行并提示。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.advisor.algorithms.f_score import calculate_f_score  # noqa: E402
from backend.app.market_warehouse.financials import (  # noqa: E402
    FINANCIAL_RATIOS,
    compute_ratios,
    f_score_local_coverage,
)
from backend.app.market_warehouse.repository import WarehouseRepository  # noqa: E402
from backend.app.settings import load_settings  # noqa: E402

CST = timezone(timedelta(hours=8))
MARKS = {True: "✅", False: "✗", None: "—"}


def day(ms: int | None) -> str:
    return datetime.fromtimestamp(ms / 1000, CST).strftime("%Y-%m-%d") if ms else "—"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="某标的在某个时点的财务快照与 F-Score（只读）")
    parser.add_argument("--symbol", required=True, help="标的代码，如 600519.SH")
    parser.add_argument("--as-of", default="", help="时点 YYYY-MM-DD；默认取行情库最新交易日")
    parser.add_argument("--json", action="store_true", help="输出 JSON（给脚本吃）")
    args = parser.parse_args()

    repository = WarehouseRepository(load_settings().market_warehouse_path)
    status = repository.financial_dataset_status()
    if not status["ready"]:
        print(f"财务数据集不可用：{status['reason']}")
        return 1

    as_of_ms = (int(datetime.strptime(args.as_of, "%Y-%m-%d").replace(tzinfo=CST).timestamp() * 1000)
                if args.as_of else repository.latest_daily_ms())
    snapshot = repository.financial_snapshot_asof(thscode=args.symbol, as_of_ms=as_of_ms)
    current, previous = snapshot["current"], snapshot["previous"]
    if not current:
        print(f"{args.symbol} 在 {day(as_of_ms)} 时点没有任何**已披露**的财务数据"
              f"（可能没同步过，或该时点早于所有报告期的闸门）。")
        return 1

    ratios = compute_ratios(current["facts"])
    score_report = (calculate_f_score({**current["facts"], **ratios},
                                      {**previous["facts"], **compute_ratios(previous["facts"])},
                                      evidence_id=f"pit:{args.symbol}:{current['period_key']}")
                    if previous else None)
    payload = {
        "symbol": args.symbol, "as_of": day(as_of_ms),
        "current": {"period_key": current["period_key"], "period_end": day(current["period_end_ms"]),
                    "available_from": day(current["available_from_ms"]),
                    "available_basis": current["available_basis"],
                    "api_disclosed_at": day(current["disclosed_at_ms"])},
        "previous_period_key": previous["period_key"] if previous else None,
        "facts": current["facts"], "local_ratios": ratios,
        "f_score": score_report,
        "dataset": {"version": status["version"], "counts": status["counts"]},
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0

    print(f"{args.symbol}｜时点 {day(as_of_ms)}｜数据集 {status['version']}"
          f"（{status['counts']['symbols']} 只标的 / {status['counts']['facts']:,} 条事实）")
    print(f"\n可见报告期：**{current['period_key']}**（期末 {day(current['period_end_ms'])}）")
    print(f"  闸门：{day(current['available_from_ms'])} 起可得"
          f"（采信：{current['available_basis']}）")
    # 接口日期**不是无条件不可信**：它早于法定截止日时我们反而采信它（更早、更精确）。
    # 所以这里如实说清它在**本期**扮演了什么角色，而不是一律打上"不可信"。
    role = ("**采信了它（早于法定截止日，更精确）**"
            if current["available_basis"] == "api_report_date"
            else "**未采信（晚于法定截止日，属漂移）**")
    print(f"  接口给的披露日：{day(current['disclosed_at_ms'])}（本期{role}"
          f"——口径见 `22` 号文档 §1）")
    print(f"  上年同季（Δ 类信号的口径对齐）："
          f"{previous['period_key'] if previous else '（缺，Δ 类项无法判定）'}")
    print(f"\n原始字段（{len(current['facts'])} 项）：")
    for name, value in sorted(current["facts"].items()):
        print(f"  {name:<38}{value:>20,.0f}")
    print("\n本地算出的比率（公式见 market_warehouse/financials.py）：")
    for name, value in ratios.items():
        formula = FINANCIAL_RATIOS[name]["formula"]
        shown = f"{value:.6f}" if value is not None else "算不出来（缺输入，不填补）"
        print(f"  {name:<30}{shown:>16}   = {formula}")
    if score_report:
        print(f"\nPiotroski F-Score：**{score_report['score']}/{score_report['available_items']}**"
              f"（共 {score_report['total_items']} 项，可算 {score_report['available_items']} 项）")
        for item in score_report["items"]:
            suffix = "" if item["available"] else "（本地方案不可算，不计分）"
            print(f"  {MARKS[item['passed']]} {item['name']}{suffix}")
        coverage = f_score_local_coverage()
        print(f"\n  覆盖说明：{coverage['locally_computable']}/{coverage['total_items']} 项本地可算；"
              f"不可算的是 {coverage['unavailable']}")
    print("\n研究参考，不构成投资建议。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
