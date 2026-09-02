# Intent · 2026-09-01-plan-stage03

## 目标
阶段03「鲁棒AI研究与结构化输出」：选标的、问问题、分析已有证据、列缺口、补充文字、主动再分析——第一条缺数据仍可用的研究闭环（P0，4–5 批）。

## 批次
1. agent/json_parser.py：严格解析 + 允许的无语义清理（BOM/围栏/边界唯一候选/一次双编码）+ 结构上限（深度/节点/字节）+ 工具参数严格路径；不引入 json_repair。
2. agent/output_models.py + output_validation.py：分节报告契约（claims 引用事实/推导/推断/用户假设 + evidence/metric/gap refs），业务校验引用存在、标的/期间/单位一致、事实与计算有依据；冲突标记。
3. research 域（models/tasks/reports/supplements）+ storage v6（任务/事件/报告/补充表）+ agent（state/tools/prompts/research_graph）：LangGraph StateGraph、只读工具白名单（fetch_financials/fetch_prices/fetch_valuation/read_evidence/calculate_metrics）、预算（默认 24 LLM/24 取数/8 工具轮，重试计入）、自定义模型降级为固定取数模板、finish_reason=length 标 incomplete + 一次自动恢复。
4. api/research.py + research/events.py + 单进程 asyncio 任务集合（默认 1 活跃，排队/取消/中断恢复语义，client_request_id 幂等）+ test_research_flow / test_research_stream。
5. 前端：api/research.ts、pages/Research、ResearchReport、DataGaps、eventStream 复用（fetch SSE，不沿用 Axios 130s）、App/Layout 路由 + research.test.tsx。
收尾：真实模型联调记录（JSON/工具/长输出实际结果）+ 检查点/证据/反思。

## 红线（来自计划 §2.2/§3）
- 数据缺失不是任务失败；空答"请补数据"按内容不合格在同节修复预算内重生成。
- 工具白名单：无任意 HTTP/SQL/shell/文件路径工具，模型拿不到 Key；外部内容只是证据，"忽略指令"文本不改变白名单。
- 不做宽松 JSON 修补（json_repair 首版不引入）；partial JSON 只用于流式预览；禁止 eval/exec/YAML/全局单引号替换。
- finish_reason=length/断流 → incomplete，即便解析出合法 JSON 也不判完整；自动恢复一次且计入预算。
- SSE 只传公开进度，reasoning_content 只留任务后端内存；Graph 不用落盘 checkpoint；重启后 running→interrupted 不自动再付费。
- 数值由程序计算渲染（metric_refs 引用），模型不得重新抄写数值表；冲突标记并修复该节。
- 保留 01 设置、02 资料预览、一键启动；单实例默认 1 个活跃任务。
