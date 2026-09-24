# aigc-lite 产品与架构设计

状态：Draft，面向 `0.3` 重构周期。

## 1. 产品定义

`aigc-lite` 是一个可自托管、以可搜索执行记忆为核心的 AI 工作台与 Agent/MCP
运行基础。模型接入是内部基础设施，不把与成熟通用 Gateway 竞争作为产品目标。
系统的长期价值来自可积累、可查询、可复用的 Conversation、Run、Step、ToolCall、
Artifact、Citation 和 Decision/Memory。

目标用户：

- 个人开发者：保留和搜索长期对话、研究过程、工具结果与生成物；
- 小团队：按 workspace 隔离知识、执行记录、模型凭据和 MCP 工具；
- Agent 开发者：检查一次任务调用了什么、在哪一步失败、产生了哪些证据和产物；
- 扩展作者：在不修改核心的情况下增加 tool、MCP server、workflow 和 knowledge backend。

一句话承诺：

> 让 Agent 做过的事不再消失在一次响应里，而是成为可搜索、可追踪、可复用的工作记忆。

### 明确不做

以下能力不进入公开核心：

- 企业专属 MySQL、GitLab、微信、OSS、风控和内部平台连接器；
- 默认开放 Shell、文件系统、浏览器、代码执行等高风险工具；
- 把 Redis、Neo4j、Qdrant、Dask 设为基础依赖；
- 在没有真实兼容性测试前宣称支持某个具体模型厂商；
- 继续扩张通用 Gateway 的协议覆盖、负载均衡和高并发卖点；
- 在 `0.x` 阶段同时实现复杂 workflow、多 Agent 协作和分布式调度；
- 用新的业务对象包装并复制 OpenAI/MCP 已经定义的协议语义。

知识库和历史会话是执行记忆的来源；后续 ToolCall、Artifact、Citation 与结构化
Decision 也必须通过同一个 workspace 检索边界进入，而不是形成互不相通的数据岛。

## 2. 从现状出发的判断

当前仓库已经完成一条可运行的纵向切片：FastAPI、SQLite/PostgreSQL
Repository、用户与租户、模型配置、会话、简单检索、Agent 工具循环、MCP
Server、审计、用量和 Web UI。现有测试、Ruff 和前端构建均可通过。

项目不再以补齐完整 AI Gateway 为目标，但以下实现问题仍需要作为内部基础设施修复：

1. provider 配置中的公开名称和上游模型名混在一起。请求指定配置名称时，
   当前逻辑可能把该名称原样发给上游；
2. `/api/chat` 运行工具循环，`/api/chat/stream` 却只做模型流式转发，两条路径
   语义不一致；
3. 本地与远程 MCP 工具已进入统一 Catalog，具备 workspace、来源、风险权限、结果
   上限、单工具 timeout 与持久化配置；后续需增加更细的调用预算和取消协议；
4. 入站 MCP 已按调用方 workspace/scope 投影 Catalog，并记录独立 Run/Step；兼容用
   `AIGC_LITE_MCP_API_KEY` 仍固定映射 `default` workspace，不作为多租户认证方案；
5. `admin` 实际是 workspace admin，但现有全局租户管理接口允许任意 workspace
   admin 查看和创建其他租户；
6. 已拆分登录签名密钥与凭据 master key，并使用版本化认证密文；后续仍需增加
   master key 轮换和批量重加密流程；
7. Schema 通过应用启动时执行 DDL，没有版本化迁移，也没有 SQLite/PostgreSQL
   一致性测试；
8. Run/Step 已有首个同步聊天切片，但工具调用、流式事件、Artifact 和 Citation
   尚未接入执行账本；
9. `main.py` 同时承担装配、协议模型、认证中间件、路由和业务编排，继续增加功能
    会快速放大耦合。

因此下一阶段的重点是执行记忆、工具治理和可搜索产物，而不是扩张 Gateway 功能或
迁移旧项目的更多模块。

## 3. 目标架构

```text
User / API / MCP
       |
       v
 AgentService -> Run -> Step/Event -> ToolCatalog -> Local tools / MCP clients
       |           |         |              |
       |           |         |              +-> permission / budget / audit
       |           |         +-> ToolCall / Citation
       |           +-> Artifact / Decision / Memory
       +-> Model Access -> ProviderAdapter -> Upstream LLM
                         |
                         v
          Workspace Search (conversation + knowledge + execution + artifacts)
```

