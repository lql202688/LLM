"""会话编排：维护对话历史，驱动「模型 -> 工具 -> 模型」的多轮循环。

AgentSession 是 UI 层唯一需要交互的 core 门面：
    session = AgentSession(load_config())
    session.send("列出当前目录", emit=my_ui_render)

同时提供界面所需的状态信息（会话 ID、轮次、模型等）与中断能力：
    session.cancel()  # 中断当前生成，本轮消息回滚
    session.reset()   # 新建会话
"""
from __future__ import annotations

import threading
import time
from uuid import uuid4

from .config import AppConfig
from .events import AgentEvent, EventEmitter, EventType
from .llm_client import GenerationCancelled, LLMClient
from .tools.registry import TOOL_SCHEMAS, dispatch

DEFAULT_MAX_MODEL_CALLS_PER_TURN = 8


class AgentSession:
    def __init__(
        self,
        config: AppConfig,
        llm_client: LLMClient | None = None,
        max_model_calls_per_turn: int = DEFAULT_MAX_MODEL_CALLS_PER_TURN,
    ):
        self._config = config
        self._llm = llm_client or LLMClient(config.llm)
        self.max_model_calls_per_turn = max_model_calls_per_turn
        self._cancel = threading.Event()
        self.session_id = uuid4().hex[:8]
        self.messages: list[dict] = []

    # ---------- 界面状态信息 ----------
    @property
    def model(self) -> str:
        return self._config.llm.model

    @property
    def base_url(self) -> str:
        return self._config.llm.base_url

    @property
    def rounds(self) -> int:
        """已写入历史的用户提问轮数。"""
        return sum(1 for m in self.messages if m.get("role") == "user")

    # ---------- 会话控制 ----------
    def cancel(self) -> None:
        """请求中断当前生成；send() 会在最近的安全点停止并回滚本轮历史。"""
        self._cancel.set()

    def reset(self) -> None:
        """新建会话：清空历史、更换会话 ID。"""
        self.messages.clear()
        self.session_id = uuid4().hex[:8]
        self._cancel.clear()

    # ---------- 主流程 ----------
    def send(self, user_text: str, emit: EventEmitter) -> None:
        """处理一轮用户输入，期间通过 emit 持续推送事件，直到模型不再调用工具。"""
        self._cancel.clear()
        snapshot = len(self.messages)
        self.messages.append({"role": "user", "content": user_text})
        try:
            self._run_tool_loop(emit)
        except GenerationCancelled:
            # 只有完成的问答才写入历史：回滚本轮（含用户输入）
            del self.messages[snapshot:]
            emit(AgentEvent(EventType.INTERRUPTED, {"message": "已中断"}))
        except Exception as e:  # 网络/协议错误统一上抛为 ERROR 事件
            emit(AgentEvent(EventType.ERROR, {"message": str(e)}))
        emit(AgentEvent(EventType.TURN_DONE))

    def _run_tool_loop(self, emit: EventEmitter) -> None:
        model_calls = 0
        while True:
            if self._cancel.is_set():
                raise GenerationCancelled()
            model_calls += 1
            if model_calls > self.max_model_calls_per_turn:
                emit(
                    AgentEvent(
                        EventType.ERROR,
                        {"message": f"已达单轮模型调用上限（{self.max_model_calls_per_turn} 次）"},
                    )
                )
                break

            assistant_text, tool_calls = self._llm.stream_chat(
                self.messages,
                TOOL_SCHEMAS,
                on_text_delta=lambda piece: emit(
                    AgentEvent(EventType.TEXT_DELTA, {"text": piece})
                ),
                should_stop=self._cancel.is_set,
            )

            self.messages.append(
                self._build_assistant_message(assistant_text, tool_calls)
            )
            if not tool_calls:
                break

            for tc in tool_calls:
                if self._cancel.is_set():
                    raise GenerationCancelled()
                emit(
                    AgentEvent(
                        EventType.TOOL_CALL_START,
                        {"name": tc["name"], "arguments": tc["arguments"] or "{}"},
                    )
                )
                started = time.perf_counter()
                result = dispatch(tc["name"], tc["arguments"])
                duration_ms = (time.perf_counter() - started) * 1000
                emit(
                    AgentEvent(
                        EventType.TOOL_CALL_RESULT,
                        {
                            "name": tc["name"],
                            "result": str(result),
                            "duration_ms": duration_ms,
                        },
                    )
                )
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": str(result),
                    }
                )

    @staticmethod
    def _build_assistant_message(text: str, tool_calls: list[dict]) -> dict:
        """构造 assistant 历史消息；有工具调用时必须携带 tool_calls 字段。"""
        msg: dict = {"role": "assistant"}
        if tool_calls:
            msg["content"] = text or ""
            msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"] or "{}",
                    },
                }
                for tc in tool_calls
            ]
        else:
            msg["content"] = text
        return msg
