import logging

import uvicorn
from pydantic import ValidationError

from .settings import load_settings


def main():
    try:
        settings = load_settings()
    except ValidationError:
        raise SystemExit("FinSight 配置无效，请检查本机 .env；未输出配置内容。") from None
    # Uvicorn's default error logger prints full exception chains. Keep only safe
    # lifecycle messages; application handlers emit their own request IDs.
    logging.getLogger("uvicorn.error").addFilter(lambda record: not record.exc_info)
    uvicorn.run("backend.app.main:create_app", factory=True, host=settings.host,
                port=settings.port, workers=1, log_level=settings.log_level.lower(), access_log=False)


if __name__ == "__main__":
    main()
