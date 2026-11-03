"""MCP Server 配置解析：从项目根目录的 .mcp.json 读取 MCP server 连接信息。

支持两种传输协议：
- streamable_http: Streamable HTTP 长连接，适用于远端 MCP server
- stdio: 通过子进程的 stdin/stdout 与本地 MCP server 通信

配置文件格式示例：
{
  "mcpServers": {
    "Bazi-MCP": {
      "type": "streamable_http",
      "url": "https://mcp.example.com/mcp"
    },
    "LocalServer": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "my_mcp_server"]
    }
  }
}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class StreamableHTTPConfig:
    """Streamable HTTP 传输配置。"""

    url: str


@dataclass
class StdioConfig:
    """stdio 传输配置。"""

    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None


@dataclass
class MCPServerConfig:
    """单个 MCP Server 的配置。"""

    name: str
    transport: str  # "streamable_http" | "stdio"
    http: StreamableHTTPConfig | None = None
    stdio: StdioConfig | None = None


def load_mcp_config(project_root: Path) -> list[MCPServerConfig]:
    """从项目根目录读取 .mcp.json，返回 MCP server 配置列表。

    配置文件不存在或为空时返回空列表（非错误）；
    单个 server 配置格式异常时跳过该 server 并记录错误提示（通过 warnings 输出），
    不影响其他 server 的加载。
    """
    config_path = project_root / ".mcp.json"
    if not config_path.is_file():
        return []

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[MCP] 读取 {config_path} 失败: {e}")
        return []

    servers = raw.get("mcpServers", {})
    if not isinstance(servers, dict):
        print(f"[MCP] .mcp.json 中 mcpServers 字段应为对象")
        return []

    result: list[MCPServerConfig] = []
    for name, cfg in servers.items():
        parsed = _parse_server_config(name, cfg)
        if parsed is not None:
            result.append(parsed)
    return result


def _parse_server_config(name: str, cfg: Any) -> MCPServerConfig | None:
    """解析单个 server 配置项，格式异常时返回 None 并打印警告。"""
    if not isinstance(cfg, dict):
        print(f"[MCP] Server '{name}' 配置应为对象，已跳过")
        return None

    transport = (cfg.get("type") or cfg.get("transport") or "").lower()

    if transport in ("streamable_http", "http", "sse"):
        url = cfg.get("url", "")
        if not url:
            print(f"[MCP] Server '{name}' (streamable_http) 缺少 url 字段，已跳过")
            return None
        return MCPServerConfig(
            name=name,
            transport="streamable_http",
            http=StreamableHTTPConfig(url=url),
        )

    elif transport == "stdio":
        command = cfg.get("command", "")
        if not command:
            print(f"[MCP] Server '{name}' (stdio) 缺少 command 字段，已跳过")
            return None
        args = cfg.get("args", [])
        if not isinstance(args, list):
            args = []
        env = cfg.get("env")
        if env is not None and not isinstance(env, dict):
            env = None
        return MCPServerConfig(
            name=name,
            transport="stdio",
            stdio=StdioConfig(command=command, args=args, env=env),
        )

    else:
        print(f"[MCP] Server '{name}' 未知传输类型: {transport or '(未指定)'}，已跳过")
        return None
