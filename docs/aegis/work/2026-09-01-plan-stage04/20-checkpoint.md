# Checkpoint · 2026-09-01-plan-stage04

## Resume state
- 2026-09-01 会话六启动阶段04（先完成阶段03 真实模型联调遗留，见 stage03/90-evidence.md 联调记录，再进入本阶段批次1）。
- TaskStartSnapshot：无 VCS 仓库（计划 §5.1 禁止 Git 操作）；批次1 起点基线 pytest 197 passed（阶段03 终态）。
- 本机后端曾以 uvicorn 127.0.0.1:8000 运行完成联调（日志 logs/backend_live.*.log）；博查 Key 已导入既有 api_config 加密存储（api_profiles 表）。

## 已完成
- 批次1（completed 2026-09-01）web_research 检索域（计划 04 §2.2/§2.1）：
  - backend/app/web_research/models.py：归一化契约 WebSearchRequest（extra=forbid，query≤200、count≤10 可见上限、freshness 枚举、本地域名过滤列表）+ SearchItem（title/url/domain/publisher/published_at/retrieved_at/snippet/source/storage_policy=reference_only/evidence_id 占位）+ WebSearchResult（filtered_by_domain/duplicates_removed/dropped_invalid/warnings）；独立错误族（not_configured/unsupported/permission_denied/quota_limited/timeout/invalid_response/upstream_rejected，全部 AppError 子类、无上游原文）。
  - backend/app/web_research/bocha.py：POST https://api.bocha.cn/v1/web-search + Bearer，仅发送已核验参数（query/freshness/summary/count），域名限制本地过滤不猜服务端参数；dateLastCrawled 是抓取时间，明确不映射 published_at（缺失发布时间保持 unknown）；分层校验（HTTP 状态 → 业务 code 信封 → schema）；瞬时失败（429/5xx/信封失败/超时/连接）最多重试两次且遵守 Retry-After（上限 8s），401/402/403 与参数拒绝不重试。
  - backend/app/web_research/tavily.py：POST https://api.tavily.com/search + Bearer；固定 include_answer=false、auto_parameters=false、search_depth=basic、topic=general，费用行为钉死不可被服务端自动升级；time_range 与 include_domains 用官方参数映射；published_date 官方字段才映射发布时间，publisher 保持 unknown。
  - backend/app/web_research/service.py：provider 分发 + 共享本地策略（域名后缀匹配过滤、URL 与 (domain, title) 去重——转载判定无可靠信号，保守只去重完全相同条目并原样保留 publisher）；api_key/provider 从既有 api_config 凭据存储按请求读取（禁用/未配置直接 409/409）；test_capability 按市场服务同款契约返回 {capability,status,message,tested_at,credential_version}，错误映射 permission_denied/quota_limited/failed，绝不伪造结果。
  - api_config/catalog.py：bocha → available；tavily → available 但 extract 能力级 implementation=planned（能力级实现状态新维度，测试入口按能力拒绝）。
  - api_config/service.py：test() 按类别路由——web_search 走 web_service.test_capability（main.py 生命周期绑定 app.state.integrations.web_service）；能力级 planned → 501 not_implemented（不发起外部请求）。
  - tests/unit/test_search_sources.py 15 例：博查请求契约与归一化（Bearer/固定 origin/恰四参数/抓取时间不当发布时间）、信封失败重试后拒绝、401 零重试、429 按 Retry-After 重试、持续 5xx invalid_response、坏条目丢弃+警告、本地域名过滤与去重计数、count 契约封顶（>10 → 422，单一事实来源在请求契约）、tavily 费用行为钉死、time_range/include_domains 官方参数、401 映射、未配置零请求、extract 能力 501 零请求、test_capability 结果可落库、目录路由断言。全部离线（httpx.MockTransport）。
  - 博查凭据导入：经 POST /api/integrations/profiles 写入既有加密存储（Key 从用户上传文件提取纯 ASCII token，仅打印长度；明文零落代码/文档/日志/检查点/测试；原上传文件保留未动）。

