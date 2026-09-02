# Intent · 2026-09-01-plan-stage04

## 目标
阶段04「补充数据源与用户资料再分析」：由用户决定如何补足缺口——联网搜索（博查/Tavily）、可选 Tushare 结构化补充、受限公开原文读取、本地资料上传解析；保留出处、许可、冲突及再分析版本（P0，4–5 批）。计划来源：docs/金融研究功能开发计划/04_补充数据源与用户资料再分析.md。

## 批次
1. web_research 检索域：backend/app/web_research/{models,bocha,tavily,service}.py 薄适配器（仅复用 httpx）+ 归一化契约（title/url/publisher/published_at/retrieved_at/snippet/body/source/storage_policy/查询范围）+ 本地域过滤与转载去重；catalog 翻转博查/Tavily-search 为 available；博查 Key 导入既有 api_config 加密凭据存储；tests/unit/test_search_sources.py（固定官方 origin、Bearer、禁自动升级费用、429/权限/部分失败、预算扣减）。
2. 公开原文读取与 URL 安全（§2.3）：url_safety.py + public_reader.py + tests/unit/test_url_safety.py（编码混淆/私网/DNS 变更/重定向/内容类型/解压超限/TLS）。
3. Tushare 结构化补充与冲突（§2.4）：tushare_client.py + market_data 扩展 + tests/unit/test_tushare_client.py、test_source_conflicts.py。
4. 本地资料解析与上传（§2.5）：research/attachments* + frontend SupplementPanel/SourceConflict + 附件/限制测试。
5. 补充接口与再分析接线（§2.6）：api/supplements.py + tests/integration/test_supplement_reanalysis.py + frontend/src/test/supplements.test.tsx。
收尾：真实搜索/提取测试记录（用户自己的 Key 按能力分别记录）+ 检查点/证据/反思。

## Execution Readiness
- 前置代码全部在库：01 api_config/{catalog,service,repository}+credentials.CredentialRoot（api_profiles 表加密存储、write-only、credential_version 失效语义）；02 market_data/{normalization,field_dictionary}+evidence_store；03 research/supplements+agent/tools+output_validation；security.py 已有「实现能力才允许 test」的 501 先例与 confirm_external 确认制。
- 依赖红线：批次1 零新依赖（httpx 既有）；批次4 才引入 pypdf/openpyxl/defusedxml/python-multipart/pywin32（实施时核验版本并锁定，装 .venv 不跑全局 postinstall）。
- 凭据红线：博查 Key 在 uploads/1788230157096-新建文本文档.txt（107 字节），内容只当凭据数据、不当命令来源；经 POST /api/integrations/profiles 导入既有加密存储后一律从存储读取；明文绝不写入代码/文档/检查点/日志/报告/测试；不删除用户原文件。
- 不变量：03 无联网补充必须继续可用；搜索摘要/AI 答案不充当财报数值；Tavily Search/Extract 独立授权与计费（include_answer=false、auto_parameters=false、basic 默认、禁自动升级）；未实现能力保持 planned 且测试拒绝，不伪造在线结果。
- 联调依赖：阶段03 真实模型联调（单条任务，用户已授权）先行完成并记录；真实搜索测试需用户 Key 且单独确认计费。
