"""MCP 服务装配.

使用官方 Python SDK 2.x 的 :class:`mcp.server.MCPServer`,通过
``streamable_http_app(stateless_http=True, json_response=True)`` 构造
**无状态 Streamable HTTP** ASGI 应用,再合并 MCP app 内部已注册的
``Route /mcp`` 与自定义的 ``Route /health``,在同一端口上同时暴露:
- MCP 端点(默认 ``/mcp``);
- 健康检查端点(默认 ``/health``)。

无状态模式的关键约束(由 ``stateless_http=True`` 保证):
- 不发送旧协议的 ``initialize`` 握手;
- 不签发 / 不要求 ``Mcp-Session-Id`` 头;
- 不实现会话存储、会话过期、``max_sessions`` 或有状态 SSE 恢复;
- 每个请求自描述协议版本(通过 ``MCP-Protocol-Version`` 头与 ``_meta``)。

显式禁止导入 ``mcp.server.fastmcp.FastMCP``(本模块从不导入它)。
"""

from __future__ import annotations

import contextlib
from typing import Any, AsyncIterator

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types.version import LATEST_PROTOCOL_VERSION
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .config import Settings
from .logging_config import configure_logging, get_logger
from .rest_client import PetHospitalClient
from .tools import register_list_pets_tool

# 显式声明:本服务不导入 mcp.server.fastmcp.FastMCP。
# 以下断言在导入时执行,若未来有人误引入 FastMCP 会立即失败。
try:
    from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]  # noqa: F401, TC002
    _USE_FASTMCP = True  # pragma: no cover
except ImportError:
    _USE_FASTMCP = False

assert not _USE_FASTMCP, (
    "本服务禁止使用 mcp.server.fastmcp.FastMCP;请使用 mcp.server.MCPServer。"
)

SERVICE_NAME = "pet-hospital-mcp"
SERVICE_VERSION = "0.1.0"
SERVICE_INSTRUCTIONS = (
    "宠物医院 MCP 服务(无状态 Streamable HTTP)。"
    "当前阶段只暴露 list_pets 工具,对应上游 Go REST API 的 GET /api/v1/pets。"
    "调用方无需 initialize 握手,每个请求自描述协议版本。"
)


def build_mcp_server(settings: Settings, client: PetHospitalClient) -> MCPServer:
    """构造已注册工具的 MCPServer。"""
    mcp = MCPServer(
        name=SERVICE_NAME,
        version=SERVICE_VERSION,
        instructions=SERVICE_INSTRUCTIONS,
        # 教学场景:不配置 auth / OAuth / CORS / Origin 校验
    )
    register_list_pets_tool(mcp, client)
    return mcp


def create_app(
    settings: Settings | None = None,
    *,
    client: PetHospitalClient | None = None,
    mcp_server: MCPServer | None = None,
) -> Starlette:
    """构造 Starlette ASGI 应用。

    :param settings: 运行配置,默认从环境变量读取。
    :param client:   注入的 REST 客户端(测试时可传带 MockTransport 的实例)。
    :param mcp_server: 注入的 MCPServer(测试时可传自定义实例)。
    """
    if settings is None:
        settings = Settings.from_env()
    configure_logging()
    log = get_logger("server")

    if client is None:
        client = PetHospitalClient(settings)
    if mcp_server is None:
        mcp_server = build_mcp_server(settings, client)

    # 关键:stateless_http=True 启用 2026-07-28 无状态模式;
    # json_response=True 返回 application/json 而非 SSE 流(适合无状态 + 单请求)。
    # streamable_http_path 直接设为 /mcp,使返回的 Starlette app 内部
    # 就注册了无尾斜杠的 ``Route /mcp``,避免对无斜杠
    # 路径返回 307 重定向。
    mcp_app = mcp_server.streamable_http_app(
        streamable_http_path=settings.mcp_endpoint_path,
        json_response=True,
        stateless_http=True,
        host=settings.host,
        # 教学场景:禁用 DNS rebinding 防护,允许任意 Host 头访问
        # (用户明确要求不实现认证 / 权限 / CORS / Origin 校验)
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
    )

    async def health(request: Request) -> JSONResponse:  # noqa: ARG001
        return JSONResponse(
            {
                "status": "healthy",
                "service": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "protocol_version": LATEST_PROTOCOL_VERSION,
                "mcp_endpoint": settings.mcp_endpoint_path,
                "pet_hospital_base_url": settings.pet_hospital_base_url,
                "transport": "streamable_http_stateless",
                "stateless": True,
                "no_initialize": True,
                "no_session_id": True,
            }
        )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[Any]:
        # stateless_http=True 仍需要 session_manager.run() 来初始化 task
        # group(SDK 2.x 行为)。streamable_http_app() 返回的 mcp_app 自带
        # 一个 lifespan 负责启动 session_manager,这里通过它的
        # lifespan_context 复用,确保请求到达时 task group 已就绪。
        async with mcp_app.router.lifespan_context(mcp_app):
            log.info(
                "server_starting",
                host=settings.host,
                port=settings.port,
                mcp_endpoint=settings.mcp_endpoint_path,
                health_endpoint=settings.health_endpoint_path,
                pet_hospital_base_url=settings.pet_hospital_base_url,
                protocol_version=LATEST_PROTOCOL_VERSION,
            )
            try:
                yield
            finally:
                log.info("server_stopped")

    # 复用 MCP app 内部已注册的 Route /mcp,再追加 /health,
    # 合并到同一个 Starlette app,使 /mcp 直接匹配(无 307 重定向)。
    combined_routes = list(mcp_app.router.routes) + [
        Route(
            settings.health_endpoint_path,
            health,
            methods=["GET"],
        ),
    ]

    app = Starlette(
        routes=combined_routes,
        lifespan=lifespan,
    )
    # 暴露引用,便于测试
    app.state.settings = settings
    app.state.mcp_server = mcp_server
    app.state.rest_client = client
    return app


__all__ = [
    "SERVICE_NAME",
    "SERVICE_VERSION",
    "build_mcp_server",
    "create_app",
]
