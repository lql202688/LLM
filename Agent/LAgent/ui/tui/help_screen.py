"""帮助弹窗：输入 /help 或按 F1 打开，Esc 关闭。"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

USAGE = """\
• 在底部输入框输入问题，Enter 发送。
• 模型需要查看/修改文件时会自动调用工具，工具卡片可点击展开。
• Esc 中断当前生成；已完成的问答才会写入历史。"""

COMMANDS = [
    ("/help", "打开本帮助"),
    ("/clear", "清空界面（保留历史）"),
    ("/new", "新建会话（重置历史）"),
    ("/tools", "列出已注册工具"),
    ("/cwd", "显示工作目录"),
    ("/model", "显示模型与接口"),
    ("/quit", "退出"),
]

SHORTCUTS = [
    ("Enter", "发送消息"),
    ("Esc", "中断当前生成 / 关闭弹窗"),
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
            yield Static("命令", classes="help-section")
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
