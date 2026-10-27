"""OpenAI 兼容的流式聊天客户端（仅依赖标准库）。

负责 HTTP 请求与 SSE 解析，把增量文本通过回调透出，
工具调用片段在内部聚合后一次性返回。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable

from .config import LLMConfig


class GenerationCancelled(Exception):
    """生成被外部（如 Esc 中断）取消。"""


class LLMClient:
    def __init__(self, config: LLMConfig):
        self._url = config.chat_completions_url
        self._model = config.model
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        }

    def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict],
        on_text_delta: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> tuple[str, list[dict]]:
        """发起一次流式请求，始终携带工具定义以支持多轮工具调用链。

        on_text_delta 收到每个增量文本片段；
        should_stop 返回 True 时抛出 GenerationCancelled（已产出的部分由调用方决定取舍）；
        返回 (assistant_text, tool_calls_list)。
        """
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "tools": tools,
            "tool_choice": "auto",
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self._url, data=data, headers=self._headers)

        tool_calls: dict[int, dict] = {}
        content_parts: list[str] = []

        try:
            resp = urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"HTTPError {e.code}: {e.reason}\n响应体: {err_body}"
            ) from e

        with resp:
            for line in resp:
                if should_stop and should_stop():
                    raise GenerationCancelled()
                line = line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[len("data:"):].strip()
                if chunk == "[DONE]":
                    break

                delta = json.loads(chunk)["choices"][0]["delta"]

                if delta.get("content"):
                    piece = delta["content"]
                    content_parts.append(piece)
                    if on_text_delta:
                        on_text_delta(piece)

                if delta.get("tool_calls"):
                    for tc in delta["tool_calls"]:
                        idx = tc["index"]
                        slot = tool_calls.setdefault(
                            idx, {"id": "", "name": "", "arguments": ""}
                        )
                        if tc.get("id"):
                            slot["id"] += tc["id"]
                        if tc.get("function"):
                            fn = tc["function"]
                            if fn.get("name"):
                                slot["name"] += fn["name"]
                            if fn.get("arguments"):
                                slot["arguments"] += fn["arguments"]

        return "".join(content_parts), list(tool_calls.values())
