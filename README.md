# FinSight · 金融洞察

FinSight 是基于 AI Agent 与同花顺 Financial-API 的本地只读金融研究软件，目标是让问题、数据、计算与结论之间有可检查的证据。

## 1. 当前进度

截至 2026-09-02，新计划阶段 01–08 的本地实现已经完成：本机加密凭据、同花顺取数与证据库、确定性指标、受限 AI 研究任务、用户资料补充与主动再分析，以及公司、同行、投资理由、板块、基金、市场复盘、研究资产库与本地导出均可用。即使用户随后删除全部 Key，应用仍可启动、阅读此前已保存的内容、维护关注/理由、查看版本并导出允许保存的报告；这不表示已配置的数据源不会使用用户自己的 Key。

阶段 05–08 均坚持“显式刷新、离线可读、缺口可见”：公司/同行页只读已保存证据；板块和基金的本地计算不擅自联网或填补不可比期间；报告库标签/笔记/版本与删除独立于不可变报告正文。报告可下载为面向阅读的 Markdown；另有仅下载的核验材料 Markdown，包含脱敏后的已保存上游响应与搜索线索，但不含 Key、数据库、原始附件或隐藏推理。本轮**未制作安装包、压缩包、发布目录，也未开放公网服务**。真实外部能力的验证状态以设置页的单项测试和本地验收记录为准，未验证能力不会标记为可用。

开发路线见 [金融研究功能计划索引](docs/金融研究功能开发计划/00_计划索引.md)；上游边界与补充方案见 [同花顺能力](docs/金融研究功能开发计划/调研资料/同花顺能力与功能边界.md)、[补充数据源选型](docs/金融研究功能开发计划/调研资料/补充数据源调研与选型.md)。原计划阶段 01 仅保留基础设施实现记录。

日常操作与验证见[功能使用与验证说明](docs/功能使用与验证说明.md)；本机 BYOK、外部数据和公网部署边界见[API 与数据使用边界](docs/API与数据使用边界.md)。

## 2. 安装与启动

项目位置 `E:/python_project/FinSight`。已在项目独立 `.venv` 安装依赖；实际验证环境为 Python 3.13.10、Node 24.15.0、npm 11.12.1。旧项目不是运行依赖。

重新安装时，在项目内创建 Python 3.13 的 `.venv`，再执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip check
cd frontend
npm ci
```

**日常使用：双击项目根目录的 `启动FinSight.bat`。** 它会关闭确认属于本项目的旧后端 / Vite 进程，启动服务，通过后端与前端代理健康检查后，自动使用系统默认浏览器打开 Web 界面。前置依赖需已安装，不会在启动时自动安装软件。

服务在后台运行，关闭 BAT 窗口或浏览器不会停止服务；再次双击 BAT 即可重启。默认访问 `http://127.0.0.1:5173`，端口读取项目 `.env` / 当前环境变量。不会修改 `.env`；前后端强制使用同一组端口，仅绑定本机回环地址。

启动日志保存在 `logs/backend.stdout.log`、`logs/backend.stderr.log`、`logs/frontend.stdout.log`、`logs/frontend.stderr.log`，每次启动覆盖。端口被其他程序占用时会报错，**不会按端口或 python/node 进程名无差别杀进程**。无法确认归属的手动启动方式须自行关闭。启动失败会清理本次已拉起的服务，BAT 保留错误提示；重复双击时有启动锁避免相互干扰。浏览器关联异常时服务保留运行，可按提示手动打开网址。

实现入口为 `scripts/start_all.ps1`；验证时可运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_all.ps1 -NoBrowser`，执行相同重启流程但不打开浏览器。这里的执行策略只作用于本次 PowerShell 进程，不修改系统策略。

需要分别调试前后端时，也可在两个 PowerShell 终端、项目根执行：

```powershell
.\scripts\start_backend.ps1
```

```powershell
.\scripts\start_frontend.ps1
```

打开 [FinSight 本机应用](http://127.0.0.1:5173)，后端 [健康检查](http://127.0.0.1:8000/health) 不发起外部请求。两个脚本在前台运行，各自 Ctrl+C 停止，不会终止其他项目进程。后端入口是 `.venv/Scripts/python.exe -m backend.app`，单 worker。

项目 `.env` 可设置 `FINSIGHT_PORT` 和 `FINSIGHT_FRONTEND_PORT`，前后端均需重启。只支持回环地址；端口被占用时直接报错，不自动换端口或结束其他进程。没有复制旧依赖、数据库或真实凭据。

## 3. 模型与本机密钥

打开模型设置，选择 DeepSeek、GLM 或 Qwen 官方客户端，填入 API Key 即可保存。默认模型分别为 `deepseek-v4-flash-vision-exp`、`glm-5.3-flash`、`qwen3.8-flash`，已按 2026-08-31 官方文档核验。地址、名称、输出预算与超时均有预置，保留自定义兼容接口。

页面按模型能力显示思考模式、推理强度、图像/视频/音频输入许可，以及 Qwen 思考预算、视频 FPS、地域和业务空间。GLM-5.3 系列只能开启思考；DeepSeek 思考模式不会发送 Temperature。详细映射和来源见[官方模型接入说明](docs/模型服务商接入说明.md)。

首次点击保存时，可安全初始化本机 `.env` 加密根密钥并立即生效，无需命令或重启。普通启动不生成根密钥；已有加密记录时禁止生成替代密钥。CLI `python -m scripts.init_local_secrets` 仍可用，但必须通过项目 `.venv/Scripts/python.exe` 执行，之后重启后端。

请私下备份 `.env`。已有凭据无法解密时须恢复原 `FINSIGHT_ENCRYPTION_KEY`。本次开发只在隔离测试目录生成无效夹具密钥，没有初始化实际项目密钥或导入真实凭据。

最多保存 12 个配置，同时最多一个活动配置。Key 只写不读；编辑留空保留，显式清除才删除。更换官方服务商、Qwen 地域/业务空间或自定义地址时须重新填写或清除 Key。SQLite v1 配置会无损迁移为 custom，保留密文和参数。

模型试用区在用户确认后发送一次文本或多模态请求，只显示最终文本，不存储对话。当前素材输入为单个公开 HTTPS URL，由服务商读取；不提供本地文件上传、语音生成或实时通话。聊天、工具协议测试均可能计费，GLM 不调用未经核验的模型列表端点。

手动确认后即可调用，无需环境开关。未确认的自动验证仍默认禁用，须显式设置 `FINSIGHT_RUN_LIVE_LLM_TESTS=true`。失败不自动重试、跟随重定向或切换模型；真实配额与权限由服务商校验。

没有任何数据库配置时，仍可通过后端 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY` 三个环境变量配置自定义兼容接口。已有未激活或锁定配置时不会回退。普通启动、健康检查和离线测试不访问外部服务。

