"""MCP 协议层测试.

覆盖:
- ``/health`` 端点:返回 200 + 必要字段;
- MCP 工具注册:``tools/list`` 返回 ``list_pets`` 工具;
- 工具 JSON Schema:包含 13 个 camelCase 参数;
- 工具调用成功:``tools/call`` 返回 ``isError=false`` + 结构化结果;
- 工具调用失败:无效 species 返回 ``isError=true`` + 统一错误结构;
- 上游 5xx:返回 ``BACKEND_API_ERROR`` 错误;
- 无状态约束:响应不含 ``Mcp-Session-Id`` 头;
- 协议版本:``MCP-Protocol-Version`` 头协商 2026-07-28。

全部通过 httpx.ASGITransport 直连 ASGI app,不监听真实端口,不访问真实 Go 服务。
测试手动进入 app 的 lifespan context,确保 MCP SDK 的 session_manager 被初始化。
"""

from __future__ import annotations

import contextlib
import json
from typing import Any, Awaitable, Callable

import httpx
import pytest

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.rest_client import PetHospitalClient
from pet_hospital_mcp.server import create_app
from pet_hospital_mcp.tools.list_pets import TOOL_NAME


# ---------------------------------------------------------------------------
# 辅助:构造带 MockTransport 的 app + 已进入 lifespan 的 httpx AsyncClient
# ---------------------------------------------------------------------------


Handler = Callable[[httpx.Request], httpx.Response]


def _default_handler() -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope(_empty_data()))
    return handler


@contextlib.asynccontextmanager
async def _app_and_client(
    settings: Settings,
    handler: Handler | None = None,
) -> Any:
    """构造 app + httpx AsyncClient,并手动进入 app 的 lifespan。

    httpx.ASGITransport 不跑 lifespan,而 MCP SDK 需要 lifespan 启动
    session_manager.run() 初始化 task group,所以必须手动进入。
    """
    if handler is None:
        handler = _default_handler()
    transport = httpx.MockTransport(handler)
    client = PetHospitalClient(settings, transport=transport)
    app = create_app(settings, client=client)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://mcp.test",
        ) as http:
            yield app, http


def _mcp_request(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    req_id: int | str = 1,
) -> dict[str, Any]:
    """构造 2026-07-28 无状态 JSON-RPC 请求。

    每个请求自描述协议版本:``params._meta`` 携带
    ``io.modelcontextprotocol/protocolVersion`` 和
    ``io.modelcontextprotocol/clientCapabilities`` 信封键。
    """
    body: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
    }
    if params is None:
        params = {}
    # 2026-07-28:每个请求通过 _meta 自描述协议版本和客户端能力,
    # 取代旧版的 initialize 握手。
    params = {
        **params,
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        },
    }
    body["params"] = params
    return body


def _headers(method: str, name: str | None = None) -> dict[str, str]:
    """构造 2026-07-28 无状态模式下的请求头。

    必须包含:
    - ``MCP-Protocol-Version``:协议版本(与 ``_meta`` 一致);
    - ``MCP-Method``:与 body.method 一致;
    - ``MCP-Name``:对 name-bearing 方法(如 tools/call),
      与 ``params.name`` 一致。
    """
    h: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "MCP-Method": method,
    }
    if name is not None:
        h["MCP-Name"] = name
    return h


def _envelope(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": 200,
        "message": "ok",
        "data": data,
        "time": "2026-09-16T12:00:00+08:00",
    }


def _empty_data() -> dict[str, Any]:
    return {
        "items": [],
        "total": 0,
        "page": 1,
        "pageSize": 20,
        "totalPages": 0,
        "totalCost": 0.0,
    }


