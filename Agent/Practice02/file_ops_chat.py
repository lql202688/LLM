import configparser
import json
import mimetypes
import os
import shutil
import urllib.request
import urllib.error
from pathlib import Path

# ---------- 配置 ----------
config = configparser.ConfigParser()
config.read(Path(__file__).with_name("config.ini"), encoding="utf-8")

url = config["llm"]["base_url"].rstrip("/") + "/chat/completions"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {config['llm']['api_key']}",
}

# ---------- 工具定义：本地文件操作 ----------
tools = [
    {
        "type": "function",
        "function": {
            "name": "getCurrentDirectory",
            "description": "获取当前工作目录的绝对路径",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listDirectory",
            "description": "列出指定目录下的所有文件和子目录",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "要列出的目录路径，例如 d:/study/MCP 或 .",
                    }
                },
                "required": ["directory"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getFileType",
            "description": "获取指定文件的文件类型（根据扩展名/MIME 判断，并区分文本或二进制）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要判断的文件路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "readFileContent",
            "description": "读取指定文件的内容，仅支持纯文本格式文件（如 .txt/.md/.py/.json 等）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要读取的文件路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createFile",
            "description": "根据指定的目录和文件名创建一个新文件，可同时写入初始内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "新文件所在的目录路径",
                    },
                    "filename": {
                        "type": "string",
                        "description": "新文件的文件名，例如 note.txt",
                    },
                    "content": {
                        "type": "string",
                        "description": "可选，新文件的初始内容，默认为空",
                    },
                },
                "required": ["directory", "filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "editFileContent",
            "description": "编辑指定文件的内容，仅支持纯文本格式文件。mode=overwrite 覆盖原内容，mode=append 追加到末尾",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要编辑的文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的新内容",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["overwrite", "append"],
                        "description": "编辑方式：overwrite 覆盖（默认），append 追加",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createDirectory",
            "description": "创建一个新目录（自动创建缺失的父目录）",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "要创建的目录路径",
                    }
                },
                "required": ["directory"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deleteDirectory",
            "description": "删除指定目录（可删除非空目录，连同其中所有内容一起删除）",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "要删除的目录路径",
                    }
                },
                "required": ["directory"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deleteFile",
            "description": "删除指定的文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要删除的文件路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "renamePath",
            "description": "重命名文件或目录（也可用于移动位置）",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "原文件或目录的路径",
                    },
                    "destination": {
                        "type": "string",
                        "description": "新的文件或目录路径",
                    },
                },
                "required": ["source", "destination"],
            },
        },
    },
]


# ---------- 文本文件判定 ----------
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


# ---------- 工具实现 ----------
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


TOOL_MAP = {
    "getCurrentDirectory": get_current_directory,
    "listDirectory": list_directory,
    "getFileType": get_file_type,
    "readFileContent": read_file_content,
    "createFile": create_file,
    "editFileContent": edit_file_content,
    "createDirectory": create_directory,
    "deleteDirectory": delete_directory,
    "deleteFile": delete_file,
    "renamePath": rename_path,
}

# ---------- 对话历史 ----------
messages = []


def stream_chat():
    """发起一次流式请求，始终携带工具定义，以支持多轮工具调用链。
    返回 (assistant_text: str, tool_calls_list: list)"""

    payload = {
        "model": config["llm"]["model"],
        "messages": messages,
        "stream": True,
        "tools": tools,
        "tool_choice": "auto",
    }

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)

    tool_calls = {}
    content_parts = []

    try:
        resp = urllib.request.urlopen(req)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"\n!!! HTTPError {e.code}: {e.reason}")
        print(f"!!! 响应体: {err_body}")
        raise

    with resp:
        for line in resp:
            line = line.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue

            chunk = line[len("data:"):].strip()
            if chunk == "[DONE]":
                break

            delta = json.loads(chunk)["choices"][0]["delta"]

            if delta.get("content"):
                piece = delta["content"]
                print(piece, end="", flush=True)
                content_parts.append(piece)

            if delta.get("tool_calls"):
                for tc in delta["tool_calls"]:
                    idx = tc["index"]
                    if idx not in tool_calls:
                        tool_calls[idx] = {"id": "", "name": "", "arguments": ""}
                    slot = tool_calls[idx]
                    if tc.get("id"):
                        slot["id"] += tc["id"]
                    if tc.get("function"):
                        fn = tc["function"]
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]

    tool_calls_list = list(tool_calls.values())
    assistant_text = "".join(content_parts)
    return assistant_text, tool_calls_list


# ---------- 主循环 ----------
print("开始对话（支持本地文件操作工具），输入「退出」结束。")

while True:
    question = input("我：").strip()
    if not question:
        continue
    if question == "退出":
        break

    messages.append({"role": "user", "content": question})

    while True:
        print("模型：", end="", flush=True)
        assistant_text, tool_calls_list = stream_chat()
        print()

        # 关键：先初始化为 {"role": "assistant"}，避免遗漏 role 字段
        assistant_msg = {"role": "assistant"}
        if tool_calls_list:
            assistant_msg["content"] = assistant_text if assistant_text else ""
            assistant_msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"] or "{}",
                    },
                }
                for tc in tool_calls_list
            ]
        else:
            assistant_msg["content"] = assistant_text
        messages.append(assistant_msg)

        if not tool_calls_list:
            break

        for tc in tool_calls_list:
            name = tc["name"]
            try:
                args = json.loads(tc["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}

            print(f"  ▶ 调用工具 {name}({args}) ...")
            func = TOOL_MAP.get(name)
            if func:
                result = func(**args) if args else func()
            else:
                result = f"未知工具: {name}"
            print(f"  ◀ 工具结果: {result}")

            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": str(result),
            })
