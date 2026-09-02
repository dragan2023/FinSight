# Evidence · 2026-09-01-plan-stage03

## 批次1 · json_parser（2026-09-01）
- backend/app/agent/json_parser.py + tests/unit/test_json_parser.py（验收清单：规范 JSON、BOM、围栏、前后解释、字符串内括号/转义、一次双编码、多对象、重复键、NaN/Infinity、过深、断流、超长）。
- 计划 §4 第 1 项达成；无 json_repair 依赖。

## 批次2 · output_models + output_validation（2026-09-01）
- backend/app/agent/output_models.py：ResearchReport/ReportSection/Claim/CounterEvidence/ModelSnapshot 契约 + 四类状态枚举 + extra=forbid + 规模上限；MetricValue 复用 02 analysis/models.py，DataGap 复用 02 research/evidence_models.py（未复制第二套契约）。
- backend/app/agent/output_validation.py：ValidationContext（authorized_evidence = 存在 + thscode 授权）；period_conflict（显式年份与被引证据期间无交集）；metric_conflict（单位感知：%/万/亿/万亿 与 ratio/CNY/CNY-share 表示形式比对，日期/年份剥离不误报，含 _PERIOD_WITH_WORD/_PERIOD_BARE_YEAR/_PERIOD_SHORT/_QUANTITY 四条正则，负向失败方向保守）；ungrounded_reclassified；unsupported_section 降级；反证引用同规则。
- tests/unit/test_output_validation.py 13 例；计划 §4 第 2 项达成（合法 JSON 但引用不存在、错标的/日期/单位、捏造数值仍失败；原始文本不进入已验证业务对象——extra=forbid 拒绝注入字段、validate_report 返回副本对象）。

## 批次3 · research 域 + storage v6 + agent 研究图（2026-09-01）
- backend/app/research/models.py + tasks.py + reports.py + supplements.py：Task.status 六态与报告覆盖（Coverage/SectionStatus/OutputIntegrity 复用批次 2 agent/output_models，未复制第二套契约）分开保存；数据缺失/模型部分失败/截断都 completed，failed 只留给本地执行意外；补充资料只保存不外发、referenced_by_report_id 由再分析显式写入。
- backend/app/storage.py v6：research_tasks / research_events（(task_id, seq) 主键，供批次 4 SSE 增量）/ research_reports / research_supplements 四表；仅 Storage.initialize() 迁移（v5 手工库迁移测试：evidence 行保留、重复 initialize 幂等）；test_official_providers 两处 user_version 断言 5→6 同步更新。
- backend/app/agent/state.py + tools.py + prompts.py + research_graph.py：StateGraph 六节点（身份确认→证据收集→确定性计算→分节解释→校验→定稿）；五工具白名单+显式 schema，无任何 HTTP/SQL/shell/文件路径工具，执行器不接触 Key；工具参数 parse_tool_arguments 严格路径、坏参数向 call id 返回结构化错误且允许改正一次、相同坏参数与相同规范化参数不重复执行；暂时错误重试≤2 次并计入取数预算；默认预算 24 LLM/24 取数/8 工具轮（搜索/提取 0），正文分析前为每模板章节预留 1 次请求；自定义模型→固定取数模板降级（limitations + model_snapshot.mode 展示）；finish_reason=length/无正常结束 → 该节 incomplete（即便可解析也不判完整），自动恢复一次（重写该节、不拼接半截 JSON）；空答/数字冲突 → 同节内容重生成一次；每类修复每节最多一次、全部计入预算、绝不自动循环；reasoning_content 只留内存（TurnResult 仅携带字符计数），Graph compile() 不配置 checkpoint；coverage/integrity/limitations 全部由程序汇总，LLM 漏写不影响缺口面板。
- tests/unit/test_research_graph.py 14 例（计划 §4 第 3 项）：有价格无财务、全无数据+工具不可用仍 completed 出框架、坏参数结构化错误+一次改正+重复不执行、重复调用复用结果、LLM/取数预算耗尽分支、一次格式修复/截断恢复/空答重生成后不循环、metric 冲突一次重生成、reasoning 不落盘、v5→v6 迁移、任务/报告/补充回路。全部离线：假模型脚本 + 本地脚本数据 + 临时库，不访问网络。

