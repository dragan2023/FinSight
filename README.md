# FinSight · 金融洞察

FinSight 是本机运行的 A 股研究与条件性决策辅助软件。它使用同花顺 Financial-API、本地全市场行情仓库和用户配置的大模型，输出可追溯的买入、持有、卖出或等待观点；它不连接券商、不自动下单，也不承诺收益。

项目只保留软件源码、运行依赖、正式行情数据和稳定使用文档。历史测试、回放、测试证据、旧日志和旧业务记录不属于项目交付内容。

## 1. 启动

项目位置：`E:/python_project/FinSight`。

日常使用时双击 `启动FinSight.bat`。程序会启动后端与前端、完成本机健康检查并打开浏览器。默认地址：

- 前端：`http://127.0.0.1:5173`
- 后端健康检查：`http://127.0.0.1:8000/health`

服务在后台运行；关闭浏览器不会停止服务。停止时双击 `停止FinSight.bat`，普通停止失败时再使用 `停止FinSight（管理员）.bat`。

重新安装依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip check
cd frontend
npm ci
```

Python 命令必须使用项目内 `.venv`，不要向系统 Python 安装依赖。

## 2. 人工真实使用

FinSight 不再配置任何影子测试或自动运行任务。日常使用由用户在交易日收盘后主动启动、刷新数据、选择候选标的、发起深度顾问分析并结算到期记录。完整操作顺序、固定提示词、入库位置和有效性判断标准见 [FinSight 人工真实使用与有效性验证方案](docs/FinSight人工真实使用与有效性验证方案/00_计划索引.md)。

## 3. 模型与数据配置

在“模型与 API 配置”页面选择并启用模型配置。Key 加密保存在本机数据库，`.env` 只保存本机加密根密钥；请私下备份 `.env`。

同花顺是正式行情、财务与估值数据源。本地全市场仓库位于 `data/market/`。全市场数据同步和页面刷新必须由用户主动触发，不会秘密联网或使用其他数据源补值。

模型最多保存 12 个配置，同时只激活一个。Key 只写不读；编辑时留空表示保留，显式清除才删除。已有密文无法解密时，必须恢复原 `FINSIGHT_ENCRYPTION_KEY`，不能生成替代密钥。

## 4. 软件目录

| 位置 | 用途 |
| --- | --- |
| `backend/app/` | 后端 API、数据、策略、顾问与存储逻辑 |
| `frontend/src/` | Web 用户界面 |
| `scripts/start_all.ps1` | 一键启动与健康检查 |
| `scripts/sync_full_market_history.py` | 全市场历史日 K 同步 |
| `scripts/sync_full_market_financials.py` | 全市场财务数据同步 |
| `data/db/finsight.db` | 本机加密配置与当前业务数据 |
| `data/market/` | 正式本地行情仓库 |
| `docs/` | 稳定使用说明、API 边界和策略知识 |

## 5. 安全边界

- 只绑定本机回环地址，不要通过公网隧道或反向代理暴露。
- 不把 Key 写入浏览器存储、URL、提示词、日志或报告。
- 不自动下单、不接管券商账户、不用回测结果承诺未来收益。
- 推荐必须同时给出依据、适用条件、风险和失效条件；数据不足时允许明确拒答。

进一步说明见 [FinSight 使用说明书](docs/FinSight使用说明书.md)、[API 与数据使用边界](docs/API与数据使用边界.md) 和 [模型服务商接入说明](docs/模型服务商接入说明.md)。
