"""命令行入口:``python -m pet_hospital_mcp`` 或 ``pet-hospital-mcp``。"""

from __future__ import annotations

import uvicorn

from .config import Settings
from .server import create_app


def main() -> None:
    settings = Settings.from_env()
    app = create_app(settings)
    # uvicorn 直接消费 Starlette app;host/port 由 settings 决定
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
        # 不打印 uvicorn 默认的 access log,改由 structlog 输出
        access_log=False,
    )


if __name__ == "__main__":
    main()