## 批次4 · api/research + events + runner + 集成测试（2026-09-01）
- backend/app/api/research.py：任务启动 202 + client_request_id 幂等（同 ID 同内容 200 复用原任务、同 ID 不同内容 409 request_conflict）；SSE 端点快照先行、按 seq 增量、终态 end 帧、心跳注释行；cancel 幂等；报告读取、补充保存（字段白名单）、再分析（子版本任务 + 显式引用补充 + 同幂等语义 + 未知补充 422）。
- backend/app/research/events.py：sse_event/sse_heartbeat/task_snapshot/TERMINAL_STATUSES；公开快照不含凭据与隐藏状态，reasoning 只以 chars 计数出现。
- backend/app/research/runner.py：单进程 asyncio 任务集合（默认 1 活跃、其余排队）；取消幂等——排队直接取消、运行中关闭在途模型流（CancelledError 传播进 SDK 流、脚本流被 GeneratorExit 关闭可观测）；shutdown 收尾。main.py lifespan：启动清扫 RUNNING/QUEUED → interrupted（不自动再付费），graph_factory 注入 TaskBudget/authorization/FactoryModelAdapter/事件实时落库。
- tests/integration/test_research_flow.py 5 例（计划 §4 第 4 项全达成）：缺数据报告可完成且 gaps 不被模型删除（程序缺口 ID 与 risk 章节 gap_refs 互相印证）、全源不可用出条件框架（no_business_evidence + 每模板章节均有产出）、补充保存零调用零新行、再分析子版本 parent_report_id + 补充进入新材料（捕获请求体含补充文本）+ 旧报告不可变、再分析重放/冲突分支。
- tests/integration/test_research_stream.py 6 例（计划 §4 第 5 项全达成）：跨块 SSE 手工分帧、单 assistant 消息多 tool index（0/1/2）合并、call id 与 role=tool 消息一一配对、坏参数向 call_c 结构化错误且不触发上游、reasoning 只见 chars 事件（每轮聚合、合计与 usage.reasoning_chars 一致、全文无思考文本）、finish=length 与缺结束标记均 → truncated（恢复输出为合法 JSON 仍整体作废、计入预算、仅一次）、取消运行中任务关闭在途流（streams_interrupted 可观测、零报告行）、取消排队任务零执行痕迹、重复 POST 复用任务不二次执行、after={seq} 续读不重放旧事件、进程重启 running/queued → interrupted 且零模型请求、重启后旧报告仍可读。
- 修复缺陷：research_graph._tool_loop 的 reasoning 事件被 _collect_evidence 二次落库（SSE 重复、事件流字符计数翻倍）；最小修复去掉重复 append、保留即时落库一次（见 checkpoint 批次4）。

## 命令与结果
- 基线恢复：`pytest -q` → 159 passed。
- 批次2 全量：172 passed（159 + 13）。
- 批次3 全量：`.venv/Scripts/python.exe -m pytest tests -m "not requires_api and not requires_llm" -q` → **186 passed**（172 + 14）。
- 批次4 全量：同上命令 → **197 passed**（186 + 11：flow 5 + stream 6）；两新文件复跑两次稳定；`pip check` → No broken requirements found。
- 冒烟：storage v6 fresh/迁移/幂等；TaskStore→TaskUsage→events 往返（含 task.created 事件 seq=1）。

