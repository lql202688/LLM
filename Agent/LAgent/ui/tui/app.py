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
from core.tools.registry import TOOL_SCHEMAS, _MCP_SCHEMAS, get_all_schemas
from ui.base import AgentUI
from ui.tui.help_screen import HelpScreen
from ui.tui.widgets import (
    AssistantMessage,
    CommandCompletionPanel,
    PromptTextArea,
    ResumeScreen,
    Sidebar,
    Spinner,
    StatusBar,
    ToolCallCard,
    TurnLabel,
    UserMessage,
)

INPUT_HINT = (
    "Enter 发送   Shift+Enter 换行   /help 命令   Tab 补全   Esc 中断   "
    "/compact 压缩上下文"
)


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
        self._mcp_thread = None

    # ---------- MCP Loader ----------
    def set_mcp_loader(self, thread) -> None:
        """接收 main.py 传来的 MCP 后台线程引用。"""
        self._mcp_thread = thread

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

        # 监控 MCP 异步加载完成（每 300ms 检查一次）
        if self._mcp_thread is not None or self.session._mcp_configs:
            self._mcp_timer = self.set_interval(0.3, self._check_mcp_ready)
        else:
            self._mcp_timer = None

    def _check_mcp_ready(self) -> None:
        if self.session.mcp_loaded:
            self._sidebar().refresh_mcp()
            self._sidebar().update_status("就绪")
            self._statusbar().update_state()
            servers = self.session.mcp_servers
            if servers:
                self._system_note(
                    f"✓ MCP 已连接 {len(servers)} 个 Server：{', '.join(servers)}，"
                    f"共 {self.session.mcp_tools_count} 个 MCP 工具可用",
                    classes="msg system",
                )
            self._sidebar().refresh_mcp()
            # 停止定时器
            if self._mcp_timer is not None:
                self._mcp_timer.stop()
                self._mcp_timer = None

    def _mount_banner(self) -> None:
        total_tools = len(get_all_schemas())
        banner = (
            f"LAgent  ·  {total_tools} 个工具（含 MCP） ·  {self.session.model}\n"
            f"工作目录  {os.getcwd()}\n"
            f"Enter 发送   Shift+Enter 换行   /help 命令   /resume 恢复会话   Esc 中断   Ctrl+N 新会话"
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
        if self.session.compressing:
            self._system_note("正在执行上下文压缩，请稍候…", classes="msg hint")
            return
        if self.session.context_locked:
            self._system_note(
                "⚠ 上下文已达上限 80%，请先执行 /compact 命令压缩后再继续对话。",
                classes="msg error",
            )
            return

        self._tool_calls = 0
        self._turn_started = time.perf_counter()
        self._mount(TurnLabel("你"))
        self._mount(UserMessage(text))
        self._close_assistant_block()
        self._set_status("生成中")
        self._set_spinner(True)
        self._run_agent(text)

    def on_key(self, event) -> None:
        """全局按键拦截：Tab 触发命令补全（仅当输入框里有 / 开头的内容时）。"""
        if event.key == "tab" and not self._busy:
            prompt = self._prompt()
            text = prompt.text.strip()
            if text.startswith("/"):
                self.push_screen(CommandCompletionPanel(text), self._on_completion_result)
                event.prevent_default()
                event.stop()

    def _on_completion_result(self, selected_cmd: str | None) -> None:
        if selected_cmd:
            self._prompt().text = selected_cmd + " "
            self._prompt().focus()

    # ---------- 斜杠命令 ----------
    def _run_slash_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/help":
            self.push_screen(HelpScreen())
        elif cmd == "/clear":
            self.action_clear_chat()
        elif cmd == "/new":
            self._confirm_new_session()
        elif cmd == "/resume":
            self._run_resume_command()
        elif cmd == "/save":
            self._run_save_command(arg)
        elif cmd == "/quit":
            self.exit()
        elif cmd == "/compact":
            self._run_compact_command()
        elif cmd == "/context":
            self._show_context_info()
        elif cmd == "/tools":
            self._show_tools_info()
        elif cmd == "/skills":
            self._show_skills_info()
        elif cmd == "/mcp":
            self._show_mcp_info()
        elif cmd == "/model":
            self._show_model_info()
        elif cmd == "/cwd":
            self._system_note(f"工作目录：{os.getcwd()}")
        elif cmd == "/system":
            self._show_system_info()
        else:
            from ui.tui.widgets import SLASH_COMMANDS
            hint = SLASH_COMMANDS.get(cmd, None)
            if hint:
                self._system_note(f"{cmd} — {hint}")
            else:
                self._system_note(f"未知命令：{cmd}（输入 /help 或 Tab 补全查看全部命令）")

    def _confirm_new_session(self) -> None:
        """新会话：先自动保存当前会话，再重置。"""
        try:
            path = self.session.save_session()
            self._system_note(f"✓ 当前会话已自动保存到 {path}")
        except Exception as e:
            self._system_note(f"⚠ 自动保存失败：{e}", classes="msg hint")
        self.session.reset()
        self.action_clear_chat()
        self._sidebar().update_status("就绪")
        self._statusbar().update_state()
        self._system_note(f"✓ 已开启新会话（{self.session.session_id}）")

    def _run_resume_command(self) -> None:
        """从历史会话列表恢复。"""
        if self._busy:
            self._system_note("模型正在响应中，请先等待或按 Esc 中断", classes="msg hint")
            return
        self.push_screen(ResumeScreen(self.session), self._on_resume_result)

    def _on_resume_result(self, path_or_name: str | None) -> None:
        if not path_or_name:
            return
        # 自动保存当前会话
        try:
            self.session.save_session()
        except Exception:
            pass
        # 恢复
        success = self.session.resume_session(path_or_name)
        if success:
            self.action_clear_chat()
            self._sidebar().update_status("就绪")
            self._statusbar().update_state()
            self._system_note(
                f"✓ 已恢复会话 {self.session.session_id}（{len(self.session.messages)} 条消息）"
            )
        else:
            self._system_note(f"✗ 恢复失败，请检查会话文件", classes="msg error")

    def _run_save_command(self, name: str) -> None:
        try:
            path = self.session.save_session(name.strip() or None)
            self._system_note(f"✓ 会话已保存到 {path}")
        except Exception as e:
            self._system_note(f"✗ 保存失败：{e}", classes="msg error")

    def _show_context_info(self) -> None:
        s = self.session
        used_k = s.context_usage_tokens / 1024
        win_k = s.context_window / 1024
        tag = "（默认）" if s.context_window_is_default else ""
        self._system_note(
            f"上下文占用：{used_k:.1f}k / {win_k:.0f}k{tag}\n"
            f"使用率：{s.context_usage_ratio * 100:.1f}%\n"
            f"消息数：{len(s.messages)}"
        )

    def _show_tools_info(self) -> None:
        all_schemas = get_all_schemas()
        local = len(TOOL_SCHEMAS)
        mcp = len(_MCP_SCHEMAS)
        names = "\n".join(f"• {s['function']['name']}" for s in all_schemas)
        self._system_note(
            f"已注册 {len(all_schemas)} 个工具（本地 {local} + MCP {mcp}）：\n{names}"
        )

    def _show_skills_info(self) -> None:
        s = self.session
        if s.skills and len(s.skills) > 0:
            names = "\n".join(f"• {sk.name} — {sk.description}" for sk in s.skills)
            self._system_note(f"发现 {len(s.skills)} 个 Skill：\n{names}")
        else:
            self._system_note("（未发现 Skill，Skill 放在 .agents/skills/<name>/SKILL.md）")

    def _show_mcp_info(self) -> None:
        s = self.session
        if not s._mcp_configs:
            self._system_note("未配置 MCP（.mcp.json 不存在）")
            return
        if not s.mcp_loaded:
            self._system_note("MCP 正在加载中，请稍候…")
            return
        servers = s.mcp_servers
        if not servers:
            self._system_note("MCP 连接失败（见侧边栏详情）")
            return
        lines = [f"已连接 {len(servers)} 个 Server："]
        for name in servers:
            lines.append(f"  • {name}")
        lines.append(f"\n共 {s.mcp_tools_count} 个 MCP 工具可用")
        if _MCP_SCHEMAS:
            lines.append("\n工具列表：")
            for schema in _MCP_SCHEMAS:
                fn = schema["function"]
                lines.append(f"  • {fn['name']} — {fn['description'][:40]}")
        self._system_note("\n".join(lines))

    def _show_model_info(self) -> None:
        self._system_note(
            f"模型：{self.session.model}\n接口：{self.session.base_url}"
        )

    def _show_system_info(self) -> None:
        s = self.session
        sessions_count = len(s.list_sessions())
        agents_status = "已注入" if s.agents_loaded else "未检测到"
        self._system_note(
            f"会话 ID: {s.session_id}\n"
            f"工作目录: {os.getcwd()}\n"
            f"已保存会话: {sessions_count} 个\n"
            f"AGENTS.md: {agents_status}\n"
            f"MCP Server: {len(s.mcp_servers)} 个已连接\n"
            f"工具总数: {len(get_all_schemas())}"
        )

    def _run_compact_command(self) -> None:
        """触发上下文压缩。"""
        if self._busy:
            self._system_note("模型正在响应中，请先等待或按 Esc 中断", classes="msg hint")
            return
        if self.session.compressing:
            self._system_note("已经在执行压缩，请勿重复触发", classes="msg hint")
            return
        if len(self.session.messages) <= 1:
            self._system_note("历史消息过少，不需要压缩", classes="msg hint")
            return
        self._busy = True
        self._tool_calls = 0
        self._turn_started = time.perf_counter()
        self._set_status("压缩中")
        self._set_spinner(True)
        self._compact_worker()

    # ---------- 快捷键动作 ----------
    def action_clear_chat(self) -> None:
        self._chat().remove_children()
        self._mount_banner()
        self._close_assistant_block()

    def action_new_session(self) -> None:
        self._confirm_new_session()

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

    @work(thread=True)
    def _compact_worker(self) -> None:
        self._busy = True
        try:
            self.session.compact(self._emit_from_worker)
        finally:
            self._busy = False
            self.call_from_thread(self._set_spinner, False)

    def _emit_from_worker(self, event: AgentEvent) -> None:
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
        elif event.type == EventType.CONTEXT_WARN:
            ratio_pct = p["ratio"] * 100
            self._system_note(
                f"⚠ 上下文使用率已达 {ratio_pct:.0f}%（{p['tokens']/1024:.1f}k / {p['window']/1024:.0f}k），"
                f"已锁定输入。请执行 /compact 压缩上下文后继续。",
                classes="msg error",
            )
        elif event.type == EventType.COMPACT_START:
            self._system_note(
                f"▶ 开始压缩上下文，原始约 {p['original_tokens']/1024:.1f}k tokens…",
                classes="msg system",
            )
        elif event.type == EventType.COMPACT_DONE:
            self._system_note(
                f"✓ 压缩完成！归档编号 `{p['archive_id']}`，"
                f"{p['original_tokens']/1024:.1f}k → {p['summary_tokens']/1024:.1f}k tokens。"
                f"原始上下文已保存到 .agents/context/{p['archive_id']}.jsonl",
                classes="msg system",
            )
        elif event.type == EventType.COMPACT_FAILED:
            self._system_note(
                f"✗ 压缩失败：{p['message']}", classes="msg error"
            )
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