- 批次2（completed 2026-09-01）公开原文读取与 URL 安全（计划 04 §2.3 + §2.2 Extract）：
  - backend/app/web_research/url_safety.py：validate_url（仅 HTTPS:443、拒绝 userinfo/IP 字面量/百分号混淆/非 ASCII 主机、允许清单后缀匹配且点边界防欺骗）；normalize_allowlist（拒绝通配/裸 TLD/非 ASCII 条目）；resolve_and_pin（解析全部 A/AAAA，任一非公网/链路本地/CGNAT/保留/6to4 → 整体拒绝，混合答案 fail-closed）；PinnedNetworkBackend（连接只允许命中已校验 IP，绝不再解析 DNS；httpcore 用原 hostname 做 TLS/SNI 与证书校验，钉定 IP 不削弱 TLS）；PinnedTransport（stdlib 默认 SSL 上下文，trust_env=False，follow_redirects=False）。
  - backend/app/web_research/public_reader.py：PublicPageReader 逐 URL 结果（ok/failed/skipped_redirect/skipped_content_type，body 恒为全文提取、永不为摘要）；仅接受 text/html、application/xhtml+xml、text/plain、application/pdf（跳过时不读正文）；重定向不自动跟随、只上报新目标待用户重选（新目标须重过全部规则，凭据/Cookie 永不附加）；流式硬上限 25 MiB 下载 / 50 MiB 解压 / 60 秒总时间（clock 可注入）；PDF 校验 %PDF 魔数并按大小约束接收（文本解析随批次4 附件解析器）；HTML 文本提取用 stdlib html.parser（script/style 剥离、子资源从不请求）；单 URL 失败不拖垮整批。
  - tavily.py +extract：POST /extract + Bearer，extract_depth=basic 钉死防费用升级；单次 ≤5 URL（按成功页面计费的可见上限）；逐 URL 成败归一（dict/string 两种 failed 形态），raw_content 缺失记失败绝不编造全文；service.extract_pages 发送前轻校验（HTTPS、无 userinfo、拒私网字面量——付费调用不花在无意义目标上）。
  - service/catalog：test_capability("tavily","extract") 真实探针（/extract 端点）；catalog tavily extract → available；未知能力仍 422。
  - tests/unit/test_url_safety.py 39 例 + tests/unit/test_public_reader.py 21 例（全部离线：MockTransport/假 resolver/spy backend）。
  - 批次1 两处断言随实现更新：extract 能力 planned→available、能力测试改断言 /extract 路由。

- 批次3（completed 2026-09-01）Tushare 结构化补充与冲突（计划 04 §2.4）：
  - backend/app/market_data/tushare_client.py：POST https://api.tushare.pro（Token 在官方请求体，仅 HTTPS 不降级）；只请求必要 fields；分层校验（HTTP → 业务 code → fields/items 对齐，行长度不符整体拒绝）；权限/积分文案 → permission_denied、频控文案 → rate_limited（业务层限流同样走 ≤2 重试），参数拒绝不重试；可选 budget_spend 钩子（预算耗尽在发起请求前拒绝，接线批次接入工具层扣减）。归一化：null≠0≠负值（空字段为缺口候选）、PERCENT 记法转比率、同报告期修订版本保留最新披露并计折叠数、report_type 决定合并/母公司与累计/单季口径（缺失记 unknown）；disclosure_date 计划/实际日期分立为不同规范字段，计划永不当作已披露。
  - identity.py：ts_code_from_thscode 显式映射点（后缀域 SH/SZ/BJ 校验，不盲拼接）+ verify_stock_basic_mapping（官方行自证映射）；field_dictionary.py：SupplementFieldMap + 8 个 API 的 Tushare→内部规范字段映射（canonical 与 hexin 字典对齐，报表类型/修订信息保留为文本字段）。
  - source_conflicts.py：可比性键（entity/period/currency/unit/合并口径/累计口径/定义/披露版本）不一致 → parallel 并列解释不合并；完全可比且主源缺失 → fill 补足并保留替代来源；可比但取值不同 → conflict 并列显示（禁止平均/择大/静默覆盖）；一致 → consistent；resolve_by_choice 用户选择只影响本次新报告（纯函数不改历史输入）。valuation_percentile：分位标签只描述实际窗口（span<10 年绝不称"十年分位"），强制记录指标/窗口/有效交易日数/缺失比例/负值处理/公式。
  - service.py：tushare 凭据源与客户端接线、fetch_tushare_supplement 通用补充取数（缓存 TTL 按能力前缀、evidence provider=tushare）、test_capability 按 provider 路由 7 个真实探针（上游 confirm_external 把关）；api_config/service.py 传递 provider；normalization.py 暴露 unit_for 公共别名。catalog：tushare → available（7 项能力）。
  - tests/unit/test_tushare_client.py 24 例 + test_source_conflicts.py 17 例（全部离线）。修复批次3 引入的回归：_fetch 改用 profile_for 后 hexin 别名缺失导致缓存键 credential_version 恒 0（evidence_flow 集成测试捕获，已修复并回归）。

