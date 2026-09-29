"""PetHospitalClient 单元测试.

覆盖:
- 正常调用:确认请求路径、方法与全部过滤/排序/分页参数正确转发;
- 超时与连接异常(分别翻译成 BACKEND_TIMEOUT / BACKEND_UNAVAILABLE);
- 上游 4xx / 5xx(BACKEND_API_ERROR);
- 上游返回非法 JSON 或 data 字段缺失(BACKEND_INVALID_RESPONSE);
- 重试行为(可重试错误重试,4xx 不重试)。

全部通过 httpx.MockTransport 拦截,不访问真实 Go 服务。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from pet_hospital_mcp.errors import (
    BACKEND_API_ERROR,
    BACKEND_INVALID_RESPONSE,
    BACKEND_TIMEOUT,
    BACKEND_UNAVAILABLE,
    ToolError,
)
from pet_hospital_mcp.rest_client import PetHospitalClient
from pet_hospital_mcp.tools.list_pets import ListPetsInput


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_envelope(data: dict[str, Any]) -> dict[str, Any]:
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


def _capture_request_handler(
    capture: dict[str, Any],
    response: httpx.Response,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        capture["url"] = str(request.url)
        capture["path"] = request.url.path
        capture["method"] = request.method
        capture["query"] = dict(request.url.params)
        capture["headers"] = dict(request.headers)
        return response

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# 正常调用 + 参数转发
# ---------------------------------------------------------------------------


def test_list_pets_forwards_all_params(settings: Settings) -> None:
    capture: dict[str, Any] = {}
    transport = _capture_request_handler(capture, httpx.Response(200, json=_make_envelope(_empty_data())))
    client = PetHospitalClient(settings, transport=transport)

    input_obj = ListPetsInput(
        q="肠胃炎",
        name="旺财",
        ownerName="张三",
        ownerPhone="13800001111",
        species="犬",
        doctor="李医生",
        disease="肠胃炎",
        status="已康复",
        min=100.0,
        max=5000.0,
        sortBy="totalCost",
        order="desc",
        page=2,
        pageSize=15,
    )
    data = client.list_pets(input_obj.to_query_params())

    # 1. 请求方法与路径正确
    assert capture["method"] == "GET"
    assert capture["path"] == "/api/v1/pets"

    # 2. 全部 13 个参数都正确转发为 camelCase 查询参数
    q = capture["query"]
    assert q["q"] == "肠胃炎"
    assert q["name"] == "旺财"
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

    # 3. 返回 data 字段(从信封中提取)
    assert data == _empty_data()


def test_list_pets_drops_none_and_empty_params(settings: Settings) -> None:
    capture: dict[str, Any] = {}
    transport = _capture_request_handler(capture, httpx.Response(200, json=_make_envelope(_empty_data())))
    client = PetHospitalClient(settings, transport=transport)

    # 只传 page / pageSize,其他全 None
    input_obj = ListPetsInput(page=1, pageSize=10)
    client.list_pets(input_obj.to_query_params())

    q = capture["query"]
    assert q == {"page": "1", "pageSize": "10"}


def test_list_pets_extracts_data_from_envelope(settings: Settings, sample_list_response: dict) -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=sample_list_response))
    client = PetHospitalClient(settings, transport=transport)
    data = client.list_pets({"page": "1"})
    assert data["total"] == 1
    assert data["items"][0]["id"] == "PET-000001"
    assert data["totalCost"] == 1040.0


# ---------------------------------------------------------------------------
# 超时 / 不可达
# ---------------------------------------------------------------------------


def test_list_pets_timeout_raises_tool_error(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated timeout")

    client = PetHospitalClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(ToolError) as exc_info:
        client.list_pets({"page": "1"})
    assert exc_info.value.code == BACKEND_TIMEOUT
    assert "超时" in exc_info.value.message
    # 不暴露 Python 堆栈
    assert "TimeoutException" not in exc_info.value.to_json()


def test_list_pets_connect_error_raises_unavailable(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = PetHospitalClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(ToolError) as exc_info:
        client.list_pets({"page": "1"})
    assert exc_info.value.code == BACKEND_UNAVAILABLE
    assert "不可达" in exc_info.value.message


def test_list_pets_connect_error_retries_then_succeeds(settings_with_retry: Settings) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("transient")
        return httpx.Response(200, json=_make_envelope(_empty_data()))

    client = PetHospitalClient(settings_with_retry, transport=httpx.MockTransport(handler))
    data = client.list_pets({"page": "1"})
    assert data == _empty_data()
    assert calls["n"] == 3  # 2 次失败 + 1 次成功


# ---------------------------------------------------------------------------
# 4xx / 5xx
# ---------------------------------------------------------------------------


def test_list_pets_4xx_raises_api_error(settings: Settings) -> None:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(400, text='{"code":400,"message":"bad request"}')
    )
    client = PetHospitalClient(settings, transport=transport)
    with pytest.raises(ToolError) as exc_info:
        client.list_pets({"page": "1"})
    assert exc_info.value.code == BACKEND_API_ERROR
    assert exc_info.value.details["status_code"] == 400


def test_list_pets_5xx_raises_api_error(settings: Settings) -> None:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(500, text="internal server error")
    )
    client = PetHospitalClient(settings, transport=transport)
    with pytest.raises(ToolError) as exc_info:
        client.list_pets({"page": "1"})
    assert exc_info.value.code == BACKEND_API_ERROR
    assert exc_info.value.details["status_code"] == 500


def test_list_pets_4xx_does_not_retry(settings_with_retry: Settings) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, text="not found")

    client = PetHospitalClient(settings_with_retry, transport=httpx.MockTransport(handler))
    with pytest.raises(ToolError) as exc_info:
        client.list_pets({"page": "1"})
    assert exc_info.value.code == BACKEND_API_ERROR
    # 4xx 不重试:只调用 1 次
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# 非法 JSON / 结构错误
# ---------------------------------------------------------------------------


def test_list_pets_invalid_json_raises_invalid_response(settings: Settings) -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text="not json at all", headers={"content-type": "text/plain"}))
    client = PetHospitalClient(settings, transport=transport)
    with pytest.raises(ToolError) as exc_info:
        client.list_pets({"page": "1"})
    assert exc_info.value.code == BACKEND_INVALID_RESPONSE
    assert "非法 JSON" in exc_info.value.message


def test_list_pets_missing_data_field_raises_invalid_response(settings: Settings) -> None:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"code": 200, "message": "ok"})
    )
    client = PetHospitalClient(settings, transport=transport)
    with pytest.raises(ToolError) as exc_info:
        client.list_pets({"page": "1"})
    assert exc_info.value.code == BACKEND_INVALID_RESPONSE
    assert "data" in exc_info.value.message


def test_list_pets_data_not_object_raises_invalid_response(settings: Settings) -> None:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"code": 200, "data": [1, 2, 3]})
    )
    client = PetHospitalClient(settings, transport=transport)
    with pytest.raises(ToolError) as exc_info:
        client.list_pets({"page": "1"})
    assert exc_info.value.code == BACKEND_INVALID_RESPONSE


def test_list_pets_data_null_raises_invalid_response(settings: Settings) -> None:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"code": 200, "data": None})
    )
    client = PetHospitalClient(settings, transport=transport)
    with pytest.raises(ToolError) as exc_info:
        client.list_pets({"page": "1"})
    assert exc_info.value.code == BACKEND_INVALID_RESPONSE


def test_list_pets_payload_not_object_raises_invalid_response(settings: Settings) -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=[1, 2, 3]))
    client = PetHospitalClient(settings, transport=transport)
    with pytest.raises(ToolError) as exc_info:
        client.list_pets({"page": "1"})
    assert exc_info.value.code == BACKEND_INVALID_RESPONSE