def _sample_data() -> dict[str, Any]:
    return {
        "items": [
            {
                "id": "PET-000001",
                "name": "旺财",
                "species": "犬",
                "ownerName": "张三",
                "ownerPhone": "13800001111",
                "doctor": "李医生",
                "disease": "急性肠胃炎",
                "status": "已康复",
                "records": None,
                "charges": None,
                "totalCost": 0.0,
                "visitCount": 0,
                "createdAt": "2025-01-12T10:00:00+08:00",
                "updatedAt": "2025-01-12T10:00:00+08:00",
            }
        ],
        "total": 1,
        "page": 1,
        "pageSize": 10,
        "totalPages": 1,
        "totalCost": 0.0,
    }


def _is_error(result: dict[str, Any]) -> Any:
    """兼容 camelCase / snake_case 的 is_error 字段。"""
    return result.get("isError", result.get("is_error"))


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_returns_200_with_required_fields(
    settings: Settings,
) -> None:
    async with _app_and_client(settings) as (_app, http):
        resp = await http.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["protocol_version"] == "2026-07-28"
    assert body["mcp_endpoint"] == "/mcp"
    assert body["pet_hospital_base_url"] == settings.pet_hospital_base_url
    assert body["stateless"] is True
    assert body["no_initialize"] is True
    assert body["no_session_id"] is True


@pytest.mark.asyncio
async def test_health_does_not_set_session_id_header(
    settings: Settings,
) -> None:
    async with _app_and_client(settings) as (_app, http):
        resp = await http.get("/health")
    # 无状态:响应不应携带 Mcp-Session-Id
    assert "mcp-session-id" not in {k.lower() for k in resp.headers.keys()}


