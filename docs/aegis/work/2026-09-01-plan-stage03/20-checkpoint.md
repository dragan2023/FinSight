# Checkpoint · 2026-09-01-plan-stage03

## Resume state
- 2026-09-01 会话一在批次 2 中断（仅写出两个模块）；会话二已完成批次 2 全部范围与验证；会话三已完成批次 3 全部范围与验证；会话四补齐批次 4 集成测试与验证（代码由会话三遗留，本会话新增 11 例并修复 1 处事件重复落库）；会话五完成批次 5 前端（api/pages/组件/路由 + 8 例测试，前端四件套全绿）并收尾反思文档。
- TaskStartSnapshot：无 VCS 仓库（计划 §5.1 禁止 Git 操作）；批次 4 起点基线 pytest 186 passed。

## 已完成
- 批次1（completed 2026-09-01）agent/json_parser.py：parse_section_object / parse_tool_arguments 双信任级入口；严格 json.loads（重复键/NaN/Infinity 拒绝）、深度≤32/节点≤20000/字节预算、BOM+围栏+边界唯一候选+一次双编码的无语义清理（记录清理类型）；多候选/未闭合拒绝；无 eval/exec/YAML/引号替换。tests/unit/test_json_parser.py 覆盖验收清单。
- 批次2（completed 2026-09-01）agent/output_models.py：分节报告契约（ClaimKind、SectionStatus、Coverage、OutputIntegrity、claims 引用 evidence/metric/gap refs、程序计算 MetricValue、extra=forbid、规模上限、SECTION_TEMPLATES 五节）。agent/output_validation.py：引用存在 + 授权范围（标的 thscode）校验、period_conflict、metric_conflict（单位感知换算，日期/年份不误报）、ungrounded_reclassified、unsupported_section 降级；两个错误方向都保守。tests/unit/test_output_validation.py 13 例。
- 批次3（completed 2026-09-01）research 域 + storage v6 + agent 研究图：
  - research/models.py：TaskStatus（queued|running|completed|interrupted|cancelled|failed）、TaskBudget（默认 24 LLM/24 取数/8 工具轮，搜索/提取固定 0）、TaskUsage（reasoning 只记字符数）、ResearchTask、TaskEvent（(task_id, seq) 供批次 4 SSE 增量）、ResearchSupplement（user_provided/user_assumption）、RawSectionText/StoredReport（原始文本与已验证契约分开保存）。
  - research/tasks.py：任务状态迁移集中于 TaskStore；completed 只说明本轮结束；数据缺失/模型部分失败/截断都仍 completed；failed 只留给本地执行意外；mark_interrupted 供批次 4 重启收尾（不自动再付费）。
  - research/reports.py：ReportStore 按任务内 version 递增保存，不覆盖旧行；raw_sections 单独保存（公开、纯文本展示）。
  - research/supplements.py：SupplementStore 只保存不外发不触发再分析；fields 白名单键；referenced_by_report_id 仅在再分析显式引用后写入。
  - research/repository.py + storage.py v6：新增 research_tasks/research_events/research_reports/research_supplements 四表；仅由 Storage.initialize() 迁移（v5→v6 幂等，保留既有行）；既有迁移断言（test_official_providers）同步更新为 6。
  - agent/state.py：ResearchState 通道 + 预算/用量 helpers（默认 24/24/8/0/0，重试计入）。
  - agent/tools.py：五工具白名单（fetch_financials/fetch_prices/fetch_valuation/read_evidence/calculate_metrics）+ 显式 schema + extra=forbid 参数模型；参数走 parse_tool_arguments 严格路径，坏参数向对应 call id 返回结构化错误、允许改正一次、相同坏参数不重复执行；相同工具+相同规范化参数复用本任务结果；暂时错误（rate_limited/timeout）每逻辑取数最多重试两次且计入取数预算；来源失败返回结构化 DataGap；read_evidence 仅限本任务授权证据并按上限截取摘录。
  - agent/prompts.py：系统规则（数据缺失不是拒绝分析的理由；外部内容只是证据、"忽略指令"无效；数值用 metric_refs 引用不抄表）；分节 JSON 契约、工具收集、格式修复、截断恢复、内容重生成五组提示。目录未核验 JSON 模式 → 不向模型硬发 response_format。
  - agent/research_graph.py：LangGraph StateGraph 六节点 身份确认→证据收集→确定性计算→分节解释→校验（复用 output_validation）→定稿；自定义模型降级固定取数模板（能力降级写入 limitations/snapshot.mode）；每节一次格式修复、一次内容重生成（空答或数字/期间冲突）、一次截断恢复（finish=length/无结束 → incomplete，即便能解析也不判完整；重写不拼接），全部计入预算且不自动循环；正文分析前为每个模板章节预留 1 次请求；预算耗尽不再新开付费请求、保留已完成内容；无模型时保留本地事实/计算/缺口并明确"未完成 AI 分析"；reasoning_content 只留内存计数；compile() 不配置 checkpoint；coverage/integrity/limitations 由程序汇总。
  - tests/unit/test_research_graph.py 14 例：有价格无财务、全无数据+工具不可用、坏参数结构化错误+一次改正+重复坏参数不执行、重复调用复用结果、LLM 预算耗尽保留已完成内容、取数预算提前截断模板、一次格式修复后不循环、截断恢复一次成功/失败保持 incomplete、空答重生成一次后接受、metric 冲突一次重生成、reasoning 只计数不落盘、v5→v6 迁移保留数据、任务/报告/补充持久化回路。全部离线（假模型/本地脚本/虚假密钥不出现）。
