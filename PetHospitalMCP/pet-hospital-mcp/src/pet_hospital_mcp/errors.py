"""统一错误模型.

所有工具调用失败都通过 ``ToolError`` 描述,并序列化为统一结构:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "可读错误信息",
    "details": {}
  }
}
```

错误码:
- ``VALIDATION_ERROR``              工具输入参数校验失败
- ``BACKEND_TIMEOUT``               上游 Go REST API 请求超时
- ``BACKEND_UNAVAILABLE``           上游连接被拒绝 / 网络不可达
- ``BACKEND_API_ERROR``             上游返回 4xx / 5xx
- ``BACKEND_INVALID_RESPONSE``     上游返回非法 JSON 或不符合数据模型
- ``INTERNAL_ERROR``                其他未分类内部异常

设计目标:不把 HTTPX / Pydantic / SDK / Python 堆栈原样暴露给 MCP 客户端,
所有外部异常都被翻译成上面的统一结构。
"""

from __future__ import annotations

import json
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# 错误码常量
# ---------------------------------------------------------------------------

VALIDATION_ERROR = "VALIDATION_ERROR"
BACKEND_TIMEOUT = "BACKEND_TIMEOUT"
BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
BACKEND_API_ERROR = "BACKEND_API_ERROR"
BACKEND_INVALID_RESPONSE = "BACKEND_INVALID_RESPONSE"
INTERNAL_ERROR = "INTERNAL_ERROR"


class ToolError(Exception):
    """工具执行失败的业务异常。

    ``code`` 是上述错误码常量之一;``message`` 是面向用户的可读说明;
    ``details`` 是可选的补充上下文(必须是 JSON 可序列化对象)。
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details) if details else {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def validation_error(message: str, details: Mapping[str, Any] | None = None) -> ToolError:
    return ToolError(VALIDATION_ERROR, message, details)


def internal_error(message: str = "internal error", details: Mapping[str, Any] | None = None) -> ToolError:
    # 不把原始异常字符串透传出去,避免泄露 Python 堆栈
    safe = message if message else "internal error"
    return ToolError(INTERNAL_ERROR, safe, details)


def translate_unknown_error(exc: Exception) -> ToolError:
    """把任意未分类异常翻译成 INTERNAL_ERROR,不泄露堆栈。

    保留异常类型名(便于排查),但不把异常字符串原样透传给 MCP 客户端。
    """
    return ToolError(
        INTERNAL_ERROR,
        "内部错误",
        {"error_kind": type(exc).__name__},
    )
