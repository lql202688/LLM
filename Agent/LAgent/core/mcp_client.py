"""MCP Client 管理器：连接外部 MCP Server，发现并调用其工具。

设计要点：
- 为了与 LAgent 现有的同步 dispatch 机制兼容，每个 MCP Server 在独立的
  后台线程中跑一个 asyncio 事件循环，外部通过 queue.Queue 提交异步任务、
  同步等待结果。
- 工具名称冲突时自动为 LLM 暴露的工具名加上服务器名称前缀
  （格式: {server_name}__{tool_name}），内部维护反向映射。
- 单个 Server 连接或调用失败只影响自身，不影响其他 Server。
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from .mcp_config import MCPServerConfig


@dataclass
class MCPToolInfo:
    """从 MCP Server 发现的单个工具信息。"""

    # 原始工具名（MCP Server 提供的）
    original_name: str
    # 暴露给 LLM 的工具名（冲突时带前缀）
    exposed_name: str
    description: str
    input_schema: dict  # JSON Schema
    server_name: str  # 所属 Server

    def to_function_schema(self) -> dict:
        """转换为 LLM Function Calling 所需的 schema 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.exposed_name,
                "description": self.description or f"(来自 MCP Server {self.server_name})",
                "parameters": self.input_schema or {"type": "object", "properties": {}},
            },
        }


class _ServerConnection:
    """单个 MCP Server 的线程+事件循环封装。

    内部维护一个运行在独立线程中的 asyncio 事件循环，提供 submit() 方法
    让外部同步提交协程并获取结果。
    """

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready_event = threading.Event()
        self._error: Exception | None = None

    def start(self) -> None:
        """启动后台线程和事件循环。"""
        self._thread = threading.Thread(
            target=self._run_event_loop,
            name=f"MCP-{self.config.name}",
            daemon=True,
        )
        self._thread.start()
        # 等待事件循环就绪或出错
        self._ready_event.wait(timeout=15)

    def stop(self) -> None:
        """停止事件循环和后台线程。"""
        if self._loop is not None and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._loop = None
        self._thread = None

    def _run_event_loop(self) -> None:
        """后台线程入口：创建并运行事件循环。"""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._ready_event.set()
            self._loop.run_forever()
        except Exception as e:
            self._error = e
            self._ready_event.set()
        finally:
            try:
                if self._loop is not None:
                    self._loop.close()
            except Exception:
                pass

    @property
    def is_ready(self) -> bool:
        return self._loop is not None and not self._loop.is_closed()

    @property
    def startup_error(self) -> Exception | None:
        return self._error

    def submit(self, coro_factory: Callable[[], Any], timeout: float = 30.0) -> Any:
        """同步提交一个协程工厂，等待并返回结果。

        Args:
            coro_factory: 无参数的可调用对象，返回一个协程
            timeout: 超时秒数

        Returns:
            协程执行结果

        Raises:
            RuntimeError: 事件循环不可用
            TimeoutError: 超时
            Exception: 协程执行时抛出的异常
        """
        if self._loop is None or self._loop.is_closed():
            raise RuntimeError(
                f"MCP Server '{self.config.name}' 事件循环不可用"
            )

        result_queue: queue.Queue = queue.Queue(maxsize=1)

        async def _runner():
            try:
                result = await coro_factory()
                result_queue.put(("ok", result))
            except BaseException as e:
                # 捕获所有异常（包括 BaseExceptionGroup/CancelledError）
                result_queue.put(("err", e))

        future = asyncio.run_coroutine_threadsafe(_runner(), self._loop)
        try:
            future.result(timeout=timeout)
        except Exception:
            try:
                status, payload = result_queue.get(timeout=1)
            except queue.Empty:
                raise
            if status == "err":
                raise payload
            return payload

        # 从 queue 取实际结果
        status, payload = result_queue.get(timeout=1)
        if status == "err":
            # 如果是 ExceptionGroup，提取第一个内层异常作为主要错误
            if hasattr(payload, "exceptions") and payload.exceptions:
                payload = payload.exceptions[0]
            raise payload
        return payload


