"""只读数据来源注册表端点（V2-01）。

静态口径来自 sources/registry.py；enabled 与状态说明按本机运行时计算，
绝不返回任何 Key 或凭据痕迹。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..sources import registry

router = APIRouter(tags=["sources"])


def _ths_runtime(state) -> dict:
    status = state.market.source_status()
    enabled = bool(status.get("configured")) and bool(status.get("enabled"))
    if enabled:
        detail = "同花顺数据服务已配置并启用。"
    elif status.get("configured"):
        detail = "已保存同花顺密钥，但数据服务尚未启用。"
    else:
        detail = "尚未配置同花顺数据服务，可在设置页接入。"
    return {"enabled": enabled, "detail": detail}


def _web_search_runtime(state) -> dict:
    status = state.web_research.source_status()
    ready = [name for name, row in status.items()
             if row.get("configured") and row.get("enabled")]
    if ready:
        detail = "博查已启用。"
    else:
        detail = "尚未配置搜索服务，可在设置页接入博查。"
    return {"enabled": bool(ready), "detail": detail}


def _warehouse_runtime(state) -> dict:
    status = state.market_warehouse.status()
    enabled = bool(status.get("available"))
    if enabled:
        count = int(status.get("row_count") or 0)
        detail = f"本地全市场仓库已就绪，共 {count:,} 行未复权日 K 数据。"
    else:
        detail = "尚未同步全市场数据，可在全市场观察页发起同步。"
    return {"enabled": enabled, "detail": detail}


def _planned_runtime(state, detail: str) -> dict:
    return {"enabled": False, "detail": detail}


def _rss_runtime(state) -> dict:
    return state.rss.runtime_summary()


_RUNTIME = {
    "ths": _ths_runtime,
    "warehouse": _warehouse_runtime,
    "web_search": _web_search_runtime,
    "rss": _rss_runtime,
    "kb": lambda state: state.kb.source_status(),
}


def _source_payload(source_id: str, state) -> dict:
    source = registry.get_source(source_id)
    runtime = _RUNTIME[source_id](state)
    static_state = registry.SOURCE_STATUS[source_id]
    if static_state["state"] == "planned":
        status_label = static_state["label"]
    elif runtime["enabled"]:
        status_label = "已启用"
    else:
        status_label = "未启用"
    return {
        "id": source["id"],
        "name": source["name"],
        "role": source["role"],
        "trust_level": source["trust_level"],
        "trust_label": registry.TRUST_LEVELS[source["trust_level"]],
        "pricing": source["pricing"],
        "pricing_label": registry.PRICING_LABELS[source["pricing"]],
        "auto_call": source["auto_call"],
        "capabilities": [dict(cap) for cap in source["capabilities"]],
        "enabled": runtime["enabled"],
        "status_label": status_label,
        "detail": runtime["detail"],
    }


@router.get("/api/sources")
def sources(request: Request):
    """当前注册的数据来源清单（静态口径 + 本机启用状态）。"""
    state = request.app.state
    return {
        "disclaimer": registry.DISCLAIMER,
        "sources": [_source_payload(source_id, state)
                    for source_id in registry.SOURCE_IDS],
    }
