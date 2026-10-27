"""命令行执行工具：RunCli —— 调用本地 CLI 程序，返回 stdout/stderr/exit_code。

设计要点：
- shell=False，command + args 拼列表（防注入）
- 跨平台：Windows 上自动处理 .cmd/.bat、Powershell 内置命令
- 输出截断（默认 4000 字符）+ 超时（默认 30s）
- 退出码 0 → 成功；非 0 → 错误；超时 → TimeoutExpired；找不到 → FileNotFoundError
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass


DEFAULT_TIMEOUT = 30
MAX_OUTPUT_DEFAULT = 4000
MAX_ARGS = 50  # 一次最多多少个参数，防止 LLM 发疯


@dataclass
class RunCliResult:
    exit_code: int | None      # None 表示超时/进程启动失败
    stdout: str
    stderr: str
    elapsed_ms: int
    truncated: bool
    error: str | None = None   # 仅在 exit_code=None 时有值


def run_cli(
    command: str,
    args: list[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_output: int = MAX_OUTPUT_DEFAULT,
) -> str:
    """执行本地命令行程序并返回结构化结果。

    参数:
        command: 程序名或路径（如 "python", "git", "node", "ping"）
        args: 参数列表（如 ["--version", "*.py"]），默认空
        timeout: 超时秒数，默认 30，取值范围 1-300
        max_output: 输出（stdout+stderr）总长度上限，默认 4000，取值范围 500-20000
    """
    if not command or not isinstance(command, str) or not command.strip():
        return "错误：command 不能为空"

    # ---------- 参数归一 ----------
    args = args or []
    if not isinstance(args, list):
        # LLM 偶尔把 args 传成 dict/string，防御性归一
        if isinstance(args, str):
            args = [args]
        else:
            args = list(args) if args else []
    args = [str(a) for a in args]  # 全部转 str
    if len(args) > MAX_ARGS:
        args = args[:MAX_ARGS] + [f"...（参数已截断，上限 {MAX_ARGS}）"]

    # 超时 & 截断钳制
    try:
        timeout = max(1, min(int(timeout), 300))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    try:
        max_output = max(500, min(int(max_output), 20000))
    except (TypeError, ValueError):
        max_output = MAX_OUTPUT_DEFAULT

    # ---------- Windows 兼容：内置命令自动用 cmd.exe /c ----------
    # Windows 上 subprocess(shell=False) 不能直接跑 dir / cd / echo 等 cmd 内置命令
    # 判断：如果 command 没有可执行后缀且系统是 Windows，默认走 cmd.exe /c
    cmd: str = command.strip()
    if os.name == "nt":
        executable_suffixes = (".exe", ".bat", ".cmd", ".ps1", ".com")
        has_suffix = cmd.lower().endswith(executable_suffixes) or os.path.isfile(cmd)
        if not has_suffix:
            # 包装为 cmd.exe /c command arg1 arg2 ...
            full_args = ["/c", cmd, *args]
            exec_cmd = "cmd.exe"
        else:
            full_args = args
            exec_cmd = cmd
    else:
        full_args = args
        exec_cmd = cmd

    # ---------- 执行 ----------
    start = time.perf_counter()
    try:
        result = subprocess.run(
            [exec_cmd, *full_args],
            capture_output=True,
            text=True,
            timeout=timeout,
            # env 继承当前环境（PATH 很重要，让 python/git 等能找到）
            env=os.environ.copy(),
        )
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        res = RunCliResult(
            exit_code=result.returncode,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            elapsed_ms=elapsed_ms,
            truncated=False,
        )
    except subprocess.TimeoutExpired as e:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        res = RunCliResult(
            exit_code=None,
            stdout=(e.stdout or "") if isinstance(e.stdout, str) else "",
            stderr=(e.stderr or "") if isinstance(e.stderr, str) else "",
            elapsed_ms=elapsed_ms,
            truncated=False,
            error=f"执行超时（{timeout}s）",
        )
    except FileNotFoundError:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        res = RunCliResult(
            exit_code=None,
            stdout="",
            stderr="",
            elapsed_ms=elapsed_ms,
            truncated=False,
            error=f"程序未找到: {command!r}（确认已安装且在 PATH 中）",
        )
    except PermissionError:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        res = RunCliResult(
            exit_code=None,
            stdout="",
            stderr="",
            elapsed_ms=elapsed_ms,
            truncated=False,
            error=f"权限不足，无法执行: {command!r}",
        )
    except OSError as e:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        res = RunCliResult(
            exit_code=None,
            stdout="",
            stderr="",
            elapsed_ms=elapsed_ms,
            truncated=False,
            error=f"进程启动失败: {e}",
        )

    # ---------- 输出截断 ----------
    total_len = len(res.stdout) + len(res.stderr)
    if total_len > max_output:
        res.truncated = True
        # 按比例截断 stdout 和 stderr
        stdout_ratio = len(res.stdout) / total_len if total_len else 1.0
        max_stdout = int(max_output * stdout_ratio)
        max_stderr = max_output - max_stdout
        if len(res.stdout) > max_stdout:
            res.stdout = res.stdout[:max_stdout].rstrip() + "\n...（stdout 已截断）"
        if len(res.stderr) > max_stderr:
            res.stderr = res.stderr[:max_stderr].rstrip() + "\n...（stderr 已截断）"

    # ---------- 组装返回 ----------
    parts: list[str] = []
    parts.append(f"命令: {command}")
    if args:
        parts.append(f"参数: {args}")
    parts.append(f"耗时: {res.elapsed_ms} ms")

    if res.exit_code is None:
        parts.append(f"状态: 执行失败 — {res.error}")
    else:
        status_label = "成功" if res.exit_code == 0 else "失败"
        parts.append(f"状态: {status_label}（exit_code={res.exit_code}）")

    if res.truncated:
        parts.append(f"注意: 输出已截断（上限 {max_output} 字符）")

    separator = "\n" + "-" * 40 + "\n"
    if res.stdout.strip():
        parts.append(separator + "stdout:\n" + res.stdout.rstrip())
    if res.stderr.strip():
        parts.append(separator + "stderr:\n" + res.stderr.rstrip())

    return "\n".join(parts)