- 批次4（completed 2026-09-01）本地资料解析与上传（计划 04 §2.5）：
  - backend/app/research/attachment_parsers.py：类型判定 = 扩展名白名单 + MIME 白名单双检 → 文件头嗅探（MZ/PK/%PDF/NUL/HTML），绝不按扩展名信任内容；文本型 PDF（pypdf strict=False，逐页页码标记，加密 → password_protected，扫描件如实 no_text_layer 不 OCR，>200 页 limit_exceeded）；TXT/Markdown 仅 UTF-8（GBK → decode_error）；CSV/TSV 结构化 stats{rows,columns}（每表 10000 行上限）；XLSX openpyxl read_only+data_only（ZIP 目录名先判宏 vbaProject.bin 拒绝、外部链接警告忽略；公式只取缓存值并固定警告"未重新计算"；每表 10000 行/每簿 100000 单元格上限）；解析文本 200000 字符截断；单文件 60 秒协作式时间预算（注入时钟，页/行/表边界检查）；13 个固定结构化错误 code + 中文文案 + 固定补救清单 → attachment_gap() DataGap 候选（dimension=local_attachment，reason=unsupported/timeout）。
  - backend/app/research/attachments.py：AttachmentStore 留存策略 reference_only（默认，原始字节解析后即弃，仅元数据+SHA-256）/ excerpt（解析文本摘录 ≤20000 字符存 data/attachments/<32-hex>.excerpt.txt），full 不提供；raw_file_retained 恒 False；32-hex 防遍历（is_attachment_id，磁盘路径仅由 ID 构成，显示名拒绝路径分隔符/..）；限额单文件 25 MiB / ≤5 个 / 合计 100 MiB（too_large/total_too_large）；失败文件结构化隔离不拖垮其他文件；解析文本入既有 user_provided 补充存储（source_note 标注来源文件与解析方式，content ≤8000 既有契约）；保存 ≠ 外发，零再分析触发。
  - backend/app/api/supplements.py：POST /api/supplements/files（multipart）；security.py 仅对该路径放行受限 multipart（spool 落盘 + 实际流字节计数 + Settings.attachment_request_cap_bytes=105 MiB 超限 413），其余写路由 multipart 仍 415、全局 JSON/32 KiB 与会话/来源校验不动；report_id 32-hex 校验、未知报告 404、策略/数量违规 422，部分失败 200 分列 saved/failed/supplements/gaps；批次5 在同一文件补 GET/PUT/DELETE 与 source-preview。
  - 存储迁移 v6→v7（research_attachments 元数据表）；settings.create_directories 增加 data/attachments；依赖 pypdf/openpyxl/defusedxml/python-multipart/pywin32 已在前锁定 requirements*.txt 并装 .venv（本批零新增，pip check 干净）。
  - tests/unit/test_attachment_parsers.py 35 例 + tests/integration/test_attachment_limits.py 12 例（全离线：手工最小 PDF/内存工作簿/zip 部件/注入时钟；越权 403、multipart 限定路由 415、数量/单文件/合计/请求体上限、伪扩展与扫描件隔离 + DataGap 候选、防遍历零写入、零外发零再分析零事件）。
  - 偏差：①解析仍在 Web 进程内（协作式超时+上限），子进程隔离（attachment_worker.py + pywin32 Job Object）随批次5 落地（注：实际于批次6 兑现，批次5 未及），未声称同等安全；②frontend SupplementPanel/SourceConflict 归批次5（与父批次定义一致）；③docx 明确 unsupported（无已装解析库，不引入重型新依赖）；④full 留存策略需明确许可依据，首版不提供。回归更新：schema 终版断言 6→7（3 处，迁移保留性断言不变）。

