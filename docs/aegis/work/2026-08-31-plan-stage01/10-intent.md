# TaskIntentDraft · 2026-08-31-plan-stage01

## Slice Card
- Goal: 执行新父计划（docs/金融研究功能开发计划）阶段01「统一API配置与模型输出上限」。用户指令"执行金融洞察软件开发计划"按旧索引的取代声明落到本阶段。
- Parent plan/spec: docs/金融研究功能开发计划/00_计划索引.md + 01_统一API配置与模型输出上限.md
- Files: backend/app/{credentials.py,api_config/*,api/api_config.py,storage.py,settings.py,agent/*,main.py,api/model_config.py}、frontend/src/{pages/Settings/*,api/*,components/Layout/index.tsx}、tests/*、frontend/src/test/*
- Boundary: 不动数据库既有表结构数据；schema 只由 Storage.initialize() 顺序迁移（v3、v4）；不做 02+ 阶段功能；不执行 Git 操作；不改一键启动脚本行为。
- Verification: scripts/test_backend.ps1（pytest 全量）、frontend npm run build/lint/test；新增测试见批次5。
- Stop: 全部验收项通过（done）；或外部依赖（如真实 Key 联调）缺失时如实标注 needs-verification，不以离线通过冒充真实接入。

## Requested outcome
所有接入服务有用户自己的凭据入口；模型默认使用实际最大输出；长回复不被当前本地限制提前切断；无隐藏环境回退。

## Non-goals
同花顺适配器实现（02）、Tushare/博查/Tavily 实现（04）、研究任务异步路由（03）。

## Risk hints
- DeepSeek 393216 整数边界未经真实调用核验：目录标注"文档值/未实测"，验收仅要求可选的真实单次验证。
- eventsource-parser 需新增 npm 依赖并锁定版本。

## BaselineUsageDraft
- required refs: 父计划 00/01、调研《模型输出上限与JSON解析依据》、backend/app 全部前置阅读文件（已读）、frontend/src 相关文件（已读）、现有 tests（已读）。
- acknowledged: 全部已读。cited: 上述路径。missing: 无。

## Execution Readiness View
- intent lock: 阶段01 范围 = 2.1–2.5 + 禁止事项 + 验收清单。
- scope fence: 仅列于计划"新增"清单的文件 + 必要的现有文件修改；不新增第二套前端常量表、不建通用插件框架。
- baseline lock: storage user_version 当前为 2，只顺序增加；api_profiles 为新表；model_profiles 密文不搬迁。
- owner/contract constraints: 数据/证据契约属 02；本阶段输出参数解析唯一入口在 agent/provider_requests.py；模型表不搬入通用配置表。
- compatibility boundary: /api/models/* 保持兼容；max_tokens 旧字段保留读写一轮；非流式 chat 端点保留。
- retirement boundary: 移除 ModelService.resolve() 的隐藏环境回退（旧 test_environment_only_when_no_saved_profiles 按新契约更新）；262144 字符上限不对报告沿用（本阶段 playground/流式即弃用）。
- test obligations: 计划 §4 验收清单全部条目。
- review gates: 每批次结束跑对应验证；全量验证在批次5。
- drift/rewind rules: 偏离计划文件边界时回计划评审；不静默加回退。
- evidence required before completion: pytest 全绿、vitest/build/lint 全绿、验收清单逐项对照。

## ImpactStatementDraft
- 受影响行为：设置页信息架构、resolve 语义、模型请求参数、流式能力；向后兼容 /api/models 契约。
