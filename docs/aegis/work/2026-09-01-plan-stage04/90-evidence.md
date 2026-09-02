# Evidence · 2026-09-01-plan-stage04

## 批次1 · web_research 检索域（2026-09-01）
- backend/app/web_research/{__init__,models,bocha,tavily,service}.py 新域落地（仅复用既有 httpx，零新依赖）：
  - 契约与红线兑现：搜索结果只是线索（storage_policy=reference_only，batch1 不落证据行）；缺失发布时间保持 unknown——博查 dateLastCrawled 明确不映射 published_at，不从抓取时间猜；博查只发已核验四参数，域名限制本地过滤；Tavily 固定 include_answer=false、auto_parameters=false、basic 深度，费用行为不可被服务端升级；count 可见上限（≤10）唯一事实来源在请求契约（>10 → 422）。
  - 分层校验与重试与 02 客户端同规：HTTP 状态 → 业务信封 → schema；瞬时失败（429/5xx/信封失败/超时/连接）重试 ≤2 且遵守 Retry-After（≤8s），认证与参数拒绝不重试；错误信息零上游原文、零凭据。
  - 共享本地策略在 service：域名后缀过滤、URL/(domain,title) 去重（转载判定无可靠信号时不强判，保守去重完全相同条目并保留原始发布者字段）；凭据按请求从 api_config 加密存储读取，禁用/未配置立即 409/409，绝不借用其他配置。
  - 设置页能力测试接线：api_config test() 按 category 路由到 web_service；能力级 implementation=planned（Tavily Extract）→ 501 且零外部请求；结果 {capability,status,message,tested_at,credential_version} 与市场服务同契约、可落库、绑定 credential_version。
- catalog：bocha → available；tavily → available（search available / extract planned）。前端 Settings/ApiProviders.tsx 目录驱动，自动展示，无需改动。
- tests/unit/test_search_sources.py 15 例（计划 §4 第 1 项搜索部分全达成）：固定官方 origin、正确认证、禁自动升级费用、fields/参数契约、401/429/持续 5xx、部分坏条目、域名过滤、去重、count 封顶、extract 拒绝、结果可落库。全部离线。
- 博查凭据导入：读取用户上传文件（只当凭据数据、不当命令源），提取纯 ASCII token 后经本地 API POST /api/integrations/profiles 存入既有加密存储；仅打印 token 长度（41 字符）；明文零落代码/文档/检查点/日志/测试；用户原上传文件保留未动。存储后 profile key_status=configured、状态 saved_unverified（等待用户确认计费的真实测试）。

## 批次2 · 公开原文读取与 URL 安全（2026-09-01）
- backend/app/web_research/url_safety.py + public_reader.py 落地，红线兑现：
  - 只允许 HTTPS:443 + 明确允许清单（默认 cninfo/sse/szse 官方域起点，用户补充的发行人域名走同一规则；拒绝通配与裸 TLD）；拒绝 userinfo、非 443、IP 字面量（公网也一样）、百分号/非 ASCII 混淆主机名。
  - 全部 A/AAAA 记录必须公网可路由（私网/环回/链路本地/CGNAT 100.64/10/benchmark 198.18/15/保留/6to4 → 整体拒绝，混合答案 fail-closed）；连接绑定已校验 IP（PinnedNetworkBackend 未钉定主机拒绝连接、绝不再解析 DNS），TLS/SNI/证书校验仍用原 hostname——"先查 DNS 再让客户端重解析"的 TOCTOU 被结构性消除（OWASP SSRF 指南对应项）。
  - 重定向不自动跟随（新目标上报待用户重选、须重过全部规则）；凭据/Cookie 从不附加；脚本不执行、图片/iframe/子资源从不请求（测试断言仅 1 次 GET）。
  - 流式上限 25 MiB 下载 / 50 MiB 解压 / 60 秒总时间；仅接受 HTML/纯文本/PDF（其余在读取正文前跳过）；PDF 校验魔数、文本解析随批次4；HTML 用 stdlib 提取（script/style 剥离）。
  - Tavily Extract：/extract + basic 钉死、单次 ≤5 URL、逐 URL 成败归一、raw_content 缺失记失败不编造；发送前拒私网字面量/非 HTTPS/userinfo。
