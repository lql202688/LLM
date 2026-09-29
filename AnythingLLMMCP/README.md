# AnythingLLM MCP Server

一个最小的 MCP Server，提供 `ask_first_workspace` 工具：使用 AnythingLLM
列表中的第一个工作区资料回答问题。

## 配置

编辑 `.env`：

```dotenv
ANYTHINGLLM_BASE_URL=http://127.0.0.1:3001/api
ANYTHINGLLM_API_KEY=your-api-key
MCP_HOST=127.0.0.1
MCP_PORT=8000
MCP_PATH=/mcp
```

项目级 MCP 已配置在 `.codex/config.toml`：

```toml
[mcp_servers.anythingllm]
url = "http://127.0.0.1:8000/mcp"
```

MCP 地址为 `http://127.0.0.1:8000/mcp`。

## 安装

在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 启动

前台启动，关闭终端即停止：

```powershell
.\.venv\Scripts\python.exe server.py
```

后台启动：

```powershell
$process = Start-Process `
  -FilePath ".\.venv\Scripts\python.exe" `
  -ArgumentList "server.py" `
  -WorkingDirectory (Get-Location) `
  -WindowStyle Hidden `
  -PassThru
$process.Id | Set-Content .mcp-server.pid
```

查看后台进程：

```powershell
Get-Process -Id (Get-Content .mcp-server.pid)
```

## 停止

```powershell
Stop-Process -Id (Get-Content .mcp-server.pid)
```

如果以前台方式启动，直接在对应终端按 `Ctrl+C`。

## 在 Codex 中使用

1. 确保 AnythingLLM 和本 MCP Server 都已启动。
2. 重开 Codex 或新建线程，使 `.codex/config.toml` 生效。
3. 让 Codex 调用 `anythingllm` MCP Server 的 `ask_first_workspace` 工具。
