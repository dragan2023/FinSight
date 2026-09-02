# Evidence · 2026-09-01-plan-stage05 · batch 1

阶段 05「公司财务同行比较与投资理由跟踪」首批仅交付确定性数据层，不含理由跟踪、路由或前端页面。

## 已完成

- `analysis/financial_metrics.py`：毛利率、费用占收比、经营现金流/归母净利润、现金减负债合计。所有计算仅使用可匹配的年报期；收入为零、缺列、季度口径未知均拒绝推导。
- `analysis/industry_applicability.py`：银行、保险、证券等金融行业的适用性门禁；“不适用”与“尚无数据”分开呈现。
- `analysis/company_quality.py`：增长与盈利、利润与现金、资产质量线索、负债与偿付、经营投入、审计与披露六个维度；没有复合健康分、预测或交易结论。
- `analysis/company_comparison.py`：只接受用户显式选择的 2–10 家公司；报告期、币种、单位、合并口径或期间口径不一致时并列展示且不排名。亏损/零利润 PE、负/零净资产 PB 标为不适用，不作为“便宜”处理。
- `research/comparison.py`：只对显式比较组取证；单家公司取数失败不影响其他公司，且不自动扩展比较组。

## 验证

`\.venv\Scripts\python.exe -m pytest tests/unit/test_financial_metrics.py tests/unit/test_company_quality.py tests/unit/test_company_comparison.py -q` → **29 passed**，仅有既有 Starlette TestClient 弃用警告。

## 后续批次

按阶段 05 计划继续实现用户确认的投资理由/反证与关注列表，然后接入任务路由和前端页面。存储 schema 维持 v9；本批未新增依赖、未调用外部 API、未修改通用研究任务契约。
