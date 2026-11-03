"""Skill 管理工具：loadSkill 动态加载、createSkill 新建 Skill。

遵循 Function Calling 标准协议 —— 工具名固定，可变信息全部放在 arguments 里。
"""
from __future__ import annotations

import re
from pathlib import Path

from ..skills.loader import SkillRegistry


# Skill 目录名规范：小写字母、数字、连字符
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _normalize_skill_name(raw: str) -> str | None:
    """将原始 Skill 名称规范化为合法目录名（小写 + 连字符）。

    转换规则：
        1. 去除首尾空白，整体转小写
        2. 空格 / 下划线 → 连字符
        3. 移除所有非 [a-z0-9-] 字符
        4. 合并连续连字符，去除首尾连字符
    返回 None 表示规范化后为空或不合法。
    """
    name = raw.strip().lower()
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"[^a-z0-9-]", "", name)
    name = re.sub(r"-+", "-", name).strip("-")
    return name if name and _SKILL_NAME_RE.match(name) else None


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


def create_skill(name: str, skillContext: str) -> str:
    """按 LLM 请求创建新 Skill。

    在 .agents/skills/<name>/ 下创建 SKILL.md。
    目录名由 name 规范化而来（小写、连字符格式），SKILL.md 内容由 skillContext 写入。
    若目标 Skill 已存在，返回明确警告，避免意外覆盖。

    参数:
        name: Skill 名称（可带空格/中文，会自动规范化为合法英文目录名）
        skillContext: SKILL.md 的完整 Markdown 内容，必须包含 YAML frontmatter
    """
    if _registry is None:
        return "错误：SkillRegistry 未初始化"

    # 1. 名称规范化
    normalized = _normalize_skill_name(name)
    if not normalized:
        return (
            f"错误：Skill 名称 {name!r} 不合法。"
            f"规范化后应为空，仅允许小写字母、数字和连字符，例如 'python-helper'"
        )

    skills_root: Path = _registry.skills_dir
    skill_dir = skills_root / normalized
    skill_md = skill_dir / "SKILL.md"

    # 2. 存在性检查
    if skill_md.exists():
        existing = _registry.get(normalized)
        if existing:
            return (
                f"错误：Skill {normalized!r} 已存在，无法覆盖。\n"
                f"  描述: {existing.description}\n"
                f"  路径: {skill_md}\n"
                f"如需修改，请改用 editFileContent 工具；"
                f"如需全新创建，请换一个名称。"
            )
        # 目录存在但 SKILL.md 缺失（历史残留），给出警告后仍继续
        return (
            f"警告：目录 {skill_dir} 已存在但 SKILL.md 缺失，"
            f"将在其中创建 SKILL.md 文件。\n"
        )

    # 3. skillContext 基本校验
    context = skillContext if skillContext is not None else ""
    if not context.strip():
        return "错误：skillContext 不能为空，请提供包含 frontmatter 和正文的完整内容"

    if not context.lstrip().startswith("---"):
        return (
            "错误：skillContext 必须以 YAML frontmatter 开头（使用 --- 分隔）。\n"
            "规范模板：\n"
            "---\nname: my-skill\ndescription: 一句话描述该 Skill 的用途\n---\n\n"
            "# Skill 正文\n在这里写详细指令..."
        )

    # 4. 写入文件
    try:
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md.write_text(context, encoding="utf-8")
    except OSError as e:
        return f"创建失败：{e}"

    # 5. 刷新 registry（让新 Skill 立即可被 loadSkill 发现）
    _registry._scan()

    size_kb = len(context.encode("utf-8")) / 1024
    return (
        f"成功创建 Skill {normalized!r}\n"
        f"  路径: {skill_md}\n"
        f"  大小: {size_kb:.1f} KB\n"
        f"提示：如需添加 references/、scripts/ 等辅助目录或其他文件，"
        f"请继续调用 createDirectory、createFile 等 fileOps 工具。"
    )