## 4. 验证

```powershell
.\.venv\Scripts\python.exe -m pip check
.\scripts\test_backend.ps1
cd frontend
npm run build
npm run lint
npm run test
```

后端脚本使用项目 `.venv`，临时库位于 `.cache/pytest/<随机目录>/`，不读取正式库或真实凭据，也不依赖系统临时目录权限。一般环境也可使用 `.venv/Scripts/python.exe -m pytest tests -m "not requires_api and not requires_llm" -q`。

阶段 05–08 的离线验收覆盖公司体检/同行可比性、理由确认、板块共同窗口、基金披露期与净值口径、市场事件去重、版本/标签/导出/删除、坏输入恢复与 BYOK 空配置边界。真实模型、搜索、Tushare、同花顺基金/财务等外部能力仍须由用户配置自己的 Key 后，按账户权限和本次范围分别确认；离线测试不把模拟结果写成真实联调。

已知非阻塞提醒：当前锁定 Starlette 对 httpx TestClient 提示未来弃用；Vite 主入口约 625 kB，设置页已延迟加载，仍有包体积提醒。未为消除提示引入第二套 HTTP 客户端或放宽构建检查。

## 5. 主要文件

| 位置 | 用途 |
| --- | --- |
| backend/app/main.py、settings.py | 应用生命周期、路径与环境配置 |
| backend/app/security.py、errors.py | 本机会话、来源限制和安全错误 |
| backend/app/storage.py | SQLite 连接与单一 schema 迁移入口 |
| backend/app/model_config/ | 模型契约、加密、存储与服务 |
| backend/app/agent/model_factory.py | 异步兼容模型客户端和手动能力测试 |
| frontend/src/pages/ | 首页与模型设置 |
| frontend/src/api/ | 类型明确的 API 与内存会话 |
| 启动FinSight.bat、scripts/start_all.ps1 | 识别并关闭旧服务、后台启动、健康检查与默认浏览器打开 |
| scripts/ | 分别启动服务、离线测试、主动密钥初始化 |
| tests/、frontend/src/test/ | 私有离线测试与无效测试夹具 |
| data/db/finsight.db | 本机模型配置库 |
| data/evidence/、data/exports/ | 本地证据与用户显式导出的报告目录 |
| requirements.lock.txt、frontend/package-lock.json | 本次安装锁定依赖 |

## 6. 范围与安全

只做研究分析，不提供订单、账户、模拟交易、回测、选股信号或仓位分配。后续最新快照也不承诺无延迟实时行情。

当前模型和数据源 Key 均通过“模型与 API 配置”写入本机加密库；不能进入浏览器持久存储、VITE 变量、URL、Prompt、日志和报告。环境变量只能通过设置页的显式导入操作进入本地配置，后续删除/禁用配置不会秘密回退到开发者 Key。不得搜索或导入历史凭据文件。

本机访问采用 Host 白名单、Origin 检查、内存会话令牌、JSON 写操作和请求大小限制，页面禁止被其他站点嵌入。后端重启会轮换会话，前端只在服务明确拒绝旧会话时安全重试，不自动重放超时写操作。日志只输出本地请求编号，不打印 SDK 异常正文。

这不是多用户认证机制，也不防御已控制本机的恶意进程。不要将端口通过隧道或代理公开，不上传 `.env`、业务数据库或证据文件。

实现参考：[Starlette 中间件](https://www.starlette.io/middleware/)、[Vite 服务选项](https://vite.dev/config/server-options)、[OpenAI 工具调用契约](https://developers.openai.com/api/docs/guides/function-calling)。
