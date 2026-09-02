# Checkpoint · 2026-08-31-plan-stage02

## 已完成
- 批次1 market_data 客户端层：models.py（能力枚举/端点/错误码/带 gap_reason 的错误类）、ths_client.py（分层校验 + 幂等重试≤2 + Retry-After + 取消传播 + 凭据不泄漏）、identity.py（TickerCandidate/SecurityIdentity，extra=forbid）。
- 批次2 口径与证据：normalization.py（Decimal 字符串契约、百分数→比值、null≠0≠负、同报告期取最新披露版本、季度口径未知不推导）、field_dictionary.py（字段字典/指标单位规则）、research/evidence_models.py（DataGap/DataResult/EvidenceMeta/StoragePolicy）、evidence_store.py（脱敏 + sha256 + 默认 reference_only 不存原文）、repository.py、cache.py（TTL：snapshot 60s / search 24h / 历史+财务 6h）、service.py（确认身份 → 缓存 → 客户端 → 规范化 → 证据 → DataResult；失败转 DataGap 不抛 500）。
- Storage v5：security_identities / evidence / data_gaps / data_cache（迁移测试同步更新到版本 5）。
- 批次3 计算与接口：analysis/{models,price_metrics,financial_metrics}.py（区间涨跌、最大回撤、营收/归母同比含基数≤0 回退绝对变化、净利润率、资产负债率=负债合计口径、OCF/净利润仅年报匹配期）；api/market_data.py（POST /api/data/resolve 双模式服务端确认、GET /api/data/identities、POST /api/data/fetch 统一 results 列表、GET /api/evidence/{id} 32-hex 防遍历）；api_config 目录 hexin_finance → available，能力测试从 501 变为经 MarketDataService 的真实小探针。
- 批次4 前端：api/marketData.ts、SecurityPicker、EvidenceDrawer、DataPreview + Home 入口（阶段 02 标签、金融数据卡状态、选标的取资料区）；provider-catalog.json 同步 available。
- 修复：pytest-asyncio auto（去 anyio 标记）、重试上限差一、Windows 无 Olson 时区库→tzdata 2026.3 装入 .venv 并声明（normalization 使用 ZoneInfo("Asia/Shanghai")）、快照/历史域字段集拆分（close_price 属历史、last_price 属快照）、statement 端点 provenance slug。

## 验证（2026-08-31）
- 后端：pytest 137 passed（原 80 + 客户端 18 + 规范化 15 + 指标 16 + 集成 8；另 api_config 2 处断言按 02 语义更新）。
- 前端：npm run build 通过（仅既有 chunk 体积警告）、lint 0 警告、vitest 19/19（原 16 + data-preview 3，pages.test 一处断言按新 Home 文案更新）。
- 真实小规模联调（8010，用户 Key）：见 90-evidence.md。

## 遗留
- 指数/基金能力（阶段 06）；证据 UI 的字段级下钻保持最小实现；缓存命中也会新增一条 evidence 行（返回 from_cache 标记，作为"本机获取行为"的诚实记录）。
