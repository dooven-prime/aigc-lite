# Execution runtime

This guide describes bounded Agent execution, persistent scheduling, unified
search, and the Run Explorer. These are runtime/workflow guarantees, not
epistemic qualification or external-action authorization.

## Agent 执行边界

每次同步或流式 Run 都受以下环境变量约束：

```dotenv
AIGC_LITE_MAX_AGENT_STEPS=8
AIGC_LITE_MAX_AGENT_TOOL_CALLS=16
AIGC_LITE_MAX_AGENT_RUN_SECONDS=300
AIGC_LITE_DEFAULT_TOOL_TIMEOUT_SECONDS=30
```

模型轮次、工具调用数或总墙钟预算耗尽时，Run 以 `limit_reached` 结束，并分别记录
`agent_model_turn_limit_reached`、`agent_tool_call_limit_reached` 或
`agent_wall_time_limit_reached`；不会把限制提示伪装成成功回答。客户端断连、上游任务取消或
`POST /api/runs/{run_id}/cancel` 会取消当前 asyncio 执行链，传播到正在等待的模型请求和远程
MCP tool，并把 Run 记录为 `cancelled/agent_cancelled`。流式请求可从 `X-Run-Id` 取得取消所需的
Run ID。

主动取消注册表目前是进程内能力，单进程自托管可直接使用；多 worker/多节点部署需要把请求路由
到持有该 Run 的 worker。异步模型和远程 MCP 调用可以被取消。本地 Python 工具可选择 `async`、
`thread` 或 `process` backend；线程模式只能停止等待，进程模式则可在超时或取消时终止独立 worker。

### Chat capability boundary

认证主体拥有某个 scope，不等于模型自动继承该 scope。`GatewayService` 在读取会话、写消息或创建
Run 之前，先由 `ChatCapabilityPolicy` 把调用上下文解析成一个服务端拥有、版本化且内容哈希固定的
capability set：

| set | 模型可见能力 | 额外边界 |
|---|---|---|
| `chat.read-only.v1`（默认） | low-risk、read-only、non-destructive、closed-world 的 local/workspace tool | 清空调用方全部 Tool scope；排除远程 MCP |
| `chat.delegated.v1` | 通过普通 workspace/risk/scope 过滤后的 local/workspace/MCP tool | 调用方先具备 `tools:write`；远程、写入、破坏性或 medium/high-risk 调用再消费窄 `AuthorizationGrant` |

Tool 描述中的 `readOnly` 等 hint 不是权限来源。远程 Provider 即使把自己标成只读，仍由宿主记录的
`source=mcp` 触发授权门；没有 grant 时 provider 不会收到调用。Prompt、system prompt、检索到的
workspace 文档与模型 tool call 都位于策略墙内，不能改变 capability set、scope 或授权账本。
Grant 中的 invocation scope、argument conditions 与 timeout budget 会针对本次已解码参数执行确定性
匹配；只拥有相同 actor/action/target 但约束不覆盖本次调用的 grant 不会被消费，也不会放行 provider。

Run 冻结 `capability_set_id + capability_policy_hash`，每个 Step 冻结同一 decision 和实际生效的
scope。这使后续 Review 可以区分“管理员发起 Chat”和“管理员权限被委托给模型”。调度器的
`agent.chat` payload 不接受 capability set，Verification Runner 的普通 Agent 执行也不自带 Tool
scope；两者默认落在只读集合。需要更强能力时应新增服务端注册、目的单一的 target/profile，并由
独立授权提供执行权，而不是允许计划内容或模型输出提升自己。

### Policy proposal and enforcement evidence

Execution policy 的变更先形成不可变 `PolicyProposal`，而不是直接改当前 runtime。Proposal 同时
冻结 base/candidate policy snapshot、revision 与 hash；`PermissionDiff` 由服务端计算。新增
permission/action、删除 limit、放宽 limit 都是扩权；constraint 的领域语义尚未由专用 verifier
证明时，任何变化也保守地记为扩权。策略必须 `default_action=deny`，candidate revision 必须紧跟
base revision。Genesis proposal 从 revision 0 的空 deny policy 开始。

当前切片只记录 `proposed`，没有 approve/apply API。后续外部 enforcer 应使用 base hash 做 CAS，
防止已过期 proposal 覆盖更新后的 runtime policy。模型、Chat tool 和普通 MCP surface 均不能创建、
批准或应用 policy proposal。

