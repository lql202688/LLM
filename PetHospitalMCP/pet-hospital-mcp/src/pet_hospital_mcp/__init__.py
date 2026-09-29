"""宠物医院 MCP 服务.

一个无状态 Streamable HTTP 的 MCP 服务,将 Go 宠物医院 REST API 的能力
暴露给 AI Agent。基于官方 Python SDK 2.x 的 ``MCPServer``,遵循
MCP 2026-07-28 协议规范。

关键约束:
- 不导入 / 不使用 ``mcp.server.fastmcp.FastMCP``;
- 不实现旧协议的 ``initialize`` 握手、``Mcp-Session-Id``、会话存储或
  有状态 SSE 恢复;
- 默认监听 ``127.0.0.1``,上游地址由 ``PET_HOSPITAL_BASE_URL`` 配置。
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