核心规则：HTTP、UI、Python facade 和 MCP 只是 transport/consumer；它们必须调用
同一个 application service，不得各自实现模型路由、工具授权或 Agent 状态机。

### 3.1 四层职责

| 层 | 所有权 | 不应包含 |
|---|---|---|
| Contract | 请求、响应、错误、事件、状态和公共类型 | HTTP client、SQL、环境变量读取 |
| Application | Gateway、Agent、Tool 调用的用例编排 | FastAPI 对象、具体数据库语句 |
| Port | Provider、Credential、Repository、Tool、EventSink 接口 | 厂商特有实现 |
| Adapter | OpenAI-compatible 上游、SQLite/PostgreSQL、Env Secret、MCP transport | 跨 adapter 的业务决策 |

借鉴 `deepseek-harness` 的 capability seam，但避免过早拆成多个发行包。每个可替换能力
先在代码层区分三个角色：

- Service Definition：核心拥有的最小端口和词汇；
- Service Provider：具体实现，例如 OpenAI-compatible、SQLite、Env Credential；
- Consumer：HTTP route、Agent loop、MCP tool projection。

出现第二个真实 Provider 或独立发布需求后，再拆 Python distribution。

### 3.2 建议目录

`0.3` 先在单一包内完成边界，不做仓库级微服务化：

```text
app/
  api/
    openai.py          # /v1/chat/completions, /v1/models
    console.py         # UI 所需会话和知识接口
    admin.py           # workspace 管理接口
    dependencies.py    # AuthContext / RequestContext
  core/
    contracts.py       # transport-neutral DTO 与枚举
    errors.py          # 稳定错误分类
    policies.py        # limit、tool permission、route policy
  services/
    gateway.py         # completion/stream 的唯一编排入口
    agents.py          # run/step/event 状态机
    tools.py           # ToolCatalog 与调用策略
  ports/
    providers.py
    credentials.py
    repositories.py
    events.py
  adapters/
    providers/openai_compatible.py
    credentials/env.py
    credentials/encrypted_db.py
    storage/sqlite.py
    storage/postgres.py
    tools/local.py
    tools/mcp.py
  transports/
    mcp_server.py
  bootstrap.py         # 唯一 composition root
  main.py              # 创建 ASGI app 和 CLI 启动，保持薄
```

迁移采用逐模块抽取，不进行一次性重写。现有 public route 在替代接口稳定前保留。

## 4. 核心领域契约

### 4.1 请求上下文

所有 application service 显式接收 `RequestContext`，禁止从全局变量猜租户：

```python
@dataclass(frozen=True)
class RequestContext:
    request_id: str
    workspace_id: str
    principal_id: str | None
    api_key_id: str | None
    scopes: frozenset[str]
```

认证 transport 负责创建上下文；业务服务只消费上下文。任何 Repository 查询都必须
显式包含 `workspace_id`。

### 4.2 Provider 与模型路由

把“上游账户”和“下游可见模型”拆开：

```text
ProviderAccount
  id, workspace_id, adapter, base_url, credential_ref, enabled

ModelRoute
  id, workspace_id, public_model, provider_id, upstream_model,
  timeout_ms, max_retries, input_price, output_price, enabled
```

下游请求 `model="general"` 时，路由解析成某个 `ProviderAccount` 和
`upstream_model="deepseek-chat"`。公开模型名永远不直接当作上游模型名。

`0.3` 只保证 OpenAI Chat Completions 的已实现子集。支持矩阵应按参数和行为记录，
而不是按厂商名称记录：普通响应、SSE、tools、usage、JSON mode、stop、错误映射等。
`/v1/responses` 在拥有独立契约测试前不提供占位实现。

### 4.3 凭据引用

配置保存 reference，不保存或传播 secret value：

```text
CredentialRef: env://OPENAI_API_KEY
CredentialRef: encrypted-db://provider/<provider-id>
```

规则：