- 测试：test_url_safety.py 39 例 + test_public_reader.py 21 例，全部离线，覆盖 §4 验收点（编码混淆、私网 IPv4/IPv6、DNS 变更/混答、重定向、恶意内容类型、解压/下载/时间超限、钉定传输、无凭据携带）。
- 边界：read_many 的 client_factory 可注入（生产默认构建钉定安全客户端，测试注入 Mock）；真实抓取/提取测试仍未执行（计费，需用户确认）。

## 批次3 · Tushare 结构化补充与冲突（2026-09-01）
- backend/app/market_data/tushare_client.py + source_conflicts.py 落地，红线兑现：
  - 仅 HTTPS 官方地址、Token 在官方请求体（适配器与测试都断言 Token 不进 header/日志/异常文本）；只请求选定标的/期间的必要 fields（请求体 fields 白名单字符校验）。
  - 分层校验：HTTP 状态 → 业务 code → fields/items 对齐（行长度不符整体拒绝，不猜）；权限/积分文案归 permission_denied、频控归 rate_limited（业务层限流走 ≤2 重试与 HTTP 429 同规）；未授权接口变具体 DataGap，不阻止其他来源（服务级测试断言 permission_denied 缺口 impact）。
  - 预算扣减：TushareClient 可选 budget_spend 钩子在每次尝试前扣减（耗尽 → budget_exhausted，零请求）；接线批次把工具层 _spend("fetches") 接到该钩子即可复用同一预算池。
  - 归一化：null≠0≠负值（空字段 → not_published 缺口候选）；fina_indicator 的 PERCENT 记法转比率；同一 end_date 多版本保留最新 f_ann_date/ann_date 并计 duplicate_versions；report_type → 合并/母公司 + 累计/单季口径（缺失记 unknown，绝不猜）；income/balancesheet/cashflow 的报表类型与修订信息按计划保留为记录字段。
  - 冲突规则（test_source_conflicts.py 17 例）：跨源比较前校验 entity/period/currency/unit/合并口径/累计口径/定义/披露版本——任一不同 → parallel 并列解释，不合并；完全可比且主源缺失 → fill 补足且替代来源单独保留；可比冲突 → 并列显示，禁止平均/择大/静默覆盖；用户选择只影响本次新报告（纯函数，历史输入不可变断言）。
  - 历史估值分位（valuation_percentile）：输出强制含指标/窗口/有效交易日数/缺失比例/负值处理/公式；短窗口标签为"近N个交易日历史分位"，绝不冒称十年分位；负值参与排名；样本不足 → unknown 并说明。
  - identity.py：ts_code 映射显式化（拒绝 SH/SZ/BJ 之外的后缀，不盲拼接 thscode）+ 官方 stock_basic 行自证核验。
- catalog：tushare → available，能力项 stock_basic/daily_basic/financials/indicators/audit/mainbz/disclosure，能力测试为真实探针（confirm_external 把关）。
- 回归修复：批次3 中 _fetch 改造后 hexin 提供方别名缺失，缓存键 credential_version 恒 0（换 Key 不再绕缓存）——tests/integration/test_evidence_flow.py::test_cache_isolated_by_credential_version 捕获，已修（profile_for 别名映射）并全量回归。
- 边界：真实 Tushare 调用未执行（需用户自己的 Token、积分与 confirm_external 确认）；积分/权限门槛以真实错误为准，不把任何积分数字当永久承诺。

