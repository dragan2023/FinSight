# Reflection · 2026-08-31-plan-stage01

## 计划依据与替代决策（重要）
- 请求最初指向旧计划 docs/金融洞察软件开发计划，其索引已声明 02–07 被新父计划 docs/金融研究功能开发计划 取代、01 为完成记录；故本次执行父计划阶段 01。该替代决策已在开工时确认并贯穿执行。
- 本阶段未使用 Git（计划明确禁止 Git 操作）；依赖仅新增 eventsource-parser@4.1.0。

## 做得好的
- 先固化共享凭据根 + 乐观锁 + 501 not_implemented 的后端契约，再叠 UI，前端没有出现“假装可用”的状态。
- 输出预算收敛到 provider_requests.completion_options 单一入口（附加 _output_budget），避免多处各算各的。
- 测试全部离线：SSE 用 bytes mock、长输出用 7s 生成 > 5s probe 时限与 4MiB 传输上限，不依赖真实等待或真实 Key。

## 过程中的坑（已解决）
- run_code 里 bash heredoc 写长文件会被截断（命令长度上限），导致两个 TSX 半截、tsc 报连锁错误；改用 write 工具（模板字面量需转义反引号与美元花括号占位符）。教训：长文件一律走 write/edit。
- api_config/repository.py 类方法 list 遮蔽内置名，注解求值报 TypeError —— 文件顶部 from __future__ import annotations 解决。
- jsdom/AntD 细节：Modal 动画期查不到内容需 findBy* 等待；requiredMark="optional" 会把 label 变成“API Key（可选）”，断言要用正则；AntD Tabs 非激活面板仍挂载，跨面板文本要 getAllByText。
- ProfileEditor 曾把模板字面量收尾写成双引号、并把 useWatch 放进条件表达式（违反 hooks 规则）——build/lint 全绿才放行。

## 遗留与后续
- 可选项：真实 Key 最大参数验证（needs-verification，见 90-evidence.md），完成后将对应模型 live_output_verified 置 true。
- 阶段 02 起按 catalog.implementation 从 planned 翻转为 available，逐个接入 hexin_finance 适配器；前端状态与测试按钮已就绪。
- 前端单 chunk >500KB 仅为提示；如需优化可对 Settings/Playground 再做动态切分（非本阶段范围）。