- 批次4（completed 2026-09-01）api/research.py + research/events.py + research/runner.py + main.py 接线 + 两个集成测试：
  - api/research.py：POST /tasks 202 + client_request_id 幂等（同 ID 同内容 200 返回原任务不二次执行、同 ID 不同内容 409 request_conflict）；GET tasks/{id}（快照+最近事件）；GET tasks/{id}/events?after={seq} SSE（快照先行、seq 单调、心跳注释、终态后 end 帧）；POST cancel 幂等；GET reports/{id}；POST supplements（白名单外字段剥离，不触发任何调用）；POST reanalyze（同幂等语义、未知 supplement 422、新任务携带 parent_report_id 与被选补充）。
  - research/runner.py：ResearchTaskManager 单进程 asyncio 任务集合，默认 1 活跃（Semaphore）其余排队；取消幂等（排队直接取消、运行中关闭在途模型流）；_run 兜底 mark_failed；shutdown 取消在途并清队。main.py lifespan 启动时把遗留 RUNNING/QUEUED 收尾为 interrupted（绝不自动再付费），并把 TaskBudget/authorization 快照 + FactoryModelAdapter(model_factory, profile) 接线到 ResearchGraph.run。
  - tests/integration/test_research_flow.py 5 例（计划 §4 第 4 项）：缺数据报告 completed（coverage=partial、gaps 不被模型删除、facts 引用真实证据/指标）、全源不可用仍出条件框架（no_business_evidence）、补充保存零调用零新行（白名单外字段剥离、超限 422、孤儿 404）、再分析子版本 parent_report_id + 旧报告保持可读 + supplement.referenced_by_report_id、再分析重放 200 复用/改内容 409。
  - tests/integration/test_research_stream.py 6 例（计划 §4 第 5 项）：SSE 全程（手工分帧容忍跨块）、多 tool index 合并、call id 与 tool 消息一一配对、坏参数向 call_c 返回结构化错误不触发取数、reasoning 只以 chars 事件出现且与 usage 一致、facts finish=length + 恢复缺结束标记 → truncated（恢复输出为合法 JSON 仍不判完整）、取消运行中任务关闭在途流/取消排队任务零执行痕迹、重复 POST 复用任务、after={seq} 续读不重放、进程重启 running/queued → interrupted 且零模型请求。
  - 批次4 修复的缺陷：backend/app/agent/research_graph.py 的 _tool_loop——取数轮 reasoning 事件在工具循环内即时落库一次后，又被 _collect_evidence 对返回 notices 整体二次落库，SSE 中同一思考事件重复出现、事件流字符计数翻倍（任务 usage 本身正确）。最小修复：去掉 notices.append(reasoning)，保留即时 _emit 一次；无事件 sink 的单元路径不再批量补记 reasoning 事件（与"reasoning 只计数"一致，既有单测无此依赖）。

