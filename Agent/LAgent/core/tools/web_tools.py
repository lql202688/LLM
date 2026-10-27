"""网页正文提取工具：curl 抓 bytes + 智能解码 + trafilatura 抽 Markdown。

修复：
1. 抓 bytes 模式（不用 text=True），避免系统默认编码（Windows 中文=GBK）解 UTF-8 HTML 导致 BOM/中文损坏
2. 智能解码：先看 BOM → 再从 Content-Type / <meta charset> 猜 → 最后 UTF-8 兜底
3. curl 加 --compressed，兼容 gzip/deflate 压缩响应
4. trafilatura 提取失败时，用 BeautifulSoup 降级（抽 <title> + <meta description> + 所有 <p>）
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request

# ---------- 可选依赖检测 ----------
try:
    import trafilatura

    _TRAFILATURA_OK = True
except ImportError:
    _TRAFILATURA_OK = False

try:
    from bs4 import BeautifulSoup

    _BS4_OK = True
except ImportError:
    _BS4_OK = False


_CURL_TIMEOUT_SECONDS = 15
_MAX_CONTENT_LENGTH = 8000  # 正文截断上限（字符）


def fetch_web(url: str, max_length: int = _MAX_CONTENT_LENGTH) -> str:
    """抓取网页正文并转为 Markdown 返回。"""
    if not url or not url.strip():
        return "错误：url 不能为空"
    url = url.strip()

    # 1. URL 格式校验
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return f"错误：不支持的 URL 格式 — {url!r}（仅支持 http/https）"
    except Exception as e:
        return f"错误：URL 解析失败 — {e}"

    if not _TRAFILATURA_OK:
        return (
            "错误：缺少 trafilatura 库（用于提取网页正文）。"
            "请运行 `pip install trafilatura beautifulsoup4` 后重试。"
        )

    curl_path = shutil.which("curl")
    if not curl_path:
        return "错误：系统未找到 curl 可执行文件。"

    # 2. 构造 curl 参数 —— 关键：不要用 text=True，我们自己按正确编码解码
    cmd = [
        curl_path,
        "-sSL",              # silent + follow redirects
        "--compressed",       # 自动解压 gzip/deflate
        "-D", "-",           # 把响应头也打到 stdout（用 \r\n\r\n 分隔 body）
        "--max-time",
        str(_CURL_TIMEOUT_SECONDS),
        "-H",
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "-H",
        "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "-H",
        "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
        url,
    ]

    start = time.perf_counter()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=_CURL_TIMEOUT_SECONDS + 5,
        )
    except subprocess.TimeoutExpired:
        return f"抓取超时（{_CURL_TIMEOUT_SECONDS}s）：{url}"
    except OSError as e:
        return f"curl 进程启动失败：{e}"

    elapsed_ms = int((time.perf_counter() - start) * 1000)

    if result.returncode != 0:
        stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()[:300]
        return f"curl 抓取失败（exit {result.returncode}）：{url}\nstderr: {stderr}"

    # 3. 分离 headers bytes 和 body bytes
    raw = result.stdout or b""
    # headers 以 \r\n\r\n 或 \n\n 与 body 分隔（因为 -D -）
    sep_crlf = b"\r\n\r\n"
    sep_lf = b"\n\n"
    if sep_crlf in raw:
        headers_bytes, body_bytes = raw.split(sep_crlf, 1)
    elif sep_lf in raw:
        headers_bytes, body_bytes = raw.split(sep_lf, 1)
    else:
        headers_bytes, body_bytes = b"", raw

    # 4. 智能解码 —— 先从 headers + HTML meta 猜 charset
    charset = _detect_charset(headers_bytes, body_bytes)
    try:
        html = body_bytes.decode(charset, errors="replace")
    except LookupError:
        html = body_bytes.decode("utf-8", errors="replace")

    # 5. 去掉 BOM（\ufeff），避免 trafilatura 把它当正文一部分
    html = html.lstrip("\ufeff").lstrip("\ufeff")  # 可能残留
    html = re.sub(r"^\uFEFF+", "", html)

    if not html.strip():
        return f"抓取成功但页面为空（{elapsed_ms} ms）：{url}"

    # 6. trafilatura 提取 Markdown
    extracted = None
    errors = []
    try:
        extracted = trafilatura.extract(
            html,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            deduplicate=True,
            favor_precision=True,
            url=url,
        )
    except Exception as e:
        errors.append(f"trafilatura-md: {e}")

    if not extracted and _BS4_OK:
        # 降级：BeautifulSoup 抽纯文本
        try:
            extracted = _bs4_extract(html)
        except Exception as e:
            errors.append(f"bs4: {e}")

    if not extracted:
        err_tail = (" / ".join(errors)[:200]) if errors else ""
        return (
            f"网页正文无法提取（可能是 JS 动态渲染、反爬或异常编码）：{url}\n"
            f"HTML 长度: {len(html)} 字符，charset 识别为 {charset}，耗时 {elapsed_ms} ms"
            + (f"\n内部错误: {err_tail}" if err_tail else "")
        )

    # 7. 截断
    trimmed = False
    content = extracted
    if len(content) > max_length:
        content = content[:max_length].rstrip() + "\n\n…（内容已截断，原文过长）"
        trimmed = True

    # 8. 组装返回
    meta_lines = [
        f"原始 URL: {url}",
        f"抓取耗时: {elapsed_ms} ms",
        f"正文长度: {len(extracted)} 字符（{'已截断' if trimmed else '完整返回'}）",
    ]
    return "\n".join(meta_lines) + "\n" + "=" * 40 + "\n" + content


# ---------- 辅助函数 ----------
_CHARSET_RE = re.compile(r"<meta\s+[^>]*?charset\s*=\s*[\"']?\s*([^\"'>\s]+)", re.IGNORECASE)
_CONTENT_TYPE_RE = re.compile(r"charset=([\w\-]+)", re.IGNORECASE)


def _detect_charset(headers_bytes: bytes, body_bytes: bytes) -> str:
    """识别页面字符集：BOM → Content-Type → meta charset → UTF-8 兜底"""
    # BOM（最高优先级，最可靠）
    if body_bytes.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if body_bytes.startswith(b"\xff\xfe") or body_bytes.startswith(b"\xfe\xff"):
        return "utf-16"
    if body_bytes.startswith(b"\x80") or body_bytes.startswith(b"\x81"):
        return "gb18030"  # GBK 超集，兼容中文 Windows 站点

    # headers Content-Type
    try:
        headers_text = headers_bytes.decode("ascii", errors="replace")
        for line in headers_text.split("\r\n"):
            if line.lower().startswith("content-type:"):
                m = _CONTENT_TYPE_RE.search(line)
                if m:
                    cs = m.group(1).strip().strip('"').strip("'").lower()
                    if cs:
                        return _normalize_charset(cs)
    except Exception:
        pass

    # HTML <meta charset>
    head_bytes = body_bytes[:8192]  # charset 肯定在 head 里，不用看全文
    try:
        head_text = head_bytes.decode("ascii", errors="replace")
        m = _CHARSET_RE.search(head_text)
        if m:
            cs = m.group(1).strip().lower()
            return _normalize_charset(cs)
    except Exception:
        pass

    # 无 BOM 也无声明 → 尝试 UTF-8（假设正确），解码失败会被 errors="replace" 兜底
    return "utf-8"


def _normalize_charset(name: str) -> str:
    """把 Web 常见别名映射到 Python codec 名"""
    aliases = {
        "utf8": "utf-8",
        "utf8mb4": "utf-8",
        "gbk": "gbk",
        "gb2312": "gbk",
        "gb18030": "gb18030",
        "big5": "big5",
        "latin1": "iso-8859-1",
        "iso-8859-1": "iso-8859-1",
        "cp1252": "cp1252",
        "euckr": "euc_kr",
        "eucjp": "euc_jp",
    }
    return aliases.get(name, name)


def _bs4_extract(html: str) -> str:
    """BeautifulSoup 降级提取：title + meta description + 所有 <p> 文本"""
    soup = BeautifulSoup(html, "html.parser")

    # 移除 script/style 节点
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()

    lines = []
    title = soup.title.get_text(strip=True) if soup.title else ""
    if title:
        lines.append(f"# {title}")

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        lines.append(meta_desc["content"].strip())

    # 所有段落
    for p in soup.find_all(["p", "article", "section"]):
        text = p.get_text(" ", strip=True)
        if len(text) > 20:  # 过滤掉导航按钮的短文本
            lines.append(text)

    return "\n\n".join(lines)
