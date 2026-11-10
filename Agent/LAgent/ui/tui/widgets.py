"""TUI 复用组件：角色标签、消息块、工具调用卡片、侧栏、状态栏、Spinner、多行输入。

均为纯展示组件，通过构造函数/方法接收数据，不直接访问网络。
"""
from __future__ import annotations

import os

from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.content import Content
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
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
        self.display = bool(value)
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
    """多行输入框：Enter 触发 Submitted；Shift+Enter 手动插入换行。"""

    def __init__(self, placeholder: str = "") -> None:
        super().__init__(
            placeholder=placeholder,
            classes="prompt",
            tab_behavior="indent",
            soft_wrap=True,
        )
        self.max_height = 10

    def on_key(self, event) -> None:
        key = event.key
        if key == "enter":
            text = self.text.strip()
            self.clear()
            if text:
                self.post_message(PromptTextArea.Submitted(text))
            event.prevent_default()
            event.stop()
        elif key == "shift+enter":
            line, col = self.cursor_location
            self.insert("\n", location=(line, col))
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
    """一次工具调用的可折叠卡片。"""

    def __init__(self, name: str, arguments: str):
        self.tool_name = name
        self.arguments = arguments or "{}"
        self._body = Static("执行中…", classes="tool-body", markup=False)
        super().__init__(
            self._body,
            title=Content.from_text(f"▶ {name}({self._short_args()}) · 执行中"),
            collapsed=True,
            classes="tool-card running",
        )

    def finish(self, result: str, duration_ms: float) -> None:
        self.title = Content.from_text(
            f"✓ {self.tool_name}({self._short_args()}) · {duration_ms:.0f} ms"
        )
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
    """右侧栏：会话信息 + MCP 服务器状态 + 工具清单。"""

    def __init__(self, session: AgentSession):
        super().__init__(id="sidebar")
        self._session = session

    def compose(self):
        from textual.widgets import Label
        yield Static("会话", classes="side-title")
        yield Static("", id="side-session", markup=False)
        yield Static("MCP 服务器", classes="side-title")
        yield Static("", id="side-mcp", markup=False)
        yield Static("本地工具", classes="side-title")
        for schema in TOOL_SCHEMAS:
            fn = schema["function"]
            yield Static(
                f"• {fn['name']}\n  {self._short(fn['description'])}",
                classes="side-tool",
                markup=False,
            )
        yield Static("MCP 工具", classes="side-title")
        yield Static("加载中…", id="side-mcp-tools", classes="side-tool", markup=False)

    def update_status(self, status: str) -> None:
        s = self._session
        info_lines = [
            f"模型   {s.model}",
            f"接口   {s.base_url}",
            f"目录   {os.getcwd()}",
            f"会话   {s.session_id}",
            f"轮次   {s.rounds}",
            f"消息   {len(s.messages)}",
            f"上限   {s.max_model_calls_per_turn} 次调用/轮",
            f"状态   {status}",
            "",
        ]
        info_lines.extend(self._context_lines())
        self.query_one("#side-session", Static).update("\n".join(info_lines))

        # ---------- MCP 区域 ----------
        self._update_mcp_section()

    def _update_mcp_section(self) -> None:
        s = self._session
        mcp_widget = self.query_one("#side-mcp", Static)
        tools_widget = self.query_one("#side-mcp-tools", Static)

        if not s._mcp_configs:
            mcp_widget.update("未配置 MCP\n（.mcp.json 不存在）")
            tools_widget.update("—")
            return

        if not s.mcp_loaded:
            mcp_widget.update("连接中…")
            tools_widget.update("等待加载")
            return

        servers = s.mcp_servers
        if not servers:
            mcp_widget.update("连接失败")
            tools_widget.update("—")
            if s._mcp_error:
                tools_widget.update(f"错误: {s._mcp_error[:40]}")
            return

        lines = []
        for name in servers:
            lines.append(f"• {name} ✓")
        mcp_widget.update("\n".join(lines))

        # MCP 工具列表
        from core.tools.registry import _MCP_SCHEMAS
        if _MCP_SCHEMAS:
            tool_lines = []
            for schema in _MCP_SCHEMAS[:10]:  # 最多显示 10 个
                fn = schema["function"]
                tool_lines.append(f"• {fn['name']}")
            if len(_MCP_SCHEMAS) > 10:
                tool_lines.append(f"… 还有 {len(_MCP_SCHEMAS) - 10} 个")
            tools_widget.update("\n".join(tool_lines))
        else:
            tools_widget.update("（无可用工具）")

    def refresh_mcp(self) -> None:
        """MCP 异步加载完成后刷新 MCP 区域。"""
        self._update_mcp_section()

    def _context_lines(self) -> list[str]:
        s = self._session
        used_k = s.context_usage_tokens / 1024
        win_k = s.context_window / 1024
        pct = s.context_usage_ratio * 100
        tag = "（默认）" if s.context_window_is_default else ""
        lines = [
            "—— 上下文 ——",
            f"占用   {used_k:.1f}k / {win_k:.0f}k{tag}",
            f"比例   {pct:.0f}%",
        ]
        if s.context_locked:
            lines.append("⚠ 已锁定，请执行 /compact")
        return lines

    @staticmethod
    def _short(text: str, limit: int = 42) -> str:
        text = " ".join(text.split())
        return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------- 状态栏 ----------
