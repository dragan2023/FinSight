# TodoCheckpointDraft · 2026-08-31-plan-stage01

## 批次1（completed 2026-08-31）
- Slice Card: 共享凭据根 + api_profiles 后端；边界 = credentials/api_config/api 路由/storage v3/local_secrets/service.resolve；验证 = pytest 全量。
- Evidence: tests 66 passed（含新增 tests/unit/test_api_config.py 10 例、tests/integration/test_credentials_flow.py 4 例）。
- 契约落点：
  - CredentialRoot 单实例根密钥（model_profiles + api_profiles 共同参与初始化检查/锁定）。
  - storage user_version 2→3（api_profiles、api_capability_tests）。
  - /api/integrations/*：providers（含 environment 探测布尔）、profiles CRUD + credential_version 乐观锁、test 一律 not_implemented(501) 不外发、import-environment（finance/llm，只回 ID 与脱敏状态）。
  - ModelService.resolve() 移除隐藏环境回退；旧 test_environment_only_when_no_saved_profiles 已按新契约改写。
  - local_secrets.initialize 双表密文检查。
- Drift check: 范围内、无新增回退、兼容边界未破坏（/api/models 契约保留），decision=continue。

## 批次2（completed 2026-08-31）
- 模型能力目录与输出迁移：providers 目录加 context/max_output/output_param/live_output_verified/limits_source；ProfileInput.output_policy（maximum/custom）+ legacy_max_tokens；storage v4（output_policy 回填 + legacy_max_tokens 保留）。
- model_config/limits.py 新增：output_budget（输入长度估算 + 思考预算冲突 + 上限未知 422）、documented_limits、maximum_budget、view_budget（含 requested）。
- agent/provider_requests.completion_options：单一输出预算入口，结果附 \`_output_budget\`；probe 非思考压至 256，思考按预算；qwen reasoning_effort/thinking_budget 互斥（config_conflict）。
- 契约测试：test_official_providers 扩展（deepseek max_tokens 393216 / glm max_tokens 131072 / qwen max_completion_tokens 131072；omni 流式 65536；思考预算 114689→422；v3→v4 迁移；custom maximum 必须先声明上限）。

## 批次3（completed 2026-08-31）
- 长输出流式传输：model_factory 重写（连接 10s/读空闲 300s、total=False 无总时限、transfer_byte_cap= max(4MiB, 输出预算×64+1MiB)、ChunkMerger 按索引合并/截断标记/外发字节上限）。
- /api/models/chat/stream 与 /{id}/chat/stream（SSE：meta/delta/reasoning/done/error；reasoning 只发字数，正文永不离开后端内存）。
- 前端 eventsource-parser@4.1.0（--save-exact）+ api/eventStream.ts + streamChat；ModelPlayground 流式渲染、停止生成、abnormal/truncated 告警。
- test_model_factory 扩展：7s 生成 > 5s probe 总时限、4MiB 传输上限截断保留部分回复、SSE 文本/日志无 reasoning 正文、finish 异常→abnormal、probe 锁释放、ChunkMerger call-id 单测。后端累计 80 passed。

## 批次4（completed 2026-08-31）
- 统一设置页：Layout 侧栏/面包屑改「模型与 API 配置」；Settings/index.tsx 改 Tabs（模型服务/金融数据/联网搜索/数据使用与本地存储），模型页含 LLM 环境导入横幅。
- 新增 ApiProviders.tsx（服务卡：用途/文档/申请入口/凭据状态/能力/阶段标记；planned→“计划接入”禁测试；finance 环境导入按钮；版本冲突刷新）与 ApiProviderEditor.tsx（密钥写-only、可选清除、enabled 开关）。
- ProfileEditor：输出策略单选（最大输出/自定义）、官方显示能力 max_output + live_output_verified 说明、custom maximum 需声明上限、自定义上限 max 联动能力上限、思考预算 max= 上限−16384、legacy_max_tokens 迁移说明、timeout 改「测试超时（秒）」、费用提示。
- 前端 build/lint/test 全绿；pages.test 同步（按钮更名、budget 默认 32768、streamChat mock）。

## 批次5（completed 2026-08-31）
- 新增 frontend/src/test/api-settings.test.tsx 4 例：tab 入口与计划接入状态、已存 Key 不回显（\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022）、输出策略费用提示与最大值联动（393216 / 114688）、本地 tab 无 Key。provider-catalog.json 补能力上限字段。
- 终验：后端 pytest 80 passed；前端 vitest 16 passed、build ✓、lint ✓。

## 当前状态
- 代码全部落地，离线验收达成；仅剩计划 §4 可选项（真实 Key 的最大参数单次验证）未执行 —— 需要用户提供 Key，标记 needs-verification。
