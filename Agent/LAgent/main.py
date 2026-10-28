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

# ---------- PyInstaller frozen 环境 & 源码环境兼容 ----------
# frozen: PyInstaller 把所有模块收进 PYZ archive，直接 import 即可，
# sys._MEIPASS 指向临时解压目录（数据文件用）。
# 源码: 脚本方式运行时把包根目录加入 sys.path。
if getattr(sys, "frozen", False):
    # PyInstaller onedir/onefile：不用改 sys.path，import 直接工作
    _APP_DIR = Path(sys.executable).resolve().parent
else:
    _APP_DIR = Path(__file__).resolve().parent
    sys.path.insert(0, str(_APP_DIR))

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
    parser.add_argument(
        "--cwd",
        default=None,
        help="项目根目录（默认当前工作目录），该目录下的 .agents/skills/ 会被扫描为可用 Skill",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    # project_root: 用户显式 --cwd > os.getcwd()（脚本/双击启动时都对）
    # Skill 扫描、数据文件路径等都从此开始
    project_root = Path(args.cwd).resolve() if args.cwd else Path.cwd()

    session = AgentSession(config, project_root=project_root)
    ui_cls = resolve_ui(args.ui)
    ui_cls(session).run()


if __name__ == "__main__":
    main()