- 批次5（completed 2026-09-01）补充接口与再分析接线（计划 04 §2.6）：
  - backend/app/api/supplements.py 补齐：GET /api/supplements/attachments?report_id（清单，恢复报告页资料面板状态）、GET /api/supplements/{id}（预览：元数据 + 摘录文本 + 关联补充引用状态；摘录路径防御校验——必须仍在附件目录内且为 <id>.excerpt.txt，异常库行一律拒读）、PUT /api/supplements/{id}/mapping（确认候选字段映射：security_id ^6位.(SH|SZ|BJ)$/期间/单位/备注，至少一项，extra=forbid；确认只保存，不触发任何计算或调用）、DELETE /api/supplements/{id}（未引用 → 删附件行 + 摘录文件 + 派生补充；已引用 → 409 request_conflict，保留旧版可复核性）。
  - backend/app/api/research.py POST /api/research/tasks/{id}/source-preview：纯本地预览——四条数据流向（hexin_structured / model_llm / web_search=任务授权快照 / local_attachment）+ 预算与用量回显 + 补充/附件计数 + notice"只在本地计算，未发起任何外部请求、模型调用或预算扣减"；零网络零模型（no_network transport 下直接可测）。
  - backend/app/research/attachments.py：preview/confirm_mapping/list_for_report/delete（引用判定经 supplement.referenced_by_report_id）；AttachmentRecord 增 mapping/mapping_confirmed_at；迁移 v7→v8（mapping_json/mapping_confirmed_at 列）。
  - backend/app/agent/research_graph.py：补充材料行附加来源标注（fields.source_note → "（来源：…）"），附件来源与解析方式随材料进入提示词，不改写内容本身。
  - 前端：frontend/src/api/supplements.ts（契约镜像 + uploadSupplementFiles 走新增 client.requestMultipart——浏览器自设 multipart boundary、会话头/错误归一与 request 同规）；components/SupplementPanel（隐私红线文案、文件选择+留存单选、解析结果与失败补救展示、候选映射确认表单、删除引用保护）已挂载 Research 报告卡补充资料区，onSupplementsChanged 按 id 合并回页面再分析勾选清单；components/SourceConflict（来源并列完整展示、不平均/不静默取舍、选择仅用于本次新报告）为组件级交付。
  - 测试：tests/integration/test_supplement_reanalysis.py 5 例（零模型 + 断网 transport：保存→映射确认→预览→主动再分析→两条补充引用标记→子版本 parent_report_id→旧版报告逐字节不变→usage.llm_requests/fetches 全零；映射/预览校验矩阵；删除引用保护与未引用清理含摘录文件删除断言；source-preview 本地性；材料行来源标注）；frontend/src/test/supplements.test.tsx 9 例（隐私文案、multipart 契约与 reference_only 默认、excerpt 切换、映射确认、删除引用保护/成功/拒绝展示、SourceConflict 并列与单次回调）；research.test.tsx 补 api/supplements mock。
  - 回归更新：schema 终版断言 7→8（3 处，迁移保留性断言不变）；research_flow 再分析回归通过。