## 边界说明
- 本批次未接真实模型/真实取数：FactoryModelAdapter 已按 01 的 ModelFactory/ChunkMerger/completion_options/输出预算接线（流式聚合、transfer 上限、确认制授权 confirmed=True），但真实供应商联调记录仍待批次 5 收尾；不将 Mock 通过说成真实供应商已通过。
- 目录（model_config/providers.py）未核验任何模型的 JSON 模式能力 → 不向模型硬发 response_format，JSON 要求由提示词给出并走同一严格解析管线；若后续目录补充该能力再启用原生结构化输出。
- SSE 公开事件流（research/events.py、任务集合、client_request_id 幂等）已于批次 4 落地并有集成覆盖；本批次事件已按 (task_id, seq) 持久化，批次 4 起进度事件经 event_sink 实时落库，无 sink 的单元路径仍在 finalize 统一落库。
- validation 无法保证语言推理正确性（模块 docstring 与计划 §2.3 一致）；模型自评不作验证。
## 批次5 · 前端研究工作台（2026-09-01）
- frontend/src/api/research.ts：任务/事件/报告/补充契约与请求封装 + GET SSE 封装 + 幂等键生成 + 中文标签表（对齐批次 3/4 后端契约，MetricValue 复用 02、DataGap 复用 marketData 视图，未复制第二套契约）。
- frontend/src/api/eventStream.ts：GET/POST 两种事件流共用 openEventStream + readSseResponse（eventsource-parser；共享错误包络 → ApiError；心跳注释不入 onEvent）；01 的模型流不受影响。
- frontend/src/components/DataGaps/index.tsx：程序缺口面板（缺什么/影响/可以怎样补 + gapReasonLabels），不折叠、空态占位。
- frontend/src/components/ResearchReport/index.tsx：覆盖/完整性/模型快照/再分析版本 Tag；limitations；程序指标块（缺数如实显示）；分节状态 + claims kind 标签 + refs；反证；待验证事项；raw_sections 以 <pre> 纯文本展示（无 innerHTML）。
- frontend/src/pages/Research/index.tsx：启动前展示模型/来源/预算（24/24/8，重试计入，搜索/提取 0）；POST 一次 + client_request_id 幂等；SSE 快照先行 + seq 去重 + reasoning 只显示字符计数 + 章节进度 + 校验提示；取消按钮；刷新续读按 after=lastSeq（不重放生成 POST）；补充保存 → "已保存，尚未再分析"（不自动触发）；再分析带勾选补充与新幂等键；truncated/unstructured → "继续完成报告（再分析）"；断流走 ErrorNotice 续读重试。
- frontend/src/App.tsx + components/Layout/index.tsx：/research 懒加载路由 + 导航"研究工作台" + 顶栏标题。
- frontend/src/test/research.test.tsx 8 例（计划 §4 第 6 项全达成）：partial 报告（程序指标/分节状态/缺口同屏）、缺口面板三问俱全不折叠、no_business_evidence 条件框架 + 待验证 + 缺数不充数、格式失败 raw_sections 纯文本、页面全流程（幂等键 32 位 hex 断言、SSE 事件 → 报告 + 缺口 + 深度思考 N 字）、运行中取消如实显示、补充待分析 + 再分析携带 supplement_ids/新幂等键、truncated 完整性提示。mock 约定与既有 pages.test.tsx 一致（vi.mock + importOriginal 展开，system/marketData/research 三模块）。

## 命令与结果（批次5）
- frontend：pnpm vitest run → **5 files / 27 tests passed**（既有 19 + 批次5 新增 8）；pnpm tsc -b → 0 error；pnpm exec eslint src --max-warnings=0 → 0 problem；pnpm build → 构建成功（>500kB chunk 警告为既有现象，antd 单包体积，非本批引入）。
- 后端回归：本批次未改动 backend/，基线保持 197 passed（批次4）。

