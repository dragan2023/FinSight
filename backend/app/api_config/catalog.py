"""Curated finance/search integration directory, reviewed 2026-08-31.

Only services selected in the parent plan appear here. Adapter implementation is
tracked per service; until it lands, saving a credential is allowed but calling
or testing the capability is refused as not_implemented (never faked online).
"""

INTEGRATIONS = [
    {
        "id": "hexin_finance",
        "category": "finance_data",
        "name": "同花顺 Financial-API",
        "purpose": "主结构化数据源：行情、估值、财务三表与指标、指数/基金公开数据。",
        "docs_url": "https://github.com/HiThink-Tech/Financial-API",
        "apply_url": "https://fuyao.aicubes.cn/",
        "credential": {"kind": "api_key", "label": "X-api-key", "hint": "用户 X-api-key；固定官方地址由适配器持有，不在此填写。"},
        "implementation": "available",
        "stage": "01",
        "capabilities": [
            {"id": "prices", "name": "行情与历史价格"},
            {"id": "valuations", "name": "估值快照"},
            {"id": "financials", "name": "财务三表与指标"},
            {"id": "reference", "name": "标的代码表与交易日历"},
            {"id": "corporate_actions", "name": "除复权事件"},
            {"id": "indices", "name": "指数、板块与成分股", "implementation": "available"},
            {"id": "auction", "name": "集合竞价", "implementation": "available"},
            {"id": "special_data", "name": "涨跌停、热榜、异动与龙虎榜", "implementation": "available"},
            {"id": "fund_profile", "name": "基金核心：基本资料", "implementation": "available"},
            {"id": "fund_portfolio", "name": "基金核心：持仓与配置", "implementation": "available"},
            {"id": "fund_performance", "name": "基金核心：净值与业绩", "implementation": "available"},
            {"id": "fund_market", "name": "基金核心：ETF 场内行情", "implementation": "available"},
            {"id": "fund_extended", "name": "基金扩展：持有人、经理、公司、财务、诊断、募集与资讯", "implementation": "available"},
            {"id": "market_dumps", "name": "全市场数据下载", "implementation": "available"},
        ],
        "notes": "实际能力、频次、存储及模型处理权限按账号确认；公开能力不含分钟/tick、海外与研报全文。",
    },
    {
        "id": "bocha",
        "category": "web_search",
        "name": "博查 Web Search",
        "purpose": "中文公司公告、监管信息与经营解释的网页发现。",
        "docs_url": "https://bocha.cn/",
        "apply_url": "https://bocha.cn/",
        "credential": {"kind": "api_key", "label": "API Key", "hint": "粘贴博查开放平台生成的 API Key 本体；无需填写 Bearer 前缀，按官方套餐计费。"},
        "implementation": "available",
        "stage": "04",
        "capabilities": [
            {"id": "web_search", "name": "网页搜索", "implementation": "available"},
        ],
        "notes": "搜索结果只是线索，不能直接覆盖财报数值；域名限制在本地过滤，不猜未核验参数。",
    },
    {
        "id": "qwen_embedding",
        "category": "embedding",
        "name": "Qwen 文本向量化",
        "purpose": "私有知识库分块的可选向量化，用于向量+关键词混合检索。",
        "docs_url": "https://help.aliyun.com/zh/model-studio/user-guide/embedding",
        "apply_url": "https://bailian.console.aliyun.com/",
        "credential": {"kind": "api_key", "label": "API Key", "hint": "阿里云百炼 DashScope API Key；仅在用户主动摄取或检索时外发分块文本。"},
        "implementation": "available",
        "stage": "04",
        "capabilities": [
            {"id": "embeddings", "name": "文本向量化", "implementation": "available"},
        ],
        "notes": "不配置则知识库走本地 FTS5 关键词检索，文档内容零外发；启用后仅分块文本发往嵌入服务用于向量化。",
    },
    {
        "id": "local_sources",
        "category": "local",
        "name": "本地附件解析",
        "purpose": "用户上传附件的本机解析，作为独立留源证据。",
        "docs_url": "",
        "apply_url": "",
        "credential": {"kind": "none", "label": "无需凭据", "hint": "无 Key；显示数据流向、存储与外发开关。"},
        "implementation": "planned",
        "stage": "04",
        "capabilities": [
            {"id": "attachments", "name": "本地附件解析"},
        ],
        "notes": "解析只在本机进行，不访问网络；原文与用户资料独立留源，冲突时不与结构化数据混算。",
    },
]

REVIEWED_AT = "2026-08-31"


def integration_spec(provider: str) -> dict | None:
    return next((item for item in INTEGRATIONS if item["id"] == provider), None)


def has_credential(provider: str) -> bool:
    spec = integration_spec(provider)
    return bool(spec) and spec["credential"]["kind"] != "none"
