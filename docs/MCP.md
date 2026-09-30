# MCP and Tool Runtime

This guide covers inbound and outbound MCP, the shared Tool Catalog, encrypted
workspace credentials, tool execution backends, and the optional ROS 2
capability provider. Production credential rules remain normative in
[PRODUCTION.md](PRODUCTION.md).

## Workspace capability surface

Direct MCP clients and the built-in Agent discover a context-bound, read-only
workspace provider in addition to ordinary Python and remote MCP tools:

| Tool | Purpose |
|---|---|
| `workspace_search` | Search candidate conversations, knowledge, Steps, Artifacts, Citations, and Claims |
| `workspace_qualified_search` | Search only current ClaimRevisions for an explicit qualification profile |
| `workspace_get_run` | Read one Run and bounded Step content previews |
| `workspace_get_artifact` | Read Artifact identity, provenance, bounded content, and Citations |
| `workspace_get_claim` | Read one exact ClaimRevision and bounded verification/promotion history |
| `workspace_get_qualification_receipt` | Read an immutable qualification certificate |
| `workspace_list_qualification_profiles` | Discover server-owned profile and policy versions |

The provider is created for each authenticated `RequestContext`; clients cannot
submit a workspace or principal argument. It calls application services
directly rather than making loopback HTTP requests. All tools carry MCP
read-only/non-destructive/idempotent hints, use `openWorld=false`, reject
unknown arguments, and return the envelope:

```json
{
  "schema": "aigc-lite.workspace-capability.v1",
  "capability": "workspace_get_run",
  "result": {}
}
```

Large fields are returned as explicit bounded previews with original character
counts and truncation flags, so result limiting never produces invalid JSON.
Calls still pass through `ToolService` and therefore create an observable
Run/Step with `source=workspace`. These capabilities cannot create or promote
Claims, evaluate a Gate, change a current-use binding, issue authorization, or
execute an external action. Qualified search may deterministically mark stale
bindings while refreshing evidence closure, but it can never grant authority.

## MCP transport

本地工具通过 `@tool` 注册，工具 JSON Schema 会在发现时根据函数签名、类型注解和 docstring 重新生成；修改代码并重启后不需要单独维护一份参数描述。支持 MCP Streamable HTTP 的客户端应连接规范地址 `/mcp/`。服务端使用官方 Python SDK 的 session manager、协议协商和 `Mcp-Session-Id` 会话机制；`/mcp-sse/sse` 保留 SSE transport 兼容性：