`EnforcementReceipt` 是另一条不可变账本。公共 HTTP 只提供查询；写入只能由 host 注册的 issuer
通过内部 `EnforcementService.record_receipt()` 完成。Receipt 绑定 Run、可选 Step/Proposal、实际
policy hash、execution envelope hash、ToolSpec/arguments digest、decision/outcome、观察或拒绝的
effect、credential binding，以及 host-owned backend identity/trust domain。相同 receipt hash
幂等折叠，跨 workspace 的 Run、Step 或 Proposal 不能绑定。

这仍不是外部 sandbox 本身。issuer registry 只保证应用 composition 不接受客户端自报 identity；
真正的 mTLS、签名验证、remote attestation、policy apply 和 quarantine 由后续具体 backend adapter
负责。在这些 adapter 完成之前，Receipt 的 trust domain 不能高于产生它的实际 enforcement 环境。

ROS2/Nav2 dispatch 一旦提交，即使调用方在 goal handle 返回前取消或超时，bridge 仍保留该 future；
若 Nav2 随后接受目标，bridge 会立即请求取消并形成 Receipt。取消确认超时或失败时 action 保持
`indeterminate` 且继续占用运动槽，直到 Nav2 返回终态或运维处置，不能在“可能仍运动”时开始下一次
导航。

## 持久化任务调度

应用启动时会从 `scheduled_tasks` 恢复活跃计划，并使用进程内多级时间轮建立可丢弃的唤醒索引；
SQLite/PostgreSQL 中的计划定义始终是事实来源。`TaskRunner` 当前注册以下独立目标：

| target | payload | 执行语义 |
|---|---|---|
| `agent.chat` | `prompt`，可选 `system/model/session_id` | 通过 `GatewayService` 创建默认只读 Chat Run；payload 不能选择委托集合 |
| `mcp.probe` | `server_id` | 探测已保存的 MCP Server 并更新最新健康投影 |
| `tool.call` | `name`、可选 `arguments` | 通过 Tool Catalog 执行本地或远程 MCP tool，并记录 Run/Step |
| `http.poll` | `url`、可选 `expected_status/timeout_seconds` | GET allowlist URL，只记录状态、延迟与 Run/Step |
| `research.verify` | `plan_id` | 执行已冻结的有效 Verification Plan，并写回完整研究证据链 |

计划任务统一使用 `system:scheduler` 身份且默认不获得 `tools:write` 或 `tools:high-risk` scope，payload
也不能注入 scope。因此 `tool.call` 只能发现低风险工具；需要更高权限的内部动作应注册成独立、窄
权限 target，而不是提升通用调度身份。

```dotenv
AIGC_LITE_SCHEDULER_ENABLED=true
AIGC_LITE_SCHEDULER_TICK_SECONDS=1
AIGC_LITE_SCHEDULER_RECONCILE_SECONDS=5
AIGC_LITE_SCHEDULER_MAX_CONCURRENCY=4
AIGC_LITE_HTTP_POLL_ALLOWED_HOSTS=status.example.com,api.example.com:8443
```

一次性计划执行后进入 `completed`；周期计划从原计划刻度推进到下一个未来时间，停机期间错过的
周期不会在重启时集中补跑。执行中进程退出时不会确认该次触发，重启恢复后会按 at-least-once
语义重试。workspace 管理员通过 `/api/schedules` 创建、查询、暂停、恢复和取消计划；入口只接受
`TaskRunner` 已注册的目标，创建时即校验目标 payload，并为每次控制操作写入不含 prompt 的审计事件。

```json
POST /api/schedules
{
  "name": "Daily workspace summary",
  "target": "agent.chat",
  "payload": {"prompt": "Summarize pending work", "system": "Be concise."},
  "cadence": "daily",
  "run_at": "2030-01-01T09:00:00+08:00"
}
```

每天和每周任务也可用 `"cadence": "daily"` 或 `"cadence": "weekly"` 简写，此时无需提供
`kind` 和 `interval_seconds`。轮询任务继续使用 `"kind": "interval"` 与所需秒数；这些都是从
`run_at` 起算的固定周期，不是带时区/DST 规则的 cron 日历表达式。