## 边界说明（批次5）
- 真实模型联调记录仍缺：本环境无真实 DeepSeek/同花顺凭据，批次 5 未执行真实供应商调用；全部前端测试基于契约 mock（与后端集成测试锁定的 SSE/JSON 契约一致）。不将 Mock 通过说成真实供应商已通过；待用户提供凭据后按计划 §5.1 执行联调并补记。
- 浏览器端"刷新续读"依赖 GET /api/research/tasks/{id}/events?after={seq}（批次 4 已有集成覆盖 after 续读不重放）；页面在流断开后不重发生成 POST，幂等键仅用于首次创建与再分析。
- 补充资料无列表端点（后端仅 POST）：页面按会话内保存记录展示"已保存，尚未再分析"；刷新页面后历史补充不在 UI 复现（数据仍在库，报告中经再分析引用），如需跨会话列表需后端补 GET 端点（超出计划 §2.6 范围，未做）。
## 真实模型联调记录（2026-09-01，用户授权的单条付费任务；关闭 needs-verification 遗留）
- 环境与本机 API：uvicorn 127.0.0.1:8000（会话制，X-FinSight-Session 经 /api/system/session 获取，令牌未落任何文档）；三个模型 profile（DeepSeek/GLM·智谱/Qwen·通义）key_status=configured、masked_api_key=••••••••，全程零密钥明文（API 响应即 write-only 视图）。Active profile：DeepSeek deepseek-v4-flash-vision-exp，temperature 0.2，thinking enabled，output_policy=maximum → effective_output max_tokens=393216；目录未核验 JSON 模式 → json_mode=false，未向模型硬发 response_format（与计划 §2.2 边界一致）。同花顺 Financial-API profile 为 saved_unverified 真实启用。
- 任务：POST /api/research/tasks，question=\"贵州茅台（600519）当前盈利质量如何？当前估值隐含了怎样的市场预期？\"，thscode=600519.SH（既有已确认身份），默认预算 24 LLM/24 取数/8 工具轮，client_request_id 32 位幂等键。轮询至终态（GET tasks/{id}），墙钟 6 分 33 秒（02:44:34Z → 02:51:07Z），终态 completed、error=None。
- 用量实况：llm_requests=18/24、fetches=5/24、tool_rounds=6/8、reasoning_chars=74809；searches/extractions=0（本阶段未接入，符合预算固定 0）。
- JSON 结构化输出成立但非满贯：五节中 4 节 validated（financials=conditional、price_valuation=supported、risk=conditional、followups=conditional）；facts 节一次格式修复后仍 invalid（raw_sections.reason=repair_still_invalid，原始文本 2389 字符以纯文本单独保存，该节按 unavailable 呈现，报告 output_integrity=unstructured）。部分结果保留红线兑现：coverage 仍=sufficient，未因单节失败整体降级或丢失。
- 工具调用实况：同花顺真实取数 5 次全部成功（evidence_ids=5 份真实证据），工具轮 6；calculate_metrics 程序计算 7 项指标含真实值（net_margin 0.5053、debt_ratio 0.1642、ocf_vs_net_profit 0.7212、range_change -0.0875、max_drawdown -0.2347 等；缺数指标保留空值与说明，不以 0 充数）。price_valuation 节给出 PE_TTM≈19.99、PB≈6.48 等真实估值并通过结构校验（supported）。
- 数值冲突红线实证：最终校验抓到 6 处 metric_conflict（正文数字与被引指标不一致，分布于 financials/price_valuation/risk/followups 四节），全部如实标记为 validation 事件并保留在报告中，相应章节保持 conditional——模型重抄数值表被程序拦截，未静默通过。
- 长输出/finish 实况：无 finish=length 迹象（无 truncated_after_recovery / truncated 标记；facts 失败码为 repair_still_invalid 而非截断恢复路径）；token 细分不在 v1 usage 契约内（契约仅暴露 reasoning_chars 聚合值），如实记录不强造。
- reasoning 红线复查：research_events 全量 27 条中 10 条 reasoning 事件 payload 键仅 {type, chars}，思考全文零落库；SSE 公开进度不含思考内容、不含凭据。
- coverage/gaps 实况：coverage=sufficient；真实 DataGap 1 条（financials.balance，reason=not_published——上游尚未披露该期数据，属\u201c不是数据错误\u201d的真实缺口）；limitations 1 条（未通过结构校验章节仅供查看，未计入已验证内容）。
- 与批次5 前端契约一致性：本次报告/原始章节/缺口的实际 JSON 形状与 frontend/src/api/research.ts 契约一致（ResearchReport/DataGaps 组件可直接渲染本任务结果）。
- 待观察（非缺陷）：facts 节大 JSON 一次性生成在 thinking 模型下的稳定性（本例一次修复未救回）——阶段04+ 继续观察分节粒度与输出预算的平衡；博查 Key 已导入既有 api_config 加密存储（profile 已建，adapter 批次1 实现），原始上传文件保留未动。
