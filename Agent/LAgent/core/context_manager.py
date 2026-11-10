"""上下文管理：Token 估算、历史归档、LLM 压缩、本地关键词检索。

所有函数都是纯函数或接收最小依赖（LLMClient 实例），
便于 AgentSession 和 recallContext 工具独立调用。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

# ---------- 常量 ----------
DEFAULT_CONTEXT_WINDOW = 128_000        # 模型默认上下文窗口（config.ini 未配置时使用）
DEFAULT_COMPACT_THRESHOLD_RATIO = 0.80   # 触发警告/暂停的使用比例（config.ini 默认值）
DEFAULT_TARGET_COMPACT_TOKENS = 5_000    # 压缩摘要目标 Token 数（config.ini 默认值）
ARCHIVE_DIRNAME = ".agents/context"       # 归档相对目录

# ---------- 压缩专用 Prompt ----------
COMPACT_SYSTEM_PROMPT = """你是一名 AI Agent 的上下文管理助手。当前会话即将达到上下文窗口限制，需要将历史对话压缩为一份高质量的工作摘要，以便 Agent 在新的上下文中继续执行任务。

请认真阅读提供的历史上下文，提取对后续工作仍然有价值的信息，并生成一份约 5,000 Tokens 的 Markdown 摘要。

摘要应重点保留以下内容：

1. **用户目标与要求**：用户希望完成什么任务？有哪些必须遵守的约束、偏好和验收条件？
2. **已经完成的工作**：哪些任务已经完成？实现了哪些功能？获得了哪些已经验证的结果？
3. **当前工作状态**：Agent 正在处理什么任务？当前进展到哪一步？是否存在尚未解决的问题？
4. **关键技术信息**：保留重要的文件路径、函数名称、接口定义、配置项、执行命令、错误信息和必要的代码片段。
5. **关键决策与原因**：记录用户和 Agent 已经确定的技术方案，以及重要的设计取舍。
6. **下一步任务**：明确接下来应该执行的操作，以及需要继续验证的事项。

请遵守以下要求：

- 优先保留对后续任务有直接影响的信息，删除寒暄、重复讨论、过时的尝试和无关内容。
- 对已经验证的事实与尚未验证的推测作出明确区分。
- 不要将计划完成的任务描述为已经完成。
- 重要的文件路径、函数名称、配置名称和命令应保留准确原文。
- 对失败的尝试，只保留有助于避免重复错误的信息。
- 如果信息存在冲突，应优先保留用户最新确认的要求，并简要说明仍然存在的冲突。
- 如果某些历史细节无法在摘要中完整保留，可简要说明它们已保存到原始上下文归档中，后续可以通过 recallContext 工具重新检索。
- 使用清晰的 Markdown 结构输出摘要，不要附加解释或无关内容。

