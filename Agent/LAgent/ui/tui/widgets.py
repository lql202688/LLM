"""TUI 复用组件：角色标签、消息块、工具调用卡片、侧栏、状态栏、Spinner、多行输入。

均为纯展示组件，通过构造函数/方法接收数据，不直接访问网络。
"""
from __future__ import annotations

import os

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Collapsible, Markdown, Static, TextArea

from core.agent import AgentSession
from core.tools.registry import TOOL_SCHEMAS


# ---------- Spinner ----------
_BRAILLE_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Spinner(Static):
    """等待提示：用 braille 字符做 10 帧循环动画，默认隐藏。"""

    visible = reactive(False, repaint=False)
    _frame = reactive(0, repaint=True)

    def __init__(self, id: str | None = None) -> None:
        super().__init__("", classes="spinner", markup=False, id=id)
        self._timer = None

    def watch_visible(self, value: bool) -> None:
        self.display = bool(value)  # Textual display=False 整个不渲染
        if value:
            self._timer = self.set_interval(0.12, self._tick)
        elif self._timer is not None:
            self._timer.stop()
            self._timer = None
            self.update("")

    def _tick(self) -> None:
        self._frame = (self._frame + 1) % len(_BRAILLE_FRAMES)
        self.update(_BRAILLE_FRAMES[self._frame])


# ---------- 多行输入：Enter 发送，Shift+Enter 换行 ----------
class PromptTextArea(TextArea):
    """多行输入框：Enter 触发 Submitted；Shift+Enter 手动插入换行。

    注意：TextArea 原生 Enter 会直接插入 \\n（绕过 BINDINGS），
    所以我们在 on_key 里精确拦截后 prevent_default 吞掉它；
    Shift+Enter 在 TextArea 原生里什么都不做，所以我们手动插入。
    """

    def __init__(self, placeholder: str = "") -> None:
        super().__init__(
            placeholder=placeholder,
            classes="prompt",
            tab_behavior="indent",
            soft_wrap=True,
        )
        self.max_height = 10  # 最多 10 行，超出后内部滚动

    def on_key(self, event) -> None:
        key = event.key
        if key == "enter":
            # 无修饰键 Enter → 发送，吞掉原生换行
            text = self.text.strip()
            self.clear()
            if text:
                self.post_message(PromptTextArea.Submitted(text))
            event.prevent_default()
            event.stop()
        elif key == "shift+enter":
            # Shift+Enter → 在当前光标位置插入换行
            line, col = self.cursor_location
            self.insert("\n", location=(line, col))
            # 插入后把光标移到新行开头
            self.move_cursor((line + 1, 0))
            event.prevent_default()
            event.stop()

    class Submitted(Message):
        def __init__(self, text: str):
            super().__init__()
            self.text = text


# ---------- 消息/角色组件 ----------
class TurnLabel(Static):
    """消息角色标签（如「你」「LAgent」），左侧带彩色竖条。"""

    def __init__(self, role: str):
        super().__init__(role, classes=f"role {'role-user' if role == '你' else 'role-agent'}")


class UserMessage(Static):
    def __init__(self, text: str):
        super().__init__(text, classes="msg user", markup=False)


class AssistantMessage(Markdown):
    """模型回答块，支持流式 update。"""

    def __init__(self):
        super().__init__("", classes="msg assistant")


class ToolCallCard(Collapsible):
    """一次工具调用的可折叠卡片：标题为「✓ 名称(参数摘要) · 耗时」，内容为参数与结果。"""

    def __init__(self, name: str, arguments: str):
        self.tool_name = name
        self.arguments = arguments or "{}"
        self._body = Static("执行中…", classes="tool-body", markup=False)
        super().__init__(
            self._body,
            title=f"▶ {name}({self._short_args()}) · 执行中",
            collapsed=True,
            classes="tool-card running",
        )

    def finish(self, result: str, duration_ms: float) -> None:
        self.title = f"✓ {self.tool_name}({self._short_args()}) · {duration_ms:.0f} ms"
        body = f"参数\n{self._pretty_args()}\n\n结果\n{result}"
        self._body.update(body)
        self.remove_class("running")

    def _pretty_args(self) -> str:
        import json

        try:
            return json.dumps(json.loads(self.arguments), ensure_ascii=False, indent=2)
        except ValueError:
            return self.arguments

    def _short_args(self, limit: int = 40) -> str:
        args = " ".join(self.arguments.split())
        return args if len(args) <= limit else args[: limit - 1] + "…"


# ---------- 侧栏 ----------
class Sidebar(VerticalScroll):
    """右侧栏：会话信息 + 已注册工具清单。"""

    def __init__(self, session: AgentSession):
        super().__init__(id="sidebar")
        self._session = session

    def compose(self) -> ComposeResult:
        yield Static("会话", classes="side-title")
        yield Static("", id="side-session", markup=False)
        yield Static(f"工具 · {len(TOOL_SCHEMAS)}", classes="side-title")
        for schema in TOOL_SCHEMAS:
            fn = schema["function"]
            yield Static(
                f"• {fn['name']}\n  {self._short(fn['description'])}",
                classes="side-tool",
                markup=False,
            )

    def update_status(self, status: str) -> None:
        s = self._session
        info = (
            f"模型   {s.model}\n"
            f"接口   {s.base_url}\n"
            f"目录   {os.getcwd()}\n"
            f"会话   {s.session_id}\n"
            f"轮次   {s.rounds}\n"
            f"消息   {len(s.messages)}\n"
            f"上限   {s.max_model_calls_per_turn} 次模型调用/轮\n"
            f"状态   {status}"
        )
        self.query_one("#side-session", Static).update(info)

    @staticmethod
    def _short(text: str, limit: int = 42) -> str:
        text = " ".join(text.split())
        return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------- 状态栏 ----------
class StatusBar(Static):
    """底部状态栏：状态 · 轮次 | 模型 | 工具次数 | 耗时 | 中断提示。"""

    def __init__(self, session: AgentSession):
        super().__init__("", id="statusbar", markup=False)
        self._session = session

    def update_state(
        self, status: str = "就绪", tool_calls: int = 0, elapsed: str = "-"
    ) -> None:
        s = self._session
        self.update(
            f"● {status} · {s.rounds} 轮 | {s.model} | "
            f"工具 {tool_calls} 次 | {elapsed} | Esc 中断"
        )
