# Pet Hospital MCP

将 [Go 宠物医院 REST API](../) 的能力暴露给 AI Agent 的 MCP 服务。

## 技术栈

| 项目 | 值 |
|------|-----|
| Python | 3.11+ |
| MCP Python SDK | `mcp==2.0.0`(SDK 2.x) |
| MCP 协议版本 | `2026-07-28` |
| 服务端类 | `mcp.server.MCPServer` |
| 传输模型 | 无状态 Streamable HTTP |
| HTTP 库 | `httpx` |
| 数据校验 | `pydantic` |
| ASGI 服务器 | `uvicorn` |
| 日志 | `structlog`(JSON) |

> **禁止**:本服务不导入 `mcp.server.fastmcp.FastMCP`,不实现旧协议的 `initialize` 握手、`Mcp-Session-Id`、会话存储、会话过期、`max_sessions` 或有状态 SSE 恢复。

## 安装

```bash
cd pet-hospital-mcp
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux/macOS
source .venv/bin/activate

pip install -e .
```

## 启动

### 1. 先启动 Go 宠物医院 REST API

```bash
# 在仓库根目录
go run . -addr 127.0.0.1:8080
```

或使用预编译的可执行文件:

```bash
./dist/pethospital -addr 127.0.0.1:8080
```

### 2. 启动 MCP 服务

```bash
cd pet-hospital-mcp
.venv\Scripts\python.exe -m pet_hospital_mcp
```

默认监听 `127.0.0.1:8000`,MCP 端点为 `http://127.0.0.1:8000/mcp`。

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MCP_HOST` | `127.0.0.1` | MCP 服务监听地址 |
| `MCP_PORT` | `8000` | MCP 服务监听端口 |
| `PET_HOSPITAL_BASE_URL` | `http://127.0.0.1:8080` | 上游 Go REST API 地址 |
| `MCP_BACKEND_TIMEOUT` | `30` | 上游请求超时(秒) |
| `MCP_BACKEND_MAX_RETRIES` | `2` | 上游失败重试次数 |
| `MCP_BACKEND_RETRY_BACKOFF_BASE` | `0.5` | 重试退避基数(秒) |

## 端点

| 路径 | 方法 | 说明 |
|------|------|------|
| `/mcp` | POST | MCP 无状态 Streamable HTTP 端点 |
| `/health` | GET | 健康检查 |

### `/health` 示例

```bash
curl http://127.0.0.1:8000/health
```

```json
{
  "status": "healthy",
  "service": "pet-hospital-mcp",
  "version": "0.1.0",
  "protocol_version": "2026-07-28",
  "mcp_endpoint": "/mcp",
  "pet_hospital_base_url": "http://127.0.0.1:8080",
  "transport": "streamable_http_stateless",
  "stateless": true,
  "no_initialize": true,
  "no_session_id": true
}
```

## MCP 工具

### `list_pets`

查询宠物医院档案列表,严格对应上游 Go REST API 的 `GET /api/v1/pets`。

**参数**(全部可选,未知字段会被拒绝):

| 参数 | 类型 | 说明 |
|------|------|------|
| `q` | string | 全文检索关键词 |
| `name` | string | 宠物姓名(模糊) |
| `ownerName` | string | 主人姓名(模糊) |
| `ownerPhone` | string | 主人电话(模糊) |
| `species` | enum | 种类:犬/猫/兔/鸟/仓鼠/爬宠/其他 |
| `doctor` | string | 医生姓名(模糊) |
| `disease` | string | 疾病(模糊) |
| `status` | enum | 状态:待就诊/就诊中/住院中/已康复/慢性病随访 |
| `min` | number | 总花费下限(非负) |
| `max` | number | 总花费上限(非负,>= min) |
| `sortBy` | enum | 排序字段:id/name/ownerName/species/doctor/disease/status/totalCost/visitCount/createdAt/updatedAt |
| `order` | enum | 排序方向:asc/desc |
| `page` | integer | 页码(>=1,默认 1) |
| `pageSize` | integer | 每页条数(1-500,默认 20) |

**成功返回**:`{items, total, page, pageSize, totalPages, totalCost}`,其中 `items` 中 `records` / `charges` 可能为 `null` 或数组。

**失败返回**:`is_error=true`,`content` 为统一错误结构 JSON:`{error:{code, message, details}}`。

### 调用示例(SDK 2.x 无状态协议)

2026-07-28 协议下,每个请求通过 `params._meta` 自描述协议版本,不需要 `initialize` 握手:

```bash
curl -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "MCP-Protocol-Version: 2026-07-28" \
  -H "MCP-Method: tools/call" \
  -H "MCP-Name: list_pets" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "list_pets",
      "arguments": {
        "input": {
          "species": "犬",
          "status": "已康复",
          "page": 1,
          "pageSize": 10
        }
      },
      "_meta": {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}
      }
    }
  }'
```

### 工具发现

```bash
curl -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "MCP-Protocol-Version: 2026-07-28" \
  -H "MCP-Method: tools/list" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/list",
    "params": {
      "_meta": {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}
      }
    }
  }'
```

## 验证

### MCP Inspector

```bash
npx @modelcontextprotocol/inspector http://127.0.0.1:8000/mcp
```

### SDK 2.x 客户端验证要点

- **不发 `initialize`**:直接发 `tools/list` 或 `tools/call`
- **无 `Mcp-Session-Id`**:响应不携带会话 ID 头
- **每请求自描述**:`params._meta` 携带 `io.modelcontextprotocol/protocolVersion` 和 `io.modelcontextprotocol/clientCapabilities`
- **路由头**:`MCP-Method` 和(对 name-bearing 方法)`MCP-Name` 必须与 body 一致

## 单元测试

```bash
cd pet-hospital-mcp
pytest -q
```

预期结果:69 tests passed。

测试覆盖:
- 正常调用:确认请求路径和全部过滤/排序/分页参数正确转发
- 输入参数校验失败(枚举/边界/NaN/Infinity/未知字段/类型错误)
- Go REST API 返回 4xx/5xx
- 超时和连接异常
- 后端返回非法 JSON 或不符合数据模型
- MCP 工具注册、工具名和 JSON Schema
- SDK 2.x 无状态连接流程(不发 `initialize`,不要求/不返回 `Mcp-Session-Id`)
- `/health` 端点
- 工具可通过 HTTP MCP 端点被发现和调用

## 项目结构

```
pet-hospital-mcp/
├── pyproject.toml
├── README.md
├── UPGRADE_PROMPT.md
├── src/
│   └── pet_hospital_mcp/
│       ├── __init__.py
│       ├── __main__.py
│       ├── config.py
│       ├── server.py
│       ├── rest_client.py
│       ├── errors.py
│       ├── logging_config.py
│       └── tools/
│           ├── __init__.py
│           └── list_pets.py
└── tests/
    ├── conftest.py
    ├── test_rest_client.py
    ├── test_list_pets.py
    └── test_server.py
```

## 阶段说明

**本次(阶段一)只实现 `list_pets` 一个工具。** 阶段二(更多工具)尚未实现。
