"""联网搜索工具：通过百度千帆 AI 搜索 API 获取互联网最新信息。

实现策略：
- 在 registry 注册时一次性加载 config.ini 并缓存（工具层不应依赖 AppConfig 实例）
- 用 urllib（标准库）发起 POST 请求，避免额外依赖
- 错误统一格式化为清晰中文文本返回给 LLM，让它据此回复用户
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from ..config import DEFAULT_CONFIG_PATH, load_config as _load_config

# ---------- 模块级初始化：加载一次 config，供后续 search_web 调用 ----------
_SEARCH_API_KEY: str = ""
_SEARCH_ENDPOINT: str = ""

try:
    _cfg = _load_config(DEFAULT_CONFIG_PATH)
    _SEARCH_API_KEY = _cfg.search.api_key
    _SEARCH_ENDPOINT = _cfg.search.endpoint
except Exception:
    # 配置缺失不应影响整个应用启动，search_web 会在运行时返回错误提示
    _SEARCH_API_KEY = ""
    _SEARCH_ENDPOINT = "https://qianfan.baidubce.com/v2/ai_search/web_search"


_MAX_RESULTS_DEFAULT = 5
_TIMEOUT_SECONDS = 10


def search_web(query: str, max_results: int = _MAX_RESULTS_DEFAULT) -> str:
    """搜索互联网并返回摘要 + 参考来源。

    参数:
        query: 搜索关键词或问题（长度限制 72 字符以内）
        max_results: 最多返回多少条参考来源（默认 5，上限 10）

    返回:
        格式化的搜索结果字符串；出错时返回清晰中文错误描述。
    """
    global _SEARCH_API_KEY, _SEARCH_ENDPOINT

    if not query or not query.strip():
        return "错误：搜索 query 不能为空"

    query = query.strip()
    if len(query) > 144:  # 文档说一个汉字占 2 字符，限制 72 字符 ≈ 144 字节
        query = query[:144]

    # 1. 配置检查
    if not _SEARCH_API_KEY:
        return (
            "搜索功能未启用：config.ini 的 [search] 小节缺少 baidu_qianfan_api_key。"
            "请配置百度千帆 API Key 后重试。"
        )

    try:
        max_n = max(1, min(int(max_results), 10))
    except (TypeError, ValueError):
        max_n = _MAX_RESULTS_DEFAULT

    # 2. 构造请求体
    body = {
        "messages": [{"role": "user", "content": query}],
        "search_source": "baidu_search_v2",
        "edition": "standard",
        "resource_type_filter": [
            {"type": "web", "top_k": max_n},
            {"type": "video", "top_k": 0},
            {"type": "image", "top_k": 0},
            {"type": "aladdin", "top_k": 0},
        ],
    }

    headers = {
        "Authorization": f"Bearer {_SEARCH_API_KEY}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(_SEARCH_ENDPOINT, data=data, headers=headers)

    # 3. 发起请求（带超时）
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        return (
            f"搜索 API 返回 HTTP {e.code} {e.reason}\n"
            f"响应体: {err_body[:500]}\n"
            f"（query={query!r}）"
        )
    except urllib.error.URLError as e:
        return f"搜索 API 网络请求失败: {e.reason}（query={query!r}）"
    except Exception as e:
        return f"搜索请求异常: {e}（query={query!r}）"

    elapsed_ms = int((time.perf_counter() - start) * 1000)

    # 4. 解析响应
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        return f"搜索 API 返回了无法解析的 JSON: {e}\n原始响应前 500 字符: {raw[:500]}"

    # 错误码处理
    if "code" in payload and "message" in payload:
        code = payload.get("code")
        msg = payload.get("message")
        return f"搜索 API 错误 code={code}: {msg}"

    references = payload.get("references") or []
    if not references:
        return f"搜索未返回任何结果（query={query!r}，耗时 {elapsed_ms} ms）"

    # 5. 格式化输出
    lines = [f"搜索结果（共 {len(references)} 条，耗时 {elapsed_ms} ms）："]
    for i, ref in enumerate(references, 1):
        title = (ref.get("title") or ref.get("web_anchor") or "(无标题)").strip()
        url = ref.get("url") or ""
        snippet = (ref.get("snippet") or ref.get("content") or "").strip()
        date = ref.get("date") or ""
        website = ref.get("website") or ""

        header_parts = [f"[{i}]"]
        if title:
            header_parts.append(title)
        if website:
            header_parts.append(f"— {website}")
        if date:
            header_parts.append(f"（{date}）")
        lines.append(" ".join(header_parts))

        if snippet:
            # 摘要过长时截断，避免 tool 消息膨胀
            if len(snippet) > 300:
                snippet = snippet[:300] + "…"
            lines.append(f"  摘要: {_clean_ws(snippet)}")

        if url:
            lines.append(f"  来源: {url}")
        lines.append("")

    return "\n".join(lines).rstrip()


def _clean_ws(text: str) -> str:
    """把连续空白（含换页符/制表符/多空格）压成单空格，保持摘要一行可读。"""
    import re

    return re.sub(r"[\s\u0000-\u001f]+", " ", text).strip()
