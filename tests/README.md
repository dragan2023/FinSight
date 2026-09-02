# FinSight 本地测试

## 1. 离线验证

项目根执行 `scripts/test_backend.ps1`。使用项目 `.venv`，临时数据库存于 `.cache/pytest/<随机目录>/`；不接触正式库和真实凭据。

- integration/test_app_lifecycle.py：无 Key 启动、关闭资源、持久化、Host/Origin/会话和安全错误。
- unit/test_model_config.py：加密、空白更新、显式清除、活动配置、地址变更与主动初始化。
- unit/test_model_factory.py：MockTransport 验证调用协议、测试开关、工具结构、超时、429 与错误脱敏。
- frontend/src/test/：Vitest 与 React Testing Library 验证表单和无数据页面。

测试中的 Key 均为无效离线夹具。本项目测试不依赖其他工作区。当前 Starlette 有 httpx TestClient 的弃用提醒，但锁定版本运行通过，没有引入第二套 HTTP 库。

## 2. 网络边界

当前没有外部请求测试。后续真实同花顺/模型测试须标记 requires_api/requires_llm，并分别检查显式环境开关，不能只靠选择 pytest 标记开启。生产模式不能以测试夹具回退。
