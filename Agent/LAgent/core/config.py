"""配置加载：从 config.ini 读取所有运行参数，输出不可变配置对象。

支持首次启动自动生成模板 config.ini（含完整 mock 信息），
并在 UI 层弹出对话框提示用户修改后重启。
"""
from __future__ import annotations

import configparser
import sys
from dataclasses import dataclass
from pathlib import Path


# ---------- 默认配置模板 ----------
DEFAULT_CONFIG_TEMPLATE = """\
# ============================================================
# LAgent 配置文件
# 此文件由 LAgent 首次启动时自动生成，含 mock 信息。
# 请修改 api_key、base_url、model 等必要配置后重新启动。
# ============================================================

[llm]
# 必填：API Base URL
base_url = https://api.deepseek.com/v1
# 必填：API Key（mock，请替换）
api_key = sk-REPLACE_WITH_YOUR_API_KEY
# 必填：模型名称
model = deepseek-chat
# 可选：上下文窗口大小（Token 数），0 表示使用默认 128000
context_window = 0
# 可选：单轮最大模型调用次数（防止死循环）
max_model_calls_per_turn = 8
# 可选：HTTP 请求超时秒数
request_timeout = 30

[search]
# 百度千帆 AI 搜索 API Key（可选）
baidu_qianfan_api_key =
# 百度千帆 AI 搜索 API 端点
endpoint = https://qianfan.baidubce.com/v2/ai_search/web_search

[context]
# 上下文压缩触发阈值（使用率）
compact_threshold = 0.80
# 压缩摘要目标 Token 数
target_compact_tokens = 5000

[mcp]
# MCP 服务器连接超时（秒）
connect_timeout = 15
# MCP 工具调用超时（秒）
call_timeout = 30
"""


def _default_config_path() -> Path:
    """frozen 环境读 exe 同级目录的 config.ini；源码环境读包根目录的 config.ini。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "config.ini"
    return Path(__file__).resolve().parent.parent / "config.ini"


DEFAULT_CONFIG_PATH = _default_config_path()


def ensure_config_exists(path: Path | None = None) -> bool:
    """确保配置文件存在；如果不存在则从模板创建。

    Returns:
        True 表示本次调用创建了新文件（首次启动），
        False 表示文件已存在。
    """
    config_path = path or DEFAULT_CONFIG_PATH
    if config_path.is_file():
        return False

    # 自动创建模板
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    return True


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    context_window: int = 0  # 0 表示未配置，由运行时回退到 DEFAULT_CONTEXT_WINDOW
    max_model_calls_per_turn: int = 8
    request_timeout: int = 30

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url}/chat/completions"


@dataclass(frozen=True)
class SearchConfig:
    """百度千帆 AI 搜索 API 配置。api_key 缺失时 enabled=False。"""

    api_key: str
    endpoint: str = "https://qianfan.baidubce.com/v2/ai_search/web_search"

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.api_key.strip())


@dataclass(frozen=True)
class ContextConfig:
    """上下文管理相关配置。"""

    compact_threshold: float = 0.80
    target_compact_tokens: int = 5000


@dataclass(frozen=True)
class MCPConfig:
    """MCP 服务器连接相关配置。"""

    connect_timeout: int = 15
    call_timeout: int = 30


@dataclass(frozen=True)
class AppConfig:
    llm: LLMConfig
    search: SearchConfig
    context: ContextConfig
    mcp: MCPConfig


def load_config(path: str | Path | None = None) -> AppConfig:
    """加载配置文件；path 为空时使用包根目录下的 config.ini。"""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    parser = configparser.ConfigParser()
    if not parser.read(config_path, encoding="utf-8"):
        raise FileNotFoundError(f"配置文件不存在或不可读: {config_path}")

    # ---------- [llm] ----------
    llm_section = parser["llm"]
    llm = LLMConfig(
        base_url=llm_section.get("base_url", "").rstrip("/"),
        api_key=llm_section.get("api_key", "").strip(),
        model=llm_section.get("model", "").strip(),
        context_window=llm_section.getint("context_window", fallback=0),
        max_model_calls_per_turn=llm_section.getint("max_model_calls_per_turn", fallback=8),
        request_timeout=llm_section.getint("request_timeout", fallback=30),
    )

    # ---------- [search] ----------
    search_section = parser["search"] if parser.has_section("search") else {}
    search = SearchConfig(
        api_key=search_section.get("baidu_qianfan_api_key", "").strip(),
        endpoint=search_section.get(
            "endpoint",
            "https://qianfan.baidubce.com/v2/ai_search/web_search",
        ).strip(),
    )

    # ---------- [context] ----------
    if parser.has_section("context"):
        ctx_section = parser["context"]
        context = ContextConfig(
            compact_threshold=ctx_section.getfloat("compact_threshold", fallback=0.80),
            target_compact_tokens=ctx_section.getint("target_compact_tokens", fallback=5000),
        )
    else:
        context = ContextConfig()

    # ---------- [mcp] ----------
    if parser.has_section("mcp"):
        mcp_section = parser["mcp"]
        mcp = MCPConfig(
            connect_timeout=mcp_section.getint("connect_timeout", fallback=15),
            call_timeout=mcp_section.getint("call_timeout", fallback=30),
        )
    else:
        mcp = MCPConfig()

    return AppConfig(llm=llm, search=search, context=context, mcp=mcp)
