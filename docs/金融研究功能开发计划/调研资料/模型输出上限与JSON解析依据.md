# 模型输出上限与JSON解析依据

## 1. 调研结论与当前差距

核验时间：2026-08-31。本次阅读官方在线文档并检查已安装库，没有使用真实模型 Key。下文把官方事实、计划设计、必须通过的参数验收分别记录。

实际代码 `ProfileInput.max_tokens` 上限 32768，官方默认 8192；`timeout` 最大 120，`ModelFactory.request_scope()` 还有同值 asyncio 总超时；`complete()` 限制 262144 字符。前端 `ProfileEditor.tsx` 重复 32768 上限，`api/client.ts` 为 130000ms。仅增大 max_tokens 不足以交付长回复。

## 2. 官方输出与参数矩阵

| 模型 | 输出上限证据 | 协议解释和采用结论 |
| --- | --- | --- |
| deepseek-v4-flash-vision-exp / v4-flash / v4-pro | 官方模型页列 384K，上下文 1M | Chat Completion 使用 max_tokens；本轮页面没有给出 384K 对应的精确整数约束。计划初始候选为 393216（按 1024 换算），必须以官方参数约束或该端点对候选整数的短回复验收确认；如不接受，采用返回的明确合法最大值并更新目录，不能把候选当作已实测事实 |
| glm-5.3-flash / glm-5.3 | 模型页 128K；Chat API 明确 `1 <= max_tokens <= 131072` | maximum 采用 131072；思考不可关闭 |
| qwen3.8-flash / qwen3.8-max | 模型页输出 131072、思维链 262144、上下文 1000000 | 兼容接口 max_completion_tokens 同时包含思考与正文，其最大值按文档为模型最大输出；采用 131072，不能将两个上限相加 |
| qwen3.5-omni-plus | 模型页输出 65536、上下文 262144、输入 196608 | 保留现有流式文本协议，不因此新增音频生成；采用该协议支持的 max_tokens，验证文本模式参数 |

来源：[DeepSeek 模型规格](https://api-docs.deepseek.com/quick_start/pricing/)、[Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)、[GLM Flash](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash)、[GLM Chat API](https://docs.bigmodel.cn/api-reference/模型-api/对话补全)、[Qwen Flash](https://help.aliyun.com/zh/model-studio/qwen3-8-flash)、[Qwen Max](https://help.aliyun.com/zh/model-studio/qwen3-8-max)、[Qwen Omni Plus](https://help.aliyun.com/zh/model-studio/qwen3-5-omni-plus)。

Qwen 兼容接口说明总输出参数包含 reasoning 与 answer，而模型页同时列出更大的思维链最大长度；这是不同参数/模式约束，不能自行推断可同时达到。新配置选择最大总输出，单独设置有界思考预算保留正文空间。参数不兼容时展示错误和文档来源，由用户决定模式变化，不自动切换另一模型、关闭思考或降低输出。[Qwen Chat 参数](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)

## 3. 原生结构化输出与工具协议

| 服务 | 本次公开支持 | FinSight 决策 |
| --- | --- | --- |
| DeepSeek | json_object；提示词必须要求 JSON；仍可能 length 或空内容 | 常规端点用 JSON Object + 本地 schema，首版不依赖 beta strict |
| GLM | json_object；本地 schema 示例 | JSON Object + 本地 schema，不误认为 JSON Object 保证业务字段 |
| Qwen3.8 | JSON Object 与 JSON Schema | 报告优先 JSON Schema；按地域/模式验证，回退须记录且仅允许格式策略变化 |
| 自定义兼容接口 | 能力未知 | 用户手动探针验证；不支持工具也允许使用固定只读取数模板后进行文本分析 |

来源：[DeepSeek JSON](https://api-docs.deepseek.com/guides/json_mode/)、[GLM 结构化输出](https://docs.bigmodel.cn/cn/guide/capabilities/struct-output)、[Qwen 结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)。工具调用和最终报告可用不同格式策略，但不能复制客户端。

DeepSeek 携带 tools 的后续请求要求回传历史 reasoning_content，缺失可导致 400；现有 complete() 明确丢弃该字段，不能直接拿它构造业务工具循环。将供应商回合封装为后端私有消息，仅保留必要的内存协议数据，展示另一份脱敏事件对象。[DeepSeek 思考和工具回合](https://api-docs.deepseek.com/guides/thinking_mode/)

## 4. 本地解析库与采用决定

本机已有 Pydantic 2.13.3 / pydantic-core 2.46.3 / OpenAI SDK 2.32.0；已通过运行时签名核实 `pydantic_core.from_json(..., allow_partial=...)` 可用。采用标准库 json 做完整解析、重复键/NaN 拒绝，以及 Pydantic schema 和语义校验；局部 JSON 解析只服务草稿预览，不能证明报告完整。

[Pydantic JSON 文档](https://docs.pydantic.dev/latest/concepts/json/) 说明部分解析可以忽略未完成尾部，因此不得用 `allow_partial=True` 的成功结果把截断报告标记为完整。

调研了维护中的 [json_repair](https://github.com/mangiucugna/json_repair)。它能处理常见非标准 JSON，但也可能补齐缺失结构或值，单凭修复成功不能证明金融事实未变化。首版不将其作为自动业务写入或工具执行的依赖；先用无语义改变的清理与一次同模型格式重试。若后续引入，仅作候选解析，新增锁定依赖、修复差异与数值/引用不变验收，不能放宽本计划安全规则。

## 5. 最大输出不等于永不截断

设置最大值只是放宽本次生成的服务端预算，不保证正文获得全部 token，也不能消除断网、上下文超限、过滤或用户取消。产品采用：输入压缩与分节、完整结束状态校验、已完成章节保存、明确截断标记和用户可选择的续写/重分析。取消后不自动重放收费请求；外部模型原始推理不持久化。

长输出验收使用构造流覆盖 UTF-8 分块、多工具参数、空 delta、usage-only chunk、断流、length、缺少结束标记、超过旧字符上限。真实测试仅验证最大参数被接受和一次短回复，不能用低 token 探针宣称最大输出边界已经验证。
