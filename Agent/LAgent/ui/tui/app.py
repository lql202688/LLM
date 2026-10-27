"""基于 Textual 的终端图形界面（xAgent 风格）。

布局：Header（标题+时钟）/ 主区（消息流 + 右侧栏）/ 输入行（Spinner + TextArea）/ 状态栏 / Footer。
core 事件在 worker 线程产生，经 call_from_thread 切回 UI 线程渲染。
"""
from __future__ import annotations

import os
import time

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, Static

from core.agent import AgentSession
from core.events import AgentEvent, EventType
from core.tools.registry import TOOL_SCHEMAS
from ui.base import AgentUI
from ui.tui.help_screen import HelpScreen
from ui.tui.widgets import (
    AssistantMessage,
    PromptTextArea,
    Sidebar,
    Spinner,
    StatusBar,
    ToolCallCard,
    TurnLabel,
    UserMessage,
)

INPUT_HINT = "Enter 发送   Shift+Enter 换行   /help 查看命令   Esc 中断生成"


class ChatApp(App, AgentUI):
    """Textual 应用主体。App 负责渲染，AgentUI 契约负责与 core 对接。"""

    CSS_PATH = "styles.tcss"

    BINDINGS = [
        Binding("ctrl+q", "quit", "退出"),
        Binding("ctrl+l", "clear_chat", "清屏"),
        Binding("ctrl+n", "new_session", "新会话"),
        Binding("ctrl+b", "toggle_sidebar", "侧栏"),
        Binding("f1", "help", "帮助"),
        Binding("escape", "interrupt", "中断", show=False),
    ]

    def __init__(self, session: AgentSession):
        App.__init__(self)
        AgentUI.__init__(self, session)
        self._assistant_widget: AssistantMessage | None = None
        self._assistant_buffer: list[str] = []
        self._current_card: ToolCallCard | None = None
        self._busy = False
        self._tool_calls = 0
        self._turn_started = 0.0

    # ---------- 布局 ----------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            yield VerticalScroll(id="chat")
            yield Sidebar(self.session)
        with Horizontal(id="input-row"):
            yield Spinner(id="spinner")
            yield PromptTextArea(placeholder=INPUT_HINT)
        yield StatusBar(self.session)
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"LAgent — {self.session.model}"
        self.sub_title = f"— {os.getcwd()}"
        self._mount_banner()
        self._sidebar().update_status("就绪")
        self._statusbar().update_state()
        self._prompt().focus()

    def _mount_banner(self) -> None:
        banner = (
            f"LAgent  ·  {len(TOOL_SCHEMAS)} 个工具  ·  {self.session.model}\n"
            f"工作目录  {os.getcwd()}\n"
            "Enter 发送   Shift+Enter 换行   /help 命令   Esc 中断   Ctrl+B 侧栏   Ctrl+N 新会话"
        )
        self._chat().mount(Static(banner, classes="banner", markup=False))

    # ---------- 输入处理 ----------
    def on_prompt_text_area_submitted(
        self, event: PromptTextArea.Submitted
    ) -> None:
        text = event.text
        if not text:
            return
        if text.startswith("/"):
            self._run_slash_command(text)
            self._prompt().focus()
            return
        if text == "退出":
            self.exit()
            return
        if self._busy:
            self._system_note("模型正在响应中，按 Esc 可中断", classes="msg hint")
            return

        self._tool_calls = 0
        self._turn_started = time.perf_counter()
        self._mount(TurnLabel("你"))
        self._mount(UserMessage(text))
        self._close_assistant_block()
        self._set_status("生成中")
        self._set_spinner(True)
        self._run_agent(text)

    # ---------- 斜杠命令 ----------
    def _run_slash_command(self, text: str) -> None:
        cmd = text.split(maxsplit=1)[0].lower()
        if cmd == "/help":
            self.push_screen(HelpScreen())
        elif cmd == "/clear":
            self.action_clear_chat()
        elif cmd == "/new":
            self.action_new_session()
        elif cmd == "/tools":
            names = "\n".join(f"• {s['function']['name']}" for s in TOOL_SCHEMAS)
            self._system_note(f"已注册 {len(TOOL_SCHEMAS)} 个工具：\n{names}")
        elif cmd == "/cwd":
            self._system_note(f"工作目录：{os.getcwd()}")
        elif cmd == "/model":
            self._system_note(
                f"模型：{self.session.model}\n接口：{self.session.base_url}"
            )
        elif cmd == "/quit":
            self.exit()
        else:
            self._system_note(f"未知命令：{cmd}（输入 /help 查看全部命令）")

    # ---------- 快捷键动作 ----------
    def action_clear_chat(self) -> None:
        """清屏：移除消息，保留历史与会话。"""
        self._chat().remove_children()
        self._mount_banner()
        self._close_assistant_block()

    def action_new_session(self) -> None:
        """新会话：重置历史、更换会话 ID。"""
        self.session.reset()
        self.action_clear_chat()
        self._sidebar().update_status("就绪")
        self._statusbar().update_state()
        self._system_note(f"已开启新会话（{self.session.session_id}）")

    def action_toggle_sidebar(self) -> None:
        sidebar = self._sidebar()
        sidebar.display = not sidebar.display

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_interrupt(self) -> None:
        if self._busy:
            self.session.cancel()

    # ---------- core 调用（worker 线程） ----------
    @work(thread=True)
    def _run_agent(self, text: str) -> None:
        self._busy = True
        try:
            self.session.send(text, self._emit_from_worker)
        finally:
            self._busy = False
            self.call_from_thread(self._set_spinner, False)

    def _emit_from_worker(self, event: AgentEvent) -> None:
        # worker 线程 → UI 线程的安全桥接
        self.call_from_thread(self.handle_event, event)

    # ---------- 事件渲染（UI 线程） ----------
    def handle_event(self, event: AgentEvent) -> None:
        p = event.payload
        if event.type == EventType.TEXT_DELTA:
            self._append_assistant_text(p["text"])
        elif event.type == EventType.TOOL_CALL_START:
            self._close_assistant_block()
            self._current_card = ToolCallCard(p["name"], p["arguments"])
            self._mount(self._current_card)
        elif event.type == EventType.TOOL_CALL_RESULT:
            if self._current_card is not None:
                self._current_card.finish(p["result"], p["duration_ms"])
                self._current_card = None
            self._tool_calls += 1
            self._close_assistant_block()
        elif event.type == EventType.INTERRUPTED:
            self._system_note("■ 已中断（本轮未写入历史）", classes="msg interrupted")
            self._set_status("已中断")
        elif event.type == EventType.ERROR:
            self._close_assistant_block()
            self._system_note(f"!!! 错误: {p['message']}", classes="msg error")
            self._set_status("错误")
        elif event.type == EventType.TURN_DONE:
            self._close_assistant_block()
            self._current_card = None
            elapsed = f"{time.perf_counter() - self._turn_started:.1f}s"
            self._statusbar().update_state("就绪", self._tool_calls, elapsed)
            self._sidebar().update_status("就绪")
            self._prompt().focus()

    # ---------- 渲染辅助 ----------
    def _append_assistant_text(self, piece: str) -> None:
        self._assistant_buffer.append(piece)
        if self._assistant_widget is None:
            self._mount(TurnLabel("LAgent"))
            self._assistant_widget = AssistantMessage()
            self._mount(self._assistant_widget)
        self._assistant_widget.update("".join(self._assistant_buffer))
        self._scroll_to_bottom()

    def _close_assistant_block(self) -> None:
        self._assistant_widget = None
        self._assistant_buffer = []

    def _system_note(self, text: str, classes: str = "msg system") -> None:
        self._mount(Static(text, classes=classes, markup=False))

    def _set_status(self, status: str) -> None:
        self._statusbar().update_state(status, self._tool_calls)
        self._sidebar().update_status(status)

    def _set_spinner(self, show: bool) -> None:
        self.query_one("#spinner", Spinner).visible = show

    def _mount(self, widget) -> None:
        self._chat().mount(widget)
        self._scroll_to_bottom()

    def _chat(self) -> VerticalScroll:
        return self.query_one("#chat", VerticalScroll)

    def _prompt(self) -> PromptTextArea:
        return self.query_one(PromptTextArea)

    def _sidebar(self) -> Sidebar:
        return self.query_one("#sidebar", Sidebar)

    def _statusbar(self) -> StatusBar:
        return self.query_one("#statusbar", StatusBar)

    def _scroll_to_bottom(self) -> None:
        self._chat().scroll_end(animate=False)
