"""Query dogs via MCP list_pets tool."""

import json
import httpx

body = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
        "name": "list_pets",
        "arguments": {
            "input": {
                "species": "犬",
                "page": 1,
                "pageSize": 500,
            }
        },
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        },
    },
}
headers = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2026-07-28",
    "MCP-Method": "tools/call",
    "MCP-Name": "list_pets",
}

r = httpx.post("http://127.0.0.1:8000/mcp", json=body, headers=headers, timeout=15)
data = r.json()
result = data.get("result", {})
if result.get("isError"):
    print("Error:", result["content"][0]["text"])
else:
    text = json.loads(result["content"][0]["text"])
    total = text["total"]
    print(f"小狗总数: {total}")
    print("姓名列表:")
    for i, pet in enumerate(text["items"], 1):
        print(f"  {i}. {pet['name']}  (主人: {pet['ownerName']}, 状态: {pet['status']})")