**最终目标：使一个从未阅读过原始对话的 Agent，仅凭这份摘要，就能够理解当前任务、继承关键决策，并继续开展工作。**"""


# ---------- Token 估算 ----------
def estimate_token_count(text: str | None) -> int:
    """近似估算一段文本的 Token 数。

    不依赖具体模型的 tokenizer，采用经验公式：
      - 中文字符/中文标点/全角字符 ≈ 1 token
      - 英文单词/英文标点/数字序列 ≈ 1.3 tokens （粗略取 4/3）
      - 其他（空格、换行）≈ 0.5 tokens

    最终结果向上取整，为近似上界。
    """
    if not text:
        return 0
    tokens = 0
    # 中文（CJK Unified Ideographs + 常见全角符号）
    cjk = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f')
    tokens += cjk
    # 英文单词和数字序列（按空白和非字母数字切分）
    words = re.findall(r'[A-Za-z0-9_]+', text)
    tokens += sum(max(1, len(w) // 3) for w in words)
    # 其他字符（标点、空格等）粗略按 2 个算 1 个 token
    other = len(text) - cjk - sum(len(w) for w in words)
    tokens += other // 2
    return max(1, tokens)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """估算整段消息列表的 Token 总数。

    包括每条消息的 role 字段、content 内容、tool_calls 参数及 tool 结果。
    每条消息加 4 个 Token 开销（JSON 结构、分隔等）。
    """
    total = 0
    for msg in messages:
        total += 4  # 消息级开销
        role = msg.get("role", "")
        total += estimate_token_count(role)
        content = msg.get("content")
        if isinstance(content, str):
            total += estimate_token_count(content)
        elif content is None:
            pass
        # 工具调用参数
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {})
            total += estimate_token_count(fn.get("name", ""))
            total += estimate_token_count(fn.get("arguments", ""))
        # tool 结果消息
        if msg.get("role") == "tool":
            total += estimate_token_count(msg.get("tool_call_id", ""))
            total += estimate_token_count(msg.get("content", ""))
    return total


def context_usage_ratio(messages: list[dict], context_window: int) -> float:
    """返回当前上下文使用比例（0-1）。"""
    if context_window <= 0:
        return 0.0
    return min(1.0, estimate_messages_tokens(messages) / context_window)


# ---------- 归档 ----------
def _resolve_archive_dir(project_root: Path) -> Path:
    """返回归档目录（自动创建）。"""
    archive_dir = project_root / ARCHIVE_DIRNAME
    archive_dir.mkdir(parents=True, exist_ok=True)
    return archive_dir


def _generate_archive_id() -> str:
    """生成形如 20261022-153000 的归档编号。"""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


@dataclass
class ArchiveResult:
    archive_id: str
    file_path: Path
    message_count: int


def archive_messages(
    messages: list[dict], project_root: Path, archive_id: str | None = None
) -> ArchiveResult:
    """将原始消息列表保存为 JSONL 归档文件。

    每条消息一行 JSON，保留完整角色、内容、tool_calls、tool_call_id 等字段。
    归档文件命名：{archive_id}.jsonl
    """
    archive_dir = _resolve_archive_dir(project_root)
    archive_id = archive_id or _generate_archive_id()
    file_path = archive_dir / f"{archive_id}.jsonl"

    with file_path.open("w", encoding="utf-8") as f:
        f.write(f"# archive_id: {archive_id}\n")
        f.write(f"# created_at: {datetime.now().isoformat()}\n")
        f.write(f"# message_count: {len(messages)}\n")
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    return ArchiveResult(
        archive_id=archive_id,
        file_path=file_path,
        message_count=len(messages),
    )


# ---------- 压缩 ----------
@dataclass
class CompressResult:
    summary: str          # LLM 生成的 Markdown 摘要
    archive_id: str       # 归档编号（本次压缩前归档的原始上下文）
    original_tokens: int
    summary_tokens: int


def _build_compact_user_prompt(messages: list[dict]) -> str:
    """把要压缩的消息序列拼成可读的文本，作为 user 内容传给压缩 LLM。

    格式：每条消息前加 [role] 前缀；assistant 的 tool_calls 和 tool 结果保留；
    不携带任何 tool schema（压缩 LLM 不需要工具调用能力）。
    """
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        lines.append(f"\n===== [{role}] =====")
        content = msg.get("content")
        if isinstance(content, str) and content:
            lines.append(content)
        # assistant 的工具调用
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {})
            lines.append(
                f"  [tool_call] {fn.get('name', '')}"
                f"  args={fn.get('arguments', '')}"
            )
        # tool 结果消息
        if role == "tool":
            lines.append(
                f"  [tool_result] id={msg.get('tool_call_id', '')}\n  {content}"
            )
    return "\n".join(lines)


def call_llm_plain(llm_client, messages: list[dict]) -> str:
    """调用 LLM 的普通（非流式、无工具定义）接口，返回纯文本。

    使用 llm_client._url / _model / _headers（访问 protected 属性在同模块是可以的）。
    注意：这里不应该在本模块直接导入 urllib 再次实现一遍，
    而是扩展 LLMClient 暴露一个 simple_chat 方法更干净。
    但为了最小侵入，这里走 json 模块 + urllib 直接请求。
    """
    import urllib.request

    payload = {
        "model": llm_client._model,
        "messages": messages,
        "stream": False,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        llm_client._url, data=data, headers=llm_client._headers
    )
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"]


def compress_messages(
    llm_client,
    messages: list[dict],
    project_root: Path,
) -> CompressResult:
    """把 messages（通常是除 system 外的全部）归档 + 调用 LLM 生成摘要。

    **失败保护（遵循 Lessons Learned 1819922）**：
      - 先归档；归档成功后才调用 LLM。
      - LLM 调用失败时（任何异常）抛出，由调用方（AgentSession.compact）
        决定保留原始 messages 还是继续。本函数不做破坏性写入。
    """
    # 1) 先归档（保留所有原始消息）
    archive_result = archive_messages(messages, project_root)

    original_tokens = estimate_messages_tokens(messages)

    # 2) 构造压缩请求
    system_msg = {"role": "system", "content": COMPACT_SYSTEM_PROMPT}
    user_msg = {"role": "user", "content": _build_compact_user_prompt(messages)}

    # 3) 调用 LLM 生成摘要（失败时直接抛，让上层决定是否重试）
    summary = call_llm_plain(llm_client, [system_msg, user_msg])

    summary_tokens = estimate_token_count(summary)

    return CompressResult(
        summary=summary,
        archive_id=archive_result.archive_id,
        original_tokens=original_tokens,
        summary_tokens=summary_tokens,
    )


def build_compacted_history(
    system_prompt: str, summary: str, archive_id: str, archive_time: str
) -> list[dict]:
    """组装压缩后的新历史：system + 摘要 + 归档说明占位。

    调用方用返回的列表替换原来的 messages（保留 system prompt 不变）。
    """
    archive_note = (
        f"\n\n---\n\n> 此前上下文已于 {archive_time} 完成压缩，"
        f"原始上下文保存在编号为 {archive_id} 的归档文件中。"
        f"如需查找历史信息，请使用 recallContext 工具。"
    )
    content = summary.rstrip() + archive_note
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "（上下文压缩说明：以下是对历史对话的摘要，Agent 应继承此摘要继续工作。）"},
        {"role": "assistant", "content": content},
    ]


# ---------- 关键词检索 ----------
@dataclass
class RecallHit:
    archive_id: str
    line_number: int
    role: str
    preview: str          # 匹配行或周围片段（截断）
    matched_field: str     # "role" / "content" / "tool_args" / "tool_result"


_MAX_PREVIEW = 500       # 每个命中片段最大字符数
_MAX_RESULTS = 15        # 单次检索最多返回条数
_CONTEXT_LINES = 3       # 额外附带的上下文行数


def _load_archive_index(archive_path: Path) -> list[tuple[int, dict]]:
    """把 JSONL 归档解析成 (行号, message_dict) 列表（跳过 # 头）。"""
    records: list[tuple[int, dict]] = []
    if not archive_path.exists():
        return records
    with archive_path.open("r", encoding="utf-8") as f:
        for idx, raw in enumerate(f, start=1):
            line = raw.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            try:
                records.append((idx, json.loads(line)))
            except json.JSONDecodeError:
                continue
    return records


