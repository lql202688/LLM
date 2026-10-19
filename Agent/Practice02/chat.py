import configparser
import json
import subprocess
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

# ---------- 工具定义：getDate ----------
tools = [
    {
        "type": "function",
        "function": {
            "name": "getDate",
            "description": "获取当前电脑的日期和时间",
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
            "name": "isLeapYear",
            "description": "判断给定的年份是否是闰年",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {
                        "type": "integer",
                        "description": "要判断的年份，例如 2024",
                    }
                },
                "required": ["year"],
            },
        },
    }
]


def get_date() -> str:
    """调用本地 date 命令获取日期和时间（Windows: date /t & time /t）"""
    try:
        out = subprocess.run(
            ["cmd", "/c", "date /t & time /t"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        result = out.stdout.strip()
        if out.returncode != 0 or not result:
            return f"命令执行失败: {out.stderr.strip()}"
        return result
    except Exception as e:
        return f"调用异常: {e}"


def is_leap_year(year: int) -> str:
    """用 Python 判断闰年：
    能被 4 整除且不能被 100 整除，或者能被 400 整除 → 闰年"""
    if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0):
        return f"{year} 年是闰年"
    return f"{year} 年不是闰年"


TOOL_MAP = {"getDate": get_date, "isLeapYear": is_leap_year}

# ---------- 对话历史 ----------
messages = []


def stream_chat(send_tools: bool = True):
    """发起一次流式请求。
    send_tools=False 时不再携带 tools 定义（工具结果回传时用）。
    返回 (assistant_text: str, tool_calls_list: list)"""

    payload = {
        "model": config["llm"]["model"],
        "messages": messages,
        "stream": True,
    }
    if send_tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

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
print("开始对话（支持 getDate / isLeapYear 工具），输入「退出」结束。")

while True:
    question = input("我：").strip()
    if not question:
        continue
    if question == "退出":
        break

    messages.append({"role": "user", "content": question})

    first_round = True

    while True:
        print("模型：", end="", flush=True)
        assistant_text, tool_calls_list = stream_chat(send_tools=first_round)
        first_round = False
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