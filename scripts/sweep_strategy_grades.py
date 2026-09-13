"""探索性可行性扫描：在全部可用标的上跑策略实验，统计 validation_grade 分布。

回答的问题是**唯一真正重要的那个**：在本地仓库全部有足够历史的标的上，
`strategy/validation.py:grade()` 的四级门禁到底能筛出多少个 `candidate`？
这个数字决定 C2/C3 是继续投入还是先止损。

⚠️ **本脚本是探索性（exploratory）用途，不是预注册评估。**
它产出的是"可行性/筛选率"这类排期依据，**不构成策略有效性的证据**，
也不得作为 C3 停用判定的样本。任何据此做出的规则调整必须走
`evaluation_batches` 预注册（见 `docs/荐股治理方案/02` §A.2：
多重检验必须统计**全部尝试过的**因子/规则数，含放弃的）。

设计要点：
- 数据源是**本地 DuckDB**（D6），口径为公司行动总收益（P0-1），**强制 as-of 截断**；
- 计算函数**复用生产代码**（`run_backtest` / `metrics` / `segments` / `grade`），
  只替换数据源——不另写一套回测，避免与生产口径漂移；
- 短路优化：`验证段+测试段交易 < 20` 时无需再跑 20bps 成本回测（与 `grade()` 分支等价）。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\sweep_strategy_grades.py --workers 8
    .\\.venv\\Scripts\\python.exe scripts\\sweep_strategy_grades.py --symbols 601799.SH --start 2019-01-01
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.market_warehouse.repository import WarehouseRepository  # noqa: E402
from backend.app.strategy.backtest import metrics, run_backtest, segments  # noqa: E402
from backend.app.strategy.local_bars import local_price_bars  # noqa: E402
from backend.app.strategy.registry import get_spec  # noqa: E402
from backend.app.strategy.service import StrategyService  # noqa: E402
from backend.app.strategy.validation import grade  # noqa: E402

MIN_BARS = 500
TRADE_REASON = "验证段与测试段已完成交易少于20笔。"
STRATEGIES = ("trend_20_60_v1", "reversal_5d_v1")

_REPO: WarehouseRepository | None = None


def _init(db_path: str) -> None:
    global _REPO
    _REPO = WarehouseRepository(Path(db_path))


def _trade_bucket(count: int) -> str:
    if count <= 0:
        return "0"
    if count < 10:
        return "1-9"
    if count < 20:
        return "10-19"
    if count < 50:
        return "20-49"
    return "50+"


def _nogap_bars(bars: list) -> list:
    """真实收盘价 + 无跳空执行价（`open_i = close_{i-1}`），消除隔夜跳空。

    它是安慰剂实验的**归因对照**：安慰剂同时改变了两件事（序列结构 + 执行价），
    只有拿"真实结构 + 同款执行价"再跑一次，才能判断安慰剂通过率的差异到底来自
    结构还是来自执行假设。
    """
    from backend.app.strategy.models import PriceBar

    out = [PriceBar(date=bars[0].date, open=float(bars[0].close), close=float(bars[0].close))]
    for index in range(1, len(bars)):
        out.append(PriceBar(date=bars[index].date, open=float(bars[index - 1].close),
                            close=float(bars[index].close)))
    return out


def _shuffle_bars(bars: list, *, seed: str) -> list:
    """安慰剂序列：把逐日收益随机重排后重建价格，破坏一切序列结构。

    保留的信息：K 线根数、日收益的**全集**（故波动率与买持基准完全相同——乘法可交换）、
    以及成交价的可执行性；被破坏的只有**顺序**，也就是趋势/反转策略赖以生存的
    序列相关性。

    执行价取 `open_i = close_{i-1}`（无跳空），比真实数据**更有利于**策略
    （真实开盘跳空通常伤害趋势跟踪）。因此安慰剂是一个"对策略友好"的零假设：
    若真实结果跑不赢它，结论是真实的择时优势不可检测。
    """
    import random

    from backend.app.strategy.models import PriceBar

    rng = random.Random(seed)
    closes = [float(bar.close) for bar in bars]
    returns = [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes))]
    rng.shuffle(returns)
    out = [PriceBar(date=bars[0].date, open=closes[0], close=closes[0])]
    level = closes[0]
    for index, value in enumerate(returns, 1):
        previous = level
        level *= (1 + value)
        out.append(PriceBar(date=bars[index].date, open=previous, close=level))
    return out


def _one(job: tuple[str, str, int, int], *, placebo: bool = False, nogap: bool = False, seed: int = 0) -> dict:
    thscode, strategy_id, start_ms, end_ms = job
    base = {"thscode": thscode, "strategy_id": strategy_id}
    try:
        bars, snapshot = local_price_bars(_REPO, thscode, start_ms=start_ms, end_ms=end_ms)
    except Exception as exc:  # 单只失败不能拖垮整批
        return {**base, "error": f"{type(exc).__name__}: {exc}"[:300]}
    base["bars"] = len(bars)
    base["snapshot"] = snapshot
    if placebo:
        bars = _shuffle_bars(bars, seed=f"{thscode}:{seed}")
        base["placebo"] = True
    elif nogap:
        bars = _nogap_bars(bars)
        base["nogap"] = True
    if len(bars) < MIN_BARS:
        return {**base, "grade": "insufficient", "reasons": ["有效日K少于500根。"]}
    structural = StrategyService._structural_errors(bars)
    if structural:
        return {**base, "grade": "rejected", "reasons": structural}

    gate = dict(get_spec("volatility_gate_v1").parameters)
    seg = segments(strategy_id, bars, 10, gate=gate)
    dev_trades = seg[0].get("trade_count") or 0
    val_trades = seg[1].get("trade_count") or 0
    test_trades = seg[2].get("trade_count") or 0
    base.update({"dev_trades": dev_trades, "val_trades": val_trades, "test_trades": test_trades,
                 "out_of_sample_trades": val_trades + test_trades,
                 "dev_return_pct": seg[0].get("cumulative_return_pct"),
                 "val_return_pct": seg[1].get("cumulative_return_pct"),
                 "test_return_pct": seg[2].get("cumulative_return_pct"),
                 "test_excess_pct": seg[2].get("excess_return_pct"),
                 "test_drawdown_pct": seg[2].get("max_drawdown_pct"),
                 "test_benchmark_drawdown_pct": seg[2].get("benchmark_max_drawdown_pct"),
                 "coverage_pct": seg[2].get("coverage_pct")})
    if val_trades + test_trades < 20:
        # 与 grade() 的第二个 insufficient 分支等价，省掉一次 20bps 全周期回测。
        return {**base, "grade": "insufficient", "reasons": [TRADE_REASON], "cost20_return_pct": None}

    cost20 = metrics(**run_backtest(strategy_id, bars, 20, gate=gate))
    grade_value, reasons = grade(bars=len(bars), segments=seg, cost20_test=cost20, test=seg[-1])
    return {**base, "grade": grade_value, "reasons": reasons,
            "cost20_return_pct": cost20.get("cumulative_return_pct"),
            "full_trade_count": cost20.get("trade_count")}


def _parse_day(value: str | None, *, default: int) -> int:
    if not value:
        return default
    day = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(day.timestamp() * 1000)


def _universe(db_path: Path, min_bars: int) -> tuple[list[str], int]:
    repo = WarehouseRepository(db_path)
    db = repo._connect()
    try:
        rows = db.execute(
            "SELECT thscode, count(*) AS bars, max(date_ms) AS last_ms FROM daily_k "
            "WHERE close_price > 0 GROUP BY thscode HAVING count(*) >= ? ORDER BY thscode",
            [min_bars]).fetchall()
        market_last = db.execute("SELECT max(date_ms) FROM daily_k").fetchone()[0]
    finally:
        db.close()
    return [(row[0], row[2]) for row in rows], market_last


def main() -> int:
    # Windows 控制台默认 GBK，报告里含非 GBK 字符会直接抛 UnicodeEncodeError。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="探索性策略门禁可行性扫描（本地 DuckDB）")
    parser.add_argument("--db", default=str(ROOT / "data" / "market" / "finsight_market.duckdb"))
    parser.add_argument("--symbols", default="", help="逗号分隔；给了就只跑这些标的")
    parser.add_argument("--strategies", default=",".join(STRATEGIES))
    parser.add_argument("--start", default="2016-01-01", help="起始日 YYYY-MM-DD（默认覆盖整个仓库）")
    parser.add_argument("--end", default="", help="as-of 截止日 YYYY-MM-DD（默认=仓库最后一根）")
    parser.add_argument("--min-bars", type=int, default=MIN_BARS)
    parser.add_argument("--limit", type=int, default=0, help=">0 时按等距抽样限制标的数")
    parser.add_argument("--placebo", action="store_true",
                        help="安慰剂检验：把逐日收益随机重排后重跑，得到零假设下的通过率")
    parser.add_argument("--nogap", action="store_true",
                        help="归因对照：真实收盘价 + 无跳空执行价（open=前一日收盘）")
    parser.add_argument("--seed", type=int, default=20260913, help="安慰剂重排的随机种子（可复现）")
    parser.add_argument("--workers", type=int, default=0, help="0=自动（CPU 数）")
    parser.add_argument("--out", default="", help="输出目录（默认 data/exports/sweeps/<时间戳>）")
    args = parser.parse_args()

    db_path = Path(args.db)
    market, market_last = _universe(db_path, args.min_bars)
    end_ms = _parse_day(args.end, default=market_last)
    start_ms = _parse_day(args.start, default=0)
    strategies = [item for item in args.strategies.split(",") if item]

    if args.symbols:
        wanted = [item.strip() for item in args.symbols.split(",") if item.strip()]
        universe = [(code, market_last) for code in wanted]
    else:
        universe = market
        if args.limit and args.limit < len(universe):
            step = len(universe) / args.limit
            universe = [universe[int(index * step)] for index in range(args.limit)]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) if args.out else ROOT / "data" / "exports" / "sweeps" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "results.jsonl"
    jobs = [(code, strategy, start_ms, end_ms) for code in [c for c, _ in universe] for strategy in strategies]

    stale_cut = market_last - 30 * 86_400_000
    stale = sum(1 for _, last in universe if last < stale_cut)
    print(f"标的数 {len(universe)}（其中最后交易日早于 {stale_cut} 的 {stale} 只，疑似退市/长期停牌）")
    print(f"策略 {strategies}｜实验数 {len(jobs)}｜区间 {start_ms}..{end_ms}｜输出 {out_dir}")

    workers = args.workers or min(8, (len(jobs) // 200) + 1)
    started = time.time()
    results: list[dict] = []
    with jsonl.open("w", encoding="utf-8") as sink, ProcessPoolExecutor(
            max_workers=workers, initializer=_init, initargs=(str(db_path),)) as pool:
        futures = [pool.submit(_one, job, placebo=args.placebo, nogap=args.nogap, seed=args.seed)
                   for job in jobs]
        for index, future in enumerate(as_completed(futures), 1):
            row = future.result()
            results.append(row)
            sink.write(json.dumps(row, ensure_ascii=False) + "\n")
            sink.flush()
            if index % 200 == 0 or index == len(jobs):
                elapsed = time.time() - started
                print(f"  {index}/{len(jobs)} 完成，已用 {elapsed:.0f}s（{index / elapsed:.1f}/s）", flush=True)

    elapsed = time.time() - started
    summary = _summarize(results, universe_count=len(universe), stale=stale, elapsed=elapsed,
                         start_ms=start_ms, end_ms=end_ms, strategies=strategies, db=str(db_path),
                         placebo=args.placebo, nogap=args.nogap,
                         seed=args.seed if args.placebo else None)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "summary.md").write_text(_render_markdown(summary), encoding="utf-8")
    print(_render_markdown(summary))
    print(f"\n输出：{out_dir}")
    return 0


def _summarize(results: list[dict], **meta) -> dict:
    grades = Counter(row.get("grade", "error") for row in results)
    reasons: Counter = Counter()
    for row in results:
        for reason in row.get("reasons") or ([row["error"]] if row.get("error") else []):
            reasons[str(reason)[:60]] += 1
    buckets = Counter(_trade_bucket(row.get("out_of_sample_trades") or 0) for row in results
                      if row.get("out_of_sample_trades") is not None)
    candidates = [{key: row.get(key) for key in
                   ("thscode", "strategy_id", "bars", "out_of_sample_trades", "test_return_pct",
                    "test_excess_pct", "cost20_return_pct")}
                  for row in results if row.get("grade") == "candidate"]
    per_strategy = {}
    for strategy in meta["strategies"]:
        rows = [row for row in results if row.get("strategy_id") == strategy]
        per_strategy[strategy] = {"experiments": len(rows),
                                  "grades": dict(Counter(row.get("grade", "error") for row in rows))}
    return {
        **meta,
        "experiments": len(results),
        "grades": dict(grades),
        "candidate_count": grades.get("candidate", 0),
        "reasons": dict(reasons.most_common(12)),
        "out_of_sample_trade_buckets": dict(buckets),
        "per_strategy": per_strategy,
        "candidates": sorted(candidates, key=lambda item: -(item.get("test_excess_pct") or 0))[:200],
    }


def _render_markdown(summary: dict) -> str:
    lines = [
        "# 策略门禁可行性扫描（探索性）"
        + ("｜安慰剂对照" if summary.get("placebo") else ("｜无跳空对照" if summary.get("nogap") else "")),
        "",
        "> ⚠️ 探索性结果：用于排期与止损判断，**不构成策略有效性证据**，不得作为 C3 判定样本。",
        "",
    ]
    if summary.get("placebo"):
        lines += ["> 🧪 **安慰剂序列**：逐日收益被随机重排（保留 K 线根数、收益全集与买持基准，"
                  "破坏一切序列相关性），执行价无跳空——即**对策略友好**的零假设。"
                  f"随机种子 {summary.get('seed')}。", ""]
    lines += [
        f"- 数据源：`{summary['db']}`（本地 DuckDB，公司行动总收益口径，强制 as-of 截断）",
        f"- 区间：{summary['start_ms']} .. {summary['end_ms']}",
        f"- 标的数：{summary['universe_count']}（其中 {summary['stale']} 只最后交易日明显偏早，疑似退市/长期停牌）",
        f"- 策略：{', '.join(summary['strategies'])}",
        f"- 实验数：{summary['experiments']}｜耗时 {summary['elapsed']:.0f}s",
        "",
        "## 结果",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| **candidate 数量** | **{summary['candidate_count']}** |",
    ]
    for grade_name, count in sorted(summary["grades"].items()):
        lines.append(f"| `{grade_name}` | {count} |")
    lines += ["", "## 验证段+测试段交易笔数分布", "", "| 笔数 | 实验数 |", "|---|---|"]
    for bucket in ("0", "1-9", "10-19", "20-49", "50+"):
        lines.append(f"| {bucket} | {summary['out_of_sample_trade_buckets'].get(bucket, 0)} |")
    lines += ["", "## 未通过原因（按出现次数）", "", "| 原因 | 次数 |", "|---|---|"]
    for reason, count in summary["reasons"].items():
        lines.append(f"| {reason} | {count} |")
    lines += ["", "## 按策略", "", "| 策略 | 实验数 | 分布 |", "|---|---|---|"]
    for strategy, info in summary["per_strategy"].items():
        dist = "、".join(f"{key} {value}" for key, value in sorted(info["grades"].items()))
        lines.append(f"| `{strategy}` | {info['experiments']} | {dist} |")
    if summary["candidates"]:
        lines += ["", "## candidate 明细（按测试段超额降序，最多 200 条）", "",
                  "| 标的 | 策略 | 日K | 样本外交易 | 测试段收益% | 测试段超额% | 20bps 全周期% |",
                  "|---|---|---|---|---|---|---|"]
        for item in summary["candidates"]:
            lines.append("| {thscode} | {strategy_id} | {bars} | {out_of_sample_trades} | "
                         "{test_return_pct} | {test_excess_pct} | {cost20_return_pct} |".format(**item))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
