# 本地文件操作 Function Call 助手

基于大模型 Function Calling 的命令行对话程序，通过自然语言完成本地文件与目录的查询、读取、创建、编辑、删除和重命名。

## 文件说明

| 文件 | 说明 |
|---|---|
| `file_ops_chat.py` | 主程序：工具定义 + 实现 + 流式对话循环 |
| `config.ini` | 模型配置（api_key / base_url / model） |
| `architecture.mmd` | 代码架构图（Mermaid 纯文本格式，可用 Mermaid 渲染为流程图） |

## 环境与依赖

- Python 3.8+（仅使用标准库，无需额外安装依赖）

## 配置

在 `config.ini` 中填写大模型接口信息：

```ini
[llm]
api_key = 你的 API Key
base_url = https://api.deepseek.com/v1
model = deepseek-chat
```

## 运行

```powershell
cd d:\study\MCP
python Agent\Practice02\file_ops_chat.py
```

启动后输入自然语言需求，输入「退出」结束。运行时可以看到工具调用日志：

```
▶ 调用工具 listDirectory({'directory': 'd:/study/MCP'}) ...
◀ 工具结果: 目录 ... 共 3 项：...
```

## 代码架构

完整架构图见 [`architecture.mmd`](architecture.mmd)（Mermaid 格式），整体分为五层：

```
① 配置层  →  ② 工具声明层  →  ③ 工具实现层
                    ↑                    ↑
④ 通信层 stream_chat()  ←→  ⑤ 主循环 main loop  →  本地文件系统
        ↓
   远程大模型 API
```

- **① 配置层**：`configparser` 读取 `config.ini`，拼接 `url` 与 `headers`。
- **② 工具声明层**：`tools` 列表以 JSON Schema 声明 10 个工具的 `name` / `description` / `parameters`，随每次请求发送给模型。
- **③ 工具实现层**：10 个 Python 函数，通过 `TOOL_MAP` 与声明一一对应；`_is_text_file()` 为文本判定的公共辅助函数。
- **④ 通信层**：`stream_chat()` 发起 SSE 流式请求，解析 `data:` 分片，聚合 `content` 与 `tool_calls`（按 `index` 拼接），返回 `(assistant_text, tool_calls_list)`。
- **⑤ 主循环**：读取用户输入 → 调用 `stream_chat()` → 保存 assistant 消息 → 若含 `tool_calls` 则执行工具并把结果以 `tool` 角色回填，循环直至无工具调用；全局 `messages` 列表维护完整对话历史。

> 提示：`.mmd` 是纯文本文件，可在 VS Code（Mermaid 插件）、GitHub、mermaid.live 等环境中直接渲染。

## 支持的 10 个工具

| 工具名 | 功能 | 实现方式 |
|---|---|---|
| `getCurrentDirectory` | 获取当前工作目录 | `os.getcwd()` |
| `listDirectory` | 列出目录下所有文件和子目录 | `pathlib.Path.iterdir()` |
| `getFileType` | 获取文件类型（含文本/二进制判断） | `mimetypes` + 扩展名 |
| `readFileContent` | 读取纯文本文件内容 | UTF-8 读取，回退 GBK |
| `createFile` | 按目录+文件名创建新文件 | `Path.write_text()` |
| `editFileContent` | 编辑文件内容（覆盖 / 追加） | `write_text` / 追加写入 |
| `createDirectory` | 创建新目录 | `Path.mkdir(parents=True)` |
| `deleteDirectory` | 删除目录（可非空） | `shutil.rmtree()` |
| `deleteFile` | 删除文件 | `Path.unlink()` |
| `renamePath` | 重命名文件或目录 | `Path.rename()` |

## 使用示例

```
> 列出 d:\study\MCP\Agent\Practice02 下的所有文件
> 在 sandbox 目录下创建 hello.txt，内容写 Hello MCP
> 把 sandbox\hello.txt 追加一行 "second line"
> 读一下 sandbox\hello.txt 的内容
> 把 sandbox\hello.txt 重命名为 note.md
```

## 实现要点

- **纯文本判定**：扩展名白名单 + MIME 类型，再用 UTF-8 解码与 `\x00` 检查兜底，保证 `read`/`edit` 只处理文本文件。
- **路径类型校验**：每个工具区分文件 / 目录，误用时提示改用正确工具。
- **编码兼容**：读写默认 UTF-8，读取对 GBK 内容回退处理。
- **流式解析**：`stream_chat` 逐行读取 SSE 响应，按 `index` 聚合多段 `tool_calls` 的 `id` / `name` / `arguments`，兼容流式分片。
- **多轮工具调用**：每轮请求都携带 `tools` 定义，支持 `读文件 → 生成 → 写文件` 这类连续多步链路。
- **消息完整性**：assistant 消息即使无文本也会写入空 `content`，避免缺少 `role`/`content` 字段导致接口报错。

## 安全提醒

- `deleteDirectory` 使用 `shutil.rmtree`，**删除不可逆**，请限定在测试目录内操作。
- 模型的工具选择由自然语言驱动，输入时带**明确路径**可降低误操作概率。
- `config.ini` 内含 `api_key`，请勿提交到公开仓库（已由 `.gitignore` 管理）。
