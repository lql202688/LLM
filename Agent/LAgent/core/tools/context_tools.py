"""recallContext 工具：从历史归档中按关键词检索原始上下文。

通过 set_project_root() 让 AgentSession 在启动时注入工作目录，
工具实现才能定位 .agents/context/ 目录。
"""
from __future__ import annotations

from pathlib import Path

from ..context_manager import list_archives, recall_context as _recall_context

_project_root: Path | None = None


def set_project_root(root: Path) -> None:
    """由 AgentSession 在启动时注入项目根目录。"""
    global _project_root
    _project_root = root


def recallContext(text: str, id: str | None = None) -> str:
    """Function Calling 入口：返回适合 LLM 阅读的 Markdown 片段。"""
    if _project_root is None:
        return "recallContext 尚未初始化（项目根目录未注入）。"
    if not text or not text.strip():
        return "请提供要检索的关键词（text 参数不能为空）。"

    hits = _recall_context(text=text, archive_id=id, project_root=_project_root)
    if not hits:
        archives = list_archives(_project_root)
        if not archives:
            return ".agents/context/ 目录下尚无任何历史归档，请先执行 /compact 命令生成归档。"
        scope = f"归档 {id}" if id else f"全部 {len(archives)} 个归档"
        return f"在{scope}中未找到包含关键词「{text}」的记录。"

    # 格式化成可读 Markdown
    lines = [f"## recallContext 结果（共 {len(hits)} 条）\n"]
    current_aid = None
    for h in hits:
        if h.archive_id != current_aid:
            lines.append(f"\n### 归档 `{h.archive_id}`\n")
            current_aid = h.archive_id
        lines.append(
            f"- **行 {h.line_number}** · role=`{h.role}` · 字段=`{h.matched_field}`"
        )
        lines.append(f"  > {h.preview}\n")
    return "\n".join(lines)