class StatusBar(Static):
    """底部状态栏：状态 · 轮次 | 模型 | 上下文 | 工具次数 | 耗时。"""

    def __init__(self, session: AgentSession):
        super().__init__("", id="statusbar", markup=False)
        self._session = session

    def update_state(
        self, status: str = "就绪", tool_calls: int = 0, elapsed: str = "-"
    ) -> None:
        s = self._session
        used_k = s.context_usage_tokens / 1024
        win_k = s.context_window / 1024
        pct = s.context_usage_ratio * 100
        tag = "默认" if s.context_window_is_default else "配置"
        warn = " ⚠" if s.context_locked else ""
        mcp_tag = f" MCP:{len(s.mcp_servers)}" if s.mcp_servers else ""
        self.update(
            f"● {status} · {s.rounds} 轮 | {s.model} | "
            f"上下文 {used_k:.1f}k / {win_k:.0f}k({tag}) {pct:.0f}%{warn} | "
            f"工具 {tool_calls} 次 | {elapsed}{mcp_tag} | Esc 中断"
        )


# ---------- 会话列表恢复弹窗 ----------
class ResumeScreen(ModalScreen[str | None]):
    """列出 .agents/sessions/ 下所有已保存会话，上下键选择，回车确认，Esc 取消。"""

    BINDINGS = [
        Binding("escape", "cancel", "取消", show=False),
        Binding("up", "cursor_up", "上移", show=False),
        Binding("down", "cursor_down", "下移", show=False),
        Binding("enter", "select", "选中", show=False),
    ]

    def __init__(self, session: AgentSession):
        super().__init__()
        self._session = session
        self._items = session.list_sessions()
        self._cursor = 0

    def compose(self):
        from textual.containers import Vertical
        from textual.widgets import Label
        with Vertical(id="resume-panel"):
            yield Label("恢复会话", id="resume-title")
            if not self._items:
                yield Static("未找到已保存会话\n（请先使用 /save 保存当前会话）", classes="resume-empty")
            else:
                self._list_widget = Static("", id="resume-list", markup=False)
                yield self._list_widget
                yield Static(
                    "↑↓ 选择   Enter 确认   Esc 取消",
                    classes="resume-hint",
                    markup=False,
                )

    def on_mount(self) -> None:
        if self._items:
            self._render()

    def _render(self) -> None:
        lines = []
        for i, item in enumerate(self._items):
            marker = "▶ " if i == self._cursor else "  "
            lines.append(
                f"{marker}{item.get('saved_at', '未知')[:19]}  "
                f"{item.get('session_id', '?')}  "
                f"({item.get('message_count', 0)} 条)"
            )
        self._list_widget.update("\n".join(lines))

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._render()

    def action_cursor_down(self) -> None:
        if self._cursor < len(self._items) - 1:
            self._cursor += 1
            self._render()

    def action_select(self) -> None:
        if self._items:
            selected = self._items[self._cursor]
            self.dismiss(selected.get("path") or selected.get("filename", ""))
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------- 命令补全面板 ----------
# 所有支持的斜杠命令
SLASH_COMMANDS: dict[str, str] = {
    "/help": "查看全部斜杠命令和快捷键",
    "/new": "新建会话（清空历史、更换 ID）",
    "/resume": "从 .agents/sessions/ 恢复历史会话",
    "/save": "保存当前会话到 .agents/sessions/",
    "/clear": "清空界面显示（保留会话历史）",
    "/compact": "压缩上下文，生成摘要并归档",
    "/context": "查看当前上下文使用情况",
    "/tools": "列出所有已注册工具",
    "/skills": "发现并列出可用 Skill",
    "/mcp": "查看已连接 MCP 服务器和工具",
    "/model": "显示当前模型和 API 接口",
    "/cwd": "显示工作目录",
    "/system": "查看会话状态摘要",
    "/quit": "退出 LAgent",
}


class CommandCompletionPanel(ModalScreen[str | None]):
    """弹出命令补全面板，显示所有可用斜杠命令及描述。"""

    BINDINGS = [
        Binding("escape", "close", "关闭", show=False),
        Binding("up", "cursor_up", "上移", show=False),
        Binding("down", "cursor_down", "下移", show=False),
        Binding("enter", "select", "选中并填入", show=False),
    ]

    def __init__(self, current_input: str = ""):
        super().__init__()
        self._input = current_input
        # 根据当前输入过滤命令
        self._filtered = [
            (cmd, desc)
            for cmd, desc in SLASH_COMMANDS.items()
            if cmd.startswith(current_input.lower())
        ]
        self._cursor = 0

    def compose(self):
        from textual.containers import Vertical
        from textual.widgets import Label
        with Vertical(id="completion-panel"):
            yield Label("命令补全（↑↓ 选择，Enter 填入，Esc 关闭）", id="completion-title")
            if not self._filtered:
                yield Static("无匹配命令", classes="completion-empty")
            else:
                self._list_widget = Static("", id="completion-list", markup=False)
                yield self._list_widget

    def on_mount(self) -> None:
        if self._filtered:
            self._render()

    def _render(self) -> None:
        lines = []
        for i, (cmd, desc) in enumerate(self._filtered):
            marker = "▶ " if i == self._cursor else "  "
            lines.append(f"{marker}{cmd:<10s} {desc}")
        self._list_widget.update("\n".join(lines))

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._render()

    def action_cursor_down(self) -> None:
        if self._cursor < len(self._filtered) - 1:
            self._cursor += 1
            self._render()

    def action_select(self) -> None:
        if self._filtered:
            selected_cmd = self._filtered[self._cursor][0]
            self.dismiss(selected_cmd)
        else:
            self.action_close()

    def action_close(self) -> None:
        self.dismiss(None)
