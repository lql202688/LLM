"""会话编排：维护对话历史，驱动「模型 -> 工具 -> 模型」的多轮循环。

AgentSession 是 UI 层唯一需要交互的 core 门面：
    session = AgentSession(load_config())
    session.send("列出当前目录", emit=my_ui_render)

同时提供界面所需的状态信息（会话 ID、轮次、模型、上下文占用等）与中断能力：
    session.cancel()  # 中断当前生成，本轮消息回滚
    session.reset()   # 新建会话
    session.compact(emit)  # 手动触发上下文压缩
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .config import AppConfig
from .context_manager import (
    DEFAULT_COMPACT_THRESHOLD_RATIO,
    DEFAULT_CONTEXT_WINDOW,
    build_compacted_history,
    compress_messages,
    estimate_messages_tokens,
)
from .events import AgentEvent, EventEmitter, EventType
from .llm_client import GenerationCancelled, LLMClient
from .mcp_client import MCPClientManager
from .mcp_config import load_mcp_config
from .skills.loader import SkillRegistry
from .tools import context_tools
from .tools.registry import dispatch, get_all_schemas, register_mcp_tools

DEFAULT_MAX_MODEL_CALLS_PER_TURN = 8


# ---------- 会话保存目录 ----------
SESSION_DIRNAME = ".agents/sessions"
AGENTS_FILENAME = "AGENTS.md"


# ---------- 系统提示词 ----------
BASE_SYSTEM_PROMPT = """你是 LAgent，一个能够调用工具执行任务的智能助手。

**工作原则**：
- 始终先思考问题，再选择合适的工具。
- 用中文回答用户，除非用户用其他语言提问。
- 对用户的操作请求（如"列出当前目录"），立即通过工具执行并汇报结果。
- 遇到不明确的参数或路径时，主动向用户澄清；不要假设。
- 所有工具调用结果都应视为事实，基于此继续推理或告知用户。