当前 MCP SDK 创建的 `httpx2.AsyncClient` 默认 `follow_redirects=false`，因此协议 POST 不应依赖 Mount root 的 307。aigc-lite 会把完全匹配的兼容地址 `/mcp` 在 ASGI 边界内直接改写为 `/mcp/`，不会向客户端返回重定向；新配置仍必须写带尾斜杠的规范地址。

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
```

自定义工具可以放在应用启动代码中导入后注册。Agent 不直接读取全局工具字典，而是在每次 Run 开始时从 Tool Catalog 获取当前 workspace 和 scope 可见的快照。远程 MCP tool 以 `{provider_id}__{tool_name}` 暴露给模型，避免不同服务之间名称冲突。

Tool Catalog 是共同的发现/执行机制，但 transport 的授权入口不同。直接连接 `/mcp/` 的客户端使用
自己的认证 identity 与 scope；`/api/chat` 中的模型还要经过 `ChatCapabilityPolicy`。Chat 默认
`chat.read-only.v1` 会剥离调用方管理 scope，并完全排除远程 MCP；显式
`chat.delegated.v1` 只允许拥有 `tools:write` 的调用方请求，且每个远程 MCP 调用无论其 hint 如何，
仍必须在 provider dispatch 前消费当前 `AuthorizationGrant`。因此“管理员能配置/直连某个 MCP”
不会自动变成“管理员发起的任意 Chat 都能让模型调用它”。

部署者可以通过环境变量静态装配远程 Streamable HTTP MCP Server。`header_env` 的值是
环境变量名，不是密钥本身；只有同时列入
`AIGC_LITE_MCP_ENV_CREDENTIAL_ALLOWLIST` 的变量才可读取。例如：

```dotenv
RESEARCH_MCP_AUTH=Bearer replace-with-real-token
AIGC_LITE_MCP_ENV_CREDENTIAL_ALLOWLIST=RESEARCH_MCP_AUTH
AIGC_LITE_MCP_SERVERS_JSON=[{"id":"research","url":"http://127.0.0.1:9000/mcp/","workspace_id":"default","header_env":{"Authorization":"RESEARCH_MCP_AUTH"},"risk":"low"}]
```

每个配置可选 `risk`（`low`、`medium`、`high`）和 `required_scopes`。`medium` 默认要求 `tools:write`，`high` 默认要求 `tools:high-risk`；管理员和显式配置的 tenant API key 拥有这两个内置 scope，普通成员及无认证 quick start 只发现低风险工具。自定义 scope 预留给后续 scoped API key。单个远程 Provider 发现失败不会影响本地工具或其他 Provider；已经发现的远程工具若调用断连，会生成稳定的 `tool_provider_unavailable` 失败结果和失败 Step。

远程 tool 还可以通过 `_meta.aigc-lite` 声明更严格的单工具风险、scope、超时上限和通用扩展元数据。声明只能提升 Provider 的最低风险、追加 scope 或缩短超时，不能由远端自行降权。管理员额外 scope 必须由部署方通过 `AIGC_LITE_ADMIN_TOOL_SCOPES` 显式授予。

登录后的 workspace 管理员也可以通过 `/api/mcp-servers` 持久化配置。请求中的认证 header 只能保存凭据引用，不能保存明文：

```json
{
  "provider_id": "research",
  "url": "https://mcp.example.com/mcp/",
  "header_credentials": {
    "Authorization": "encrypted-db://credential/00000000-0000-0000-0000-000000000000"
  },
  "risk": "medium",
  "required_scopes": [],
  "timeout_seconds": 30,
  "enabled": true
}
```

workspace 持久化配置只接受同一 workspace 中处于 active 状态的
`encrypted-db://credential/UUID`，不接受任何 `env://NAME`。因此 workspace 管理员不能借助
任意 MCP 地址读取服务进程环境。管理员先创建 workspace 隔离的加密凭据：

```json
POST /api/credentials
{"name":"research-token","secret":"Bearer replace-with-real-token"}
```

响应只包含 `configured/source/writable`、生命周期字段和形如
`encrypted-db://credential/UUID` 的引用，绝不返回密钥值。将引用写入 MCP 配置即可：

```json
{
  "provider_id": "research",
  "url": "https://mcp.example.com/mcp/",
  "header_credentials": {
    "Authorization": "encrypted-db://credential/00000000-0000-0000-0000-000000000000"
  }
}
```

运行时在每次建立连接时按当前 workspace 解析引用，不缓存明文。替换操作会写入新的
`enc:v1` 密文并恢复可用状态；删除接口执行可审计的撤销，已撤销或跨 workspace 的引用统一表现为 `credential_not_configured`。

workspace 模型配置遵循同一边界：自定义 HTTPS endpoint 必须原子绑定自己的 active
`credential_reference`，不会继承 `AIGC_LITE_LLM_API_KEY`。平台级 LLM 环境变量只服务于
未选择 workspace model config 的默认路由；自定义 client 禁止跟随 HTTP redirect。

配置按 workspace 隔离，每次 Agent Run 发现工具时重新读取，因此禁用、凭据轮换和配置更新不要求重启。凭据值只在建立远程连接时解析且不缓存。`timeout_seconds` 同时限制发现/调用所使用的 HTTP client 和单次工具调用。

管理员可调用 `POST /api/mcp-servers/{server_id}/probe` 执行一次真实的 `tools/list` 探测。服务只持久化最新投影，不创建第二套调用日志：`healthy/unhealthy`、探测时间、延迟、工具数，以及 `auth_failed`、`timeout`、`protocol_mismatch`、`unreachable` 或 `discovery_failed` 之一；底层异常文本不会写入数据库或返回客户端。

最小工具示例：

```python
from app.tools import tool

@tool()
def get_status() -> dict[str, str]:
    """Return the current application status."""
    return {"status": "ok"}
```

本地 Tool Execution Backend 按函数类型选择默认模式：`async def` 使用 `async`，普通 `def`
使用 `thread`。可以在注册时覆盖单工具超时，或显式使用可硬终止的独立进程：

