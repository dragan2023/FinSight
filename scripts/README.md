# FinSight 脚本

## 1. 入口

| 脚本 | 用途 |
| --- | --- |
| start_backend.ps1 | 使用项目 .venv 前台启动后端，端口来自配置 |
| start_frontend.ps1 | 前台启动本项目 Vite，不自动安装依赖 |
| start_all.ps1 | 启动后端、前端并完成本机健康检查 |
| sync_full_market_history.py | 同步全市场历史日 K |
| sync_full_market_financials.py | 同步全市场财务数据 |
| sync_market_adjustment_factors.py | 同步公司行动与复权因子 |
| init_local_secrets.py | 用户主动初始化根密钥，不输出、不覆盖、不自动运行 |

密钥初始化从根目录使用 `.venv/Scripts/python.exe -m scripts.init_local_secrets`。它会写入本机 `.env`；请自行私下备份，已有加密记录时拒绝生成替代密钥。

## 2. 约束

所有 Python 调用使用项目虚拟环境。启动脚本根据自身位置定位项目，可从其他工作目录调用。不按端口或通用进程名称误杀其他项目，不修改其他项目。