- consumer 在每次上游操作边界解析一次，不跨操作缓存 secret；
- API 只返回 `configured/source/writable`，永不返回值；
- 环境变量 Provider 是默认核心能力；
- encrypted-db Provider 使用独立 `AIGC_LITE_MASTER_KEY`，不得从登录签名密钥派生；
- 生产模式发现默认密钥、空 master key 或无法解密的记录时启动失败；
- 日志、异常、审计 metadata 和 trace attributes 统一经过脱敏器。

### 4.4 API key 与角色

区分三个概念：

- `platform_admin`：部署级管理，仅由显式 bootstrap 配置创建；
- `workspace_admin`：管理自己 workspace 的成员、路由和 key；
- `member/service_account`：按 scope 使用数据面。

下游 API key 入库存储 hash，只在创建时返回一次明文，并带可识别前缀、scope、
过期时间和撤销时间。建议 scope：

```text
models:read  chat:write  agents:run  tools:call  knowledge:read
knowledge:write  workspace:admin  platform:admin
```

环境变量单 key 模式仅作为单用户 quick start，不与数据库用户体系混成同一种身份。

### 4.5 Agent Run

会话是用户内容容器，Run 是一次执行尝试，两者不可混同：

```text
Run: queued -> running -> waiting_tool -> running -> succeeded|failed|cancelled|limit_reached
Step: model | tool
Event: run.started | model.delta | tool.started | tool.completed | run.completed | run.failed
```

每个事件含 `run_id`、单调递增 `sequence`、时间和类型化 payload。同步 HTTP、SSE、
后台执行和未来 WebSocket 都投影同一条事件流。

至少提供以下预算：

- 最大模型轮次；
- 最大工具调用数；
- 最大墙钟时间；
- 单工具超时与结果字节数；
- 最大输入/输出 token；
- 请求取消传播。

当前实现已经覆盖模型轮次、工具调用数、总墙钟、单工具超时/结果长度和 asyncio
取消传播。预算耗尽统一写入 `limit_reached`，主动取消写入 `cancelled`；模型轮次耗尽不再以
普通文本冒充成功结果。`POST /api/runs/{run_id}/cancel` 使用进程内 active-run handle，适用于
当前单进程部署；多 worker/后台执行时必须替换为带 owner/lease 的持久化调度句柄。同步本地
Python 工具默认进入工作线程，无法强杀；需要硬终止的工具可显式选择一次一进程的 process
backend。token 预算仍留待模型调用契约能够可靠提供 usage/finish reason 后实现。

模型的 `finish_reason`、Run 状态和工具结果状态是不同字段，不能互相代替。

### 4.6 Tool Catalog 与 MCP

本地函数和远程 MCP tool 都适配成统一 `ToolSpec`：

```text
name, description, input_schema, source, workspace_id,
risk_level, side_effects, required_scopes, timeout_ms, enabled,
execution_mode, cancellation_mode
```

调用路径固定为：发现 -> policy filter -> 参数校验 -> 执行 -> 结果截断/结构化 -> 审计。
Agent 只能看到当前上下文允许的工具，而不是整个进程的注册表。

本地执行固定经过 `ToolExecutionBackend` port：异步函数使用协作取消，普通同步函数默认使用
thread 软取消，显式 `execution=process` 的同步顶层函数使用 spawn worker，并在超时或取消时执行
terminate/kill。进程隔离提供可回收的生命周期，不视为权限 sandbox；不受信任代码必须进入容器
或外部 sandbox service。执行方式和取消保证写入 Tool Step metadata。

MCP 有两个方向：

- 出站 MCP Client 是 Tool Provider，负责 session、鉴权 header、重连和 tool refresh；
- 入站 MCP Server 是 Tool Catalog/Application Service 的 transport projection。

两者都必须使用 workspace 身份和同一套权限策略。`mcp-legacy` 只作调试兼容，标记
deprecated，不扩展其能力。

### 4.7 可观测性与审计

最小关联键：

```text
request_id -> run_id -> step_id -> upstream_request_id / tool_call_id
```

用量账本记录 workspace、API key、public model、provider route、token、费用、延迟、
状态和时间。费用是可重算的估算值，价格版本或请求时价格快照必须保留。

