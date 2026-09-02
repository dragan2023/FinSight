"""Explicit CLI entry point for the safe first-save initializer."""
from backend.app.model_config.local_secrets import initialize

if __name__ == "__main__":
    try:
        print(initialize())
    except (RuntimeError, OSError) as error:
        print(str(error) if isinstance(error, RuntimeError) else "写入本机 .env 失败，请检查文件权限。")
        raise SystemExit(1) from None
