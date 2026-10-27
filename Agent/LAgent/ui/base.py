"""UI 抽象层：所有界面实现（CLI / TUI / 未来的 Web / Desktop）的统一契约。

注意：刻意不使用 ABC，因为 Textual 的 App 自带元类，与 ABCMeta 冲突。
此处采用鸭子类型基类：子类需覆写 run() 与 handle_event()。

新界面只需：
1. 继承 AgentUI 并实现 run() / handle_event()；
2. 在 main.py 的 UI_REGISTRY 中注册；
core 层与入口装配逻辑均无需改动。
"""
from __future__ import annotations

from core.agent import AgentSession
from core.events import AgentEvent


class AgentUI:
    """界面基类：持有会话对象，负责把 core 事件渲染到具体终端/页面。"""

    def __init__(self, session: AgentSession):
        self.session = session

    def run(self) -> None:
        """启动界面主循环。"""
        raise NotImplementedError

    def handle_event(self, event: AgentEvent) -> None:
        """消费 core 推送的事件（文本增量、工具调用、错误等）。"""
        raise NotImplementedError
