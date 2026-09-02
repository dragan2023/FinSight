# Evidence · 2026-08-31-plan-stage02

## 离线验证
- \`pytest tests -m 'not requires_api and not requires_llm' -q\` → **137 passed**（15.9s）。
  - test_ths_client.py 18：鉴权头注入且 Key 不入日志/错误、缺 Key 不发请求、停用即停、HTTP200+业务码≠0（1001 不重试）、非 JSON、401/2003 权限、429+Retry-After 重试后成功、429/4001 重试耗尽→RateLimited、5xx 重试后成功、超时重试→Timeout、连接失败→ConnectionFailed、任务取消传播且只发一次请求、报表取数模式互斥（不发请求）、历史窗口上限客户端拦截、检索/代码/报告期格式前置校验、3002→not_published。
  - test_data_normalization.py 15：百分数→比值保留 raw、null→not_published 缺口且不补零、0/负数原样、NaN→invalid_response、同报告期取最新披露版本（duplicate_versions=1）、季度 basis 未知且无推导字段、年报 basis/期末、历史排序与 CNY 单位、缺失收盘→缺口、空窗无记录无异常、快照 timestamp null 保持 null、未知指标→缺口、上海时区日期换算（date_ms=1756656000000 = 2025-09-01 00:00+08:00）、to_decimal 拒绝 NaN/Infinity/bool、估值字段比值单位。
  - test_financial_metrics.py / test_price_metrics.py 16：区间涨跌首尾收盘、跳过空收盘不插值、0 基数拒算并给原因、两点不足拒算、最大回撤运行峰值（-0.25）、无回撤=0 不为 null、同比正基数 0.3、负基数回退绝对变化 20（单位 CNY）、不足两期拒算、**季度行绝不参与同比**、归母字段、合并净利润口径净利率、负债合计口径资产负债率、OCF/净利润年报匹配 1.25、**季度现金流不匹配推导**。
  - test_evidence_flow.py 8（全 App + MockTransport）：resolve/确认入库、未确认标的取数→404、三表并发取数一表失败不影响其余（income available + balance rate_limited + cashflow not_published）、标的不存在→DataResult unavailable 而非 500、**响应中的模拟 Token 在 evidence/cache 落库内容中被剔除**、证据 ID 非法格式防遍历、**换 Key（credential_version 1→2）后缓存隔离必须重新请求且用新 Key**、日线区间的原始记录不虚构。
- 前端：build 通过、eslint 0 警告、vitest **19 passed**（data-preview 3：检索→确认→显示已确认标的、缺口原因"缺失：上游未披露"+ 金额字符串 "174144000000" + null 显示 "--" 不补零、抽屉证据出处 reference_only/annual_report/finsight.evidence.v1）。

## 真实小规模联调（2026-08-31，用户 Key，FINSIGHT_PORT=8010，数据库 data/db/finsight.db v4→v5）
- 启动：\`uvicorn --factory backend.app.main:create_app --port 8010\`。
- 确认标的：POST /api/data/resolve {confirm_thscode:"600519.SH"} → 服务端 search 精确回显后入库，identity 600519.SH 贵州茅台 confirmed_source=user_selected。
- 行情快照（refresh）：status=available，last_price=1299.52，open 1297.99 / high 1305，无缺口（修复 close_price 域错配后）。
- 财务三表（annual limit=1）：三表全部 available，最新报告期 **2025-FY**；利润表 operating_income=**168,838,102,514.79**（原币元），净利润/归母/负债合计/现金流字段同批入库；三份 evidence，storage_policy=reference_only。
- 缓存：同参数二次请求 returned_scope.from_cache=true；data_cache 4 行（snapshot + 三表），credential_version=1。
- 账本：evidence 10 行、security_identities 1 行；未观察到 429（旧探针的一次性限流未复现）。
- 明文 Key 未出现在任何响应、日志或数据库明文列；联调结束 8010 实例已停止，用户 8000 实例未受影响。

## 契约钉准记录（官方 llms-full.txt + 实测）
- search：q（非 query）；snapshot/valuations：thscodes 复数、逗号分隔，**单代码若用错参数名会返回全市场（total=5563）**，已加前置校验防误用。
- historical：start/end 为**毫秒 Unix 时间戳**（非日期串），interval 当前仅 1d，adjust 默认 forward，窗口≤10 年。
- statements：period 必填 annual/quarterly，limit∈[1,20] 与 start+end 互斥（1004）；金额字段原币元、basic_eps 元/股。
- indicators：report=yyyy-Q（1002 格式校验）、value 保留字符串精度、五类能力块固定顺序。
- calendar：返回 date_ms + date(yyyyMMdd)；业务码表 1001/1002/1003/1004/2001/2003/3001/3002/3004/4001/5001/5002/5003 已映射到 DataGap reasons。
