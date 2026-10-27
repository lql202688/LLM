"""命令行界面：与原 file_ops_chat.py 体验一致的最简实现。

可作为无 textual 环境下的回退界面，也是 UI 契约的最小参考实现。
"""
from __future__ import annotations

from core.events import AgentEvent, EventType

from .base import AgentUI


class ConsoleUI(AgentUI):
    def run(self) -> None:
        print("开始对话（支持本地文件操作工具），输入「退出」结束。")
        while True:
            try:
                question = input("我：").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not question:
                continue
            if question == "退出":
                break
            print("模型：", end="", flush=True)
            self.session.send(question, self.handle_event)
            print()

    def handle_event(self, event: AgentEvent) -> None:
        p = event.payload
        if event.type == EventType.TEXT_DELTA:
            print(p["text"], end="", flush=True)
        elif event.type == EventType.TOOL_CALL_START:
            print(f"\n  ▶ 调用工具 {p['name']}({p['arguments']}) ...")
        elif event.type == EventType.TOOL_CALL_RESULT:
            print(f"  ◀ 工具结果: {p['result']}")
            print("模型：", end="", flush=True)
        elif event.type == EventType.INTERRUPTED:
            print(f"\n■ {p['message']}（本轮未写入历史）")
        elif event.type == EventType.ERROR:
            print(f"\n!!! 错误: {p['message']}")
        elif event.type == EventType.TURN_DONE:
            pass
