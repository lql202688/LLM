"""共享 fixtures.

- 全部测试都不访问真实 Go 服务,通过 ``httpx.MockTransport`` 拦截上游请求;
- 提供一个可编程的 ``backend`` fixture,允许单个测试用例动态决定上游响应;
- 提供 ``settings`` / ``app`` / ``mcp_headers`` 等通用对象。
"""

from __future__ import annotations

from typing import Any, Callable

import httpx
import pytest

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.rest_client import PetHospitalClient
from pet_hospital_mcp.server import create_app


# ---------------------------------------------------------------------------
# 基础配置
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    # backend_max_retries=0 表示只尝试一次,不重试,便于超时 / 不可达测试
    return Settings(
        host="127.0.0.1",
        port=8000,
        pet_hospital_base_url="http://pet-hospital.test",
        backend_timeout_seconds=1.0,
        backend_max_retries=0,
        backend_retry_backoff_base=0.0,
    )


@pytest.fixture
def settings_with_retry() -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8000,
        pet_hospital_base_url="http://pet-hospital.test",
        backend_timeout_seconds=1.0,
        backend_max_retries=2,
        backend_retry_backoff_base=0.0,  # 测试时不真正退避
    )


# ---------------------------------------------------------------------------
# 上游 Go REST API 模拟
# ---------------------------------------------------------------------------


@pytest.fixture
def backend_handler() -> Callable[[httpx.Request], httpx.Response]:
    """返回一个可被测试用例覆盖的 handler 工厂。

    默认行为:对 ``GET /api/v1/pets`` 返回空列表信封。
    测试用例可以 monkeypatch 这个 fixture 返回的 handler,或直接构造
    自己的 MockTransport。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        # 默认返回空列表
        return httpx.Response(
            200,
            json={
                "code": 200,
                "message": "ok",
                "data": {
                    "items": [],
                    "total": 0,
                    "page": 1,
                    "pageSize": 20,
                    "totalPages": 0,
                    "totalCost": 0.0,
                },
                "time": "2026-09-16T12:00:00+08:00",
            },
        )

    return handler


@pytest.fixture
def mock_transport(
    backend_handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.MockTransport:
    return httpx.MockTransport(backend_handler)


@pytest.fixture
def rest_client(
    settings: Settings,
    mock_transport: httpx.MockTransport,
) -> PetHospitalClient:
    return PetHospitalClient(settings, transport=mock_transport)


# ---------------------------------------------------------------------------
# 样本响应
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_pet_dict() -> dict[str, Any]:
    """单条 Pet 的样本 JSON(对应 Go 的 Pet 模型,含 records/charges)。"""
    return {
        "id": "PET-000001",
        "name": "旺财",
        "species": "犬",
        "breed": "金毛寻回犬",
        "gender": "公",
        "ageMonths": 36,
        "color": "金黄",
        "chipNo": "CHIP-90001",
        "ownerName": "张三",
        "ownerPhone": "13800001111",
        "ownerAddr": "北京市朝阳区",
        "doctor": "李医生",
        "disease": "急性肠胃炎",
        "status": "已康复",
        "allergy": "青霉素过敏",
        "note": "注意饮食",
        "records": [
            {
                "id": "MR-1",
                "visitDate": "2025-01-12",
                "doctor": "李医生",
                "diagnosis": "急性肠胃炎",
                "symptoms": "呕吐腹泻",
                "treatment": "补液消炎",
                "prescription": ["阿莫西林"],
                "weightKg": 28.4,
                "temperature": 39.2,
                "followUp": "3天后复查",
                "charge": 860,
                "createdAt": "2025-01-12T10:00:00+08:00",
            }
        ],
        "charges": [
            {
                "id": "CH-1",
                "item": "血常规检查",
                "category": "检查",
                "amount": 180,
                "doctor": "李医生",
                "date": "2025-01-12",
                "note": "",
            }
        ],
        "totalCost": 1040.0,
        "visitCount": 1,
        "createdAt": "2025-01-12T10:00:00+08:00",
        "updatedAt": "2025-01-12T10:00:00+08:00",
    }


@pytest.fixture
def sample_list_response(sample_pet_dict: dict[str, Any]) -> dict[str, Any]:
    """上游 GET /api/v1/pets 的完整信封响应。"""
    return {
        "code": 200,
        "message": "ok",
        "data": {
            "items": [sample_pet_dict],
            "total": 1,
            "page": 1,
            "pageSize": 20,
            "totalPages": 1,
            "totalCost": 1040.0,
        },
        "time": "2026-09-16T12:00:00+08:00",
    }


# ---------------------------------------------------------------------------
# ASGI 应用 + MCP 客户端
# ---------------------------------------------------------------------------


@pytest.fixture
def app(
    settings: Settings,
    mock_transport: httpx.MockTransport,
) -> Any:
    """构造带 MockTransport 的 Starlette app(不监听真实端口)。"""
    client = PetHospitalClient(settings, transport=mock_transport)
    return create_app(settings, client=client)


@pytest.fixture
def mcp_headers() -> dict[str, str]:
    """2026-07-28 无状态模式下的标准请求头。"""
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
    }


@pytest.fixture
async def http_client(app: Any):
    """基于 ASGITransport 的 httpx AsyncClient(不监听真实端口)。

    手动进入 app 的 lifespan context,确保 MCP SDK 的
    session_manager.run() 被触发(否则请求会抛
    "Task group is not initialized")。
    """
    import contextlib

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://mcp.test",
        ) as client:
            yield client
