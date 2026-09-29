"""结构化日志配置.

使用 structlog 输出 JSON 日志,每条日志至少包含:
``timestamp``、``tool_name``、``params``、``status``、``duration_ms``。

敏感字段脱敏:``ownerPhone``、``ownerAddr``、``chipNo`` 及其 snake_case
写法(``owner_phone`` / ``owner_addr`` / ``chip_no``)在日志中递归脱敏,
不会把完整敏感数据写出去。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

import structlog


# 需要(递归)脱敏的字段名,统一小写比较,兼容 camelCase / snake_case。
_SENSITIVE_KEYS = {
    "ownerphone",
    "owner_phone",
    "owneraddr",
    "owner_addr",
    "chipno",
    "chip_no",
}

# 脱敏后保留的字符数:电话保留前 3 后 2(区号 + 末两位);其他字段全打码。
_PHONE_HEAD = 3
_PHONE_TAIL = 2


def _mask_phone(value: str) -> str:
    if not isinstance(value, str):
        value = str(value)
    digits = re.sub(r"\D", "", value)
    if len(digits) <= _PHONE_HEAD + _PHONE_TAIL:
        return "***"
    return f"{digits[:_PHONE_HEAD]}***{digits[-_PHONE_TAIL:]}"


def _mask_text(value: str) -> str:
    if not isinstance(value, str):
        value = str(value)
    if len(value) <= 2:
        return "***"
    return f"{value[0]}***{value[-1]}"


def _mask_value(key_lower: str, value: Any) -> Any:
    if key_lower in {"ownerphone", "owner_phone"}:
        return _mask_phone(value)
    return _mask_text(value)


def redact(obj: Any) -> Any:
    """递归脱敏:对 dict 中匹配敏感键的值打码,列表逐项递归。"""
    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            key_lower = str(k).lower()
            if key_lower in _SENSITIVE_KEYS:
                out[k] = _mask_value(key_lower, v)
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(redact(x) for x in obj)
    return obj


def configure_logging() -> None:
    """配置 structlog:JSON 输出,键名 snake_case,时间戳 ISO8601。"""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=False),
            _redact_processor,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _redact_processor(_logger: Any, _method: str, event_dict: Mapping[str, Any]) -> dict[str, Any]:
    """对整个 event_dict 递归脱敏后再交给 JSONRenderer。"""
    redacted = redact(event_dict)
    # structlog 期望返回 dict
    return dict(redacted) if isinstance(redacted, Mapping) else {"event": redacted}


def get_logger(tool_name: str | None = None) -> Any:
    logger = structlog.get_logger("pet_hospital_mcp")
    if tool_name:
        logger = logger.bind(tool_name=tool_name)
    return logger