审计记录安全与管理事件；运行 trace 记录执行事实；应用日志用于诊断。三者分开，
且都不保存 prompt、文档内容、secret 或完整 tool result，除非部署者显式开启内容记录。

## 5. HTTP 表面

### 数据面

```text
GET  /v1/models
POST /v1/chat/completions
```

`/v1/chat/completions` 尽量保持 OpenAI Chat Completions 的请求、响应、SSE chunk 和
错误 envelope。服务自有字段放响应 header 或明确的扩展对象，不污染标准字段。

### Agent/工作台面

```text
POST /api/v1/sessions
GET  /api/v1/sessions
POST /api/v1/agent-runs
GET  /api/v1/agent-runs/{run_id}
GET  /api/v1/agent-runs/{run_id}/events
POST /api/v1/agent-runs/{run_id}/cancel
```

### 控制面

```text
/api/v1/providers
/api/v1/model-routes
/api/v1/api-keys
/api/v1/mcp-servers
/api/v1/tools
/api/v1/usage
/api/v1/audit
```

现有 `/api/chat` 与 `/api/chat/stream` 可以在 `0.3` 内部转调新的 service，`0.4`
标记 deprecated。不要让 console API 反向成为公共 SDK 契约。

## 6. 存储与迁移

- 已引入 Alembic 显式 migration；后续增加 PostgreSQL contract CI 和升级/回滚演练；
- SQLite 是单进程 quick start，开启 foreign key、WAL 和合理 busy timeout；
- PostgreSQL 是多实例推荐后端；CI 对两者运行同一组 repository contract tests；
- Repository 按聚合拆分为 `IdentityRepository`、`GatewayRepository`、
  `RunRepository`、`KnowledgeRepository`，避免一个超大 Protocol；
- 删除、撤销、禁用等治理状态保留显式时间，不用物理删除关键审计对象；
- 备份/恢复文档至少覆盖数据库、master key 和外部对象存储三者的一致性要求。

## 7. 安全基线

发布前必须满足：

1. 将旧 `aigc` 明确定义为私有来源边界：不得复制其 `config.yaml`、凭据或公司专属地址，
   迁移代码前执行 secret scan；
2. `aigc-lite` 和示例中只出现不可用占位符，CI 增加 secret scan；
3. 修复 platform/workspace admin 越权边界；
4. 生产模式禁止默认 `auth_secret`、默认开放注册和无认证 MCP；
5. provider/MCP URL 做 scheme、host、DNS/IP 与重定向校验，默认阻止云 metadata、
   loopback 和私网 SSRF；本地开发通过显式 allowlist 放行 Ollama/vLLM；
6. API key、登录、注册、模型调用和 tool call 分别限流；多实例使用共享 limiter；
7. 工具默认 deny，副作用工具要求更高 scope；高风险执行器不进入默认安装；
8. 上传限制同时覆盖 body bytes、解压后大小、解析时间和保存配额；
9. 明确 trusted proxy、HTTPS、CORS、Host header 和 cookie/token 部署要求；
10. 对租户隔离、IDOR、SSRF、secret redaction 和 tool authorization 建立负向测试。

## 8. 版本路线

### P0：发布基线

- 固化旧 `aigc` 的私有边界，保证配置、凭据和公司连接器不进入公开仓库；
- 冻结 `aigc-lite` 当前可运行基线，建立首个 Git commit/tag；
- 统一 Python、API、前端版本号；
- 增加 secret scan、dependency audit 和最小安全说明。

### M1：`0.3` Execution Memory vertical slice

- 引入 `RequestContext`、稳定错误分类和 composition root；
- 建立 workspace 隔离的 Run/Step 状态与稳定错误码；
- 同步聊天写入执行账本，失败记录不保存敏感诊断文本；
- 提供运行详情和跨 conversation/knowledge/run step 的统一搜索；
- 将流式聊天迁入同一 Run/Event 语义；
- 为 ToolCall、Artifact 和 Citation 建立可扩展载体；
- SQLite 先提供有界词法检索，后续替换为 FTS/可选 embedding backend。

验收：一次成功或失败执行都能按 `run_id` 找到状态和步骤；同一 workspace 能搜索到
历史对话、知识与执行结果；两个 workspace 无法互读任何记录。

