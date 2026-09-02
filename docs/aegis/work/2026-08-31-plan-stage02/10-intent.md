# Intent · 2026-08-31-plan-stage02

## 目标
执行 docs/金融研究功能开发计划 阶段02「同花顺数据证据与基础计算」：把用户选定标的的可用数据转成有口径、有来源、可复算的研究证据，缺失转换为可解释的 DataGap。

## 边界（Scope Card）
- In: market_data（ths_client/models/service/identity/cache/normalization/field_dictionary）、research（evidence_models/evidence_store/repository）、analysis（models/price_metrics/financial_metrics）、api/market_data.py、storage v5、前端（marketData.ts/SecurityPicker/EvidenceDrawer/Home 入口）、对应离线测试。
- Out: 指数/基金能力（06）、LLM 证据整合（03）、pandas/金融 SDK（明令禁止）、改变 01 凭据契约。

## 关键契约（真实联调 + 官方 llms-full.txt 钉准，2026-08-31）
- Origin https://fuyao.aicubes.cn，X-api-key；信封 {code,message,request_id,data:{timestamp,item}}；HTTP 恒 200 承载业务码。
- 业务码：1001 缺参 / 1002 格式 / 1003 越界 / 1004 参数冲突（financials limit 与 start/end 互斥）/ 2001 未认证 / 2003 权限 / 3001 标的不存在 / 3002 数据未就绪 / 3004 类型不支持 / 4001 限频 / 5001-5003 服务端。
- 端点参数钉准：search 用 q（非 query）；snapshot/valuations 用 thscodes（复数、逗号分隔；**单 thscode 会被忽略返回全市场，必须先本地校验**）；historical 用毫秒时间戳 start/end + interval=1d + adjust；statements 用 thscode+period+limit 或 start+end；indicators 用 report=yyyy-Q；calendar 返回 date_ms+date。
- 历史实测：snapshot batch、valuations、search、calendar、historical 空窗（时间戳格式）已验证；financials 未实测（旧探针 429）。

## 批次
1. ths_client + models + identity + test_ths_client（18 例）。
2. normalization + field_dictionary + evidence_models + evidence_store/repository + cache + storage v5 + service 编排 + test_data_normalization。
3. analysis 三件 + api/market_data.py + test_financial/price_metrics + test_evidence_flow。
4. 前端 marketData/SecurityPicker/EvidenceDrawer/Home + data-preview.test.tsx。
