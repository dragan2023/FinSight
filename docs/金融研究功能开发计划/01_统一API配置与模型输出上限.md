# 01 — 统一API配置与模型输出上限

## 1. 摘要与前置阅读

优先级 P0；预计 3–4 批；直接依赖已完成的基础工程；目标：所有接入服务有用户自己的凭据入口，模型默认使用实际最大输出，长回复不被当前本地限制提前切断。

前置阅读：`backend/app/model_config/models.py`、`providers.py`、`service.py`、`repository.py`、`encryption.py`、`local_secrets.py`，`backend/app/agent/model_factory.py`、`provider_requests.py`，`backend/app/storage.py`、`settings.py`、`main.py`、`api/model_config.py`、`api/system.py`，`frontend/src/pages/Settings/index.tsx`、`ProfileEditor.tsx`、`ModelPlayground.tsx`、`api/models.ts`、`api/client.ts`、`components/Layout/index.tsx`，以及本计划两份 API/模型调研。

## 2. 任务详情

### 2.1 扩展同一个配置入口

保留 `/settings` 路由，将侧栏、页标题和面包屑改为“模型与 API 配置”。在 `frontend/src/pages/Settings/index.tsx` 内使用标签页或清晰分区：模型服务、金融数据、联网搜索、数据使用与本地存储。不要另开隐藏数据设置页。

| 服务 | 配置内容 | 实际接入阶段 |
| --- | --- | --- |
| DeepSeek、GLM、Qwen、自定义兼容模型 | 原有 Key、模型和模态；输出策略、结构化能力与长请求设置 | 01 |
| 同花顺 Financial-API | 用户 Key、固定官方地址、各能力测试状态 | 02 |
| Tushare Pro | 用户 Token、HTTPS 地址、个人研究/许可状态、按能力测试 | 04 |
| 博查 Web Search | 用户 Key、搜索授权与条数预算 | 04 |
| Tavily Search / Extract | 同一用户 Key，搜索和正文提取分开启用与测试 | 04 |
| 本地附件和受限公开原文读取 | 无 Key，显示数据流向、存储/外发开关 | 04 |

这些是最终必须出现的已选服务；适配器未实现时显示“计划接入”，禁用调用和测试，不能伪装连接成功。完成相应阶段后激活功能。Brave、AKShare、BaoStock、巨潮商业接口等未选择直接接入的候选仅出现在调研文档，不做无效 Key 表单。

所有服务卡统一显示：服务商、用途、官方文档/申请入口、凭据状态、适配能力、授权说明、最后一次手动测试时间。状态至少区分未配置、已保存未验证、能力可用、权限不足、额度/限流、过期、锁定、尚未实现；“有 Key”与“已验证能力”分开。

### 2.2 复用加密并消除隐藏环境回退

新增 `backend/app/api_config/catalog.py`、`models.py`、`repository.py`、`service.py`，`backend/app/api/api_config.py`，`frontend/src/api/apiConfig.ts`、`pages/Settings/ApiProviderEditor.tsx`、`ApiProviders.tsx`。新增 SQLite `api_profiles` 表，包含 id、provider、name、encrypted_api_key、enabled、options_json、credential_version、created_at、updated_at；独立保存能力测试结果，绑定 credential_version，换 Key 后旧测试失效。模型表和既有密文不搬迁到通用配置表。

复用 `KeyEncryption`，新增 `backend/app/credentials.py` 作为很小的共享根密钥持有者及初始化检查入口。`ModelService` 和 `ApiConfigService` 持有同一个实例；初始化检查同时覆盖所有凭据表。更新 `model_config/local_secrets.py` 的 CLI 检查，避免仅检查 model_profiles 导致其他 API 密钥存在时错误生成新根密钥。不要在两个服务中各缓存一份可失效的加密对象；写操作复用一把根密钥初始化锁。

