# aigc-lite

[![M8ven Score](https://m8ven.ai/badge/mcp/dooven-prime/aigc-lite)](https://m8ven.ai/mcp/dooven-prime/aigc-lite)

> M8ven 徽章表示第三方对公开 MCP 源码的扫描状态，不构成本项目 Qualification Plane 的资格或授权结论。

一个可自托管、以可搜索执行记忆为核心的 AI 工作台与 Agent/MCP 运行时。项目通过 OpenAI-compatible 上游调用模型，但不以重复建设通用 AI Gateway 为目标；重点是持久化对话、Agent Run、执行步骤、知识和后续工具产物，让运行过程可以查询、搜索和复用。

## 当前范围

- FastAPI HTTP API 与 `/health` 健康检查
- 同步聊天和 SSE 流式聊天
- 环境变量配置，不在代码中保存密钥
- 独立 master key、版本化密文与严格解密错误
- 有模型轮次、工具调用数、墙钟时间和单工具边界的 Agent 运行入口
- workspace-aware Tool Catalog，本地函数与远程 MCP tool 统一发现、授权和调用
- SQLite 会话持久化和租户级数据隔离
- 同步与流式 Agent Run/Step 执行账本，记录模型、工具、成功失败和稳定错误码
- 跨对话、知识文档、执行步骤、Artifact 和 Citation 的租户级统一搜索
- 可追溯到 Run/Step 的 Artifact/Citation；远程 MCP 结构化结果和资源自动归档
- 文本、Markdown、CSV、JSON 知识库上传与检索
- MCP-compatible `/mcp` JSON-RPC Server，以及远程 MCP Client
- 官方 MCP SDK 的 Streamable HTTP `/mcp` 和 SSE `/mcp-sse/sse` transport
- `/ui` 聊天、知识库与执行记忆工作台，包含 Run Explorer
- 跨领域 Protocol/Claim/Receipt/Review/Freeze 证据控制层，以及首个 Decision Lab
- 可导入版本化 Claim Registry 的 Research Workspace，以及支持类型化关系、版本化 Verification Plan、Agent/计划任务验证、验证收据和逐级晋升门的 Claim Explorer
- Qualification Plane：领域 Verifier Registry、冻结 ClaimRevision、证据闭包、确定性 Gate、可携带 Qualification Receipt、独立授权账本和 qualified-only retrieval
- 可选的独立 ROS 2 Physical Capability Bridge：模拟器与 Nav2 backend 通过 MCP 投影，复用权限、预算、取消、Run/Step、Artifact 和 Receipt
- 可作为 Python 包或独立服务运行

企业私有业务、数据库连接器、第三方平台凭据和运行时数据不属于公开核心，将通过独立扩展接入。

项目下一阶段的产品边界、目标架构、核心契约和 `0.3+` 路线见
[`docs/DESIGN.md`](docs/DESIGN.md)。总体原则是 Execution Memory first：模型调用只是内部能力，Agent、MCP 与 UI
共享同一套 Run/Step、工具、产物和检索基础。

版本变化见 [`CHANGELOG.md`](CHANGELOG.md)，安全部署边界与漏洞报告方式见
[`SECURITY.md`](SECURITY.md)。当前 `v0.3.0` 的本地冻结范围和验证记录见
[`docs/releases/v0.3.0.md`](docs/releases/v0.3.0.md)，初始 `v0.2.0` 基线保留在
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

MiniMax Token Plan 使用当前的 OpenAI-compatible 地址和 M 系列模型，例如：

```dotenv
AIGC_LITE_LLM_API_KEY=your-sk-cp-key
AIGC_LITE_LLM_BASE_URL=https://api.minimaxi.com/v1
AIGC_LITE_LLM_MODEL=MiniMax-M3
```

MiniMax 的思考内容会自动与最终 `content` 分离，避免 `<think>` 内容进入聊天答案和 Agent 工具循环。

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
- `POST /api/runs/{run_id}/cancel`：取消当前进程中正在执行的 Run
- `GET /api/artifacts`：列出当前 workspace 的产物，可按 `run_id` 过滤
- `GET /api/artifacts/{artifact_id}`：读取产物及其 Citation
- `GET /api/evidence/protocols`：列出当前 workspace 的证据协议，可按 `profile` 过滤
- `GET /api/evidence/protocols/{protocol_id}`：读取 Claim、Receipt、Review、Freeze 和关联 Artifact
- `GET /api/decision-lab`：查询 NanoJev 决策实验、概率分布与人工复核投影
- `POST /api/decision-lab/import/nanojev`：管理员上传并验证冻结的 NanoJev bundle
- `GET /api/research-registry`：查询 AI Frontier 研究案例、Claim Revision 与 Source Closure
- `GET /api/research-registry/claims/{claim_id}`：读取单条研究主张及其来源定位和 blocker
- `POST /api/research-registry/claims/{claim_id}/relations`：管理员登记 supports/refutes/depends_on/qualifies 类型化关系；`.../relations/{relation_id}/withdraw` 保留理由地撤回关系
- `POST /api/research-registry/claims/{claim_id}/verification-attempts`：管理员登记摘要与 Artifact 绑定的验证尝试及 Receipt
- `POST /api/research-registry/claims/{claim_id}/verification-plans`：管理员冻结 Verification Plan 的新版本，同一 `plan_key` 的旧版本自动退役
- `POST /api/research-registry/verification-plans/{plan_id}/runs`：通过 Agent 立即执行有效 Plan，并闭合 Run/Step/Artifact/Receipt/Promotion Gate
- `POST /api/research-registry/claims/{claim_id}/promotion-gates`：管理员执行下一阶段的失败关闭晋升评估
- `POST /api/research-registry/import/frontier`：管理员上传、验证并冻结 Claim Registry
- `GET /api/qualification/profiles`：列出代码注册、版本固定的领域资格 Profile
- `GET /api/qualification/kernel-verifiers`：列出 Lean/Coq backend 的配置状态和非敏感执行边界
- `POST /api/qualification/math-theorems`：冻结 theorem 候选；只完成存储准入，不授予资格或权限
- `POST /api/qualification/claims/{claim_id}/kernel-verifications`：用服务端配置的 Lean/Coq 可执行文件验证冻结 proof，并写入 Run/Step/Artifact/Receipt/Attempt
- `POST /api/qualification/claims/{claim_id}/evaluations`：按指定 Profile 运行确定性 Qualification Gate
- `GET /api/qualification/receipts/{receipt_id}`：读取不可变、可携带的资格证书
- `GET /api/qualification/search?q=...&profile=math.formal.v1`：只查询当前仍具该资格的 ClaimRevision
- `POST /api/authorization-grants`：基于 current Qualification Receipt 签发窄 action/target/scope/budget/max_calls 授权
- `GET|POST /api/schedules`：管理员创建或列出当前 workspace 的计划任务
- `GET /api/schedules/{task_id}`：管理员读取计划任务详情
- `POST /api/schedules/{task_id}/pause|resume|cancel`：管理员控制后续触发
- `GET /api/search?q=...`：搜索对话、知识文档、执行步骤、产物、来源和研究主张
- Decision Case 与 Research Claim 同样进入统一搜索，可跳转到对应工作台
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

## 持久化任务调度

应用启动时会从 `scheduled_tasks` 恢复活跃计划，并使用进程内多级时间轮建立可丢弃的唤醒索引；
SQLite/PostgreSQL 中的计划定义始终是事实来源。`TaskRunner` 当前注册以下独立目标：

| target | payload | 执行语义 |
|---|---|---|
| `agent.chat` | `prompt`，可选 `system/model/session_id` | 通过 `GatewayService` 创建正常 Agent Run |
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

## Qualification Plane

Qualification 不是 Artifact 属性，而是冻结关系：

```text
Q(ClaimRevision, QualificationProfile, EvidenceClosure, PolicyVersion)
```

系统中没有 `artifact.trusted=true` 或 `trusted_agent=true`。模型只能产生 candidate、Artifact、验证/复核
proposal 和反例；只有确定性的 `QualificationGate` 能写 `ADMITTED / BLOCKED / UNRESOLVED / STALE /
NOT_APPLICABLE` 评估，且只有 `ADMITTED` 才签发不可变 Qualification Receipt。Claim statement、scope、
定义、负边界或依赖发生变化时 semantic hash 改变，旧证据继续作为历史记录存在，但不会自动继承。

首个领域锁 `math.formal.v1` 要求 exact revision identity、formal proof Artifact、带 checker identity 与
executable hash 的 kernel certificate、无 `sorry`/额外公理、current dependency closure，以及非模型的
semantic-alignment review。资格与权力严格分账：发布或高风险执行仍需要独立、窄范围的
`AuthorizationGrant(action, target, scope, budget, expiry, max_calls)`。原始候选进入 `/api/search`；
知识使用面可走 `/api/qualification/search`，避免未资格化候选被下一轮 Agent 当成事实。
Kernel attempt 还必须来自服务端持有的 `system:verifier:*` identity 且不能带 model route。真实
Lean 4/Coq process backend 只接受 proof source、固定 declaration 和 backend id；可执行文件、argv、
环境与 verifier identity 都由服务器配置，客户端不能上传 command 或用普通 JSON 冒充 certificate。
Lean 使用 `--trust=0` 并读取 `#print axioms`，Coq 读取 `Print Assumptions`；非零退出、超时、缺失
closure marker、`sorry`/`Admitted` 或任意额外公理都生成失败 Attempt，不能通过 Gate。
即使 kernel 通过也只增加一条合格的 evidence edge，不直接写 Qualification verdict；语义对齐 review、
dependency closure 和显式 `QualificationGate` 仍必须分别满足。

```dotenv
# 默认留空并失败关闭；生产环境建议固定到具体 toolchain 的绝对路径。
AIGC_LITE_LEAN_EXECUTABLE=C:\Users\you\.elan\toolchains\stable\bin\lean.exe
AIGC_LITE_COQ_EXECUTABLE=/usr/bin/coqc
AIGC_LITE_KERNEL_VERIFY_TIMEOUT_SECONDS=30
AIGC_LITE_KERNEL_VERIFY_MEMORY_MB=512
```

```json
POST /api/qualification/claims/{claim_revision_id}/kernel-verifications
{
  "backend": "lean4",
  "declaration_name": "add_zero_demo",
  "source": "theorem add_zero_demo (n : Nat) : n + 0 = n := by exact Nat.add_zero n"
}
```

当前 backend 使用无 shell、有限时间/输出的子进程和一次性工作目录，但这不是 OS 安全沙箱；Lean
metaprogram 与 Coq plugin 仍可能接触宿主文件系统/网络。因此该入口只开放给 workspace admin，
不应作为公开匿名 proof upload 服务。需要验证不可信任意代码时，应把同一 backend 放进独立容器/
VM，并把镜像、库闭包和网络策略纳入后续 Qualification Profile。

## Portable Assurance Bundle

管理员可以把一个 Research Case 只读导出为确定性 ZIP：

```text
GET /api/research-registry/cases/{case_id}/assurance-bundle

research-artifact/
├── artifact.json
├── claims.json
├── provenance.json
├── receipts.json
├── evidence.json
├── verification.json
├── qualification.json
├── reviews.json
├── limitations.json
├── manifest.json
├── payloads/
└── signatures/status.json
```

`manifest.json` 对每个 JSON 文档和 Artifact 原始 payload 保存 SHA-256 与字节长度，并对成员清单
再形成 `bundle_digest`。相同数据库快照会导出相同字节；ZIP 时间戳固定，不把导出时间伪装成研究
事件。当前版本明确标记 `unsigned`：哈希闭包可以发现导出后的改动，但不能冒充发布者签名。
通用离线 verifier 只检查基础设施闭包，不重新裁决数学定理、实验设计或领域事实；这些锁必须由
对应 profile 的领域验证器提供，包中会明确报告 `domain_semantics=not_evaluated_by_bundle_verifier`。

包的校验完全离线，不启动数据库、模型、MCP 或网络：

```bash
aigc-lite verify research-artifact.zip
aigc-lite verify research-artifact/ --require-authorized
```

命令输出 `identity / provenance / reproducibility / evidence_closure / verification /
independence / epistemic_state / authority_state` 状态包，而不是总分。普通 `verify` 只以格式、引用与
摘要闭包决定退出码；`--require-authorized` 要求包中存在仍绑定 current qualification 的有效
AuthorizationGrant，workflow 的 `release_ready` 不再冒充 authority。验证器
会重算 Artifact、Verification Plan、冻结 Execution Input 与 Promotion Gate 摘要；旧版只有
`independent=true`、没有重叠依据的记录会显示为 `undetermined`，不会被升级成独立验证。

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

`/api/search` 通过独立 `SearchBackend` 查询消息、文档 chunk、Run Step、Artifact 和 Citation。
SQLite 使用 FTS5 `trigram` 索引，不再受旧版“每类最多扫描 500 条候选”的窗口限制；`0006` migration
会回填已有记录，数据库触发器负责后续新增、更新和删除同步。用户查询会被转换成引用后的 FTS
表达式，不直接执行客户端提供的操作符或列选择器。

少于三个字符的中文/英文查询会走有界词法 fallback；运行时 SQLite 缺少 FTS5 或不支持 trigram
时也会自动降级。PostgreSQL 当前继续使用相同结果契约的词法 backend，后续可以独立替换成原生
全文检索或 embedding backend，而无需修改 `MemoryService` 和 HTTP API。

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

远程 tool 还可以通过 `_meta.aigc-lite` 声明更严格的单工具风险、scope、超时上限和通用扩展元数据。声明只能提升 Provider 的最低风险、追加 scope 或缩短超时，不能由远端自行降权。管理员额外 scope 必须由部署方通过 `AIGC_LITE_ADMIN_TOOL_SCOPES` 显式授予。

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

将这个模块在 `app.main` 启动时导入后，入站 MCP `tools/list` 和 Agent 都通过同一个 Tool Catalog 发现它。`/mcp` 与 `/mcp-sse/sse` 会根据登录 Bearer token 或 tenant API key 解析 workspace 和 scope，因此只暴露当前身份允许的本地及远程 MCP tool。`AIGC_LITE_MCP_API_KEY` 仅作为兼容模式保留，它固定映射到 `default` workspace；多 workspace 部署应使用用户 token 或 tenant API key。公开核心不自动启用文件系统、Shell、网络爬取等高风险工具，扩展应由部署方显式注册。

每次入站 `tools/call` 都创建独立 Run 和 Tool Step，MCP 响应的 `_meta.aigc-lite.run_id` 可用于查询执行详情。参数和结果使用与 Agent 相同的账本脱敏规则；客户端仍获得工具原始结果。出站 MCP 请求会附带内部 hop 标记，收到 hop 标记的 aigc-lite 只投影本地工具，避免两个 Catalog 互相代理或配置指回自身时形成递归发现。正式 `/mcp` 的协议版本由官方 SDK 协商，当前 SDK v2 回归测试固定为 `2026-07-28`；`AIGC_LITE_LEGACY_MCP_PROTOCOL_VERSION` 仅影响手写的 `/mcp-legacy`，后者只用于本地调试兼容。

### ROS 2 Physical Capability Bridge

`extensions/ros2-bridge` 是独立发行包和进程，核心不导入 ROS 2。它先提供
`robot_get_state`、`robot_inspect`、`robot_navigate_to` 与 `robot_cancel_action`，通过现有远程 MCP
Provider 自动进入 Tool Catalog、Run/Step、结构化 Artifact 和 Citation。导航要求幂等键，收据明确
区分 simulation/hardware、成功、失败、取消、状态不确定和停止是否确认。没有 ROS 的环境可用确定性
SimulatorBackend 完整验证；安装并 source ROS 2/Nav2 后再切换 Nav2Backend。安装、配置、验证边界和
安全要求见 [`extensions/ros2-bridge/README.md`](extensions/ros2-bridge/README.md)。

Agent/MCP 的取消不是急停。真实机器人必须把 e-stop、安全 PLC/控制器、碰撞保护、速度限制和 watchdog
保留在独立的物理安全路径中，不能依赖模型、网络、Python event loop 或 Tool Catalog。

同步 Agent 会把本地和远程 MCP 的每次模型调用、工具调用分别记录为 Step，并记录
`source`、`provider_id`、原始工具名与风险级别。工具参数和结构化结果中常见的
`api_key`、`token`、`password`、`secret` 等嵌套字段，以及常见 AWS/GCS 预签名 URL 参数和 Bearer token，在进入执行账本或审计记录前会统一脱敏；模型实际执行仍接收原始工具结果。任意纯文本中的非结构化秘密仍无法可靠识别，因此涉及敏感数据的工具应返回 JSON 对象。流式响应通过 `X-Run-Id` 和 `X-Session-Id` header 返回追踪身份。

数据库结构由 Alembic 管理。应用启动时会自动执行到最新 revision；首次接管旧数据库时，幂等基线迁移保留已有表和数据，再追加后续字段。部署升级仍应先备份数据库与 `AIGC_LITE_MASTER_KEY`，二者必须成对恢复。

## 开发

```bash
pytest
ruff check app tests extensions/ros2-bridge/src
```

当前知识检索使用 SQLite 中保存的分块哈希向量，Repository 可切换 PostgreSQL；后续可把
embedding provider 替换为真实模型而不改变 API 使用方式。核心坚持显式扩展点、可替换 capability
和官方 MCP transport，不把组织专属连接器带入公开运行时。

## 许可证

本项目使用 MIT License，见 [LICENSE](LICENSE)。