**工具使用约定**：
- 同一轮内可以连续调用多个工具（最多 8 次）。
- 需要最新时间/日期时调用 getDate；需要联网搜索时调用 searchWeb；需要读取网页时调用 fetchWeb。
- 需要执行本地 CLI 程序时调用 RunCli。"""


class AgentSession:
    def __init__(
        self,
        config: AppConfig,
        llm_client: LLMClient | None = None,
        project_root: Path | None = None,
        max_model_calls_per_turn: int | None = None,
    ):
        self._config = config
        self._llm = llm_client or LLMClient(config.llm)
        self.max_model_calls_per_turn = (
            max_model_calls_per_turn
            if max_model_calls_per_turn is not None
            else config.llm.max_model_calls_per_turn
        )
        self._cancel = threading.Event()
        self.session_id = uuid4().hex[:8]
        self.messages: list[dict] = []

        # 压缩相关状态
        self._compressing = False
        self._context_window = (
            config.llm.context_window
            if config.llm.context_window > 0
            else DEFAULT_CONTEXT_WINDOW
        )
        self._context_window_is_default = config.llm.context_window <= 0
        self._context_locked = False  # True 时拒绝新的普通用户提示
        self._compact_threshold = config.context.compact_threshold

        # ---------- 项目根目录 ----------
        if project_root is None:
            import sys as _sys
            if getattr(_sys, "frozen", False):
                project_root = Path(_sys.executable).resolve().parent
            else:
                project_root = Path(__file__).resolve().parent.parent
        self._project_root = project_root

        # 把 project_root 注入 recallContext 工具
        context_tools.set_project_root(project_root)

        # ---------- Skill 发现 ----------
        self.skills = SkillRegistry(project_root)
        from .tools import skill_tools
        skill_tools.set_registry(self.skills)

        # ---------- MCP Server 连接（异步，先启动 TUI 再连接） ----------
        self._mcp_manager = MCPClientManager()
        mcp_server_configs = load_mcp_config(project_root)
        self._mcp_configs = mcp_server_configs
        self._mcp_loaded = threading.Event()
        self._mcp_error: str | None = None

        # 构建 system prompt 并作为 messages 第一条
        self._system_prompt = self._build_system_prompt()
        self._append_system_message()

        # ---------- AGENTS.md 一次注入 ----------
        self._agents_injected = False
        self._try_inject_agents_md()

    # ---------- MCP 异步加载 ----------
    def start_mcp_loading(self) -> threading.Thread | None:
        """启动后台线程异步连接 MCP Server，不阻塞 TUI 渲染。

        Returns:
            后台线程引用（供 UI 监控），无 MCP 配置时返回 None。
        """
        if not self._mcp_configs:
            self._mcp_loaded.set()
            return None

        def _loader():
            try:
                print(f"[MCP] 发现 {len(self._mcp_configs)} 个 MCP Server 配置，正在连接...")
                for cfg in self._mcp_configs:
                    self._mcp_manager.connect(cfg)
                register_mcp_tools(self._mcp_manager)
                server_names = self._mcp_manager.connected_servers
                total_mcp_tools = len(self._mcp_manager.all_tools)
                print(
                    f"[MCP] 已连接 {len(server_names)} 个 Server，"
                    f"共注册 {total_mcp_tools} 个 MCP 工具"
                )
            except Exception as e:
                self._mcp_error = str(e)
                print(f"[MCP] 异步加载出错: {e}")
            finally:
                self._mcp_loaded.set()

        t = threading.Thread(target=_loader, name="MCP-Loader", daemon=True)
        t.start()
        return t

    @property
    def mcp_loaded(self) -> bool:
        return self._mcp_loaded.is_set()

    @property
    def mcp_servers(self) -> list[str]:
        return self._mcp_manager.connected_servers

    @property
    def mcp_tools_count(self) -> int:
        return len(self._mcp_manager.all_tools)

    # ---------- AGENTS.md 注入 ----------
    def _try_inject_agents_md(self) -> None:
        """如果项目根目录存在 AGENTS.md，将其内容作为一条 system 消息注入。

        采用「一次注入、全程复用」策略：
        - 首次启动（messages 只有 system prompt）时检查并注入
        - 重置会话（reset）时会再次注入（因为 reset 会清空 messages）
        - 后续对话轮次不再重复检查
        """
        agents_path = self._project_root / AGENTS_FILENAME
        if not agents_path.is_file():
            self._agents_injected = True  # 标记已检查过（不存在即无需注入）
            return

        try:
            content = agents_path.read_text(encoding="utf-8").strip()
        except Exception as e:
            print(f"[AGENTS.md] 读取失败: {e}")
            self._agents_injected = True
            return

        if not content:
            self._agents_injected = True
            return

        # 插入到 system prompt 之后，作为第二条 system 消息
        agents_message = {
            "role": "system",
            "content": (
                f"以下是项目中 AGENTS.md 文件的内容（已自动注入，"
                f"请作为项目级别的永久指令遵守）：\n\n{content}"
            ),
        }
        self.messages.insert(1, agents_message)
        self._agents_injected = True
        print(f"[AGENTS.md] 已注入（{len(content)} 字符）")

    @property
    def agents_loaded(self) -> bool:
        return self._agents_injected

    # ---------- 系统提示词构建 ----------
    def _build_system_prompt(self) -> str:
        parts = [BASE_SYSTEM_PROMPT]
        skills_block = self.skills.format_for_system_prompt()
        if skills_block:
            parts.append("")
            parts.append(skills_block)
        return "\n".join(parts)

    def _append_system_message(self) -> None:
        """把 system prompt 作为 messages 第一条；已存在时替换。"""
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0] = {"role": "system", "content": self._system_prompt}
        else:
            self.messages.insert(0, {"role": "system", "content": self._system_prompt})

    # ---------- 界面状态信息 ----------
    @property
    def model(self) -> str:
        return self._config.llm.model

    @property
    def base_url(self) -> str:
        return self._config.llm.base_url

    @property
    def rounds(self) -> int:
        """已写入历史的用户提问轮数。"""
        return sum(1 for m in self.messages if m.get("role") == "user")

    @property
    def skill_count(self) -> int:
        return len(self.skills)

    @property
    def context_window(self) -> int:
        """当前模型的上下文窗口大小（Token 数）。"""
        return self._context_window

    @property
    def context_window_is_default(self) -> bool:
        """True 表示窗口大小使用了默认值（config.ini 未显式配置 context_window）。"""
        return self._context_window_is_default

    @property
    def context_usage_tokens(self) -> int:
        """估算当前 messages 的 Token 总数。"""
        return estimate_messages_tokens(self.messages)

    @property
    def context_usage_ratio(self) -> float:
        """上下文使用率（0-1）。"""
        if self._context_window <= 0:
            return 0.0
        return min(1.0, self.context_usage_tokens / self._context_window)

    @property
    def compressing(self) -> bool:
        """True 表示当前正处于压缩流程中（UI 据此提示用户）。"""
        return self._compressing

    @property
    def context_locked(self) -> bool:
        """True 表示上下文超出阈值，拒绝新的普通用户输入，需先 /compact。"""
        return self._context_locked

    # ---------- 会话控制 ----------
    def cancel(self) -> None:
        """请求中断当前生成；send() / compact() 会在最近的安全点停止并回滚本轮历史。"""
        self._cancel.set()

    def reset(self) -> None:
        """新建会话：清空历史、更换会话 ID、重新注入 system prompt 和 AGENTS.md。"""
        self.messages.clear()
        self.session_id = uuid4().hex[:8]
        self._cancel.clear()
        self._compressing = False
        self._context_locked = False
        self._append_system_message()
        # 重置后重新尝试注入 AGENTS.md（一次性策略）
        self._agents_injected = False
        self._try_inject_agents_md()

    # ---------- 会话保存 / 恢复 ----------
    def save_session(self, name: str | None = None) -> Path:
        """将当前会话保存到 .agents/sessions/{name_or_session_id}.json。

        Args:
            name: 可选，自定义会话名称；不提供则使用 session_id。

        Returns:
            保存文件的路径。
        """
        import json as _json
        from datetime import datetime as _dt

        session_dir = self._project_root / SESSION_DIRNAME
        session_dir.mkdir(parents=True, exist_ok=True)

        filename = (name or self.session_id).replace("/", "_").replace("\\", "_")
        if not filename.endswith(".json"):
            filename += ".json"
        file_path = session_dir / filename

        data = {
            "session_id": self.session_id,
            "saved_at": _dt.now().isoformat(),
            "project_root": str(self._project_root),
            "model": self.model,
            "message_count": len(self.messages),
            "messages": self.messages,
        }
        file_path.write_text(
            _json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[Session] 已保存到 {file_path}")
        return file_path

    def list_sessions(self) -> list[dict]:
        """列出 .agents/sessions/ 下所有已保存会话的元信息。"""
        import json as _json

        session_dir = self._project_root / SESSION_DIRNAME
        if not session_dir.is_file() if hasattr(session_dir, "is_file") else session_dir.is_file():
            pass
        if not session_dir.exists():
            return []

        results: list[dict] = []
        for path in sorted(session_dir.glob("*.json"), reverse=True):
            try:
                raw = _json.loads(path.read_text(encoding="utf-8"))
                results.append({
                    "filename": path.name,
                    "path": str(path),
                    "session_id": raw.get("session_id", ""),
                    "saved_at": raw.get("saved_at", ""),
                    "model": raw.get("model", ""),
                    "message_count": raw.get("message_count", 0),
                })
            except Exception:
                # 文件损坏时跳过
                continue
        return results

    def resume_session(self, path_or_name: str) -> bool:
        """从保存文件恢复会话历史。

        Args:
            path_or_name: 完整文件路径，或仅文件名（会在 .agents/sessions/ 下查找）。

        Returns:
            True 表示成功恢复，False 表示文件未找到或格式错误。
        """
        import json as _json

        if Path(path_or_name).is_file():
            file_path = Path(path_or_name)
        else:
            session_dir = self._project_root / SESSION_DIRNAME
            file_path = session_dir / path_or_name
            if not file_path.is_file() and not file_path.name.endswith(".json"):
                file_path = session_dir / (path_or_name + ".json")

        if not file_path.is_file():
            print(f"[Session] 会话文件不存在: {file_path}")
            return False

        try:
            data = _json.loads(file_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[Session] 读取会话文件失败: {e}")
            return False

        messages = data.get("messages", [])
        if not messages:
            print("[Session] 会话文件无 messages 字段")
            return False

        # 重置当前状态
        self._cancel.clear()
        self._compressing = False
        self._context_locked = False

        # 如果文件中有 system prompt，用它替换当前的
        if messages and messages[0].get("role") == "system":
            self.messages = messages
            self._system_prompt = messages[0].get("content", self._system_prompt)
        else:
            # 没有 system prompt，先注入再追加
            self.messages.clear()
            self._append_system_message()
            self.messages.extend(messages)

        self.session_id = data.get("session_id", uuid4().hex[:8])
        self._agents_injected = True  # 恢复时不重复注入（原会话已处理过）

        print(f"[Session] 已恢复会话 {self.session_id}（{len(self.messages)} 条消息）")
        return True

    # ---------- 主流程 ----------
    def send(self, user_text: str, emit: EventEmitter) -> None:
        """处理一轮用户输入，期间通过 emit 持续推送事件，直到模型不再调用工具。"""
        # 压缩状态或上下文锁定：拒绝新的普通输入
        if self._compressing:
            emit(
                AgentEvent(
                    EventType.ERROR,
                    {"message": "正在执行上下文压缩，请稍候…"},
                )
            )
            emit(AgentEvent(EventType.TURN_DONE))
            return
        if self._context_locked:
            emit(
                AgentEvent(
                    EventType.ERROR,
                    {
                        "message": "上下文已达上限的 80%，请先执行 /compact 命令压缩上下文。"
                    },
                )
            )
            emit(AgentEvent(EventType.TURN_DONE))
            return

        self._cancel.clear()
        snapshot = len(self.messages)
        self.messages.append({"role": "user", "content": user_text})
        try:
            self._run_tool_loop(emit)
        except GenerationCancelled:
            del self.messages[snapshot:]
            emit(AgentEvent(EventType.INTERRUPTED, {"message": "已中断"}))
        except Exception as e:
            emit(AgentEvent(EventType.ERROR, {"message": str(e)}))

        # TURN_DONE 前检查上下文使用情况（触发锁定/警告）
        self._check_context_usage(emit)
        emit(AgentEvent(EventType.TURN_DONE))

    def _run_tool_loop(self, emit: EventEmitter) -> None:
        model_calls = 0
        while True:
            if self._cancel.is_set():
                raise GenerationCancelled()
            model_calls += 1
            if model_calls > self.max_model_calls_per_turn:
                emit(
                    AgentEvent(
                        EventType.ERROR,
                        {"message": f"已达单轮模型调用上限（{self.max_model_calls_per_turn} 次）"},
                    )
                )
                break

            assistant_text, tool_calls = self._llm.stream_chat(
                self.messages,
                get_all_schemas(),
                on_text_delta=lambda piece: emit(
                    AgentEvent(EventType.TEXT_DELTA, {"text": piece})
                ),
                should_stop=self._cancel.is_set,
            )

            self.messages.append(
                self._build_assistant_message(assistant_text, tool_calls)
            )
            if not tool_calls:
                break

            for tc in tool_calls:
                if self._cancel.is_set():
                    raise GenerationCancelled()
                started = time.perf_counter()
                try:
                    emit(
                        AgentEvent(
                            EventType.TOOL_CALL_START,
                            {"name": tc["name"], "arguments": tc["arguments"] or "{}"},
                        )
                    )
                    result = dispatch(tc["name"], tc["arguments"])
                except Exception as e:
                    result = f"工具执行失败: {e}"
                duration_ms = (time.perf_counter() - started) * 1000
                try:
                    emit(
                        AgentEvent(
                            EventType.TOOL_CALL_RESULT,
                            {
                                "name": tc["name"],
                                "result": str(result),
                                "duration_ms": duration_ms,
                            },
                        )
                    )
                except Exception:
                    pass
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": str(result),
                    }
                )

    # ---------- 上下文压缩 ----------
    def compact(self, emit: EventEmitter) -> None:
        """手动触发上下文压缩。

        流程：
          1) 归档当前历史（JSONL）
          2) 调用 LLM 生成压缩摘要
          3) 组装新历史（system + 摘要 + 归档说明）
          4) 替换 self.messages，解除 context_locked

        失败保护：任何异常都不会改写 messages（保留原始历史），
        只会推送 COMPACT_FAILED 事件让 UI 提示用户。
        """
        if self._compressing:
            emit(
                AgentEvent(
                    EventType.ERROR,
                    {"message": "已经在执行压缩，请勿重复触发。"},
                )
            )
            emit(AgentEvent(EventType.TURN_DONE))
            return

        self._cancel.clear()
        self._compressing = True
        self._context_locked = False  # 压缩期间先解除锁定，避免 send 被拒绝

        try:
            # 取 system prompt 之外的全部消息作为压缩输入
            if self.messages and self.messages[0].get("role") == "system":
                system_prompt = self.messages[0]["content"]
                to_compact = self.messages[1:]
            else:
                system_prompt = self._system_prompt
                to_compact = list(self.messages)

            original_tokens = estimate_messages_tokens(self.messages)
            emit(
                AgentEvent(
                    EventType.COMPACT_START, {"original_tokens": original_tokens}
                )
            )

            # 压缩：内部会先归档再调 LLM；任何异常都会抛给上层
            result = compress_messages(
                llm_client=self._llm,
                messages=to_compact,
                project_root=self._project_root,
            )

            if self._cancel.is_set():
                raise GenerationCancelled()

            # 组装新历史
            archive_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            new_history = build_compacted_history(
                system_prompt=system_prompt,
                summary=result.summary,
                archive_id=result.archive_id,
                archive_time=archive_time,
            )

            # 原子替换
            self.messages = new_history

            emit(
                AgentEvent(
                    EventType.COMPACT_DONE,
                    {
                        "archive_id": result.archive_id,
                        "original_tokens": result.original_tokens,
                        "summary_tokens": result.summary_tokens,
                    },
                )
            )

        except GenerationCancelled:
            emit(
                AgentEvent(
                    EventType.COMPACT_FAILED,
                    {"message": "压缩已被用户中断，原始历史未被改写。"},
                )
            )
        except Exception as e:
            emit(
                AgentEvent(
                    EventType.COMPACT_FAILED,
                    {"message": f"压缩失败：{e}（原始历史未被改写）"},
                )
            )
        finally:
            self._compressing = False
            # 重新检查：如果压缩后仍然超限（不太可能），保持锁定保护
            self._check_context_usage(emit)
            emit(AgentEvent(EventType.TURN_DONE))

    # ---------- 上下文监控 ----------
    def _check_context_usage(self, emit: EventEmitter) -> None:
        ratio = self.context_usage_ratio
        threshold = self._compact_threshold
        if ratio >= threshold and not self._context_locked:
            self._context_locked = True
            emit(
                AgentEvent(
                    EventType.CONTEXT_WARN,
                    {
                        "tokens": self.context_usage_tokens,
                        "window": self._context_window,
                        "ratio": ratio,
                    },
                )
            )
        elif ratio < threshold and self._context_locked:
            # 成功压缩后低于阈值，解除锁定
            self._context_locked = False

    # ---------- 构造 ----------
    @staticmethod
    def _build_assistant_message(text: str, tool_calls: list[dict]) -> dict:
        """构造 assistant 历史消息；有工具调用时必须携带 tool_calls 字段。"""
        msg: dict = {"role": "assistant"}
        if tool_calls:
            msg["content"] = text or ""
            msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"] or "{}",
                    },
                }
                for tc in tool_calls
            ]
        else:
            msg["content"] = text
        return msg
