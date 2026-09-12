"""数据来源注册表：五源总线的唯一归属地（V2-01）。

口径与 docs/API与数据使用边界.md 第 2 节保持一致，两者不一致时以边界文档为准。

- trust_level（可信级别）：
  - authoritative 权威依据：可直接作为事实引用（同花顺 API 及其本地落库副本）。
  - corroborated 可核验佐证：可用作依据，但必须给出原文或数据批次定位（私有知识库；
    线索类内容经交叉验证后也升级到该级别）。
  - lead 线索：未经第二来源或工具数据交叉验证，不得作为结论依据（搜索、RSS）。
- pricing（费用标记）：paid=付费额度，仅限用户主动触发的会话/任务内调用；
  free=免费。
- auto_call：是否允许在无用户主动触发的场景自动调用。付费来源恒为 False；
  免费来源中 RSS 的后台轮询仍需用户显式开启。同花顺 API 经核实为免费
  （官方 README 只约定限流、无计费条款；平台无充值入口），其 auto_call 仍为
  False——原因是边界文档 §5 的用户控制原则（取数需用户主动操作），与费用无关。
"""

from __future__ import annotations

# 常驻免责声明：任何建议/观点类输出必须携带（边界文档 §4）。
DISCLAIMER = "研究参考，不构成投资建议"

# 五源枚举：顺序即展示顺序（权威在前，线索在后）。
SOURCE_IDS = ("ths", "warehouse", "web_search", "rss", "kb")

# 可信级别 -> 中文标签；注册表之外的级别一律视为非法。
TRUST_LEVELS: dict[str, str] = {
    "authoritative": "权威依据",
    "corroborated": "可核验佐证",
    "lead": "线索，需交叉验证",
}

# 费用标记 -> 中文标签。
PRICING_LABELS: dict[str, str] = {
    "paid": "付费，仅用户触发",
    "free": "免费",
}

# 来源状态（静态规划位）：planned 的来源在对应阶段上线前不参与取证。
# rss 与 kb 已分别于 V2-02 / V2-03 上线。
SOURCE_STATUS: dict[str, dict[str, str]] = {
    "ths": {"state": "live", "label": "已接入"},
    "warehouse": {"state": "live", "label": "已接入"},
    "web_search": {"state": "live", "label": "已接入"},
    "rss": {"state": "live", "label": "已接入"},
    "kb": {"state": "live", "label": "已接入"},
}

# 五源注册表。capability 只描述产品能力，字段命名保持英文供程序使用，
# 用户界面一律渲染 label（中文）。
SOURCES: list[dict] = [
    {
        "id": "ths",
        "name": "同花顺金融数据 API",
        "role": "唯一权威行情、财务、估值、指数、基金与特色数据源",
        "trust_level": "authoritative",
        "pricing": "free",
        "auto_call": False,
        "capabilities": [
            {"id": "meta_search", "label": "标的检索与代码表"},
            {"id": "quotes", "label": "行情快照与历史日线"},
            {"id": "adjustment", "label": "除复权因子与复权行情"},
            {"id": "financials", "label": "财务三表"},
            {"id": "indicators", "label": "财务指标"},
            {"id": "valuations", "label": "估值快照"},
            {"id": "index", "label": "指数目录、成分与行情"},
            {"id": "fund", "label": "基金全域研究数据"},
            {"id": "special_data", "label": "涨跌停、热榜、龙虎榜与竞价"},
            {"id": "market_dumps", "label": "全市场数据导出"},
        ],
    },
    {
        "id": "warehouse",
        "name": "本地全市场仓库",
        "role": "同花顺官方导出落库（DuckDB），供基率统计与本地研究型回测",
        "trust_level": "authoritative",
        "pricing": "free",
        "auto_call": False,
        "capabilities": [
            {"id": "market_query", "label": "全市场历史行情查询"},
            {"id": "base_rate", "label": "历史同类案例基率统计"},
            {"id": "backtest", "label": "本地研究型回测"},
            {"id": "cross_section", "label": "横截面筛选与统计"},
        ],
    },
    {
        "id": "web_search",
        "name": "博查搜索 API",
        "role": "付费官方搜索 API 的解释线索与外部信息发现，不取代财报数据",
        "trust_level": "lead",
        "pricing": "paid",
        "auto_call": False,
        "capabilities": [
            {"id": "web_search", "label": "网页搜索"},
        ],
    },
    {
        "id": "rss",
        "name": "金融媒体 RSS",
        "role": "订阅端新闻聚合：同花顺官方资讯接口、媒体公开 RSS 与第三方聚合端点（仅订阅读取，本产品不爬取）",
        "trust_level": "lead",
        "pricing": "free",
        "auto_call": True,
        "capabilities": [
            {"id": "feed_manage", "label": "订阅源管理"},
            {"id": "news_fetch", "label": "新闻订阅与去重"},
            {"id": "entity_link", "label": "新闻关联到标的"},
        ],
    },
    {
        "id": "kb",
        "name": "私有知识库",
        "role": "用户上传文档的分块检索与引用回链",
        "trust_level": "corroborated",
        "pricing": "free",
        "auto_call": False,
        "capabilities": [
            {"id": "doc_ingest", "label": "文档摄取与分块"},
            {"id": "knowledge_search", "label": "知识检索"},
            {"id": "citation", "label": "原文引用回链"},
        ],
    },
]


def get_source(source_id: str) -> dict:
    """按来源 ID 返回注册表条目（浅拷贝），未知来源抛 KeyError。"""
    for source in SOURCES:
        if source["id"] == source_id:
            return dict(source)
    raise KeyError(f"未登记的数据来源：{source_id}")


def all_sources() -> list[dict]:
    """按 SOURCE_IDS 展示顺序返回全部注册表条目（浅拷贝）。"""
    return [get_source(source_id) for source_id in SOURCE_IDS]


def trust_label(source_id: str) -> str:
    """来源的可信级别中文标签。"""
    level = get_source(source_id)["trust_level"]
    return TRUST_LEVELS[level]


def pricing_label(source_id: str) -> str:
    """来源的费用中文标签。"""
    pricing = get_source(source_id)["pricing"]
    return PRICING_LABELS[pricing]


def is_paid(source_id: str) -> bool:
    """来源是否消耗付费额度。"""
    return get_source(source_id)["pricing"] == "paid"
