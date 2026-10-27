"""工具注册表：集中维护工具 schema 与实现函数的分发关系。

新增工具时只需在 TOOL_SCHEMAS / TOOL_MAP 中各加一条，core 与 UI 无需改动。
"""
from __future__ import annotations

import json
from typing import Callable

from . import cli_tools, datetime_tools, file_ops, search_tools, web_tools

# ---------- 工具定义（发送给模型的 function-calling schema） ----------
TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "RunCli",
            "description": "在本地执行任意命令行程序（CLI）并返回 stdout、stderr 和退出码。可用于调用 Python、Git、Node、npm、pip、ping、curl、ffmpeg 等已安装的程序。注意：Windows 内置命令（如 dir、cd、echo）会自动走 cmd.exe /c 包装",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的程序名或路径，例如 python、git、pip、ping、curl",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "程序参数列表，最多 50 个。例如 [\"--version\"]、[\"install\", \"requests\"]、[\"-c\", \"8.8.8.8\"]",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 30，取值范围 1-300",
                    },
                    "max_output": {
                        "type": "integer",
                        "description": "输出（stdout+stderr）总长度上限字符数，默认 4000，取值范围 500-20000",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetchWeb",
            "description": "抓取指定网页并提取主要正文为 Markdown。用于获取网页内容、阅读文档、分析文章。会去除导航栏/广告/页脚等无关内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要抓取的网页 URL，例如 https://docs.python.org/3/library/asyncio.html",
                    },
                    "max_length": {
                        "type": "integer",
                        "description": "正文返回上限字符数，默认 8000，取值范围 1000-20000",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "searchWeb",
            "description": "联网搜索互联网最新信息。当用户询问时事新闻、实时数据、需要查阅公开网页资料时使用",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词或完整问题，例如 2026年诺贝尔物理学奖得主、Python 3.13 新特性",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最多返回多少条参考来源，默认 5，取值范围 1-10",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getDate",
            "description": "获取当前电脑的日期和时间（通过执行本地 date 命令获得）",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "isLeapYear",
            "description": "判断指定年份是否为闰年，返回判断结果及依据",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {"type": "integer", "description": "要判断的年份，例如 2026"}
                },
                "required": ["year"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getCurrentDirectory",
            "description": "获取当前工作目录的绝对路径",
            "parameters": {"type": "object", "properties": {}, "required": []},
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
                    "path": {"type": "string", "description": "要判断的文件路径"}
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
                    "path": {"type": "string", "description": "要读取的文件路径"}
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
                    "directory": {"type": "string", "description": "新文件所在的目录路径"},
                    "filename": {"type": "string", "description": "新文件的文件名，例如 note.txt"},
                    "content": {"type": "string", "description": "可选，新文件的初始内容，默认为空"},
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
                    "path": {"type": "string", "description": "要编辑的文件路径"},
                    "content": {"type": "string", "description": "要写入的新内容"},
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
                    "directory": {"type": "string", "description": "要创建的目录路径"}
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
                    "directory": {"type": "string", "description": "要删除的目录路径"}
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
                    "path": {"type": "string", "description": "要删除的文件路径"}
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
                    "source": {"type": "string", "description": "原文件或目录的路径"},
                    "destination": {"type": "string", "description": "新的文件或目录路径"},
                },
                "required": ["source", "destination"],
            },
        },
    },
]

# ---------- 工具名 -> 实现函数 ----------
TOOL_MAP: dict[str, Callable] = {
    "RunCli": cli_tools.run_cli,
    "fetchWeb": web_tools.fetch_web,
    "searchWeb": search_tools.search_web,
    "getDate": datetime_tools.get_date,
    "isLeapYear": datetime_tools.is_leap_year,
    "getCurrentDirectory": file_ops.get_current_directory,
    "listDirectory": file_ops.list_directory,
    "getFileType": file_ops.get_file_type,
    "readFileContent": file_ops.read_file_content,
    "createFile": file_ops.create_file,
    "editFileContent": file_ops.edit_file_content,
    "createDirectory": file_ops.create_directory,
    "deleteDirectory": file_ops.delete_directory,
    "deleteFile": file_ops.delete_file,
    "renamePath": file_ops.rename_path,
}


def dispatch(name: str, arguments_json: str) -> str:
    """按工具名分发执行；参数解析失败时降级为空参数。"""
    try:
        args = json.loads(arguments_json or "{}")
    except json.JSONDecodeError:
        args = {}
    func = TOOL_MAP.get(name)
    if func is None:
        return f"未知工具: {name}"
    return func(**args) if args else func()
