"""LAgent 入口：装配配置与会话，按 --ui 参数启动对应界面。

用法：
    python main.py            # 默认 TUI
    python main.py --ui tui   # Textual 终端界面
    python main.py --ui cli   # 命令行界面（无依赖回退）

扩展新界面（web / desktop）时：
1. 在 ui/ 下新建实现并继承 ui.base.AgentUI；
2. 在 UI_REGISTRY 注册名称；core 层与装配逻辑无需改动。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许以脚本方式直接运行：把包根目录加入模块搜索路径
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.agent import AgentSession
from core.config import load_config
from ui.base import AgentUI
from ui.cli import ConsoleUI

UI_REGISTRY: dict[str, str] = {
    "cli": "ui.cli:ConsoleUI",
    "tui": "ui.tui.app:ChatApp",
}


def resolve_ui(name: str) -> type[AgentUI]:
    """惰性导入界面实现，避免启动 cli 时强依赖 textual。"""
    if name == "cli":
        return ConsoleUI
    if name == "tui":
        from ui.tui.app import ChatApp

        return ChatApp
    raise ValueError(f"未注册的界面: {name}（可选: {', '.join(UI_REGISTRY)}）")


def main() -> None:
    parser = argparse.ArgumentParser(description="LAgent 文件操作助手")
    parser.add_argument(
        "--ui",
        choices=sorted(UI_REGISTRY),
        default="tui",
        help="选择界面实现（默认 tui）",
    )
    parser.add_argument("--config", default=None, help="配置文件路径（默认包内 config.ini）")
    args = parser.parse_args()

    config = load_config(args.config)
    session = AgentSession(config)
    ui_cls = resolve_ui(args.ui)
    ui_cls(session).run()


if __name__ == "__main__":
    main()
