# Evidence · 2026-08-31-plan-stage01

阶段 01「统一API配置与模型输出上限」完成证据（离线验收）。

## 命令与结果
- 后端：`.\.venv\Scripts\python.exe -m pytest tests -m 'not requires_api and not requires_llm' -q` → **80 passed**（1 条 starlette 弃用警告，与本次改动无关）。
- 前端（frontend/）：
  - `npm run build`（tsc -b + vite）→ 成功（仅 chunk 体积提示，非错误）。
  - `npm run lint`（--max-warnings 0）→ 0 错误。
  - `npm run test`（vitest）→ **3 个文件 16 个用例全部通过**（pages 10 + api-settings 4 + 其余 2 文件既有用例）。

## 验收清单 §4 逐项状态
1. 无 Key 新安装进入同一设置页，所有服务显示真实实现状态；无隐藏环境回退 — **达成**（ApiProviders「计划接入」标签 + not_implemented 501；ModelService.resolve 无 env 回退；api-settings 测试 + test_model_config 覆盖）。
2. 仅 api_profiles 密文时禁止重初始化；模型与 API 共用初始化 — **达成**（local_secrets 双表检查 CREDENTIAL_TABLES；test_credentials_flow 4 例）。
3. 迁移保留原密文/活动模型/选项；默认最大输出有说明、自定义上限未知有状态 — **达成**（storage v4 + legacy_max_tokens + effective_output.reason；ProfileEditor 迁移说明；test_official_providers 迁移用例）。
4. 每个模型实际 HTTP 参数校验；Qwen 不混发两种输出参数、不叠加思考上限 — **达成**（completion_options 单一预算入口 + 互斥 422；offline_payload wire 断言）。
5. 新增 test_api_config.py（10）+ test_credentials_flow.py（4），覆盖空 Key 编辑、跨 origin 更换、清除/禁用、失效测试状态、环境显式导入 — **达成**。
6. test_model_factory.py：超旧字符上限、超 120s 逻辑时间（7s 生成 vs 5s probe 时限）、跨块工具片段、结束状态 — **达成**（全部离线 mock，无真实等待）。
7. frontend/src/test/api-settings.test.tsx：所有入口、费用提示、最大值联动、状态、密钥不回显 — **达成**（4 用例）。
8. 真实 Key 的最大参数 + 短回复单次验证 — **LLM 模型部分未执行**：本轮用户仅提供同花顺 Key；GLM 模型配置已存在于本机库中，是否花少量额度做一次最大参数短回复验证待用户确认。

## 2026-08-31 真实 Key 联调补充（用户提供同花顺 Key）

### 直接探针（不经过适配器，单次最小请求）
- 端点：`GET https://fuyao.aicubes.cn/api/meta/tickers/search?q=600519&limit=1`，头 `X-api-key`。
- 第一次用 `query=` 参数返回业务码 1001「Missing required parameter: q」——**确认参数名为 `q`**（旧调研记录未写明参数名，阶段 02 实现按 `q` 传参）。
- 第二次：HTTP 200 / code 0 / success，返回 600519.SH 贵州茅台（a-share，CNY），request_id 04e91778f778416a9a412af479d93155，顶层 timestamp 非空（与旧探针记录一致：快照可能带时间戳，解析器需兼容空/非空）。
- 结论：Key 对 meta 标的搜索能力有效；其余能力（财务/估值/基金）未实测，保持「未验证」标注，不额外消耗额度。

### 阶段 01 凭据链路端到端（本地，不外发）
- 用户 8000 端口旧进程占用 → 用 `FINSIGHT_PORT=8010` 起新代码后端验证（同一真实 DB）。
- storage v2→v3→v4 迁移自动执行：`user_version=4`；既有 GLM 模型配置完好（provider glm，output_policy=maximum，legacy_max_tokens=32768）；用户旧后端（8000）迁移后仍 200 正常。
- POST /api/integrations/profiles 保存真实 Key → HTTP 201，返回 `key_status=configured`、`masked_api_key=••••••••`、`credential_version=1`、`status=not_implemented`。
- POST /profiles/{id}/test → HTTP 501 `not_implemented`「本次未发起外部请求」——诚实行为确认，planned 适配器不伪装连接成功。
- 静态检查：finsight.db 原始字节中不含 `sk-fuyao` 明文；`api_profiles.encrypted_api_key` 为 140 字节 Fernet 密文。
- 验证后 8010 实例已停止；Key 已加密留存于正式库，阶段 02 可直接使用。

## needs-verification（人工）
- LLM 最大参数短回复验证：**GLM 已完成（2026-08-31）**——scripts/verify_llm_live.py 用库中已保存的真实 Key，经阶段 01 完整链路（documented limits → completion_options → ModelFactory.chat，total=False 生成时限）发送 max_tokens=131072（目录最大值）+ 思考开启 + 短提示；上游接受参数，finish=complete，回复「1+1等于2。」，无截断无巨量输出。providers.py 中 glm-5.3-flash 已置 live_output_verified=true（glm-5.3 未实测保持 false；deepseek/qwen 待用户提供各自 Key 后用同一脚本验证）。回归：后端 80 passed，前端 16 passed。
- 同花顺其余能力（财务/估值/基金/搜索）未实测，保持「未验证」标注；阶段 02 适配器接入时按需小范围联调。
- uploads 目录中的同花顺 Key 明文文件，用户选择自行处理（暂保留）。
## 关键变更文件
- 后端：app/credentials.py（新）、app/api_config/{catalog,models,repository,service}.py（新）、app/api/api_config.py（新）、app/storage.py（v3/v4）、app/model_config/{limits.py（新）,models.py,service.py,providers.py,local_secrets.py}、app/agent/{provider_requests.py,model_factory.py}、app/api/{model_config.py,system.py}、app/main.py、app/dependencies.py。
- 前端：src/api/{eventStream.ts（新）,apiConfig.ts（新）,models.ts,client.ts,system.ts}、src/pages/Settings/{index.tsx,ApiProviders.tsx（新）,ApiProviderEditor.tsx（新）,ProfileEditor.tsx,ModelPlayground.tsx}、src/components/Layout/index.tsx。
- 测试：tests/unit/test_api_config.py（新）、tests/integration/test_credentials_flow.py（新）、tests/unit/{test_official_providers.py,test_model_factory.py,test_model_config.py}、frontend/src/test/{api-settings.test.tsx（新）,pages.test.tsx,provider-catalog.json}。
- 依赖：eventsource-parser 4.1.0（--save-exact 锁定）。