```python
from app.tools import tool

@tool(timeout_seconds=10)
async def fetch_status() -> dict:
    """Use cooperative asyncio cancellation."""
    ...

@tool(execution="thread", timeout_seconds=5)
def parse_small_file(path: str) -> dict:
    """Run blocking code in a thread; timeout only stops waiting."""
    ...

@tool(execution="process", timeout_seconds=30)
def render_large_document(path: str) -> dict:
    """Run in a disposable worker that can be terminated."""
    ...
```

Run/Step metadata 会记录 `execution_mode` 和 `cancellation_mode`：异步是 `cooperative`，
线程是 `soft`，进程是 `hard`。进程工具必须是可导入模块中的同步顶层函数；参数和结果应为 JSON
兼容值，也不能依赖父进程中的可变内存状态。每次调用都会创建一个 spawn worker，因此适合需要硬
超时边界的高风险、阻塞或 CPU 型工具，不适合大量细碎调用。`process` 只是生命周期隔离，不是安全
沙箱：worker 仍继承服务进程的环境变量以及文件、网络和系统权限；执行不受信任代码仍需容器或
专用 sandbox。

将这个模块在 `app.main` 启动时导入后，入站 MCP `tools/list` 和 Agent 都通过同一个 Tool Catalog 发现它。`/mcp/` 与 `/mcp-sse/sse` 会根据登录 Bearer token 或 tenant API key 解析 workspace 和 scope，因此只暴露当前身份允许的本地及远程 MCP tool。`AIGC_LITE_MCP_API_KEY` 仅作为兼容模式保留，它固定映射到 `default` workspace；多 workspace 部署应使用用户 token 或 tenant API key。公开核心不自动启用文件系统、Shell、网络爬取等高风险工具，扩展应由部署方显式注册。

每次入站 `tools/call` 都创建独立 Run 和 Tool Step，MCP 响应的 `_meta.aigc-lite.run_id` 可用于查询执行详情。参数和结果使用与 Agent 相同的账本脱敏规则；客户端仍获得工具原始结果。出站 MCP 请求会附带内部 hop 标记，收到 hop 标记的 aigc-lite 只投影本地工具，避免两个 Catalog 互相代理或配置指回自身时形成递归发现。正式 `/mcp/` 的协议版本由官方 SDK 协商，当前 SDK v2 回归测试固定为 `2026-07-28`；`AIGC_LITE_LEGACY_MCP_PROTOCOL_VERSION` 仅影响手写的 `/mcp-legacy`，后者只用于本地调试兼容。

### ROS 2 Physical Capability Bridge

`extensions/ros2-bridge` 是独立发行包和进程，核心不导入 ROS 2。它先提供
`robot_get_state`、`robot_inspect`、`robot_navigate_to` 与 `robot_cancel_action`，通过现有远程 MCP
Provider 自动进入 Tool Catalog、Run/Step、结构化 Artifact 和 Citation。导航要求幂等键，收据明确
区分 simulation/hardware、成功、失败、取消、状态不确定和停止是否确认。没有 ROS 的环境可用确定性
SimulatorBackend 完整验证；安装并 source ROS 2/Nav2 后再切换 Nav2Backend。安装、配置、验证边界和
安全要求见 [ROS 2 bridge README](../extensions/ros2-bridge/README.md)。

Agent/MCP 的取消不是急停。真实机器人必须把 e-stop、安全 PLC/控制器、碰撞保护、速度限制和 watchdog
保留在独立的物理安全路径中，不能依赖模型、网络、Python event loop 或 Tool Catalog。

同步 Agent 会把本地和远程 MCP 的每次模型调用、工具调用分别记录为 Step，并记录
`source`、`provider_id`、原始工具名与风险级别。工具参数和结构化结果中常见的
`api_key`、`token`、`password`、`secret` 等嵌套字段，以及常见 AWS/GCS 预签名 URL 参数和 Bearer token，在进入执行账本或审计记录前会统一脱敏；模型实际执行仍接收原始工具结果。任意纯文本中的非结构化秘密仍无法可靠识别，因此涉及敏感数据的工具应返回 JSON 对象。流式响应通过 `X-Run-Id` 和 `X-Session-Id` header 返回追踪身份。

数据库结构由 Alembic 管理。应用启动时会自动执行到最新 revision；首次接管旧数据库时，幂等基线迁移保留已有表和数据，再追加后续字段。部署升级仍应先备份数据库与 `AIGC_LITE_MASTER_KEY`，二者必须成对恢复。
