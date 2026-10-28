"""loadSkill 工具：动态加载 Skill 正文或附加文件。

遵循 Function Calling 标准协议 —— name 固定为 "loadSkill"，
可变信息全部放在 arguments 里。
"""
from __future__ import annotations

from ..skills.loader import SkillRegistry


# SkillRegistry 在模块加载时懒初始化一次（实际由 AgentSession 传入）
_registry: SkillRegistry | None = None


def set_registry(registry: SkillRegistry) -> None:
    """由 AgentSession 在初始化时注入 SkillRegistry。"""
    global _registry
    _registry = registry


def load_skill(name: str, filename: str | None = None) -> str:
    """按 LLM 请求加载 Skill 内容。

    参数:
        name: Skill 名称（对应 SKILL.md frontmatter 的 name 字段）
        filename: 可选；仅提供 name 时读 SKILL.md 全文；
                  同时提供 filename 时读附加文件。支持相对/绝对/纯文件名。
    """
    if _registry is None:
        return "错误：SkillRegistry 未初始化"

    info = _registry.get(name)
    if not info:
        available = _registry.names()
        hint = (
            f"\n当前可用 Skills: {available}" if available
            else "\n当前无可用 Skills"
        )
        return f"Skill 不存在: {name!r}{hint}"

    # 仅 name → 加载 SKILL.md 正文
    if not filename:
        try:
            content = info.skill_md.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return f"读取 SKILL.md 失败: {e}"
        # 去掉 frontmatter（LLM 不需要再看元数据）
        import re as _re
        body = _re.sub(
            r"^---\s*\r?\n.*?\r?\n---\s*\r?\n?", "", content, count=1, flags=_re.DOTALL
        )
        # 组装返回
        header = f"Skill: {info.name}\n{'=' * 40}\n"
        return header + body.strip()

    # filename 提供 → 加载附加文件
    content = _registry.read_skill_file(name, filename)
    if content.startswith("文件不存在") or content.startswith("路径越界") or content.startswith("存在多个同名"):
        return content  # 把错误信息直接返回给 LLM
    if content.startswith("读取失败"):
        return content

    header = (
        f"Skill: {info.name}  (附加文件: {filename})\n"
        f"{'=' * 40}\n"
    )
    return header + content