# ---------------------------------------------------------------------------
# tools/list:工具发现
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_list_returns_list_pets(
    settings: Settings,
) -> None:
    async with _app_and_client(settings) as (_app, http):
        resp = await http.post(
            "/mcp",
            json=_mcp_request("tools/list", {}),
            headers=_headers("tools/list"),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "result" in body
    tools = body["result"]["tools"]
    assert isinstance(tools, list)
    assert len(tools) == 1
    tool = tools[0]
    assert tool["name"] == TOOL_NAME
    assert "description" in tool
    assert len(tool["description"]) > 0


@pytest.mark.asyncio
async def test_list_pets_tool_schema_has_camelcase_params(
    settings: Settings,
) -> None:
    async with _app_and_client(settings) as (_app, http):
        resp = await http.post(
            "/mcp",
            json=_mcp_request("tools/list", {}),
            headers=_headers("tools/list"),
        )

    body = resp.json()
    tool = body["result"]["tools"][0]
    # inputSchema 或 input_schema,兼容两种写法
    schema = tool.get("inputSchema") or tool.get("input_schema")
    assert schema is not None
    # SDK 2.x 将函数的 Pydantic 参数包装在 ``input`` 属性下,
    # 实际参数 schema 通过 $ref 引用 $defs/ListPetsInput
    assert "properties" in schema
    assert "input" in schema["properties"]
    input_prop = schema["properties"]["input"]
    # input 可能是 $ref 引用,也可能内联
    if "$ref" in input_prop:
        # 解析 $defs 中的实际模型
        defs = schema.get("$defs", {})
        ref_name = input_prop["$ref"].split("/")[-1]
        input_schema = defs.get(ref_name, {})
    else:
        input_schema = input_prop
    # 14 个 camelCase 参数(q/name 也是参数)
    props = set(input_schema.get("properties", {}).keys())
    expected = {
        "q", "name", "ownerName", "ownerPhone", "species",
        "doctor", "disease", "status", "min", "max",
        "sortBy", "order", "page", "pageSize",
    }
    assert props == expected, f"unexpected props: {props ^ expected}"
    # 拒绝未知字段
    assert input_schema.get("additionalProperties") is False


# ---------------------------------------------------------------------------
# tools/call:成功调用
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_list_pets_success(
    settings: Settings,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = dict(request.url.params)
        return httpx.Response(200, json=_envelope(_sample_data()))

    async with _app_and_client(settings, handler) as (_app, http):
        resp = await http.post(
            "/mcp",
            json=_mcp_request(
                "tools/call",
                {"name": TOOL_NAME, "arguments": {"input": {"page": 1, "pageSize": 10}}},
                req_id=2,
            ),
            headers=_headers("tools/call", TOOL_NAME),
        )

    assert resp.status_code == 200
    body = resp.json()
    result = body["result"]
    assert _is_error(result) is False
    # content 是数组,至少一个 TextContent
    content = result.get("content", [])
    assert len(content) >= 1
    text = content[0]["text"]
    payload = json.loads(text)
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == "PET-000001"
    assert payload["page"] == 1
    assert payload["pageSize"] == 10

    # 请求转发:page=1, pageSize=10
    assert captured["query"]["page"] == "1"
    assert captured["query"]["pageSize"] == "10"


@pytest.mark.asyncio
async def test_call_list_pets_forwards_all_arguments(
    settings: Settings,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = dict(request.url.params)
        return httpx.Response(200, json=_envelope(_empty_data()))

    async with _app_and_client(settings, handler) as (_app, http):
        await http.post(
            "/mcp",
            json=_mcp_request(
                "tools/call",
                {
                    "name": TOOL_NAME,
                    "arguments": {
                        "input": {
                            "q": "肠胃炎",
                            "ownerName": "张三",
                            "ownerPhone": "13800001111",
                            "species": "犬",
                            "doctor": "李医生",
                            "disease": "肠胃炎",
                            "status": "已康复",
                            "min": 100.0,
                            "max": 5000.0,
                            "sortBy": "totalCost",
                            "order": "desc",
                            "page": 2,
                            "pageSize": 15,
                        }
                    },
                },
                req_id=3,
            ),
            headers=_headers("tools/call", TOOL_NAME),
        )

    q = captured["query"]
    assert q["q"] == "肠胃炎"
    assert q["ownerName"] == "张三"
    assert q["ownerPhone"] == "13800001111"
    assert q["species"] == "犬"
    assert q["doctor"] == "李医生"
    assert q["disease"] == "肠胃炎"
    assert q["status"] == "已康复"
    assert q["min"] == "100"
    assert q["max"] == "5000"
    assert q["sortBy"] == "totalCost"
    assert q["order"] == "desc"
    assert q["page"] == "2"
    assert q["pageSize"] == "15"


# ---------------------------------------------------------------------------
# tools/call:校验失败 + 上游错误
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_list_pets_invalid_species_returns_error(
    settings: Settings,
) -> None:
    # 上游不应被调用(参数校验在工具内失败);放一个会爆炸的 handler 兜底
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("上游不应在 species 非法时被调用")

    async with _app_and_client(settings, handler) as (_app, http):
        resp = await http.post(
            "/mcp",
            json=_mcp_request(
                "tools/call",
                {"name": TOOL_NAME, "arguments": {"input": {"species": "外星人"}}},
                req_id=4,
            ),
            headers=_headers("tools/call", TOOL_NAME),
        )

    body = resp.json()
    # SDK 2.x 在调用工具函数前用 Pydantic 校验输入模型;校验失败时
    # 返回 is_error=true 的 CallToolResult,content 为 SDK 的错误文本
    # (不是我们自定义的 JSON 错误结构,因为我们的函数尚未被调用)。
    if "error" in body:
        # JSON-RPC error 路径
        assert body["error"]["code"] < 0
    else:
        result = body["result"]
        assert _is_error(result) is True
        text = result["content"][0]["text"]
        # SDK 的验证错误文本应包含 species 相关信息
        assert "species" in text or "Input should be" in text


@pytest.mark.asyncio
async def test_call_list_pets_unknown_field_rejected(
    settings: Settings,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("上游不应在未知字段时被调用")

    async with _app_and_client(settings, handler) as (_app, http):
        resp = await http.post(
            "/mcp",
            json=_mcp_request(
                "tools/call",
                {"name": TOOL_NAME, "arguments": {"input": {"petName": "旺财"}}},
                req_id=5,
            ),
            headers=_headers("tools/call", TOOL_NAME),
        )

    body = resp.json()
    # 未知字段:extra=forbid,SDK 校验失败
    assert (
        "error" in body
        or _is_error(body.get("result", {})) is True
    )


@pytest.mark.asyncio
async def test_call_list_pets_backend_5xx_returns_api_error(
    settings: Settings,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error")

    async with _app_and_client(settings, handler) as (_app, http):
        resp = await http.post(
            "/mcp",
            json=_mcp_request(
                "tools/call",
                {"name": TOOL_NAME, "arguments": {"input": {"page": 1}}},
                req_id=6,
            ),
            headers=_headers("tools/call", TOOL_NAME),
        )

    body = resp.json()
    result = body["result"]
    assert _is_error(result) is True
    text = result["content"][0]["text"]
    err = json.loads(text)
    assert err["error"]["code"] == "BACKEND_API_ERROR"
    assert err["error"]["details"]["status_code"] == 500


@pytest.mark.asyncio
async def test_call_list_pets_backend_timeout_returns_timeout_error(
    settings: Settings,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated")

    async with _app_and_client(settings, handler) as (_app, http):
        resp = await http.post(
            "/mcp",
            json=_mcp_request(
                "tools/call",
                {"name": TOOL_NAME, "arguments": {"input": {"page": 1}}},
                req_id=7,
            ),
            headers=_headers("tools/call", TOOL_NAME),
        )

    body = resp.json()
    result = body["result"]
    assert _is_error(result) is True
    err = json.loads(result["content"][0]["text"])
    assert err["error"]["code"] == "BACKEND_TIMEOUT"


# ---------------------------------------------------------------------------
# 无状态约束
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_response_has_no_session_id(
    settings: Settings,
) -> None:
    async with _app_and_client(settings) as (_app, http):
        resp = await http.post(
            "/mcp",
            json=_mcp_request("tools/list", {}),
            headers=_headers("tools/list"),
        )

    # 无状态:响应不应签发 Mcp-Session-Id
    header_keys = {k.lower() for k in resp.headers.keys()}
    assert "mcp-session-id" not in header_keys


@pytest.mark.asyncio
async def test_mcp_stateless_no_initialize_required(
    settings: Settings,
) -> None:
    """无状态模式:直接发 tools/list,不需要先发 initialize。"""
    async with _app_and_client(settings) as (_app, http):
        # 直接 tools/list,不发 initialize
        resp = await http.post(
            "/mcp",
            json=_mcp_request("tools/list", {}),
            headers=_headers("tools/list"),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "result" in body
    assert "error" not in body


@pytest.mark.asyncio
async def test_mcp_protocol_version_header_accepted(
    settings: Settings,
) -> None:
    async with _app_and_client(settings) as (_app, http):
        resp = await http.post(
            "/mcp",
            json=_mcp_request("tools/list", {}),
            headers=_headers("tools/list"),
        )

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_call_list_pets_with_null_records_in_response(
    settings: Settings,
) -> None:
    """Go 对 nil slice 序列化为 null,工具应兼容。"""

    def handler(request: httpx.Request) -> httpx.Response:
        data = _sample_data()
        data["items"][0]["records"] = None
        data["items"][0]["charges"] = None
        return httpx.Response(200, json=_envelope(data))

    async with _app_and_client(settings, handler) as (_app, http):
        resp = await http.post(
            "/mcp",
            json=_mcp_request(
                "tools/call",
                {"name": TOOL_NAME, "arguments": {"input": {"page": 1}}},
                req_id=8,
            ),
            headers=_headers("tools/call", TOOL_NAME),
        )

    body = resp.json()
    result = body["result"]
    assert _is_error(result) is False
    payload = json.loads(result["content"][0]["text"])
    # records / charges 可能为 null
    assert "records" in payload["items"][0]
    assert "charges" in payload["items"][0]
    assert payload["items"][0]["records"] in (None, [])
    assert payload["items"][0]["charges"] in (None, [])
