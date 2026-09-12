def grade(*, bars: int, segments: list[dict], cost20_test: dict, test: dict,
          structural_errors: list[str] | None = None) -> tuple[str, list[str]]:
    """四级门禁：rejected / insufficient / experimental / candidate（计划 02 §3.4）。"""
    if structural_errors:
        return "rejected", list(structural_errors)
    if bars < 500: return "insufficient", ["有效日K少于500根。"]
    if (segments[1].get("trade_count",0)+segments[2].get("trade_count",0)) < 20: return "insufficient", ["验证段与测试段已完成交易少于20笔。"]
    positive=sum(1 for item in segments[1:] if (item.get("cumulative_return_pct") or 0)>0)
    drawdown_ok = test.get("max_drawdown_pct", 0) >= test.get("benchmark_max_drawdown_pct", 0)
    candidate = (test.get("cumulative_return_pct",0)>0 and positive>=2 and cost20_test.get("cumulative_return_pct",0)>=0 and (test.get("excess_return_pct",0)>0 or drawdown_ok))
    if candidate: return "candidate", ["通过历史候选门禁；仍需前向样本，不能标为已验证。"]
    return "experimental", ["结构正确，但未满足候选策略的稳定性、成本或基准比较门禁。"]
