"""运行时配置.

所有配置从环境变量读取,默认值面向本地教学场景:
- MCP 服务默认只监听 ``127.0.0.1``;
- 上游 Go REST API 默认地址 ``http://127.0.0.1:8080``;
- 不实现认证 / 权限 / CORS / Origin 校验。
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_str(key: str, default: str) -> str:
    value = os.environ.get(key)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip(), 10)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """服务运行配置(只读快照)。"""

    # MCP 服务监听地址与端口(MCP_HOST / MCP_PORT)
    host: str = "127.0.0.1"
    port: int = 8000

    # 上游 Go REST API 基址(PET_HOSPITAL_BASE_URL)
    pet_hospital_base_url: str = "http://127.0.0.1:8080"

    # HTTP 调用上游的超时(秒)与重试次数
    backend_timeout_seconds: float = 5.0
    backend_max_retries: int = 2
    # 重试之间的退避基数(秒);实际退避 = backoff_base * (2 ** attempt)
    backend_retry_backoff_base: float = 0.2

    # MCP 端点路径(2026-07-28 规范允许服务器自选)
    mcp_endpoint_path: str = "/mcp"
    health_endpoint_path: str = "/health"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=_env_str("MCP_HOST", "127.0.0.1"),
            port=_env_int("MCP_PORT", 8000),
            pet_hospital_base_url=_env_str(
                "PET_HOSPITAL_BASE_URL", "http://127.0.0.1:8080"
            ).rstrip("/"),
            backend_timeout_seconds=_env_float("MCP_BACKEND_TIMEOUT", 5.0),
            backend_max_retries=_env_int("MCP_BACKEND_MAX_RETRIES", 2),
            backend_retry_backoff_base=_env_float("MCP_BACKEND_RETRY_BACKOFF", 0.2),
        )