class MCPClientManager:
    """管理多个 MCP Server 连接，统一提供工具发现和调用能力。"""

    def __init__(self):
        self._connections: dict[str, _ServerConnection] = {}
        # exposed_name -> (server_name, original_name)
        self._tool_registry: dict[str, tuple[str, str]] = {}
        # server_name -> MCPToolInfo list
        self._server_tools: dict[str, list[MCPToolInfo]] = {}

    # ---------- 生命周期 ----------

    def connect_all(self, server_configs: list[MCPServerConfig]) -> None:
        """依次连接所有 MCP Server，单个失败不影响其他。"""
        for cfg in server_configs:
            self.connect(cfg)

    def connect(self, config: MCPServerConfig) -> list[MCPToolInfo]:
        """连接单个 MCP Server，发现并返回其工具列表。"""
        name = config.name
        try:
            conn = _ServerConnection(config)
            conn.start()
            if conn.startup_error:
                raise conn.startup_error

            # 发现工具
            tools = conn.submit(lambda: self._discover_tools(conn, config))
            print(f"[MCP] Server '{name}' 连接成功，发现 {len(tools)} 个工具")

            # 注册工具，处理名称冲突
            registered = self._register_tools(name, tools)
            self._connections[name] = conn
            self._server_tools[name] = registered
            return registered

        except Exception as e:
            print(f"[MCP] Server '{name}' 连接失败: {e}")
            if self._connections.get(name):
                self._connections[name].stop()
                self._connections.pop(name, None)
            return []

    def disconnect_all(self) -> None:
        """断开所有 MCP Server 连接。"""
        for name in list(self._connections.keys()):
            try:
                self._connections[name].stop()
            except Exception:
                pass
        self._connections.clear()
        self._tool_registry.clear()
        self._server_tools.clear()

    # ---------- 内部辅助 ----------

    async def _discover_tools(
        self, conn: _ServerConnection, config: MCPServerConfig
    ) -> list[MCPToolInfo]:
        """连接 MCP Server 并 list_tools。"""
        import traceback as _tb

        from mcp.client.session import ClientSession
        from mcp.client.stdio import stdio_client, StdioServerParameters
        from mcp.client.streamable_http import streamable_http_client

        try:
            if config.transport == "streamable_http" and config.http:
                async with streamable_http_client(config.http.url) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools_result = await session.list_tools()
                        return [
                            MCPToolInfo(
                                original_name=t.name,
                                exposed_name="",  # 稍后由注册逻辑填充
                                description=t.description or "",
                                input_schema=t.input_schema or {},
                                server_name=config.name,
                            )
                            for t in tools_result.tools
                        ]

            elif config.transport == "stdio" and config.stdio:
                params = StdioServerParameters(
                    command=config.stdio.command,
                    args=config.stdio.args,
                    env=config.stdio.env,
                )
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools_result = await session.list_tools()
                        return [
                            MCPToolInfo(
                                original_name=t.name,
                                exposed_name="",
                                description=t.description or "",
                                input_schema=t.input_schema or {},
                                server_name=config.name,
                            )
                            for t in tools_result.tools
                        ]

        except BaseExceptionGroup as eg:
            # TaskGroup 会把异常包成 ExceptionGroup
            inner = eg.exceptions[0] if eg.exceptions else eg
            msg = f"{inner}"
            print(f"[MCP] _discover_tools inner error: {msg}")
            _tb.print_exception(type(inner), inner, inner.__traceback__)
            raise

        return []

    def _register_tools(
        self, server_name: str, tools: list[MCPToolInfo]
    ) -> list[MCPToolInfo]:
        """为一组工具分配暴露名称并更新注册表，处理跨 server 名称冲突。"""
        existing_exposed = set(self._tool_registry.keys())
        registered: list[MCPToolInfo] = []

        for t in tools:
            # 默认用原始名称，冲突时加前缀
            candidate = t.original_name
            if candidate in existing_exposed:
                candidate = f"{server_name}__{t.original_name}"
            # 再加一层检查（防止恰好跟本地工具重名）
            if candidate in existing_exposed:
                candidate = f"{server_name}__{t.original_name}"

            t.exposed_name = candidate
            existing_exposed.add(candidate)
            self._tool_registry[candidate] = (server_name, t.original_name)
            registered.append(t)

        return registered

    # ---------- 工具查询 ----------

    def get_all_tool_schemas(self) -> list[dict]:
        """返回所有已注册 MCP 工具的 function schema 列表。"""
        schemas: list[dict] = []
        for tools in self._server_tools.values():
            for t in tools:
                schemas.append(t.to_function_schema())
        return schemas

    @property
    def all_tools(self) -> dict[str, tuple[str, str]]:
        """exposed_name -> (server_name, original_name) 的映射表。"""
        return self._tool_registry

    @property
    def connected_servers(self) -> list[str]:
        return list(self._connections.keys())

    # ---------- 工具调用 ----------

    def call_tool(self, exposed_name: str, arguments_json: str) -> str:
        """调用 MCP 工具，返回文本结果。

        Args:
            exposed_name: 暴露给 LLM 的工具名
            arguments_json: JSON 字符串参数

        Returns:
            工具执行结果的文本表示

        Raises:
            KeyError: 工具名未注册
            RuntimeError: 对应 MCP Server 连接不可用
        """
        mapping = self._tool_registry.get(exposed_name)
        if mapping is None:
            return f"[MCP] 未知工具: {exposed_name}"

        server_name, original_name = mapping
        conn = self._connections.get(server_name)
        if conn is None or not conn.is_ready:
            return f"[MCP] Server '{server_name}' 不可用，无法调用工具 '{exposed_name}'"

        try:
            args = json.loads(arguments_json or "{}")
        except json.JSONDecodeError:
            args = {}

        try:
            result = conn.submit(
                lambda: self._call_tool_async(conn, original_name, args)
            )
            return result
        except Exception as e:
            traceback.print_exc()
            return f"[MCP] 调用 {exposed_name} 失败: {e}"

    async def _call_tool_async(
        self,
        conn: _ServerConnection,
        tool_name: str,
        arguments: dict,
    ) -> str:
        """实际调用 MCP Server 工具的异步实现。"""
        from mcp.client.session import ClientSession
        from mcp.client.stdio import stdio_client
        from mcp.client.stdio import StdioServerParameters
        from mcp.client.streamable_http import streamable_http_client

        config = conn.config

        if config.transport == "streamable_http" and config.http:
            async with streamable_http_client(config.http.url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await self._execute_and_format(session, tool_name, arguments)

        elif config.transport == "stdio" and config.stdio:
            params = StdioServerParameters(
                command=config.stdio.command,
                args=config.stdio.args,
                env=config.stdio.env,
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await self._execute_and_format(session, tool_name, arguments)

        return f"[MCP] 未知传输类型: {config.transport}"

    async def _execute_and_format(
        self,
        session: Any,
        tool_name: str,
        arguments: dict,
    ) -> str:
        """调用工具并将返回值格式化为文本。"""
        call_result = await session.call_tool(tool_name, arguments)
        is_error = getattr(call_result, "isError", False)
        content_items = getattr(call_result, "content", [])

        parts: list[str] = []
        for item in content_items:
            text = getattr(item, "text", None)
            if text is None:
                # 尝试其他内容类型（如 ImageContent）
                item_type = getattr(item, "type", "unknown")
                if item_type == "image":
                    text = f"[图片内容: {getattr(item, 'mimeType', 'unknown')}]"
                else:
                    text = f"[{item_type}]"
            parts.append(text)

        result_text = "\n".join(parts) if parts else "(无输出)"
        if is_error:
            return f"[MCP 工具报错] {result_text}"
        return result_text
