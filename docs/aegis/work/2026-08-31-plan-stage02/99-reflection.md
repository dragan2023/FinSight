# Reflection · 2026-08-31-plan-stage02

## 做得好
- 先用最小探针 + 官方 llms-full.txt 钉准契约再写代码，避免了按旧调研资料假设参数名（q vs query、thscodes 复数、毫秒时间戳）返工。
- DataGap reason 在客户端错误类上一次成型（gap_reason 属性），service/API/前端零重映射。
- 域字段集拆分（快照 vs 历日线）来自真实联调的 partial 反馈，而不是猜——诚实状态原则又兑现了一次。

## 教训
- Windows CPython 不带 Olson 时区库，zoneinfo("Asia/Shanghai") 直接炸；venv 只隔离第三方包、标准库与基础解释器共享，报错栈出现基础解释器路径不代表没走 venv。最终按用户要求装 tzdata 2026.3 进 .venv（requirements.txt/lock 已声明），normalization 用 ZoneInfo；教训：『少加依赖』不应优先于项目惯例的标准解法，缺库先问补不补依赖，而不是绕过。
- JS 模板字面量里写嵌套反引号/\\${ 的转义仍然易错（EvidenceDrawer 一次、test 两次踩坑）。规则更新：凡是生成 TSX 的内容一律用 tools.write 整文件写入，模板串内避免嵌套模板。
- taskkill 按映像名差点波及用户的 8000 实例——以后只按 PID 杀自己启动的进程，动手前先 netstat 确认。

## 对后续阶段的建议
- 阶段 03（LLM 证据整合）直接消费 evidence_id：DataResult.records 已带 basis/口径字段，提示词注入无需重新取数。
- 缓存命中当前也写 evidence 行（from_cache 标记）；若阶段 03 认为证据粒度太碎，可改为命中时复用首次 evidence id，属小改动。
- 设置卡"财务"探针在真实账号可能触发一次性限流（4001→quota_limited 显示），属预期行为，UI 无需特殊处理。
