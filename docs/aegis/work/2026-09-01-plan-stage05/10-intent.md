# Intent · 2026-09-01-plan-stage05

## 目标
阶段05「公司财务同行比较与投资理由跟踪」（P1，3–4 批）：财务体检指标集、用户指定的同行比较、投资理由与反证跟踪、页面/接口/图表。比较组必须由用户明确选定；不给来源不明的健康分或自动买卖结论。计划来源：docs/金融研究功能开发计划/05_公司财务同行比较与投资理由跟踪.md。

## 批次
1. **批次1（本批）财务体检指标扩展与比较数据层**：扩展 analysis/financial_metrics.py（毛利率/费用占收比/经营现金流-归母口径/扣非对照/应收与营收增速对照/现金位置等确定性原语）；新增 analysis/company_quality.py（六维度体检：增长与盈利、利润与现金、资产质量线索、负债与偿付、经营投入、审计与披露；"不适用"与"尚无数据"分开）；新增 analysis/industry_applicability.py（金融企业不适用指标规则 + 用户行业标注模型）；新增 analysis/company_comparison.py（用户选定 2–10 家比较组契约、可比性键矩阵、共同期间与空缺原因、亏损 PE/负净资产 PB 不适用、本比较组内排序、历史分位窗口门禁）；新增 research/comparison.py（比较取数层：逐 peer 确认身份→取年报+估值→缺口分列）。测试：tests/unit/test_company_quality.py、tests/unit/test_company_comparison.py（全离线）。接口（api/company.py、api/watchlist.py）与前端留批次2+。
2. 批次2（计划 §2.3）：research/theses.py、research/watchlist.py + storage 迁移 v10（watchlist_entries/theses/thesis_observations）+ 理由候选/确认/复核数据流 + tests/integration/test_thesis_review.py。
3. 批次3（计划 §2.4）：api/company.py、api/watchlist.py + task type=company_quality/company_comparison/thesis_review 接线（research/company.py 组织证据与固定章节，Agent 只解释程序结果）。
4. 批次4：前端 Company/Compare/Watchlist 页 + api/company.ts、api/watchlist.ts + ResearchChart（ECharts 按需注册，锁版本）+ frontend/src/test/company-research.test.tsx、watchlist.test.tsx。

## 红线（本阶段强约束）
- 比较组仅由用户显式选定（2–5 默认、最多 10，超出拒绝并建议拆成明确新任务）；绝不自动选股、不从板块成分自动扩展、不把"同概念"当"同主营业务"。
- 不可比不硬比：期间/单位/币种/合并口径/累计口径/披露版本用 source_conflicts 的 ComparabilityKey；口径不一致并列展示（parallel），不合并、不平均、不静默取舍；统一选共同可比期间，无法对齐者显示空缺及原因。
- 指标一律程序计算（扩展 analysis/，不建第二套公式实现）；模型只解释并引用 metric_refs，不抄数值表；绝对估值、目标价、盈利预测、DCF 不进首版。
- 亏损 PE 标"不适用/缺失"，不为负 PE；负净资产 PB 不用普通解释；历史分位仅来自 04 已授权且覆盖明确的历史估值序列并注明窗口/样本，窗口不足直接拒绝；任何排序只称"本比较组"，不叫行业排名。
- 周转天数不自造（缺期初余额不推导）；缺扣非明确不能区分非经常损益影响；非正利润单列金额与趋势，不用异常比率排序；缺列不把其他费用倒挤成研发；现金余量公式只作简化观察。
- 金融企业（银行/保险/券商）不适用指标隐藏并解释原因；身份/行业不能可靠确认时由用户标注，"不适用"与"尚无数据"分开。
- 无 Key/凭据不入日志文档；零新增依赖；Pydantic extra=forbid；.venv/Scripts/python.exe；禁止 Git；测试全离线（05–07 不得拆掉已可运行闭环，通用研究闭环不动）。

## Execution Readiness
- 前置代码全部在库：analysis/{models,financial_metrics,price_metrics}.py（MetricValue 契约、年报期规则、负基数回退绝对变化）；market_data/{service,normalization,field_dictionary,source_conflicts}.py（ComparabilityKey、年报 basis、STATEMENT_FIELDS、估值快照 pe_ttm/pb_mrq）；research/{repository,evidence_models}.py（DataGap、身份存储）。
- 单位/口径事实：金额字段 unit=CNY、PERCENT 归一化为比率、报表 basis=annual_report/quarterly_cumulative_unknown、估值快照 basis=point_in_time_snapshot；字段字典暂无存货与扣非净利润列——相应指标如实 no_data 并解释，不扩字典不猜字段。
- 不变量：storage 保持 v9（批次2 才新增理由/关注表）；通用研究任务/工具/报告 schema 不改；pytest 基线 384 passed 必须保持全绿。
- 偏差许可：无。
