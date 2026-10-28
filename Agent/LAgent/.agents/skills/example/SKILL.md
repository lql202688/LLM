---
name: example
description: 演示用的示例 Skill，展示 SKILL.md frontmatter 规范
---

# Example Skill

这是一个示例 Skill，用来验证 Agent 的 Skill 动态加载机制工作正常。

## 使用场景

当用户询问"示例 skill 有什么用"时，调用 `loadSkill(name="example")` 加载本文件。

## 附加文件

该 Skill 还附带了一个 `hello.txt` 示例文件，可通过
`loadSkill(name="example", filename="hello.txt")` 加载。