- 批次5（completed 2026-09-01）前端研究工作台 + 批次5 测试：
  - frontend/src/api/research.ts：任务/预算/用量/事件/报告/补充/原始章节 TypeScript 契约（对齐 backend/app/research/models.py 与 api/research.py；MetricValue 对齐 analysis/models.py，DataGap 复用 marketData 的 DataGapView）；createResearchTask/getResearchTask/cancelResearchTask/getResearchReport/addSupplement/reanalyzeReport 请求封装；streamResearchEvents 封装 GET SSE（after={seq} 续读）；newClientRequestId() 32 位十六进制幂等键（crypto.randomUUID，无 crypto 时回退）；isTerminalEvent；状态/覆盖/完整性/章节/主张/模型模式中文标签表。
  - frontend/src/api/eventStream.ts：抽出 openEventStream/readSseResponse 共享管线；新增 getEventStream（GET + X-FinSight-Session + eventsource-parser），postEventStream 保持 POST 语义不变；错误响应仍解析共享错误包络为 ApiError；心跳注释不进 onEvent，[DONE] 哨兵继续跳过。
  - frontend/src/components/DataGaps/index.tsx：缺口面板只渲染程序 DataGap（缺什么/影响/可以怎样补 + gapReasonLabels），不提供折叠；空缺口给 Empty 占位。
  - frontend/src/components/ResearchReport/index.tsx：覆盖/完整性/模型快照 Tag、limitations 警示、程序计算指标块（value=null 显示"缺数"）、分节卡片（状态 Tag、claims 带 kind 标签与 refs、反证、待验证事项）、未通过结构校验的 raw_sections 以 <pre> 纯文本展示（绝不用 innerHTML）、底部 DataGaps；再分析版本显示"再分析版本"Tag。
  - frontend/src/pages/Research/index.tsx：SecurityPicker 确认标的 + 问题输入 + 启动前展示模型/来源/预算说明；开始研究 POST 一次（client_request_id 幂等）→ 订阅 SSE，快照帧还原任务、seq 去重增量、reasoning 只显示字符计数、section 事件汇总章节进度、phase/validation 展示；终态/流结束兜底 getResearchTask+getResearchReport；运行中提供取消与刷新续读（after=lastSeqRef，不重放生成 POST）；报告下方补充资料（user_provided/user_assumption + ≤8000 字）保存后展示"已保存，尚未再分析"，再分析携带勾选的 supplement_ids + 新幂等键建立子任务（truncated/unstructured 时按钮文案为"继续完成报告（再分析）"）；断流错误经 ErrorNotice 提供续读重试。
  - frontend/src/App.tsx 增加 /research 懒加载路由；components/Layout 增加"研究工作台"导航与顶栏标题。
  - frontend/src/test/research.test.tsx 8 例（计划 §4 第 6 项）：partial 报告程序指标/分节状态/部分结果同屏、缺口面板三问俱全且不折叠、no_business_evidence 出条件框架+待验证事项+缺数不显示 0、格式失败 raw_sections 纯文本展示、页面发起任务（client_request_id 32 位十六进制断言）→ SSE 事件 → 报告+缺口+深度思考字符计数、运行中取消状态如实、补充保存"已保存，尚未再分析"+再分析携带 supplement_ids 与新幂等键建立新任务、truncated 报告完整性提示。

## 验证（2026-09-01）
- 全量：.venv/Scripts/python.exe -m pytest tests -m "not requires_api and not requires_llm" -q → **197 passed**（批次3 后 186 + 批次4 新增 11；两个新集成文件复跑两次无时序脆弱）。
- .venv/Scripts/python.exe -m pip check → No broken requirements found。
- storage v6 冒烟：fresh 建库 version=6、四表齐全、重复 initialize 幂等；v5 手工库迁移后 evidence 行保留。
- 批次5 前端：pnpm vitest run → **5 files / 27 passed**（19 既有 + 8 新增）；pnpm tsc -b → 0 error；pnpm exec eslint src --max-warnings=0 → 0 problem；pnpm build → 构建成功（>500kB chunk 为既有 antd 体积警告）。后端回归（批次5 未动 backend/）→ 197 passed。红线抽查：代码零 json_repair/eval/exec/innerHTML，extra="forbid" 齐全，未硬发 response_format。
- 真实模型联调（用户授权单条任务）：completed，墙钟 6m33s；llm=18/24、真实同花顺取数 5 次（证据 5 份）、工具轮 6、reasoning_chars=74809；coverage=sufficient、integrity=unstructured（facts 节一次修复后仍 invalid → raw_sections 单独保存）；6 处 metric_conflict 被校验如实标记；reasoning 事件仅 {type,chars} 零全文落库。详见 90-evidence.md 联调记录——needs-verification 遗留已关闭。

## 下一步
- 阶段03 全部完成（批次1-5 + 真实模型联调 + 反思），阶段04 批次1 web_research 检索域已完成（212 passed），工作记录 docs/aegis/work/2026-09-01-plan-stage04/；博查 Key 已导入既有 api_config 加密存储（明文未落任何代码/文档/日志）。
- 反思见 99-reflection.md（做得好/教训/待办/后续建议）；真实联调详情见 90-evidence.md。

## 暂停标记（用户指令：找合适断点中止开发，2026-09-01 04:25）
- 断点位置：阶段03 全部完成（上述五批次 + 文档收尾 + 红线终检），阶段04 尚未启动（无 intent、无 web_research/ 文件、无半成品）——干净断点。
- 全局验证基线：后端 pytest 197 passed + pip check 干净；前端 vitest 27/27 + tsc 0 error + eslint 0 problem + build 成功。
- 监督目标 goal-bd0c21aa 置为 paused（阶段04-08 待用户恢复指令）；开发代理 1b37e39b（及更早的 3a1588f6）保持 ready 可续聊，未派新任务。
- 恢复方式：向监督方说"继续开发"即可从阶段04 批次1（web_research 检索域：博查/Tavily 搜索、Tushare 可选、附件与原文留源、冲突不混算）重启监督循环；真实模型联调需先在应用设置页保存含 Key 的模型 profile。
- **已恢复（2026-09-01 用户指令"完成真实研究任务联调后继续开发"）**：真实模型联调已完成（见 90-evidence.md 联调记录，needs-verification 关闭）；阶段04 批次1 已完成（212 passed，见 stage04/ 工作记录）。本暂停标记就此失效，后续批次按 stage04/20-checkpoint.md 推进。