## 批次4 · 本地资料解析与上传（2026-09-01）
- backend/app/research/attachment_parsers.py 落地（解析只在本机：零网络、零模型、零 OCR）：
  - 双检 + 内容嗅探：扩展名白名单（txt/md/markdown/csv/tsv/xlsx/pdf）+ MIME 按扩展名白名单双检（type_mismatch）→ 文件头嗅探（MZ 可执行 / PK 归档 / %PDF / NUL 二进制 / HTML 文本），绝不按扩展名信任内容；pdf/zip 内容与声明扩展名不符 → content_rejected；docx 明确 unsupported_type（无已装解析库，提示另存受支持格式，不为它引入重型新依赖）。
  - 文本型 PDF：pypdf strict=False 提取文本层，逐页"[第 n 页]"标记进解析结果；加密 → password_protected；扫描/空白页 → no_text_layer 如实报"无文本层，本应用不做 OCR"，不冒充文本；单页提取失败记警告跳过；>200 页 limit_exceeded。
  - CSV/TSV：stdlib csv，结构化 stats{rows,columns} + tab 渲染文本；行列不齐警告；每表 >10000 行 limit_exceeded；空文件/全空行 empty_file。
  - XLSX：openpyxl read_only + data_only + keep_vba=False；先按 ZIP 目录名判宏（vbaProject.bin → content_rejected"检测到宏工作簿"）与外部链接（警告"已忽略，未加载任何外部数据"），不执行任何部件；公式单元格只取文件内缓存值并固定警告"未重新计算"；每表 10000 行/每簿 100000 实际处理单元格上限；全空簿 empty_file。
  - TXT/Markdown：仅 UTF-8（utf-8-sig 容 BOM）；GBK 等 → decode_error 提示另存 UTF-8；解析文本统一 200000 字符上限截断（truncated + 警告）。
  - 时间预算：单文件 60 秒，deadline 由可注入时钟在页/行/表边界协作检查 → time_budget_exceeded（DataGap reason=timeout）。
  - 结构化错误族：13 个固定 code（PARSE_ERROR_CODES）+ 中文文案 + 固定补救清单（REMEDIES_BY_CODE）；attachment_gap() 转成 DataGap 候选（dimension=local_attachment、reason=unsupported/timeout、含 remedies），只随响应返回，不自动写入报告缺口清单。
- backend/app/research/attachments.py（AttachmentStore）红线兑现：
  - 留存策略：reference_only（默认；原始字节解析后即弃，仅库内元数据 + SHA-256 + 统计）| excerpt（另存解析文本摘录 ≤20000 字符至 data/attachments/<32-hex id>.excerpt.txt，供预览/再分析引用）；full 首版不提供（需明确许可依据，避免"摘录名义规避禁止存储"）；raw_file_retained 恒 False，原始文件从不落盘。
  - 32-hex 防遍历：is_attachment_id 校验（^32-hex$）+ 磁盘路径只由该 ID 构成；显示名仅展示字段，拒绝路径分隔符/".."/NUL/超长（invalid_filename），绝不参与任何文件系统路径。
  - 可见限额（AttachmentLimits 全部可在测试缩小）：单文件 25 MiB / 一次 ≤5 个 / 合计 100 MiB（按实际字节逐文件累计）→ too_large / total_too_large；失败文件结构化隔离，不拖垮其他文件，更不让任务失败。
  - 解析文本入既有 user_provided 补充存储（SupplementStore.add；fields 走既有白名单，source_note="附件「名」（kind/parse_method[, 已截断]）"；content 限 8000 沿用既有契约）。保存 ≠ 外发，全程零再分析触发。
- backend/app/api/supplements.py + 接线：
  - POST /api/supplements/files（multipart，python-multipart 已锁定依赖）；security.py 仅对该路径放行受限 multipart：请求体落盘 spool（内存 ≤1 MiB）+ 实际流字节计数（不信任 Content-Length）+ Settings.attachment_request_cap_bytes（默认 105 MiB = 100 MiB 合计上限 + multipart 开销）超限 413；其余写路由 multipart 仍 415，全局 JSON/32 KiB 限制原样保留；Host/Origin/会话校验不动。
  - 端点逐块读取 UploadFile（二次实际字节计数）；report_id 32-hex 校验（422）、报告不存在 404、留存策略/数量违规 422（全部 AppError 结构化中文错误）；部分失败仍 200（saved/failed/supplements/gaps 分列）。
  - 存储迁移 v6→v7：research_attachments 表（元数据 + warnings/stats JSON + supplement_id，FK 报告级联）；旧库/新库迁移均验证。settings.create_directories 增加 data/attachments。