- 批次6（completed 2026-09-01）收尾·附件解析子进程隔离（计划 04 §2.5 遗留偏差落地）：
  - backend/app/research/attachment_worker.py（新）：每份文件一次 python -m 短生命周期子进程，stdin 收 JSON（base64 文件字节 + limits），stdout 回一行 JSON（字节级 UTF-8——Windows 管道默认 cp936 会破坏中文 JSON，已绕过）；pywin32 Job Object 内存硬限制 512 MiB（JOB_OBJECT_LIMIT_PROCESS_MEMORY，pywin32 扩展限制为嵌套 dict：LimitFlags 在 BasicLimitInformation 内——踩坑已修正），pywin32 缺失时降级普通子进程并保留进程隔离；环境变量按白名单重建（SYSTEMROOT/PATH/TEMP 等，密钥/代理一律不继承）+ 子进程启动二次 scrub 疑似密钥变量；父进程硬性超时 = parse_time_budget_seconds + 5s，超时 TerminateJobObject 杀整树 → time_budget_exceeded；子进程崩溃/输出非法 → parse_failed（结构化，绝不带敏感上下文）。
  - backend/app/settings.py + attachment_parse_subprocess: bool = True（生产默认隔离；False 仅限测试显式降级）；backend/app/research/attachments.py AttachmentStore 增 parse_subprocess 注入与 _parse_isolated 分支，_process_entry 按开关分流（in-process 路径仅测试可达）。子进程 cwd/PYTHONPATH 用 worker 模块自知的代码库根，与运行时 project_root（测试临时目录）解耦。
  - tests/unit/test_attachment_worker.py 9 例：settings 默认值、环境白名单（三个注入密钥变量一个不剩）、worker main 协议往返（中文不乱码）、不支持类型结构化错误、真实子进程端到端解析、--print-env 双重验证子进程环境零密钥、Job Object 内存硬限制杀内存贪吃进程（Windows 实测 1.5s 内终止）、硬性超时整树终止、超时→time_budget_exceeded 映射。全离线。
  - tests/integration/test_attachment_limits.py 12 例全部改走真实子进程路径（5.3s），无断言改动——批次4 契约在隔离边界下原样成立。
  - SourceConflict 页面级接线维持组件级交付（待冲突逐来源数值视图契约，见批次5 边界）；真实搜索/提取/财务测试记录需用户 Key 与计费确认，收尾清单已写入 evidence（不执行、不伪造）。

- 批次7（completed 2026-09-01）映射候选值计算半环（计划 04 §2.5 "候选需用户确认"的前半环，批次5 边界遗留项）：
  - backend/app/research/mapping_candidates.py（新）：确定性规则提取（零模型/零网络/零新依赖）——标的仅认显式交易所后缀（600519.SH / sh600519，规范化大写；裸 6 位数字宁缺毋滥不猜后缀）；期间 2024Q1/2024年第一季度/一季报/中报/年报 → 2024Qn/H1/FY（年/第 粒子可选，兼容"2024年报"单"年"写法）；单位 人民币/CNY/USD/HKD；万元/亿元 → 备注"金额以万元计"（只提示不改写数值）；每类取首个命中，无命中即 None。
  - 存储：迁移 v8→v9（research_attachments +mapping_candidates_json）；AttachmentRecord +mapping_candidates（仅预填表单，确认仍必须经 PUT mapping——候选 ≠ 确认）。
  - 前端：SupplementPanel 映射表单以候选值预填（draft 未触碰时回显候选；点击「确认映射」合并草稿提交——显式清空的项不提交；渲染/预填绝不自动提交）。
  - 测试：tests/unit/test_mapping_candidates.py 9 例（典型财报文本全字段、前后缀/大小写规范化、期间变体矩阵、裸数字拒绝、首命中确定性、币种变体、空文本、extra=forbid）+ test_supplement_reanalysis.py 增 1 例（候选入库存档/预览回显/mapping 仍空待确认）+ supplements.test.tsx 增 2 例（预填零自动提交、改写/清空按草稿提交）；schema 终版断言 8→9（3 处）。

## 验证（截至批次7）
- 全量：.venv/Scripts/python.exe -m pytest tests -m "not requires_api and not requires_llm" -q → 批次1 后 **212 passed**，批次2 后 **272 passed**，批次3 后 **313 passed**，批次4 后 **360 passed**，批次5 后 **365 passed**，批次6 后 **374 passed**，批次7 后 **384 passed**（197 基线 + 187 新增）。
- 前端：npx vitest run → **38 passed**（6 文件）；npx tsc -b 干净；npx eslint（改动文件）零告警。
- pip check：No broken requirements found（批次7 后复验）。
- 既有 tests/unit/test_api_config.py 12 例在 test() 路由改造后回归通过。
- 前端无需改动：Settings/ApiProviders.tsx 目录驱动（/api/integrations catalog），bocha/tavily 卡片自动随 catalog 出现。

## 下一步
- 仅剩用户依赖项（工程侧无未竟工作）：①真实搜索/提取/财务测试记录——需用户配置 HITHINK_FINANCE_API_KEY（计费）与/或 LLM_API_KEY(+LLM_BASE_URL/LLM_MODEL)，显式开启 run_live_api_tests/run_live_llm_tests 后运行 pytest -m "requires_api or requires_llm"，结果如实记入 90-evidence.md；②SourceConflict 页面接线——等产品决策冲突逐来源数值的视图契约后再开工。
