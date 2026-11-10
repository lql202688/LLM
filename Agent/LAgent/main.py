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
if getattr(sys, "frozen", False):
    _APP_DIR = Path(sys.executable).resolve().parent
else:
    _APP_DIR = Path(__file__).resolve().parent
    sys.path.insert(0, str(_APP_DIR))

from core.agent import AgentSession
from core.config import DEFAULT_CONFIG_PATH, ensure_config_exists, load_config
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


def _enable_windows_vt() -> None:
    """在 Windows 旧控制台里打开 ANSI 转义序列解析。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


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

    _enable_windows_vt()

    # ---------- 1. 确保 config.ini 存在 ----------
    config_path = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
    first_run = ensure_config_exists(config_path)

    if first_run:
        print("=" * 60)
        print("  LAgent 首次启动检测")
        print("=" * 60)
        print(f"\n✓ 已自动创建配置文件: {config_path}")
        print("\n⚠ 配置文件中包含 mock API Key，请修改后重新启动。")
        print("  需要修改的关键项：")
        print("    [llm] api_key     - 你的 API Key")
        print("    [llm] model       - 模型名称")
        print("    [llm] base_url    - API 端点")
        print("\n按 Enter 继续（将使用 mock 配置启动，功能受限）...")
        try:
            if args.ui == "cli":
                input()  # CLI 模式等待用户确认
        except Exception:
            pass

    # ---------- 2. 加载配置 ----------
    try:
        config = load_config(config_path)
    except Exception as e:
        print(f"\n✗ 配置文件加载失败: {e}")
        print(f"  请检查 {config_path} 的格式是否正确。")
        if first_run:
            print("\n  这是首次运行，配置文件刚刚创建，请先填写有效配置后再启动。")
        sys.exit(1)

    # ---------- 3. 项目根目录 ----------
    project_root = Path(args.cwd).resolve() if args.cwd else Path.cwd()

    # ---------- 4. 创建会话 ----------
    session = AgentSession(config, project_root=project_root)

    # ---------- 5. 启动 MCP 异步加载（在 UI 渲染前启动，UI 侧监控完成信号） ----------
    mcp_thread = session.start_mcp_loading()
    if mcp_thread:
        # 标记为可监控的后台线程
        pass

    # ---------- 6. 启动 UI ----------
    ui_cls = resolve_ui(args.ui)
    ui = ui_cls(session)
    # 把 MCP 线程引用传给 UI，让它能够监控加载状态
    if hasattr(ui, "set_mcp_loader"):
        ui.set_mcp_loader(mcp_thread)
    ui.run()


if __name__ == "__main__":
    main()
