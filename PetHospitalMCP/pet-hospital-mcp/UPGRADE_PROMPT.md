# UPGRADE_PROMPT — 阶段二扩展指引

> 本文件为后续开发者提供扩展 MCP 工具的指引。阶段一只实现了 `list_pets`,阶段二应新增更多工具以覆盖 Go REST API 的全部 29 个接口。

## 现有基础设施(可直接复用)

| 模块 | 作用 | 扩展时如何复用 |
|------|------|----------------|
| `config.py` | `Settings` dataclass,从环境变量读取配置 | 新工具无需修改;如需新配置项,在此添加字段 |
| `errors.py` | 6 种错误码 + `ToolError` 异常 + `translate_unknown_error` | 新工具的 try/except 直接复用相同的错误结构和翻译函数 |
| `logging_config.py` | structlog 配置 + `redact()` 递归脱敏 | 新工具用 `get_logger(tool_name)` 获取 logger,脱敏自动生效 |
| `rest_client.py` | `PetHospitalClient`,已带超时+重试 | 在此类中新增对应 HTTP 方法;或在工具内直接用 httpx |
| `server.py` | `create_app()` + `build_mcp_server()` | 在 `build_mcp_server()` 中调用新工具的 `register_xxx_tool()` |

## 扩展步骤

1. **在 `tools/` 下新建模块**,例如 `get_pet.py`:

```python
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent
from ..rest_client import PetHospitalClient
from ..logging_config import get_logger, redact
from ..errors import ToolError, translate_unknown_error, ...

TOOL_NAME = "get_pet"

def register_get_pet_tool(mcp: MCPServer, client: PetHospitalClient) -> None:
    log = get_logger(TOOL_NAME)

    @mcp.tool(name=TOOL_NAME, description=TOOL_DESCRIPTION)
    async def get_pet(input: GetPetInput) -> CallToolResult:
        # 同 list_pets 的 try/except 结构
        ...

    get_pet.__name__ = TOOL_NAME
```

2. **在 `tools/__init__.py` 中导入并注册**:

```python
from .list_pets import register_list_pets_tool
from .get_pet import register_get_pet_tool

__all__ = ["register_list_pets_tool", "register_get_pet_tool"]
```

3. **在 `server.py` 的 `build_mcp_server()` 中调用**:

```python
def build_mcp_server(settings, client):
    mcp = MCPServer(name=..., version=..., instructions=...)
    register_list_pets_tool(mcp, client)
    register_get_pet_tool(mcp, client)  # 新增
    return mcp
```

4. **编写测试**:
   - 在 `tests/` 下新建 `test_get_pet.py`
   - 使用 `httpx.MockTransport` 模拟上游,禁止访问真实 Go 服务
   - 覆盖:正常调用、参数校验失败、上游 4xx/5xx、超时、非法 JSON

5. **运行测试**:

```bash
cd pet-hospital-mcp
pytest -q
```

## Go REST API 待覆盖接口(阶段二参考)

阶段一只覆盖了 `GET /api/v1/pets`。以下接口尚未实现 MCP 工具适配:

| 分类 | 接口 |
|------|------|
| 档案管理 | `POST /api/v1/pets`, `GET /api/v1/pets/{id}`, `PUT /api/v1/pets/{id}`, `DELETE /api/v1/pets/{id}` |
| 病历管理 | `POST /api/v1/pets/{id}/records`, `DELETE /api/v1/pets/{id}/records/{recordId}` |
| 消费明细 | `POST /api/v1/pets/{id}/charges`, `DELETE /api/v1/pets/{id}/charges/{chargeId}` |
| 统计 | `GET /api/v1/stats/summary`, `GET /api/v1/stats/species`, `GET /api/v1/stats/status`, `GET /api/v1/stats/doctor` |
| 数据维护 | `POST /api/v1/admin/compact`, `POST /api/v1/admin/seed` |

## 约定提醒

- 工具名使用 `snake_case`
- 不新增适配器私有业务参数
- 使用 Pydantic 定义输入/输出模型,`extra="forbid"` 拒绝未知字段
- 敏感字段(`ownerPhone`/`ownerAddr`/`chipNo` 及 snake_case)在日志中递归脱敏
- 后端调用必须有超时和有限重试
- 不得把 HTTPX/Pydantic/SDK/Python 堆栈原样暴露给 MCP 客户端
- 所有错误返回统一结构 `{error:{code, message, details}}`
- 工具失败标记 `is_error=True`(SDK 2.x CallToolResult)
