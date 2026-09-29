"""上游 Go REST API 客户端.

只负责把请求转发给上游 ``GET /api/v1/pets``,并对网络异常 / 超时 /
非 2xx / 非法 JSON 进行分类,翻译成 :mod:`pet_hospital_mcp.errors` 中的
``ToolError``。不做任何业务校验(由工具层 Pydantic 模型负责)。
"""

from __future__ import annotations

import json
from typing import Any, Mapping

import httpx

from .config import Settings
from .errors import (
    BACKEND_API_ERROR,
    BACKEND_INVALID_RESPONSE,
    BACKEND_TIMEOUT,
    BACKEND_UNAVAILABLE,
    INTERNAL_ERROR,
    ToolError,
    translate_unknown_error,
)
from .logging_config import get_logger, redact


class PetHospitalClient:
    """对上游 Go REST API 的薄封装,带超时 + 有限重试。"""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._log = get_logger("rest_client")

    def _client(self) -> httpx.Client:
        # 每次构造一个短生命周期 client,便于测试注入 MockTransport
        return httpx.Client(
            base_url=self._settings.pet_hospital_base_url,
            timeout=self._settings.backend_timeout_seconds,
            transport=self._transport,
        )

    def list_pets(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """调用上游 ``GET /api/v1/pets``。

        :param params: 已经过工具层 Pydantic 校验的查询参数(键为字符串,
            值为标量)。``None`` / 空字符串会被剔除。
        :returns: 上游响应 JSON 中 ``data`` 字段对应的字典。
        :raises ToolError: 超时 / 不可达 / 4xx-5xx / 非法响应 / 内部错误。
        """
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        log = self._log.bind(
            endpoint="/api/v1/pets",
            params=redact(dict(clean)),
        )
        max_attempts = max(1, self._settings.backend_max_retries + 1)
        last_error: ToolError | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                with self._client() as client:
                    response = client.get("/api/v1/pets", params=clean)
            except httpx.TimeoutException as exc:
                last_error = ToolError(
                    BACKEND_TIMEOUT,
                    "上游宠物医院服务请求超时",
                    {"endpoint": "/api/v1/pets", "timeout_seconds": self._settings.backend_timeout_seconds},
                )
                log.warning("backend_timeout", attempt=attempt, error=str(exc))
            except httpx.ConnectError as exc:
                last_error = ToolError(
                    BACKEND_UNAVAILABLE,
                    "上游宠物医院服务不可达",
                    {"endpoint": "/api/v1/pets", "base_url": self._settings.pet_hospital_base_url},
                )
                log.warning("backend_unavailable", attempt=attempt, error=str(exc))
            except httpx.HTTPError as exc:
                # 其他 HTTPX 异常(读错误、协议错误等)归为后端不可用
                last_error = ToolError(
                    BACKEND_UNAVAILABLE,
                    "与上游宠物医院服务通信失败",
                    {"endpoint": "/api/v1/pets", "error_kind": type(exc).__name__},
                )
                log.warning("backend_http_error", attempt=attempt, error=str(exc))
            else:
                return self._handle_response(response, log, attempt)

            # 仅对可重试错误进行重试(超时 / 不可达),4xx/5xx 不重试
            if attempt < max_attempts:
                import time

                backoff = self._settings.backend_retry_backoff_base * (2 ** (attempt - 1))
                time.sleep(backoff)
                continue

        assert last_error is not None
        raise last_error

    def _handle_response(
        self,
        response: httpx.Response,
        log: Any,
        attempt: int,
    ) -> dict[str, Any]:
        status = response.status_code
        # 4xx / 5xx:不重试,直接归为 BACKEND_API_ERROR
        if status >= 400:
            body_preview = self._safe_text(response)
            log.warning(
                "backend_api_error",
                attempt=attempt,
                status_code=status,
                body_preview=body_preview[:300],
            )
            raise ToolError(
                BACKEND_API_ERROR,
                f"上游宠物医院服务返回 {status}",
                {"status_code": status, "body_preview": body_preview[:300]},
            )

        # 解析 JSON
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            log.warning("backend_invalid_json", attempt=attempt, error=str(exc))
            raise ToolError(
                BACKEND_INVALID_RESPONSE,
                "上游返回非法 JSON",
                {"error": str(exc), "body_preview": self._safe_text(response)[:300]},
            ) from exc

        # 统一响应信封:{ "code": 200, "message": "ok", "data": {...} }
        if not isinstance(payload, dict):
            raise ToolError(
                BACKEND_INVALID_RESPONSE,
                "上游响应不是 JSON 对象",
                {"payload_type": type(payload).__name__},
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            # 兼容 data 为 null / 非对象:统一视为非法响应
            raise ToolError(
                BACKEND_INVALID_RESPONSE,
                "上游响应缺少 data 字段或 data 不是对象",
                {"has_data": data is not None, "data_type": type(data).__name__},
            )
        log.info("backend_ok", attempt=attempt, status_code=status)
        return data

    @staticmethod
    def _safe_text(response: httpx.Response) -> str:
        try:
            return response.text
        except Exception:
            return ""

    def close(self) -> None:
        """关闭客户端(兼容 transport 复用场景)。"""
        # 当前实现为每次创建短 client,无需显式关闭
