import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_root: Path = PROJECT_ROOT
    host: Literal["127.0.0.1", "localhost"] = "127.0.0.1"
    port: int = Field(default=8000, ge=1024, le=65535)
    frontend_port: int = Field(default=5173, ge=1024, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    finance_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    encryption_key: SecretStr = SecretStr("")
    run_live_api_tests: bool = False
    run_live_llm_tests: bool = False
    # 附件上传（阶段04 批次4）：multipart 请求体外层硬上限（合计 100 MiB 产品上限
    # 之外再留 multipart 编码开销）；中间件按实际流字节计数，不信任 Content-Length。
    attachment_request_cap_bytes: int = Field(default=105 * 1024 * 1024, ge=1024)
    # 全市场 Parquet 是本机研究原始数据，允许与下载通道相同的 2 GiB 上限；
    # 仅白名单导入路由可使用，其他写操作仍保留 32 KiB JSON 限制。
    market_import_cap_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=1024)
    # 附件解析在独立短生命周期子进程中执行（批次6）：Job Object 内存硬限制 +
    # 环境白名单（密钥不继承）+ 硬性超时杀树。生产默认必须开启；False 仅限测试。
    attachment_parse_subprocess: bool = True

    @property
    def db_path(self) -> Path:
        return self.project_root / "data" / "db" / "finsight.db"

    @property
    def exports_path(self) -> Path:
        return self.project_root / "data" / "exports"

    @property
    def market_warehouse_path(self) -> Path:
        return self.project_root / "data" / "market" / "finsight_market.duckdb"

    @property
    def allowed_origins(self) -> list[str]:
        return [
            f"http://{host}:{port}"
            for host in ("127.0.0.1", "localhost")
            for port in {self.port, self.frontend_port}
        ]

    def create_directories(self) -> None:
        for path in ("data/db", "data/evidence", "data/exports", "data/attachments", "data/market", "logs"):
            (self.project_root / path).mkdir(parents=True, exist_ok=True)


def load_settings(project_root: Path = PROJECT_ROOT) -> Settings:
    # No process-wide environment mutation; tests can use entirely isolated roots.
    values = {**dotenv_values(project_root / ".env", interpolate=False), **os.environ}
    mapping = {
        "host": "FINSIGHT_HOST", "port": "FINSIGHT_PORT",
        "frontend_port": "FINSIGHT_FRONTEND_PORT", "log_level": "FINSIGHT_LOG_LEVEL",
        "finance_api_key": "HITHINK_FINANCE_API_KEY", "llm_base_url": "LLM_BASE_URL",
        "llm_api_key": "LLM_API_KEY", "llm_model": "LLM_MODEL",
        "encryption_key": "FINSIGHT_ENCRYPTION_KEY",
        "run_live_api_tests": "FINSIGHT_RUN_LIVE_API_TESTS",
        "run_live_llm_tests": "FINSIGHT_RUN_LIVE_LLM_TESTS",
    }
    return Settings(project_root=project_root, **{
        field: values[key] for field, key in mapping.items() if values.get(key)
    })
