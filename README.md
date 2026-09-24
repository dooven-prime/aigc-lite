# aigc-lite

一个可自托管、以可搜索执行记忆为核心的 AI 工作台与 Agent/MCP 运行时。项目通过 OpenAI-compatible 上游调用模型，但不以重复建设通用 AI Gateway 为目标；重点是持久化对话、Agent Run、执行步骤、知识和后续工具产物，让运行过程可以查询、搜索和复用。

## 当前范围

- FastAPI HTTP API 与 `/health` 健康检查
- 同步聊天和 SSE 流式聊天
- 环境变量配置，不在代码中保存密钥
- 独立 master key、版本化密文与严格解密错误
- 有上限的 Agent 运行入口
- workspace-aware Tool Catalog，本地函数与远程 MCP tool 统一发现、授权和调用
- SQLite 会话持久化和租户级数据隔离
- 同步与流式 Agent Run/Step 执行账本，记录模型、工具、成功失败和稳定错误码
- 跨对话、知识文档和执行步骤的租户级统一搜索
- 文本、Markdown、CSV、JSON 知识库上传与检索
- MCP-compatible `/mcp` JSON-RPC Server，以及远程 MCP Client
- 官方 MCP SDK 的 Streamable HTTP `/mcp` 和 SSE `/mcp-sse/sse` transport
- `/ui` 轻量聊天和知识库工作台
- 可作为 Python 包或独立服务运行

企业私有业务、数据库连接器、第三方平台凭据和运行时数据不属于公开核心，将通过独立扩展接入。

项目下一阶段的产品边界、目标架构、核心契约和 `0.3+` 路线见
[`docs/DESIGN.md`](docs/DESIGN.md)。总体原则是 Execution Memory first：模型调用只是内部能力，Agent、MCP 与 UI
共享同一套 Run/Step、工具、产物和检索基础。

版本变化见 [`CHANGELOG.md`](CHANGELOG.md)，安全部署边界与漏洞报告方式见
[`SECURITY.md`](SECURITY.md)。`v0.2.0` 的本地冻结范围和验证记录见
[`docs/releases/v0.2.0.md`](docs/releases/v0.2.0.md)。

## 快速开始

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

使用 PostgreSQL 时安装额外驱动：

```bash
pip install -e ".[postgres]"
```

前端开发或构建：

```bash
cd frontend
npm ci
npm run dev       # http://127.0.0.1:5173/ui/
npm run build     # 输出到 frontend/dist，由后端 /ui/ 提供
```

编辑 `.env`，至少设置：

```dotenv
AIGC_LITE_LLM_API_KEY=your-provider-key
AIGC_LITE_LLM_BASE_URL=https://api.openai.com/v1
AIGC_LITE_LLM_MODEL=gpt-4o-mini
```

如果要通过管理接口保存模型凭据，还必须生成独立于登录签名密钥的 Fernet master key：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

将输出写入 `AIGC_LITE_MASTER_KEY`。新凭据使用 `enc:v1:` 版本前缀；缺少密钥、密钥格式错误或密文认证失败都会返回稳定错误，不会尝试把数据库内容当作明文。升级前已有的旧版加密记录仍可由原 `AIGC_LITE_AUTH_SECRET` 读取，更新后会使用新格式写入。

启动服务：

```bash
python -m app.main
```

服务默认监听 `http://127.0.0.1:8000`，API 文档位于 `/docs`，工作台位于 `/ui/`。

主要接口：