密钥只写不读；编辑留空保留、显式清除才删除；固定掩码不暴露前后缀。更换服务商、地域、业务空间或目标 origin 必须重新输入/清除 Key。删除或禁用后，下一个外部请求不得继续使用旧凭据；在途请求可能已计费，界面说明这一事实。

改造 `ModelService.resolve()` 与 `Settings` 中 finance/LLM 环境读取：业务调用不再在无配置时自动使用隐藏环境 Key。为了兼容本机历史配置，在此页面显示“检测到后端环境配置，可导入本机配置”；用户点击导入后由后端读取指定变量、加密保存并显示来源，不将明文返回浏览器，不搜索其他磁盘文件。未导入或已删除的配置没有环境回退。不得在发布包中附带开发者 `.env`、数据库或公共 Key。

### 2.3 本地 API 与测试语义

| 端点 | 行为 |
| --- | --- |
| GET `/api/integrations/providers` | 静态目录与实现状态；不主动联网 |
| GET/POST `/api/integrations/profiles` | 脱敏列表/加密创建 |
| PUT/DELETE `/api/integrations/profiles/{id}` | 编辑/删除，版本检查避免覆盖并发修改 |
| POST `/api/integrations/profiles/{id}/test` | 明确 capability 和 confirm_external，单次小范围测试 |
| POST `/api/integrations/import-environment` | 用户明确导入指定服务；结果只返回配置 ID 与脱敏状态 |

模型 `/api/models/*` 保持兼容，供应商测试按实现阶段注册。无 adapter 返回 `not_implemented`，不外发。一个服务某个端点成功不能标记其他能力全部成功。外部 API Key 均由各适配器加入其官方认证位置；Tushare Token 位于请求体，禁止记录该请求体。

### 2.4 每模型最大输出与旧配置迁移

扩展现有 `model_config/providers.py` 唯一模型能力目录，增加 context/input/output 上限、请求参数名、是否包括思考、JSON 模式、核验日期和来源链接，不另建第二份前端常量表。

| 已有模型 | 文档最大输出 | 计划请求参数 |
| --- | --- | --- |
| DeepSeek V4 Flash/Pro/Vision Exp | 384K | `max_tokens`，整数边界按调研说明验证 |
| GLM-5.3 / GLM-5.3 Flash | 131072 | `max_tokens` |
| Qwen3.8 Flash / Max | 131072 | `max_completion_tokens`，包含思考与正文 |
| Qwen3.5 Omni Plus | 65536 | 已核验文本流式协议的 `max_tokens` |

新增配置字段 `output_policy=maximum|custom`，默认 maximum。官方旧配置迁移为 maximum，保留原数值为 `legacy_max_tokens` 以供用户查看/恢复自定义；页面显著提示本次升级扩大默认上限，后续调用可能增加费用。自定义接口无法推断模型最大值，迁移保留其原值，显示“上限未知”；用户填写服务商明确的 context/max_output 后才能启用 maximum，不能硬套官方模型数值。

新请求不再使用 32768 全局上限。`max_tokens` 旧字段保留兼容读写一轮，读响应补充有效参数和值；实际唯一解析入口为 `agent/provider_requests.py` 的输出预算函数，前后端不能各自猜算。

有效输出上限取模型/协议最大值与剩余上下文允许值的较小者，但应首先压缩重复工具结果、按证据 ID 读取和分节组织输入，尽量保留完整最大输出空间。确需因上下文减少时，记录 requested/effective/reason 并在 UI 告知；禁止静默降到 8K/32K。输入上限与上下文上限不是同一数值，多模态输入、工具定义、需要回传的推理字段都计入。没有精确 tokenizer 时保守估计并标为估计，不能宣称保证不超限。

Qwen 的总输出 131072 与最大思考长度 262144 是不同约束，不能相加构造不存在的 393216 总输出。新配置思考预算默认 32768，最大值受总输出约束并保留至少 16384 正文预算；用户自定义时展示冲突并要求调整，不静默裁剪。已有合法思考预算保留。Qwen3.8 `reasoning_effort` 与 `thinking_budget` 互斥，如扩展强度选项须更新校验，不能同时发送。GLM 保持强制思考，DeepSeek 思考时省略 temperature。