- 依赖：pypdf 6.16.2 / openpyxl 3.1.5 / defusedxml 0.7.1 / python-multipart 0.0.32 / pywin32 312(win32) 已锁定于 requirements*.txt 并装入 .venv（pip check 干净，本批零新增依赖）。
- 测试（全离线）：tests/unit/test_attachment_parsers.py 35 例（手工最小 PDF 字节、openpyxl 内存工作簿、zip 部件注入、注入时钟超预算、魔数伪造/伪扩展/编码边界/全部限额）+ tests/integration/test_attachment_limits.py 12 例（MockTransport 对任何外发直接断言失败；会话越权 403、multipart 限定路由 415、未知/穿越报告 404/422、>5 文件 422、reference_only 零落盘、excerpt 仅 ID 命名摘录、失败隔离 + DataGap 候选、防遍历文件名零写入、单文件/合计/请求体上限、零再分析零事件零外发）。
- 回归更新：schema 终版断言 6→7（test_official_providers.py 2 处、test_research_graph.py 1 处）——迁移链合法延伸，迁移保留性断言本身未改。

## 批次5 · 补充接口与再分析接线（2026-09-01）
- backend/app/api/supplements.py §2.6 四路由补齐（同一文件承接批次4 上传）：
  - GET /api/supplements/attachments?report_id：报告附件清单 + 补充清单（只读，供报告页刷新后恢复面板）；GET /api/supplements/{id}：预览元数据 + 摘录文本 + 关联补充引用状态——摘录路径双重防御（必须位于 data/attachments 内且命名为 <id>.excerpt.txt，异常库行拒读），绝不提供原始文件（本就不留存）。
  - PUT /api/supplements/{id}/mapping：确认候选字段映射（security_id ^6位.(SH|SZ|BJ)$ / 期间 / 单位 / 备注，至少一项；MappingInput extra=forbid）。确认只保存（mapping_json/mapping_confirmed_at，迁移 v7→v8），不触发任何确定性计算或外部调用——对应 §2.5"用户确认后才进入确定性计算"的存储半环，计算侧消费随后续接线。
  - DELETE /api/supplements/{id}：未引用 → 删附件行 + 摘录文件 + 派生补充（原子于一次请求）；已引用（supplement.referenced_by_report_id 非空）→ 409 request_conflict"为保证旧版本可复核，不删除已引用资料"——按 §2.6 保留规则处理已引用资料。
  - 全部路由经 is_attachment_id 防遍历（32-hex），错误均为结构化 AppError 中文文案。
- backend/app/api/research.py POST /api/research/tasks/{id}/source-preview：纯本地预览四条数据流向（hexin_structured / model_llm / web_search=任务授权快照（默认 false "本任务未授权联网搜索"） / local_attachment=附件与补充计数）+ 预算与用量回显 + notice 明示"只在本地计算，未发起任何外部请求、模型调用或预算扣减"；不扣预算、不触来源——落实 §2.6"source-preview 只预览本地拟定的数据流与预算"。
- backend/app/agent/research_graph.py：用户补充材料行附来源标注（fields.source_note → "（来源：附件「名」（kind/parse_method））"），附件来源随材料进入提示词，让模型区分"用户自述"与官方证据；test_research_flow 全量回归通过。
- 前端（首版接线）：
  - frontend/src/api/supplements.ts：契约镜像（AttachmentView/UploadOutcomeView/SourcePreviewView 等，extra 对齐后端 forbid 语义）；uploadSupplementFiles 走 client.ts 新增 requestMultipart——不强制 Content-Type（浏览器自设 multipart boundary），会话头/错误归一与既有 request 完全同规。
  - components/SupplementPanel：隐私红线常驻（"只保存到本机数据库；原始文件默认不留存（仅解析文本入库）。保存 ≠ 同意外发，也不会自动再分析"）；文件选择 + 留存单选（仅记录/保留摘录）；解析结果与失败补救展示（DataGap remedies）；候选映射确认表单（标的/期间/单位）；删除按钮仅对未引用附件出现。已挂载 Research 报告卡补充资料区，onSupplementsChanged 把新解析补充按 id 合并回页面再分析勾选清单（保存 ≠ 自动再分析：勾选与再分析仍由用户显式操作）。
  - components/SourceConflict：来源逐条并列完整展示（不隐藏任何一方）、固定文案"并列显示，不平均、不静默取舍；选择只影响本次新报告，历史证据与旧版本不可变"、单次回调携带冲突 ID 与所选来源。
