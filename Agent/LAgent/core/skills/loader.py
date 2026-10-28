"""Skill 发现与元数据加载。

职责：
- 扫描项目根目录下 .agents/skills/ 子目录
- 轻量解析 SKILL.md 的 YAML frontmatter（name, description）
- 返回可直接注入 System Prompt 的 Skill 列表文本

设计原则：
- 初始化阶段只读 frontmatter，不读完整正文
- 正文在 LLM 调用 loadSkill 工具时才加载（动态注入对话）
- 安全：路径解析限制在指定 skill 目录内部
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


SKILLS_DIR_NAME = ".agents"
SKILLS_SUBDIR = "skills"
SKILL_FILENAME = "SKILL.md"


@dataclass(frozen=True)
class SkillInfo:
    """Skill 的轻量元数据（只在发现阶段加载）。"""

    name: str
    description: str
    skill_dir: Path          # 该 Skill 所在目录（绝对路径）
    skill_md: Path           # SKILL.md 绝对路径

    def to_system_prompt_line(self) -> str:
        """格式化为 System Prompt 里的一行。"""
        return f"- `{self.name}`: {self.description}"


@dataclass
class SkillRegistry:
    """管理所有已发现 Skill 的注册表。"""

    project_root: Path
    skills_dir: Path = field(init=False)
    _skills: dict[str, SkillInfo] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.skills_dir = self.project_root / SKILLS_DIR_NAME / SKILLS_SUBDIR
        self._scan()

    # ---------- 发现 ----------
    def _scan(self) -> None:
        """扫描 skills_dir 下所有子目录，收集 SkillInfo。"""
        self._skills.clear()
        if not self.skills_dir.is_dir():
            return  # 没有 skills 目录就静默跳过

        for child in sorted(self.skills_dir.iterdir()):
            if not child.is_dir():
                continue
            skill_md = child / SKILL_FILENAME
            if not skill_md.is_file():
                continue
            info = _parse_skill_frontmatter(skill_md)
            if info:
                self._skills[info.name] = info

    # ---------- 查询 ----------
    @property
    def skills(self) -> dict[str, SkillInfo]:
        return dict(self._skills)

    def __len__(self) -> int:
        return len(self._skills)

    def get(self, name: str) -> Optional[SkillInfo]:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return sorted(self._skills.keys())

    def format_for_system_prompt(self) -> str:
        """生成 System Prompt 里的 Skill 公告段。"""
        if not self._skills:
            return ""
        lines = ["You have access to the following skills:"]
        for info in self._skills.values():
            lines.append(info.to_system_prompt_line())
        lines.append(
            "Use the `loadSkill` tool to load the full instructions of a skill "
            "when needed. You can also load additional files referenced by a skill "
            "(e.g., examples, schemas, helper scripts)."
        )
        return "\n".join(lines)

    # ---------- 文件解析（安全） ----------
    def resolve_skill_file(
        self, skill_name: str, filename: str
    ) -> tuple[Path | None, str | None]:
        """安全地解析 loadSkill 请求里的 filename。

        返回 (resolved_path, error_message)；成功时 error_message=None。
        路径约束：目标必须在该 Skill 目录内部，防止 ../ 越权。
        """
        info = self._skills.get(skill_name)
        if not info:
            return None, f"Skill 不存在: {skill_name!r}"

        skill_dir = info.skill_dir.resolve()
        request = filename.strip()

        # 1. 精确路径匹配
        if os.path.isabs(request):
            candidate = Path(request).resolve()
        elif request.startswith("..") or os.sep in request or "/" in request:
            # 相对路径（包含分隔符），解析到 skill_dir 下
            candidate = (skill_dir / request).resolve()
        else:
            # 纯文件名（无路径分隔符），在 skill_dir 内递归搜索
            matches = list(skill_dir.rglob(request))
            if len(matches) == 0:
                return None, f"未找到文件: {filename!r}（在 {skill_dir} 下）"
            if len(matches) > 1:
                candidates_str = "\n".join(
                    f"  - {str(m.relative_to(skill_dir))}" for m in matches
                )
                return (
                    None,
                    f"存在多个同名文件，请指定完整路径:\n{candidates_str}",
                )
            candidate = matches[0].resolve()

        # 2. 安全边界：candidate 必须在 skill_dir 内
        try:
            candidate.relative_to(skill_dir)
        except ValueError:
            return (
                None,
                f"路径越界: {filename!r}（只能访问 {skill_dir} 内部文件）",
            )

        if not candidate.is_file():
            return None, f"文件不存在: {candidate}"

        return candidate, None

    def read_skill_file(self, skill_name: str, filename: str) -> str:
        """读取指定文件内容，供 loadSkill 工具调用。"""
        path, err = self.resolve_skill_file(skill_name, filename)
        if err:
            return err
        assert path is not None
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return f"读取失败: {e}"


# ---------- 模块级辅助 ----------

_YAML_FRONTMATTER_RE = re.compile(
    r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", re.DOTALL
)


def _parse_skill_frontmatter(skill_md: Path) -> Optional[SkillInfo]:
    """读取 SKILL.md 开头的 YAML frontmatter，返回 SkillInfo 或 None。"""
    try:
        head = skill_md.read_bytes()[:2048].decode("utf-8", errors="replace")
    except OSError:
        return None

    m = _YAML_FRONTMATTER_RE.match(head)
    if not m:
        return None

    raw = m.group(1)
    fields = _simple_yaml_parse(raw)

    name = fields.get("name", "").strip()
    description = fields.get("description", "").strip()

    if not name or not description:
        return None

    return SkillInfo(
        name=name,
        description=description,
        skill_dir=skill_md.parent.resolve(),
        skill_md=skill_md.resolve(),
    )


def _simple_yaml_parse(text: str) -> dict[str, str]:
    """极简 YAML 解析 —— 只支持 top-level key: value（不引入 PyYAML 依赖）。

    覆盖 Skill frontmatter 的常见场景：
        name: myskill
        description: 简短描述（可以多行）
    多行值以 | 或 > 开头时，取后续所有缩进行。
    """
    result: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        # 顶层 key: value
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key = m.group(1)
        value = m.group(2).strip()
        if value in ("|", ">", "|-", ">-"):
            # 多行块：收集后续所有缩进 >= 2 的行
            i += 1
            block_lines = []
            while i < len(lines) and (lines[i].startswith(" ") or lines[i].startswith("\t")):
                block_lines.append(lines[i].strip())
                i += 1
            result[key] = "\n".join(block_lines)
        else:
            # 单行，可能带引号
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            result[key] = value
            i += 1
    return result
