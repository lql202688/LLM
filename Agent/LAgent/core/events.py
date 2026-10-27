"""core → UI 的事件契约。

core 层在会话推进过程中持续产生 AgentEvent，UI 层只需订阅并渲染，
两者通过事件类型与 payload 结构解耦。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class EventType:
    TEXT_DELTA = "text_delta"            # payload: {"text": str}
    TOOL_CALL_START = "tool_call_start"  # payload: {"name": str, "arguments": str}
    TOOL_CALL_RESULT = "tool_call_result"  # payload: {"name": str, "result": str, "duration_ms": float}
    INTERRUPTED = "interrupted"          # payload: {"message": str}（本轮消息已回滚）
    TURN_DONE = "turn_done"              # payload: {}
    ERROR = "error"                      # payload: {"message": str}


@dataclass(frozen=True)
class AgentEvent:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)


# UI 订阅事件的回调签名
EventEmitter = Callable[[AgentEvent], None]