- 测试：tests/integration/test_supplement_reanalysis.py 5 例——零模型 + 断网 transport（无任何档案配置，任何外发/模型调用即显形）：保存文本补充与附件→映射确认→预览→主动再分析（引用两条补充）→usage.llm_requests=0 且 fetches=0→子版本 parent_report_id→旧版报告响应逐字节不变→补充 referenced_by_report_id=子版本；映射/预览校验矩阵（空映射/坏 security_id/未知字段/未知附件/非法 id/未知报告）；删除引用保护 409 与未引用清理（摘录文件同步删除）；source-preview 本地性与四流向内容；材料行来源标注。frontend/src/test/supplements.test.tsx 9 例（隐私文案、multipart 契约与 reference_only 默认、excerpt 切换、映射确认载荷、删除引用保护/成功/拒绝原因、SourceConflict 并列与单次回调）。
- 回归更新：schema 终版断言 7→8（3 处，迁移保留性断言不变）；research.test.tsx 补 api/supplements mock（面板挂载后既有断言零改动）。

## 命令与结果
- 全量：pytest -m "not requires_api and not requires_llm" -q → 批次1 后 **212 passed**；批次2 后 **272 passed**；批次3 后 **313 passed**；批次4 后 **360 passed**；批次5 后 **365 passed**；批次6 后 **374 passed**；批次7 后 **384 passed**（197 基线 + 187 新增）。
- 前端：npx vitest run → **38 passed**（6 文件，含 supplements.test.tsx 11 例）；npx tsc -b 干净；npx eslint（改动文件）零告警。
- pip check：No broken requirements found（批次6 后复验）。
- 阶段03 真实模型联调（本会话上半段完成并记入 stage03/90-evidence.md）：任务 completed、墙钟 6m33s、llm=18/24、真实同花顺取数 5 次、coverage=sufficient、integrity=unstructured（facts 节 repair_still_invalid → raw_sections 单独保存）、6 处 metric_conflict 如实标记、reasoning 事件仅 {type,chars}。

## 批次7 · 映射候选值计算半环（2026-09-01）
- backend/app/research/mapping_candidates.py（新）：兑现批次5 边界记录的"候选值生成半环"——确定性规则提取，零模型/零网络/零新依赖。口径宁缺毋滥（首版已实测纠正）：
  - 标的：仅接受显式交易所后缀（600519.SH / 000001.sz / sh600519 / bj835185，后缀规范化大写，边界防撞更长数字串）；裸 6 位数字一律不作候选（可能是日期/金额片段），不猜后缀。
  - 期间：2024Q1 / 2024年第一季度（第 可选）/ 2024年一季报 / 2024年中报（半年报/半年度）/ 2024年报（年度/全年）→ 规范化 2024Qn / 2024H1 / 2024FY；"年"粒子可选（实测纠正：原实现漏配"2024年报"单"年"写法）。
  - 单位：人民币/CNY/RMB → CNY；美元/USD；港元/港币/HKD。万元/亿元 → note 提示"金额以万元计"（只提示口径，绝不改写任何数值）。
  - 每类取首个命中（确定性），无命中即 None；MappingCandidates extra=forbid。
- 存储：迁移 v8→v9（research_attachments +mapping_candidates_json，DEFAULT '{}'）；AttachmentRecord +mapping_candidates；候选在解析入库时计算并存档（preview/清单/上传响应随记录自动回显），reference_only 行同样有候选（不依赖摘录留存）。
- 隐私/确认语义不变：候选仅预填确认表单——SupplementPanel 以候选预填输入框，draft 未触碰回显候选、显式清空的项不提交、点击「确认映射」才合并提交；渲染/预填绝不自动提交，确认仍必须经 PUT mapping，未经确认的候选绝不进入确定性计算。
- 测试：tests/unit/test_mapping_candidates.py 9 例 + test_supplement_reanalysis.py 增"候选确定性提取且候选≠确认"1 例 + supplements.test.tsx 增 2 例（预填零自动提交；改写/清空按草稿提交——vi.clearAllMocks 修复跨用例 mock 泄漏）+ schema 终版断言 8→9（3 处）。
- 全量回归：**384 passed**（批次6 后 374 + 10）；前端 vitest **38 passed**；tsc/eslint 干净；pip check 干净。