def _message_matches(msg: dict, keywords: Iterable[str]) -> list[tuple[str, str]]:
    """返回 [(field_name, matched_text), ...]，field_name 用于 matched_field。"""
    hits: list[tuple[str, str]] = []
    for kw in keywords:
        if not kw:
            continue
        kw_lower = kw.lower()
        # content
        content = msg.get("content")
        if isinstance(content, str) and kw_lower in content.lower():
            hits.append(("content", content))
        # assistant tool_calls arguments
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {})
            if kw_lower in fn.get("name", "").lower():
                hits.append(("tool_call_name", fn.get("name", "")))
            args = fn.get("arguments", "")
            if kw_lower in args.lower():
                hits.append(("tool_args", args))
        # tool 结果消息
        if msg.get("role") == "tool":
            c = msg.get("content", "") or ""
            if kw_lower in c.lower():
                hits.append(("tool_result", c))
        if kw_lower in msg.get("role", "").lower():
            hits.append(("role", msg.get("role", "")))
    return hits


def _extract_preview(text: str, keyword: str, max_len: int = _MAX_PREVIEW) -> str:
    """围绕 keyword 截取一段预览文本（去掉多余空白）。"""
    if not text:
        return ""
    text_single = " ".join(text.split())
    kw_idx = text_single.lower().find(keyword.lower())
    if kw_idx == -1:
        return text_single[:max_len]
    half = max_len // 2 - len(keyword) // 2
    start = max(0, kw_idx - half)
    end = min(len(text_single), start + max_len)
    if start > 0:
        prefix = "..."
    else:
        prefix = ""
    if end < len(text_single):
        suffix = "..."
    else:
        suffix = ""
    return prefix + text_single[start:end] + suffix


def recall_context(
    text: str,
    project_root: Path,
    archive_id: str | None = None,
    max_results: int = _MAX_RESULTS,
) -> list[RecallHit]:
    """在归档文件中按关键词检索原始消息。

    text 支持以空格分隔的多个关键词（AND 语义：任一词命中即返回）。
    archive_id 为空时遍历 .agents/context/ 下全部 .jsonl 文件。
    """
    archive_dir = _resolve_archive_dir(project_root)
    keywords = [w.strip() for w in text.split() if w.strip()]
    if not keywords:
        return []

    # 定位目标文件
    if archive_id:
        candidates = [archive_dir / f"{archive_id}.jsonl"]
    else:
        candidates = sorted(archive_dir.glob("*.jsonl"), reverse=True)  # 新→旧优先

    hits: list[RecallHit] = []
    seen = set()  # 去重：同一消息同一 field 只保留第一条命中

    for path in candidates:
        records = _load_archive_index(path)
        aid = path.stem
        for lineno, msg in records:
            matched = _message_matches(msg, keywords)
            for field, content in matched:
                key = (aid, lineno, field)
                if key in seen:
                    continue
                seen.add(key)
                # 找到第一个实际命中的关键词做预览锚点
                anchor_kw = next(
                    (kw for kw in keywords if kw.lower() in content.lower()),
                    keywords[0],
                )
                preview = _extract_preview(content, anchor_kw)
                hits.append(
                    RecallHit(
                        archive_id=aid,
                        line_number=lineno,
                        role=msg.get("role", "?"),
                        preview=preview,
                        matched_field=field,
                    )
                )
                if len(hits) >= max_results:
                    return hits
    return hits


def list_archives(project_root: Path) -> list[dict]:
    """列出全部归档的简要元信息，供调试或工具返回。"""
    archive_dir = _resolve_archive_dir(project_root)
    results: list[dict] = []
    for path in sorted(archive_dir.glob("*.jsonl"), reverse=True):
        try:
            with path.open("r", encoding="utf-8") as f:
                head = "".join(next(f) for _ in range(3)).strip()
        except (StopIteration, OSError):
            head = ""
        msg_count = 0
        for line in path.open("r", encoding="utf-8"):
            if line.strip() and not line.startswith("#"):
                msg_count += 1
        results.append(
            {
                "archive_id": path.stem,
                "path": str(path),
                "message_count": msg_count,
                "head": head,
            }
        )
    return results
