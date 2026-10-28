"""配置加载：从 config.ini 读取 LLM / 搜索等连接信息，输出不可变配置对象。"""
from __future__ import annotations

import configparser
import sys
from dataclasses import dataclass
from pathlib import Path


def _default_config_path() -> Path:
    """frozen 环境读 exe 同级目录的 config.ini；源码环境读包根目录的 config.ini。"""
    if getattr(sys, "frozen", False):
        # PyInstaller onedir / onefile：config.ini 跟 exe 放一起
        return Path(sys.executable).resolve().parent / "config.ini"
    return Path(__file__).resolve().parent.parent / "config.ini"


DEFAULT_CONFIG_PATH = _default_config_path()


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str

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
class AppConfig:
    llm: LLMConfig
    search: SearchConfig


def load_config(path: str | Path | None = None) -> AppConfig:
    """加载配置文件；path 为空时使用包根目录下的 config.ini。"""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    parser = configparser.ConfigParser()
    if not parser.read(config_path, encoding="utf-8"):
        raise FileNotFoundError(f"配置文件不存在或不可读: {config_path}")

    llm = parser["llm"]
    search_section = parser["search"] if parser.has_section("search") else {}
    search = SearchConfig(
        api_key=search_section.get("baidu_qianfan_api_key", "").strip()
    )

    return AppConfig(
        llm=LLMConfig(
            base_url=llm["base_url"].rstrip("/"),
            api_key=llm["api_key"],
            model=llm["model"],
        ),
        search=search,
    )