### M2：`0.4` MCP Tool Workspace

- 将 Run/Step 扩展到流式事件和逐次工具调用；
- `/api/chat` 和 streaming console 统一转调 `AgentService`；
- Tool Catalog 支持 JSON Schema、权限、超时、结果上限和审计；
- 使用官方 SDK 完成远程 MCP session client，并适配为 Tool Provider；
- MCP Server 投影同一 Tool Catalog；
- 增加恶意参数、超时、断连、重复 tool call、取消和预算耗尽测试。

验收：同一个 Agent run 可通过同步结果或事件流观察；本地/MCP 工具遵守同一权限和预算；
禁用工具不会出现在模型或 MCP 的发现结果中。

### M3：`0.5` Artifact 与特色工作流

- Artifact、Citation、Decision/Memory 的版本和来源闭环；
- 以多模型研究/讨论/共识作为首个上层工作流；
- 结果可回到 workspace search，并能追溯对应 Run、模型和工具；
- 工作流保持可选，不把多 Agent 调度变成核心强依赖。

### M4：可运营自托管版

- platform/workspace 角色闭环；
- Alembic migration 与 PostgreSQL contract CI；
- 配额、持久化限流、审计查询和使用量维度；
- credential reference 管理与 master key 轮换流程；
- health/readiness、结构化日志、OpenTelemetry 可选输出；
- 备份恢复和版本升级文档。

### M5：扩展生态

- 将 knowledge/embedding/vector store 改成独立 capability seam；
- 定义 Python entry point 插件发现和 manifest；
- 选择性迁移旧项目连接器到独立扩展仓库；
- 只有出现真实需求后再增加 workflow、后台 job 或分布式队列。

## 9. 下一轮实现顺序

建议按下列小 PR/commit 顺序推进，每一步都保持现有测试可运行：

1. 已完成：架构回归测试、tenant isolation、MCP transport 和 fake upstream；
2. 已完成：`core/contracts.py`、稳定错误类型和 application service 边界；
3. 已完成：同步聊天 Run/Step 账本、运行查询和统一搜索首个切片；
4. 已完成：streaming chat 进入同一 Run 生命周期，完成、失败和取消可追踪；
5. 已完成：抽取 Tool Catalog；本地与远程 MCP tool 共享发现、workspace/risk/scope
   权限、结果上限、脱敏和 Tool Step；Provider 发现与调用失败使用稳定安全投影；
6. 已完成：环境变量与 workspace 持久化 MCP server 配置、`env://` Credential
   Provider、动态配置刷新、单工具 timeout、入站 MCP 的 workspace Catalog 投影、
   独立版本化 master key、统一账本/审计脱敏、Alembic migration、带稳定错误码的
   MCP 探测状态，以及 write-only `encrypted-db://credential/UUID` Credential Provider；
   Agent 模型轮次、工具次数和墙钟预算，稳定 `limit_reached` 状态，以及模型/MCP 等待链的
   取消传播与 active-run 取消接口；本地 Tool Execution Backend 的 async/thread/process
   三种隔离模式、单工具超时和执行 metadata；
7. 增加 Artifact/Citation 载体，并接入统一搜索；
8. 将 SQLite 词法检索升级为 FTS，可选接入 embedding provider；
9. 最后迁移 UI，让 UI 展示 Run、步骤、来源和产物。

每个切片都必须产生可查询的真实纵向行为，不为了目录完整度创建没有 consumer 的抽象。

## 10. 设计决策摘要

- 核心定位：Execution Memory first，Agent/MCP workspace second，Gateway internal；
- 模型访问：维持必要的 OpenAI-compatible adapter，不扩张通用 Gateway 产品面；
- 多租户主键：所有操作显式携带 workspace context；
- 模型治理：公开模型路由与上游账户分离；
- Secret：引用优先，每次操作解析，独立 master key；
- Agent：持久化 Run/Step/Event，预算受限；
- Tool：统一 Catalog，本地与 MCP 同策同审计；
- Transport：HTTP、MCP、UI 共享 application service；
- 存储：显式 migration，同一 repository contract 覆盖 SQLite/PostgreSQL；
- 扩展：先代码内 seam，第二个真实实现出现后再拆包。