## 边界说明（批次1）
- 真实博查搜索测试未执行：属计费外部调用，需用户单独确认（confirm_external 闸门已就绪，测试路径 test_capability 全部实现）；不伪造在线结果。
- Tavily Extract 未实现（随批次2 公开原文读取），能力级 planned，测试入口 501 拒绝。
- 本地域名过滤采用后缀匹配（host == allowed 或 endswith "." + allowed），不做registrable domain 智能推断；URL 的完整 SSRF 防护（IP/私网/重定向/端口等）属批次2 url_safety/public_reader，检索层仅做 scheme/host/userinfo 基本校验。
- 查询构造（围绕确认标的/期间/缺口）属后续与研究图接线批次；批次1 的 service 接受显式 query。

## 边界说明（批次4）
- 子进程隔离未在本批实现（计划 §2.5 的"独立短生命周期子进程 + pywin32 Job Object 内存硬限制"）：当前解析在 Web 进程内执行，以协作式时间预算（60 秒，页/行/表边界检查）+ 大小/行数/单元格上限约束，入口仅 127.0.0.1 + 会话保护；不得声称与子进程隔离同等安全。attachment_worker.py（Job Object、清理继承密钥、超时杀树）原计划随批次5 接口接线落地，未兑现，已如实更正并列入收尾项（见「边界说明（批次5）」）。
- frontend SupplementPanel/SourceConflict 组件与 supplements.test.tsx 归批次5（与 §2.6 补充接口/再分析接线同批交付，契约已在 AttachmentUploadOutcome/AttachmentRecord 固定）。
- 扫描 PDF 的"截图 + 已配置视觉模型"分析与图片上传（PNG/JPEG）未接线：属 §2.5 后半（用户显式选择视觉模型）与再分析批次；当前扫描件只如实报 no_text_layer + 补救入口。
- GET/PUT/DELETE /api/supplements/*、source-preview 与 reanalyze 对附件补充的引用属批次5；本批路由文件已按该契约预留。
- 留存策略 full 未提供（无明确许可依据不默认永久存提取全文）；reference_only 下用户需重新上传才能复用原文。

## 批次6 · 收尾：附件解析子进程隔离（2026-09-01）
- backend/app/research/attachment_worker.py（新）兑现批次4/5 记录的安全边界，解析不再在 Web 进程内执行：
  - 独立短生命周期子进程：每份文件一次 "python -m backend.app.research.attachment_worker"，stdin 收 JSON（base64 文件字节 + AttachmentLimits 快照），stdout 回一行 JSON。输出走 sys.stdout.buffer 字节级 UTF-8——踩坑记录：Windows 管道默认 cp936，子进程 ensure_ascii=False 的中文 JSON 按 UTF-8 解码即失败，已绕过。
  - pywin32 Job Object 内存硬限制 512 MiB：JOB_OBJECT_LIMIT_PROCESS_MEMORY；踩坑记录：pywin32 的 JOBOBJECT_EXTENDED_LIMIT_INFORMATION 是嵌套 dict（LimitFlags 在 BasicLimitInformation 内），顶层塞 LimitFlags 会 TypeError 致 Set 静默失败——已修正并经实测（内存贪吃进程 1.5s 内被终止）。pywin32 不可用时降级为普通子进程（仍进程隔离）。
  - 清理继承密钥：父进程按白名单重建子进程环境（SYSTEMROOT/PATH/TEMP/PYTHONPATH 等，FINSAIGHT_*、Token、代理一律不继承）+ 子进程启动后二次 scrub 名字疑似 key/token/secret/password/credential 的变量；测试以 --print-env（只输出变量名）双端验证零残留。
  - 硬性超时：parse_time_budget_seconds + 5s 宽限，超时 TerminateJobObject 杀整棵进程树 → time_budget_exceeded；子进程崩溃/输出非法 → parse_failed（中文结构化文案，不带敏感上下文）。
- backend/app/settings.py：+attachment_parse_subprocess: bool = True（生产默认；False 仅限测试显式降级）；backend/app/research/attachments.py：AttachmentStore 增 parse_subprocess 注入与 _parse_isolated 分支；子进程 cwd/PYTHONPATH 用 worker 模块自知的代码库根（PROJECT_ROOT），与运行时 project_root（测试临时目录）解耦——避免 "python -m" 在临时目录找不到包。
- 测试：tests/unit/test_attachment_worker.py 9 例（全离线）：settings 默认隔离、环境白名单三个注入密钥变量零残留、worker main 进程内协议往返（中文不乱码）、不支持类型→unsupported_type、真实子进程端到端解析、--print-env 双端零密钥、Job Object 内存限制杀贪吃进程（win32 实测）、超时整树终止（TimeoutError）、超时→time_budget_exceeded 映射（monkeypatch 打在 attachments 命名空间——from-import 绑定处）。tests/integration/test_attachment_limits.py 12 例全部改走真实子进程路径，断言零改动（5.3s）。
- 全量回归：**374 passed**（批次5 后 365 + 9）；pip check 干净。解析安全声明随本批升级：解析在受限子进程内完成（内存硬限制 + 环境白名单 + 硬性超时杀树），不再声称"与子进程隔离不同等安全"。

## 边界说明（批次5）
- attachment_worker.py 子进程隔离仍未实现（批次4 记录的"随批次5 落地"未兑现，如实更正）：解析仍在 Web 进程内，依赖协作式时间预算与行/表边界检查兜底；升级为独立短生命周期子进程 + pywin32 Job Object 内存硬限制 + 清理继承密钥列入收尾项。落地前对外的安全声明保持批次4 口径（不得声称与子进程隔离同等安全）。
- SourceConflict 为组件级交付（测试覆盖并列展示/不平均/单次选择回调）；页面级接线待冲突逐来源数值的视图契约——当前 metric_conflict 仅以 DataGap（reason=source_conflict）与报告 claims 文本呈现，无结构化 {来源, 数值} 出口，不为接线而伪造数据结构。
- PUT mapping 的候选值计算半环（从摘录文本确定性提取 标的/期间/单位）未在本批实现：本批交付确认存储（mapping_json/mapping_confirmed_at，迁移 v7→v8）与防御校验；候选值生成及确定性计算消费随后续接线，UI 先由用户手填。
- 批次5 全部接口与测试在无模型档案 + 断网 transport 下验证（usage.llm_requests=0、fetches=0、旧版报告响应逐字节不变）；带模型档案的再分析完整路径属阶段03 已验证能力，未重复联调。
- GET /api/supplements/{id} 预览摘录文本时对库行路径做防御校验（必须位于附件目录内且命名为 <id>.excerpt.txt），异常行一律拒读——摘录文件本就不含原始文件内容。

## 边界说明（批次6）
- 批次5 记录的"attachment_worker.py 未实现"已于本批兑现（见批次6 主记录）；本批起安全声明升级为"解析在受限子进程内完成"。剩余边界：
- SourceConflict 仍为组件级交付：页面级接线待冲突逐来源数值的视图契约（当前 metric_conflict 仅以 DataGap reason=source_conflict 与 claims 文本呈现），等产品决策后再接线，不为接线伪造数据结构。
- PUT mapping 候选值计算半环（摘录→标的/期间/单位 确定性提取）仍未实现：本批交付的仍是确认存储+防御校验，UI 由用户手填。
- 真实外部测试（requires_api / requires_llm 标记）未执行，不伪造：需①用户自己的 HITHINK_FINANCE_API_KEY（计费）与/或 LLM_API_KEY+LLM_BASE_URL+LLM_MODEL；②显式开启 run_live_api_tests / run_live_llm_tests；③命令 .venv/Scripts/python.exe -m pytest tests -m "requires_api or requires_llm" -q；④结果（含失败）如实记入本 evidence。Key 明文零落代码/文档/日志。
- 全量 374 passed 为"not requires_api and not requires_llm"口径；真实联调属计费外部调用，不在本批执行。