### 2.5 长输出传输与协议完整性

修改 `ModelFactory` 的响应处理，分离“连接探针超时”和“生成过程超时”：连接 10 秒、读取空闲超时默认 300 秒；研究生成不继承旧的 120 秒总时限，默认无短总时限，用户可设置任务级时间预算并主动取消。使用流式接收保持进度；没有内容但持续有思考片段是有效活动，不按“无正文”误报停滞。

报告正文增量写入任务私有临时文件，结束后原子定稿；普通试用仍不持久化会话。传输字节上限根据模型输出预算计算，例如 `max(4 MiB, effective_output_tokens * 64 + 1 MiB)`，属于防异常响应的保守工程上限，不等于 token 估算。命中时明确标记异常、保留已接收结果，不能无提示删除或假装完整。不要对报告沿用 262144 字符上限。

参数型工具继续使用较小且明确的 JSON schema/字节限额，禁止将长报告作为工具参数规避限制。流式消息按 choice/tool index 合并并保留 call id；必须收到正常结束状态才执行工具。DeepSeek/GLM 工具回合所需的 reasoning_content 仅在后端内存回传对应服务，不进日志、页面、SQLite 或报告；进程重启后从证据重建新的分析，不伪造隐藏推理。

研究任务的异步路由在 03 实现；本阶段先把 ModelPlayground 的长生成改为流式 fetch，不能继续等待 Axios 130 秒请求。在 `api/model_config.py` 新增 POST `/api/models/chat/stream` 与 `/api/models/{profile_id}/chat/stream`，原非流式端点保留兼容；前端新增 `frontend/src/api/eventStream.ts`，使用成熟 [eventsource-parser](https://github.com/rexxars/eventsource-parser)，在本阶段核验并锁定版本，03 复用。取消/断开试用流会关闭上游生成，不自动重放。

共用后端流式读取器，不复制供应商解析逻辑；探针仍为显式小请求，只提示回复 OK，探针用量与正式研究参数分别展示，不以探针参数限制研究输出。普通试用没有报告任务的落盘状态，不能提前依赖 03 的 task 表。

## 3. 禁止事项与兼容性

不覆盖已有根密钥，不重建 v2 数据库，不读取旧凭据 TXT。不开启模型隐式联网搜索；未来启用时必须作为同页可见的外部能力另行配置。不要修改一键启动脚本来自动联网测试或灌入 Key。不以 maximum 保证永不截断：网络、服务商限制与用户取消依然需要 03 的恢复路径。

## 4. 验收标准

- [ ] 无 Key 新安装能进入同一设置页，看到所有已选服务的真实实现状态；无隐藏环境回退。
- [ ] 仅存在 api_profiles 密文、model_profiles 为空时，丢失根密钥也禁止重新初始化；模型与 API 共用初始化后立即可保存。
- [ ] 原配置密文、活动模型和选项保留；默认最大输出迁移有明显说明，自定义上限未知有明确状态。
- [ ] `tests/unit/test_official_providers.py` 验证每个模型实际 HTTP 请求参数，Qwen 不混发两种输出参数、不把思考上限相加。
- [ ] 新增 `tests/unit/test_api_config.py`、`tests/integration/test_credentials_flow.py`，覆盖空 Key 编辑、跨 origin 更换、清除/禁用、失效测试状态与环境显式导入。
- [ ] `tests/unit/test_model_factory.py` 增加超过旧字符上限、超过 120 秒逻辑时间、工具片段跨块和结束状态测试，不靠真实等待 120 秒。
- [ ] 新增 `frontend/src/test/api-settings.test.tsx`，覆盖所有入口、费用提示、最大值联动、状态与密钥不回显。
- [ ] 已选官方模型用用户自己的 Key 做“最大参数 + 短回复提示”的可选单次验证；未测模型显示未验证，不生成巨量文本消耗额度。