- `GET /health`：服务健康检查
- `GET|POST /api/sessions`：创建和列出会话
- `GET /api/sessions/{session_id}`：读取当前租户会话及消息
- `POST /api/chat`：同步 Agent 对话
- `POST /api/chat/stream`：SSE 流式对话
- `GET /api/runs`：列出当前租户的 Agent 运行记录
- `GET /api/runs/{run_id}`：读取运行详情和步骤
- `GET /api/search?q=...`：搜索对话、知识文档和执行步骤
- `POST /api/knowledge/documents`：写入文本知识
- `POST /api/knowledge/upload`：上传 `.txt`、`.md`、`.csv` 或 `.json`
- `GET /api/knowledge/search?q=...`：搜索当前租户知识
- `GET|POST /api/credentials`：管理员列出凭据状态或创建 write-only 加密凭据
- `POST /api/credentials/{credential_id}/replace`：替换凭据值并重新启用
- `DELETE /api/credentials/{credential_id}`：撤销凭据引用，不物理删除记录
- `GET|POST /api/mcp-servers`：管理员列出或保存 workspace 的远程 MCP Server
- `POST /api/mcp-servers/{server_id}/probe`：测试连接并保存最新健康状态、延迟、工具数和稳定错误码
- `DELETE /api/mcp-servers/{server_id}`：管理员删除远程 MCP Server 配置
- `POST /mcp`：官方 MCP Streamable HTTP Server
- `GET /mcp-sse/sse`：官方 MCP SSE Server（兼容旧客户端）
- `POST /mcp-legacy`：简单 JSON-RPC 调试接口

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt":"用三句话介绍人工智能"}'
```

生产环境建议设置 `AIGC_LITE_API_KEY`，并在反向代理层配置 TLS、限流和日志脱敏。

## 多租户配置

本地单租户模式可以不配置 API Key。启用 `AIGC_LITE_API_KEY` 后，所有 API 请求使用：

```text
Authorization: Bearer your-api-key
```

需要多个租户时使用 JSON 配置，每个租户的数据按 `tenant_id` 隔离：

```dotenv
AIGC_LITE_TENANTS_JSON=[{"id":"team-a","name":"Team A","api_key":"team-a-secret"},{"id":"team-b","name":"Team B","api_key":"team-b-secret"}]
```

## MCP

本地工具通过 `@tool` 注册，工具 JSON Schema 会在发现时根据函数签名、类型注解和 docstring 重新生成；修改代码并重启后不需要单独维护一份参数描述。支持 MCP Streamable HTTP 的客户端可直接连接 `/mcp`。服务端使用官方 Python SDK 的 session manager、协议协商和 `Mcp-Session-Id` 会话机制；`/mcp-sse/sse` 保留 SSE transport 兼容性：

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
```

自定义工具可以放在应用启动代码中导入后注册。Agent 不直接读取全局工具字典，而是在每次 Run 开始时从 Tool Catalog 获取当前 workspace 和 scope 可见的快照。远程 MCP tool 以 `{provider_id}__{tool_name}` 暴露给模型，避免不同服务之间名称冲突。

远程 Streamable HTTP MCP Server 通过环境变量装配。`header_env` 的值是环境变量名，不是密钥本身；对应环境变量在每次建立连接时读取。例如：

```dotenv
RESEARCH_MCP_AUTH=Bearer replace-with-real-token
AIGC_LITE_MCP_SERVERS_JSON=[{"id":"research","url":"http://127.0.0.1:9000/mcp","workspace_id":"default","header_env":{"Authorization":"RESEARCH_MCP_AUTH"},"risk":"low"}]
```

每个配置可选 `risk`（`low`、`medium`、`high`）和 `required_scopes`。`medium` 默认要求 `tools:write`，`high` 默认要求 `tools:high-risk`；管理员和显式配置的 tenant API key 拥有这两个内置 scope，普通成员及无认证 quick start 只发现低风险工具。自定义 scope 预留给后续 scoped API key。单个远程 Provider 发现失败不会影响本地工具或其他 Provider；已经发现的远程工具若调用断连，会生成稳定的 `tool_provider_unavailable` 失败结果和失败 Step。

登录后的 workspace 管理员也可以通过 `/api/mcp-servers` 持久化配置。请求中的认证 header 只能保存凭据引用，不能保存明文：

```json
{
  "provider_id": "research",
  "url": "https://mcp.example.com/mcp",
  "header_credentials": {"Authorization": "env://RESEARCH_MCP_AUTH"},
  "risk": "medium",
  "required_scopes": [],
  "timeout_seconds": 30,
  "enabled": true
}
```

除了默认的 `env://`，管理员可以创建 workspace 隔离的加密凭据：

```json
POST /api/credentials
{"name":"research-token","secret":"Bearer replace-with-real-token"}
```

响应只包含 `configured/source/writable`、生命周期字段和形如
`encrypted-db://credential/UUID` 的引用，绝不返回密钥值。将引用写入 MCP 配置即可：

```json
{
  "provider_id": "research",
  "url": "https://mcp.example.com/mcp",
  "header_credentials": {
    "Authorization": "encrypted-db://credential/00000000-0000-0000-0000-000000000000"
  }
}
```

