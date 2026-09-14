"""跑「财务质量 × 涨停事件」交互评价（执行预注册设计）。

**只读本地数据，不联网。** 分组变量是**外部财务字段**（Piotroski F-Score，按事件当日做
point-in-time 取值）；估计量、对照匹配、分层、重抽与多重检验计数**全部复用**既有事件研究
（`screener/events.py`），没有第二套实现。

    # 主检验（可成交口径）+ 声明的对照，一次跑完
    .\\.venv\\Scripts\\python.exe scripts\\run_financial_interaction.py

    # 只跑一段（调试；正式结论不要缩小样本）
    .\\.venv\\Scripts\\python.exe scripts\\run_financial_interaction.py --from 2024-01-01 --draws 50

结果落盘 `data/exports/financial-interaction/<日期>/<字段>.<口径>.json`。**不产出买入清单。**

⚠️ 族级门槛用**保守读法**：把两个母族（连板族 43 + 现金族 36）已看过的比较也计入
→ 159 条 → |t| ≥ 3.6031（登记条目 `limit_up_financial_interaction_design@v1` 写死）。
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
from backend.app.screener.events import EXTERNAL_FIELDS, LimitUpEventStudy  # noqa: E402
from backend.app.screener.financial_ic import FinancialFactorProvider  # noqa: E402
from backend.app.settings import load_settings  # noqa: E402

CST = timezone(timedelta(hours=8))
FIELDS = ("f_score", "f_score_ratio")
#: 登记里写死的族级先验：母族 79 + 本轮同族兄弟 20 + 另一口径 40 = 139。
FAMILY_PRIOR = {field: 139 for field in FIELDS}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="财务质量 × 涨停事件（预注册设计）")
    parser.add_argument("--from", dest="from_text", default="")
    parser.add_argument("--to", dest="to_text", default="")
    parser.add_argument("--bases", default="next_open,close_t",
                        help="前向口径；next_open = 可成交（主检验），close_t = 对照")
    parser.add_argument("--draws", type=int, default=200)
    parser.add_argument("--out", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    def to_ms(text: str) -> int | None:
        return (int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=CST).timestamp() * 1000)
                if text else None)

    repository = WarehouseRepository(load_settings().market_warehouse_path)
    status = repository.financial_dataset_status()
    if not status["ready"]:
        print(f"财务数据集不可用：{status['reason']}")
        return 1

    provider = FinancialFactorProvider(repository)
    study = LimitUpEventStudy(repository, external=provider)
    dates = study.event_dates(start_ms=to_ms(args.from_text), end_ms=to_ms(args.to_text))
    if not dates:
        print("本地涨停池没有内容：先跑 scripts/sync_limit_pools.py --pools limit_up。")
        return 1

    out_dir = (Path(args.out) if args.out
               else ROOT / "data" / "exports" / "financial-interaction"
               / datetime.now(CST).strftime("%Y-%m-%d"))
    out_dir.mkdir(parents=True, exist_ok=True)
    bases = tuple(part.strip() for part in args.bases.split(",") if part.strip())
    started = time.time()
    summary: dict[str, dict] = {}
    for basis in bases:
        print(f"\n{'#' * 78}\n# 口径 {basis}\n{'#' * 78}")
        reports = study.run_fields(fields=FIELDS, start_ms=to_ms(args.from_text),
                                   end_ms=to_ms(args.to_text), horizon_days=20,
                                   draws=args.draws, source="panel", return_basis=basis,
                                   family_prior_statistics=FAMILY_PRIOR)
        for field, report in reports.items():
            target = out_dir / f"{field}.{basis}.json"
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
                              encoding="utf-8", newline="\n")
            if not report.get("results"):
                print(f"  {field}: 未得出结论（{report.get('reason', '')}）")
                continue
            multiplicity = report["multiplicity"]
            print(f"\n字段 {field}（{EXTERNAL_FIELDS[field]['name']}）")
            print(f"  可用评估日 {report.get('dates_usable')}/{report.get('dates_with_pool')}"
                  f"｜事件 {report.get('events_total'):,}")
            print(f"  {'变体':<20}{'事件':>7}{'高组':>11}{'低组':>11}{'高−低':>11}"
                  f"{'t值':>9}{'零分布95':>10}")
            for name, payload in report["variants"].items():
                diff = payload["results"]["high_minus_low"]
                null = payload["placebo"]["by_group"]["high_minus_low"]["t_abs_p95"]
                print(f"  {name:<20}{payload['events']:>7}"
                      f"{payload['results']['high']['mean_excess']:>11}"
                      f"{payload['results']['low']['mean_excess']:>11}"
                      f"{diff['mean_excess']:>11}{str(diff['t_stat']):>9}{str(null):>10}")
            print(f"  多重检验：{multiplicity['definition']}；族 {multiplicity['family']} "
                  f"先验 {multiplicity['family_prior_statistics']} → 族级共 "
                  f"{multiplicity['family_total_statistics']} 条，门槛 |t| ≥ "
                  f"{multiplicity['bonferroni_family_abs_t']}")
            print(f"  输出：{target}")
            base_all = report["variants"]["base_all"]["results"]["high_minus_low"]
            summary.setdefault(field, {})[basis] = {
                "t": base_all["t_stat"], "mean_excess": base_all["mean_excess"],
                "events": report.get("events_total"),
                "dates_usable": report.get("dates_usable"),
                "threshold": multiplicity["bonferroni_family_abs_t"],
            }

    if len(bases) > 1:
        print(f"\n{'=' * 78}\n两口径并排（base_all 的『高−低』；主检验是可成交口径）\n{'=' * 78}")
        for field, per_basis in summary.items():
            print(f"\n字段 {field}")
            for basis in bases:
                item = per_basis.get(basis)
                if not item:
                    continue
                verdict = ("过门槛" if item["t"] is not None
                           and abs(item["t"]) >= item["threshold"] else "未过门槛")
                print(f"  {basis:<15}事件 {item['events']:>7}  高−低 {item['mean_excess']:>10}"
                      f"  t={str(item['t']):>9}  门槛 {item['threshold']}  → {verdict}")
    print(f"\n耗时 {time.time() - started:.0f}s｜provider 缓存 {provider.cache_size} 个 (标的, 日期)")
    print("\n研究参考，不构成投资建议。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
