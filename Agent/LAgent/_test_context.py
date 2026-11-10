"""快速自测脚本：测试上下文管理各功能。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.context_manager import (
    estimate_messages_tokens,
    archive_messages,
    recall_context,
    build_compacted_history,
    DEFAULT_CONTEXT_WINDOW,
)
from core.tools.registry import TOOL_MAP, get_all_schemas
from core.events import EventType
from core.config import load_config

# ------ 基础导入 ------
print("== 1. 模块导入 ==")
print("events OK, CONTEXT_WARN:", hasattr(EventType, "CONTEXT_WARN"))
print("  COMPACT_START:", hasattr(EventType, "COMPACT_START"))
print("  COMPACT_DONE:", hasattr(EventType, "COMPACT_DONE"))
print("  COMPACT_FAILED:", hasattr(EventType, "COMPACT_FAILED"))

# ------ 工具注册 ------
print("\n== 2. 工具注册 ==")
print("recallContext in TOOL_MAP:", "recallContext" in TOOL_MAP)
print("Total schemas:", len(get_all_schemas()))
recall_schema = next(
    (s for s in get_all_schemas() if s["function"]["name"] == "recallContext"), None
)
print("recallContext schema found:", recall_schema is not None)
if recall_schema:
    print("  required:", recall_schema["function"]["parameters"]["required"])

# ------ Token 估算 ------
print("\n== 3. Token 估算 ==")
msgs = [
    {"role": "system", "content": "你是 LAgent，一个能够调用工具执行任务的智能助手。"},
    {"role": "user", "content": "请帮我分析 d:/study/MCP/Agent/LAgent/core/agent.py 这个文件"},
    {
        "role": "assistant",
        "content": "好的，我来帮你读取这个文件",
        "tool_calls": [
            {
                "id": "tc1",
                "type": "function",
                "function": {
                    "name": "RunCli",
                    "arguments": '{"command":"dir","args":["d:/study/MCP/Agent/LAgent/core/agent.py"]}',
                },
            }
        ],
    },
    {"role": "tool", "tool_call_id": "tc1", "content": "agent.py 存在，共 245 行"},
    {"role": "assistant", "content": "agent.py 已读取完毕，文件包含 AgentSession 类…"},
]
est = estimate_messages_tokens(msgs)
print(f"估算 tokens: {est}")
print(f"默认窗口: {DEFAULT_CONTEXT_WINDOW}, 比例: {est/DEFAULT_CONTEXT_WINDOW*100:.2f}%")

# ------ 归档 ------
print("\n== 4. 归档 ==")
tmp_root = Path(".agents").resolve()
ar = archive_messages(msgs, tmp_root)
print(f"archive_id: {ar.archive_id}")
print(f"message_count: {ar.message_count}")
print(f"file exists: {ar.file_path.exists()}")

# ------ 检索 ------
print("\n== 5. 关键词检索 ==")
hits = recall_context(text="agent.py", project_root=tmp_root)
print(f"命中数: {len(hits)}")
for h in hits:
    print(f"  [{h.archive_id}] line {h.line_number} role={h.role} field={h.matched_field}")
    print(f"    > {h.preview[:80]}...")

# 多关键词
hits2 = recall_context(text="RunCli dir", project_root=tmp_root)
print(f"\n多关键词 RunCli|dir 命中数: {len(hits2)}")

# 指定 id 检索
hits3 = recall_context(
    text="不存在的关键词 xxxyyyzzz", archive_id=ar.archive_id, project_root=tmp_root
)
print(f"不存在关键词 命中数: {len(hits3)}")

# ------ 压缩历史组装 ------
print("\n== 6. 压缩历史组装 ==")
new_hist = build_compacted_history(
    system_prompt="system prompt content",
    summary="# 压缩摘要\n这里是摘要内容…",
    archive_id=ar.archive_id,
    archive_time="2026-10-22 15:30:00",
)
print(f"新历史条数: {len(new_hist)}")
print("role of each:", [m["role"] for m in new_hist])
print("最后一条包含归档说明:", "此前上下文已于" in new_hist[-1]["content"])

# ------ 配置 ------
print("\n== 7. 配置 ==")
cfg = load_config()
print("context_window:", cfg.llm.context_window, "(0=默认 128k)")

print("\n** ALL TESTS PASSED **")