Verification Plan 是不可变版本；创建同一 `plan_key` 的新版本会把旧版本标为 `retired`，已有计划任务
不会悄悄切换到新版本。周期执行使用 target `research.verify` 与 payload `{"plan_id":"..."}`，因此升级
方案后需要显式更新或新建计划任务。Agent 输出必须满足 `research.verification-result.v1` 严格 JSON
合同；有效结果以及无效输出、上游失败或取消都会形成可查询的 execution。只要 Agent Run 已建立，
Runner 还会写入结果/错误 Artifact、Receipt-bound Attempt 和失败关闭的下一阶段 Promotion Gate。
自动 Gate 额外要求触发它的本次 Attempt 为 `passed`，不会借用历史通过记录为一次失败执行顺带
晋升。Agent 执行永远不能自述独立性。Verification Attempt API 不接受 `independent` 或客户端填写的
overlap assessment；principal、workspace、Run、model route、plan hash、checker/toolchain 和数据/环境
摘要由服务端从已有对象绑定，independence 只是 lineage 上的派生投影。旧布尔值仍可读，但不能成为
资格或权限依据。`registered -> release_ready` 只表示 workflow stage，不是证据强度。

## HTTP polling and scheduler control

HTTP 轮询必须在 `AIGC_LITE_HTTP_POLL_ALLOWED_HOSTS` 中逐个配置精确 host 或 `host:port`。它不接受
自定义 header、请求体或 URL 凭据，不跟随重定向，也不读取或保存响应正文。带认证的服务应封装为
使用 Credential Store 的 MCP/local tool，再通过 `tool.call` 调度：

```json
{
  "name": "MCP health poll",
  "target": "mcp.probe",
  "payload": {"server_id": "stored-server-uuid"},
  "kind": "interval",
  "run_at": "2030-01-01T00:00:00Z",
  "interval_seconds": 300
}
```

`pause` 和 `cancel` 会阻止后续触发，但不会强制终止已经开始的 Agent Run；已经运行的实例应使用
`POST /api/runs/{run_id}/cancel`。调度控制当前只对登录的 workspace 管理员开放，普通成员和跨
workspace 查询均被拒绝。

## 统一搜索

`/api/search` 通过独立 `SearchBackend` 查询消息、导入候选消息、文档 chunk、Run Step、Artifact 和 Citation。
SQLite 使用 FTS5 `trigram` 索引，不再受旧版“每类最多扫描 500 条候选”的窗口限制；`0006` migration
会回填已有记录，数据库触发器负责后续新增、更新和删除同步。用户查询会被转换成引用后的 FTS
表达式，不直接执行客户端提供的操作符或列选择器。

少于三个字符的中文/英文查询会走有界词法 fallback；运行时 SQLite 缺少 FTS5 或不支持 trigram
时也会自动降级。PostgreSQL 当前继续使用相同结果契约的词法 backend，后续可以独立替换成原生
全文检索或 embedding backend，而无需修改 `MemoryService` 和 HTTP API。

Conversation Import 将每个外部消息图节点作为 `conversation_message` 候选写入同一搜索契约，
并返回 `import_batch_id`、`conversation_id` 与原始 Artifact 定位。它不会进入 qualified-only
retrieval；导入存储准入与知识资格仍是两条独立路径。

登录后的侧栏 **Search** 是该契约的统一 UI：一次查询同时返回 Conversation、Knowledge、Run
Step、Artifact 和 Citation，并可按来源类型过滤。执行类结果保留完整定位关系；用户可以分别打开
所属 Run，或直接滚动并高亮对应的 Step / Artifact。Conversation 结果也可以直接返回原会话。
跳转通过稳定 ID 再读取详情，不依赖当前 Run 列表是否已经加载到该条记录。

## Run Explorer

登录后可从侧栏进入 **Runs**。页面加载最近 100 个 workspace Run，可按模型、状态、错误码或
Run ID 过滤；详情按照执行顺序展示模型、工具、Agent 和检索 Step，并呈现稳定状态、耗时、来源、
执行 backend 和取消模式等 metadata。Step 输入输出默认使用可折叠的等宽预览，超长内容只在 UI
截断，不改变数据库记录。

同一详情页会展示 Run 产生的 Artifact 和 Citation，包括类型、媒体类型、版本、大小、来源 URI、
摘录与 locator。只有 HTTP(S) URI 会成为可点击的外部链接，其他 MCP/resource URI 仅按文本显示。
窄屏下 Run 列表与详情自动改为上下布局。

生产环境建议设置 `AIGC_LITE_API_KEY`，并在反向代理层配置 TLS、限流和日志脱敏。
