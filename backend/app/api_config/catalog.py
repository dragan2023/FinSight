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
        "stage": "02",
        "capabilities": [
            {"id": "prices", "name": "行情与历史价格"},
            {"id": "valuations", "name": "估值快照"},
            {"id": "financials", "name": "财务三表与指标"},
        ],
        "notes": "实际能力、频次、存储及模型处理权限按账号确认；公开能力不含分钟/tick、海外与研报全文。",
    },
    {
        "id": "tushare",
        "category": "finance_data",
        "name": "Tushare Pro",
        "purpose": "可选结构化补充：基础资料、daily_basic 历史估值、补充三表与披露计划。",
        "docs_url": "https://tushare.pro/document/1?doc_id=290",
        "apply_url": "https://tushare.pro/register",
        "credential": {"kind": "token", "label": "Token", "hint": "用户 Token 位于请求体，适配器禁止记录请求体；积分门槛与单独权限并存。"},
        "implementation": "available",
        "stage": "04",
        "capabilities": [
            {"id": "stock_basic", "name": "股票基础资料"},
            {"id": "daily_basic", "name": "历史每日指标"},
            {"id": "financials", "name": "补充财务报表"},
            {"id": "indicators", "name": "可比财务指标"},
            {"id": "audit", "name": "审计意见"},
            {"id": "mainbz", "name": "主营构成"},
            {"id": "disclosure", "name": "披露计划/实际日期"},
        ],
        "notes": "个人默认许可不等于商用；仅 HTTPS 官方地址，TLS 失败不降级明文；未授权接口按缺口处理。",
    },
    {
        "id": "bocha",
        "category": "web_search",
        "name": "博查 Web Search",
        "purpose": "中文公司公告、监管信息与经营解释的网页发现。",
        "docs_url": "https://bocha.cn/",
        "apply_url": "https://bocha.cn/",
        "credential": {"kind": "api_key", "label": "Bearer Key", "hint": "用户 Bearer Key，按官方套餐计费。"},
        "implementation": "available",
        "stage": "04",
        "capabilities": [
            {"id": "web_search", "name": "网页搜索", "implementation": "available"},
        ],
        "notes": "搜索结果只是线索，不能直接覆盖财报数值；域名限制在本地过滤，不猜未核验参数。",
    },
    {
        "id": "tavily",
        "category": "web_search",
        "name": "Tavily Search / Extract",
        "purpose": "可选搜索与页面正文提取，来源链接随证据返回。",
        "docs_url": "https://docs.tavily.com/documentation/api-reference/endpoint/search",
        "apply_url": "https://app.tavily.com/",
        "credential": {"kind": "api_key", "label": "Bearer Key", "hint": "同一把 Key；Search 与 Extract 独立消耗 credit，分别启用与测试。"},
        "implementation": "available",
        "stage": "04",
        "capabilities": [
            {"id": "search", "name": "搜索（basic/advanced）", "implementation": "available"},
            {"id": "extract", "name": "正文提取", "implementation": "available"},
        ],
        "notes": "搜索与提取均固定 basic 深度、禁自动升级与 AI 答案；提取逐 URL 记录成败。默认不发私人材料；持久保存提取正文需满足其服务条款。",
    },
    {
        "id": "local_sources",
        "category": "local",
        "name": "本地附件与受限公开原文",
        "purpose": "用户上传附件与受限公开 URL 原文，作为独立留源证据。",
        "docs_url": "",
        "apply_url": "",
        "credential": {"kind": "none", "label": "无需凭据", "hint": "无 Key；显示数据流向、存储与外发开关。"},
        "implementation": "planned",
        "stage": "04",
        "capabilities": [
            {"id": "attachments", "name": "本地附件解析"},
            {"id": "public_pages", "name": "受限公开原文读取"},
        ],
        "notes": "原文与用户资料独立留源；冲突时不与结构化数据混算。",
    },
]

REVIEWED_AT = "2026-08-31"


def integration_spec(provider: str) -> dict | None:
    return next((item for item in INTEGRATIONS if item["id"] == provider), None)


def has_credential(provider: str) -> bool:
    spec = integration_spec(provider)
    return bool(spec) and spec["credential"]["kind"] != "none"