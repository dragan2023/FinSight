# Reflection · 2026-09-01-plan-stage03

## 做得好
- 契约先行：批次3/4 的集成测试把 SSE 帧、client_request_id 幂等、重启 interrupted 语义全部钉死在 pytest 里，批次5 前端直接照契约 mock 开发，一次接通零返工；前后端没有出现第二套契约（前端类型对齐 backend research/analysis 模型，DataGap 直接复用 marketData 视图，MetricValue 复用 02 的 analysis/models）。
- 红线落地方式可验证：json_parser 拒绝 eval/exec/YAML、工具白名单显式 schema、finish_reason=length 即便解析出合法 JSON 也判 incomplete、reasoning 只留内存字符计数、重启不自动再付费——每条红线都有对应测试断言，而不是只写在文档里。
- 缺口诚实呈现形成闭环：DataGap 只由程序产生（模型漏写不影响缺口面板）、前端 DataGaps 面板不折叠、指标 value=null 显示"缺数"、无模型时保留本地事实/计算/缺口并明确"未完成 AI 分析"——"数据缺失不是失败"从后端贯通到 UI。
- 批次4 集成测试反捉出真实缺陷（取数轮 reasoning 事件双写、SSE 字符计数翻倍），最小修复（去掉 notices.append，保留即时 _emit）后 197 例复跑稳定。
- 前端验证四件套（vitest/tsc/eslint/build）全绿收口：5 files / 27 tests passed，0 type error，0 lint problem。

## 教训
- 文档结构化调整依赖字符串 replace 改「下一步」段落，锚点没对上时 replace 静默失败，批次5 段落一度挂错在「下一步」之下；教训：对文档做结构性调整用行级重排，写回后立即重读验证，不依赖 replace 的隐式成功。
- JS 模板字面量嵌套反引号/转义在批次5 又踩两次（写页面与写测试各一次）；stage02 已有规则但执行不彻底。最终规则：生成 TSX 一律 tools.write 整文件写入，组件内避免嵌套模板串（改用字符串拼接）；正则里的 / 转义会被外层模板串吃掉，一律避免在模板串里写正则字面量。
- vitest 4 移除了 it(name, fn, timeout) 三参签名（tsc 报 TS2554 才发现），超时必须 it(name, { timeout }, fn)；antd v6 的 Space direction→orientation、Alert message→title 迁移告警在旧组件仍大量存在，新代码直接用新 API，不新增告警。
- antd Select 在懒加载页面里不是立即可查的：路由内容 findByText 就绪后仍需 findByRole("combobox") 等 identities 查询返回；测试等待要挂在数据依赖链上，不能只挂路由上。
- testing-library 精确断言优先：/未生成/ 这类宽泛正则会同时命中状态 Tag 与正文摘要（getByText 多匹配报错），标签类断言用整串精确匹配更稳。

## 待办（收尾唯一遗留）
- 真实模型联调记录（JSON/工具/长输出实际结果）：本环境无真实 DeepSeek/同花顺凭据，未执行真实供应商调用；全部通过均基于契约 mock，不将 Mock 通过说成真实供应商已通过。待用户提供凭据后按计划 §5.1 执行并补记到 90-evidence.md。

## 对后续阶段的建议
- 补充资料目前只有 POST 端点，页面按会话内记录展示"已保存，尚未再分析"；如需跨会话在 UI 复现历史补充，需后端补 GET /research/reports/{id}/supplements（小改动，未纳入本阶段计划，未擅自扩范围）。
- 前端 eventStream 管线已统一 GET/POST（openEventStream + readSseResponse）；后续新增流式端点直接复用，不要再复制 fetch+parser 逻辑。
- research_events 按 (task_id, seq) 无限增长：前端恢复靠快照帧，不依赖旧事件；若后续做任务历史列表，可考虑事件归档策略。
- 真实联调时重点关注三点：长输出触发 finish=length 的真实分布（truncated 路径）、工具循环"坏参数改正一次"是否足够、自定义模型降级固定取数模板的输出质量。
