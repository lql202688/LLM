import configparser
import json
import urllib.request
from pathlib import Path

# 读取与脚本同目录下的 config.ini
config = configparser.ConfigParser()
config.read(Path(__file__).with_name("config.ini"), encoding="utf-8")

# 拼出 OpenAI 兼容的对话接口地址
url = config["llm"]["base_url"].rstrip("/") + "/chat/completions"

# 请求头（鉴权带上 api_key）
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {config['llm']['api_key']}",
}

# 保存对话历史，让模型记得上下文
messages = []

print("开始对话，输入「退出」结束。")

# 循环读取用户输入并请求模型
while True:
    question = input("我：").strip()
    if not question:
        continue

    messages.append({"role": "user", "content": question})

    # 构造请求体，开启流式输出
    data = json.dumps(
        {"model": config["llm"]["model"], "messages": messages, "stream": True}
    ).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)

    # 逐行读取 SSE 数据，打字机式输出
    answer = ""
    print("模型：", end="", flush=True)
    with urllib.request.urlopen(req) as resp:
        for line in resp:
            line = line.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue

            chunk = line[len("data:"):].strip()
            if chunk == "[DONE]":  # 流结束标记
                break

            piece = json.loads(chunk)["choices"][0]["delta"].get("content")
            if piece:  # 跳过空 content 分片
                print(piece, end="", flush=True)
                answer += piece

    # 完整回复加入历史，供后续多轮对话使用
    messages.append({"role": "assistant", "content": answer})
    print()
