"""帮助弹窗：输入 /help 或按 F1 打开，Esc 关闭。"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

USAGE = """\
• 在底部输入框输入问题，Enter 发送。
• Tab 键弹出命令补全面板（仅在输入以 / 开头时有效）。
• 模型需要查看/修改文件时会自动调用工具，工具卡片可点击展开。
• Esc 中断当前生成；已完成的问答才会写入历史。
• /new 自动保存当前会话，/resume 可从历史恢复。"""

COMMANDS = [
    ("/help", "打开本帮助"),
    ("/new", "新建会话（自动保存当前，重置历史）"),
    ("/resume", "从 .agents/sessions/ 恢复历史会话"),
    ("/save", "保存当前会话（可加名称参数）"),
    ("/clear", "清空界面（保留历史与会话）"),
    ("/compact", "压缩上下文，生成摘要并归档原始历史"),
    ("/context", "查看当前上下文使用情况"),
    ("/tools", "列出所有已注册工具（含 MCP）"),
    ("/skills", "发现并列出项目可用 Skill"),
    ("/mcp", "查看已连接 MCP 服务器和工具详情"),
    ("/model", "显示当前模型和 API 接口"),
    ("/cwd", "显示工作目录"),
    ("/system", "会话状态摘要"),
    ("/quit", "退出 LAgent"),
]

SHORTCUTS = [
    ("Enter", "发送消息"),
    ("Shift+Enter", "输入框内换行"),
    ("Tab", "命令补全（/ 开头时）"),
    ("Esc", "中断生成 / 关闭弹窗"),
    ("Ctrl+Q", "退出"),
    ("Ctrl+L", "清屏"),
    ("Ctrl+N", "新会话"),
    ("Ctrl+B", "显示/隐藏侧栏"),
    ("F1", "帮助"),
]


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "关闭", show=False)]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help"):
            yield Static("LAgent 帮助", id="help-title")
            yield Static("使用方式", classes="help-section")
            yield Static(USAGE, markup=False)
            yield Static("命令（共 14 个）", classes="help-section")
            yield self._table(("命令", "作用"), COMMANDS)
            yield Static("快捷键", classes="help-section")
            yield self._table(("按键", "作用"), SHORTCUTS)

    @staticmethod
    def _table(headers: tuple[str, str], rows: list[tuple[str, str]]) -> DataTable:
        table = DataTable(show_cursor=False, zebra_stripes=True)
        table.add_columns(*headers)
        table.add_rows(rows)
        table.styles.height = len(rows) + 2
        return table

    def action_close(self) -> None:
        self.dismiss(None)