运行时在每次建立连接时按当前 workspace 解析引用，不缓存明文。替换操作会写入新的
`enc:v1` 密文并恢复可用状态；删除接口执行可审计的撤销，已撤销或跨 workspace 的引用统一表现为 `credential_not_configured`。

配置按 workspace 隔离，每次 Agent Run 发现工具时重新读取，因此禁用、凭据轮换和配置更新不要求重启。凭据值只在建立远程连接时解析且不缓存。`timeout_seconds` 同时限制发现/调用所使用的 HTTP client 和单次工具调用；同步本地 Python 函数会移入工作线程，超时可以释放 Agent，但不能强制终止已经运行的线程，因此有不可逆副作用的本地工具仍需自身实现取消和幂等。

管理员可调用 `POST /api/mcp-servers/{server_id}/probe` 执行一次真实的 `tools/list` 探测。服务只持久化最新投影，不创建第二套调用日志：`healthy/unhealthy`、探测时间、延迟、工具数，以及 `auth_failed`、`timeout`、`protocol_mismatch`、`unreachable` 或 `discovery_failed` 之一；底层异常文本不会写入数据库或返回客户端。

最小工具示例：

```python
from app.tools import tool

@tool()
def get_status() -> dict[str, str]:
    """Return the current application status."""
    return {"status": "ok"}
```

将这个模块在 `app.main` 启动时导入后，入站 MCP `tools/list` 和 Agent 都通过同一个 Tool Catalog 发现它。`/mcp` 与 `/mcp-sse/sse` 会根据登录 Bearer token 或 tenant API key 解析 workspace 和 scope，因此只暴露当前身份允许的本地及远程 MCP tool。`AIGC_LITE_MCP_API_KEY` 仅作为兼容模式保留，它固定映射到 `default` workspace；多 workspace 部署应使用用户 token 或 tenant API key。公开核心不自动启用文件系统、Shell、网络爬取等高风险工具，扩展应由部署方显式注册。

每次入站 `tools/call` 都创建独立 Run 和 Tool Step，MCP 响应的 `_meta.aigc-lite.run_id` 可用于查询执行详情。参数和结果使用与 Agent 相同的账本脱敏规则；客户端仍获得工具原始结果。出站 MCP 请求会附带内部 hop 标记，收到 hop 标记的 aigc-lite 只投影本地工具，避免两个 Catalog 互相代理或配置指回自身时形成递归发现。正式 `/mcp` 的协议版本由官方 SDK 协商，当前 SDK v2 回归测试固定为 `2026-07-28`；`AIGC_LITE_LEGACY_MCP_PROTOCOL_VERSION` 仅影响手写的 `/mcp-legacy`，后者只用于本地调试兼容。

同步 Agent 会把本地和远程 MCP 的每次模型调用、工具调用分别记录为 Step，并记录
`source`、`provider_id`、原始工具名与风险级别。工具参数和结构化结果中常见的
`api_key`、`token`、`password`、`secret` 等嵌套字段，以及常见 AWS/GCS 预签名 URL 参数和 Bearer token，在进入执行账本或审计记录前会统一脱敏；模型实际执行仍接收原始工具结果。任意纯文本中的非结构化秘密仍无法可靠识别，因此涉及敏感数据的工具应返回 JSON 对象。流式响应通过 `X-Run-Id` 和 `X-Session-Id` header 返回追踪身份。

数据库结构由 Alembic 管理。应用启动时会自动执行到最新 revision；首次接管旧数据库时，幂等基线迁移保留已有表和数据，再追加后续字段。部署升级仍应先备份数据库与 `AIGC_LITE_MASTER_KEY`，二者必须成对恢复。

## 开发

```bash
pytest
ruff check app tests
```

核心设计参考了 Goku-AIOS 的环境变量和自托管部署方式、租户治理思路，以及 DeepSeek Harness 和本地 `mcp` 项目的插件化、显式扩展点、MCP session manager 和 Streamable HTTP transport。当前知识检索使用 SQLite 中保存的分块哈希向量，Repository 可切换 PostgreSQL，后续可把 embedding provider 替换为真实模型而不改变 API 使用方式。

## 许可证

本项目使用 MIT License，见 [LICENSE](LICENSE)。
