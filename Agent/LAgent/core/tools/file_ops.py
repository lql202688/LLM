"""本地文件操作工具实现。

每个函数返回人类可读的字符串结果，直接作为 tool 消息回传给模型。
"""
from __future__ import annotations

import mimetypes
import os
import shutil
from pathlib import Path

_TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".ini",
    ".cfg", ".conf", ".json", ".yaml", ".yml", ".toml", ".xml", ".html",
    ".htm", ".css", ".js", ".ts", ".py", ".java", ".c", ".h", ".cpp",
    ".hpp", ".go", ".rs", ".sh", ".bat", ".ps1", ".sql", ".env", ".gitignore",
}


def _is_text_file(path: Path) -> bool:
    """判断是否为纯文本文件：先看扩展名/MIME，再尝试用 UTF-8 解码样本。"""
    if path.suffix.lower() in _TEXT_EXTENSIONS:
        return True
    mime, _ = mimetypes.guess_type(str(path))
    if mime and mime.startswith("text/"):
        return True
    try:
        with open(path, "rb") as f:
            sample = f.read(4096)
        if b"\x00" in sample:
            return False
        sample.decode("utf-8")
        return True
    except (UnicodeDecodeError, OSError):
        return False


def get_current_directory() -> str:
    """获取当前工作目录"""
    return os.getcwd()


def list_directory(directory: str) -> str:
    """列出目录下的所有文件和子目录"""
    p = Path(directory)
    if not p.exists():
        return f"错误：目录不存在 -> {directory}"
    if not p.is_dir():
        return f"错误：{directory} 不是一个目录"

    try:
        entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    except OSError as e:
        return f"读取目录失败: {e}"

    if not entries:
        return f"目录 {p.resolve()} 为空"

    lines = [f"目录 {p.resolve()} 共 {len(entries)} 项："]
    for item in entries:
        if item.is_dir():
            lines.append(f"  [目录] {item.name}/")
        else:
            try:
                size = item.stat().st_size
            except OSError:
                size = -1
            lines.append(f"  [文件] {item.name}  ({size} 字节)")
    return "\n".join(lines)


def get_file_type(path: str) -> str:
    """获取文件类型"""
    p = Path(path)
    if not p.exists():
        return f"错误：文件不存在 -> {path}"
    if p.is_dir():
        return f"{path} 是一个目录，不是文件"

    suffix = p.suffix.lower() or "(无扩展名)"
    mime, _ = mimetypes.guess_type(str(p))
    mime = mime or "未知"
    kind = "纯文本文件" if _is_text_file(p) else "二进制文件"
    return f"文件: {p.name}\n扩展名: {suffix}\nMIME 类型: {mime}\n类别: {kind}"


def read_file_content(path: str) -> str:
    """读取纯文本文件内容"""
    p = Path(path)
    if not p.exists():
        return f"错误：文件不存在 -> {path}"
    if not p.is_file():
        return f"错误：{path} 不是一个文件"
    if not _is_text_file(p):
        return f"错误：{path} 不是纯文本文件，暂不支持读取"

    try:
        content = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = p.read_text(encoding="gbk")
        except Exception as e:
            return f"读取失败: {e}"
    except OSError as e:
        return f"读取失败: {e}"

    return f"文件 {p.resolve()} 的内容如下：\n{content}"


def create_file(directory: str, filename: str, content: str = "") -> str:
    """在指定目录下创建新文件"""
    p = Path(directory) / filename
    if p.exists():
        return f"错误：文件已存在 -> {p}"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"创建失败: {e}"
    return f"已创建文件 {p.resolve()}（{len(content)} 字符）"


def edit_file_content(path: str, content: str, mode: str = "overwrite") -> str:
    """编辑纯文本文件内容"""
    p = Path(path)
    if not p.exists():
        return f"错误：文件不存在 -> {path}"
    if not p.is_file():
        return f"错误：{path} 不是一个文件"
    if not _is_text_file(p):
        return f"错误：{path} 不是纯文本文件，暂不支持编辑"

    mode = (mode or "overwrite").lower()
    if mode not in ("overwrite", "append"):
        return f"错误：不支持的 mode -> {mode}，仅支持 overwrite 或 append"

    try:
        if mode == "append":
            with open(p, "a", encoding="utf-8") as f:
                f.write(content)
        else:
            p.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"编辑失败: {e}"

    action = "追加" if mode == "append" else "覆盖"
    return f"已{action}文件 {p.resolve()} 的内容（{len(content)} 字符）"


def create_directory(directory: str) -> str:
    """创建新目录"""
    p = Path(directory)
    if p.exists():
        return f"错误：路径已存在 -> {p}"
    try:
        p.mkdir(parents=True, exist_ok=False)
    except OSError as e:
        return f"创建目录失败: {e}"
    return f"已创建目录 {p.resolve()}"


def delete_directory(directory: str) -> str:
    """删除目录（可非空）"""
    p = Path(directory)
    if not p.exists():
        return f"错误：目录不存在 -> {directory}"
    if not p.is_dir():
        return f"错误：{directory} 不是一个目录，如需删除文件请使用 deleteFile"
    try:
        shutil.rmtree(p)
    except OSError as e:
        return f"删除目录失败: {e}"
    return f"已删除目录 {p.resolve()}（含其中所有内容）"


def delete_file(path: str) -> str:
    """删除文件"""
    p = Path(path)
    if not p.exists():
        return f"错误：文件不存在 -> {path}"
    if not p.is_file():
        return f"错误：{path} 不是一个文件，如需删除目录请使用 deleteDirectory"
    try:
        p.unlink()
    except OSError as e:
        return f"删除文件失败: {e}"
    return f"已删除文件 {p.resolve()}"


def rename_path(source: str, destination: str) -> str:
    """重命名文件或目录"""
    src = Path(source)
    dst = Path(destination)
    if not src.exists():
        return f"错误：源路径不存在 -> {source}"
    if dst.exists():
        return f"错误：目标路径已存在 -> {destination}"
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
    except OSError as e:
        return f"重命名失败: {e}"
    kind = "目录" if dst.is_dir() else "文件"
    return f"已将{kind} {src.name} 重命名为 {dst.resolve()}"
