# HTTP API reference

This document is the compact route index for the current public HTTP surface.
Interactive OpenAPI documentation is served at `/docs`; authentication,
configuration, and deployment constraints are documented separately in
[CONFIGURATION.md](CONFIGURATION.md) and [PRODUCTION.md](PRODUCTION.md).

## Routes

- `GET /health`：不访问依赖的进程 liveness
- `GET /ready`：数据库、Alembic head 与 scheduler readiness；未就绪返回 503
- `POST /api/auth/register`：按部署 signup 策略注册 workspace 与用户
- `POST /api/auth/login`：创建登录 session
- `GET /api/auth/me`：读取当前登录身份和角色
- `GET|POST /api/sessions`：创建和列出会话
- `GET /api/sessions/{session_id}`：读取当前租户会话及消息
- `POST /api/chat`：同步 Agent 对话
- `POST /api/chat/stream`：SSE 流式对话
- `GET /api/chat/capability-sets`：列出服务端注册的 Chat capability set、版本与内容哈希
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
- `GET /api/research/explorer?research_case_id=...`：跨 profile 只读浏览已冻结的 research case、ClaimRevision 和证据轨迹；不包含仅存储在候选目录中的数学 family
- `GET /api/research/importers` 与 `GET /api/research/importers/{importer_id}/{version}`：列出版本化导入适配器的格式、目标存储层和专用入口；不提供万能提交接口
- `GET /api/research-registry/claims/{claim_id}`：读取单条研究主张及其来源定位和 blocker
- `POST /api/research-registry/claims/{claim_id}/relations`：管理员登记 supports/refutes/depends_on/qualifies 类型化关系；`.../relations/{relation_id}/withdraw` 保留理由地撤回关系
- `POST /api/research-registry/claims/{claim_id}/verification-attempts`：管理员登记摘要与 Artifact 绑定的验证尝试及 Receipt
- `POST /api/research-registry/claims/{claim_id}/verification-plans`：管理员冻结 Verification Plan 的新版本，同一 `plan_key` 的旧版本自动退役
- `POST /api/research-registry/verification-plans/{plan_id}/runs`：通过 Agent 立即执行有效 Plan，并闭合 Run/Step/Artifact/Receipt；旧字段 `auto_promote=true` 只追加 proposal-only Promotion Gate evaluation，不更新 workflow stage
- `POST /api/research-registry/claims/{claim_id}/promotion-gates`：管理员显式执行并应用下一阶段的失败关闭 workflow Gate
- `POST /api/research-registry/import/frontier`：管理员上传、验证并冻结 Claim Registry
- `GET /api/qualification/profiles`：列出代码注册、版本固定的领域资格 Profile
- `GET /api/qualification/kernel-verifiers`：列出 Lean/Coq backend 的配置状态和非敏感执行边界
- `POST /api/qualification/math-theorems`：冻结 theorem 候选；只完成存储准入，不授予资格或权限
- `POST /api/qualification/claims/{claim_id}/kernel-verifications`：用服务端配置的 Lean/Coq 可执行文件验证冻结 proof，并写入 Run/Step/Artifact/Receipt/Attempt
- `POST /api/qualification/claims/{claim_id}/evaluations`：按指定 Profile 运行确定性 Qualification Gate
- `GET /api/qualification/claims/{claim_id}/status`：按精确 revision 查看评估、历史 Receipt、知识准入和当前绑定；读取时会按现有规则刷新依赖失效状态，不会创建资格或授权
- `GET /api/qualification/receipts/{receipt_id}`：读取不可变、可携带的资格证书
- `POST /api/qualification/invalidation-notices/verify`：管理员从固定 OpenAI math commit 获取 `history.md`，校验并保存来源字节；不改变资格
- `GET /api/qualification/invalidation-notices/{verification_id}`：读取已核对的来源记录
- `POST /api/qualification/invalidation-decisions`：管理员对精确 revision/receipt/attempt 作版本化本地失效决定，原子更新受影响的 current binding
- `GET /api/qualification/claims/{claim_id}/invalidation-decisions`：追查旧 receipt 当前失效的决定与证据
- `GET /api/qualification/knowledge-admission-policies`：列出服务端持有的知识准入 policy
- `POST /api/qualification/receipts/{receipt_id}/knowledge-admissions`：管理员显式准入一张仍有效的资格证书，并原子更新 current knowledge binding
- `GET /api/qualification/knowledge-admissions`：读取不可变知识准入历史，可按 `claim_id` 过滤
- `GET /api/qualification/search?q=...&profile=math.formal.v1`：只查询当前仍具该资格的 ClaimRevision
- `POST /api/authorization-grants`：基于 current Qualification Receipt 签发窄 action/target/scope/budget/max_calls 授权
- `GET /api/reviews/profiles`：列出代码注册、内容哈希固定的 Review Profile
- `POST /api/reviews/runs/{run_id}`：管理员对冻结的 Run 视图执行确定性审查并写入 ReviewRun/Finding
- `GET /api/reviews` 与 `GET /api/reviews/{review_run_id}`：查询 workspace 审查历史及其发现
- `GET /api/review-findings` 与 `GET /api/review-findings/{finding_id}`：按 subject、severity、status 查询证据定位后的建议
- `GET /api/conversation-importers`：列出版本化的 ChatGPT/DeepSeek 导入器
- `POST /api/conversation-imports/preview`：管理员解析上传的 JSON，返回 source/preview hash、告警和有界样本，不创建导入记录
- `POST /api/conversation-imports`：管理员用相同文件和 `expected_preview_hash` 原子提交 Artifact、不可变 ImportBatch 与完整消息图
- `GET /api/conversation-imports` 与 `GET /api/conversation-imports/{batch_id}`：查询 workspace 导入批次；详情可显式传 `include_messages=true`
- `POST /api/research/math-release-imports/preview`：管理员预览固定 `openai/math` commit 的 family/manuscript 目录，不写入数据
- `POST /api/research/math-release-imports`：管理员提交相同 commit 与 `expected_preview_hash`，原子保存原始目录 Artifact 和 candidate-only manifest
- `GET /api/research/math-release-imports` 与 `GET /api/research/math-release-imports/{import_id}`：查询目录快照；详情可显式传 `include_families=true`，不代表数学资格
- `POST /api/enforcement/policy-proposals`：管理员提交 base/candidate execution policy；服务端冻结两份 snapshot 并确定性计算 `PermissionDiff`，不批准或应用策略
- `GET /api/enforcement/policy-proposals` 与 `GET /api/enforcement/policy-proposals/{proposal_id}`：查询 workspace 的不可变策略提案与扩权/收权明细
- `GET /api/enforcement/issuers`：列出主机代码注册、能够签发 enforcement evidence 的 backend identity；请求不能注册 issuer
- `GET /api/enforcement/adapters`：列出部署者在 composition root 注册的外部 adapter 及其精确 target；没有公共注册或 dispatch API
- `GET /api/enforcement/tool-bindings`：列出当前 workspace 由部署者注册的精确 tool → adapter/proposal/policy-hash 绑定
- `GET /api/enforcement/verification-keys`：只返回可信 Ed25519 key 的 ID、有效期、吊销状态和公钥指纹，不返回公钥配置载体
- `GET /api/enforcement/dispatches` 与 `GET /api/enforcement/dispatches/{dispatch_id}`：查询持久化 dispatch/reconciliation 状态
- `POST /api/enforcement/dispatches/{dispatch_id}/reconcile`：管理员要求外部 enforcer 重查 `dispatching/indeterminate` workload；不能创建新的任意动作
- `GET /api/enforcement/receipts` 与 `GET /api/enforcement/receipts/{receipt_id}`：只读查询外部 backend 写入的不可变 `EnforcementReceipt`；没有公共 Receipt 写入口
- `GET /api/enforcement/receipts/{receipt_id}/signature-verification`：使用当前 host-owned trust roots 重验已存签名；不改写历史验证结果
- `GET|POST /api/schedules`：管理员创建或列出当前 workspace 的计划任务
- `GET /api/schedules/{task_id}`：管理员读取计划任务详情
- `POST /api/schedules/{task_id}/pause|resume|cancel`：管理员控制后续触发
- `GET /api/search?q=...`：搜索对话、导入候选消息、知识文档、执行步骤、产物、来源和研究主张
- Decision Case 与 Research Claim 同样进入统一搜索，可跳转到对应工作台
- `POST /api/knowledge/documents`：写入文本知识
- `POST /api/knowledge/upload`：上传 `.txt`、`.md`、`.csv` 或 `.json`
- `GET /api/knowledge/search?q=...`：搜索当前租户知识
- `GET /api/usage`：读取当前 workspace 的聚合使用量
- `GET /api/audit`：管理员读取当前 workspace 的审计事件
- `GET /api/admin/tenants`：平台管理员列出租户
- `GET|POST /api/admin/tenants/{tenant_id}/users`：平台管理员查询或创建租户用户
- `GET|POST /api/models`：列出或保存 workspace 模型路由；自定义 endpoint 必须绑定自己的 credential
- `GET|POST /api/credentials`：管理员列出凭据状态或创建 write-only 加密凭据
- `POST /api/credentials/{credential_id}/replace`：替换凭据值并重新启用
- `DELETE /api/credentials/{credential_id}`：撤销凭据引用，不物理删除记录
- `GET|POST /api/mcp-servers`：管理员列出或保存 workspace 的远程 MCP Server
- `POST /api/mcp-servers/{server_id}/probe`：测试连接并保存最新健康状态、延迟、工具数和稳定错误码
- `DELETE /api/mcp-servers/{server_id}`：管理员删除远程 MCP Server 配置
- `POST /mcp/`：官方 MCP Streamable HTTP Server；这是规范地址。兼容地址
  `/mcp` 在 ASGI 边界内直接规范化，不返回会破坏默认 SDK POST 的 307
- `GET /mcp-sse/sse`：官方 MCP SSE Server（兼容旧客户端）
- `POST /mcp-legacy`：简单 JSON-RPC 调试接口

## Minimal examples

```bash
curl http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/ready
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt":"用三句话介绍人工智能"}'
```

`POST /api/chat` 与 `/api/chat/stream` 可传 `capability_set_id`。省略时固定选择
`chat.read-only.v1`：调用方即使是 workspace admin，传给模型 Tool Catalog 的 scope 也会被清空，
且只保留 low-risk、read-only、non-destructive、closed-world 的 local/workspace tool。

两种 Chat 请求均可附带至多 4 个 `.txt`/`.md` UTF-8 文本附件：
`attachments: [{"name":"notes.md","content":"..."}]`。单文件上限 16 KiB，总计
32 KiB。附件只在本轮作为不可信输入使用，并保存为关联该 Run、Session 的 candidate-only
Artifact；不会调用 `/api/knowledge/upload`，不会自动进入知识索引或取得 qualification。
`/api/chat/stream` 以 POST SSE 返回增量 `{"content":"..."}`，头部提供
`X-Run-Id` 和 `X-Session-Id`；取消使用 `POST /api/runs/{run_id}/cancel`。
当前流式端点是模型增量输出，不执行同步 `/api/chat` 的 Agent Tool Catalog
调用循环；前端的流式聊天因此不是工具调用模式。两条路径的执行能力不可等同。

`chat.delegated.v1` 必须由拥有 `tools:write` 的已认证调用方显式选择；它不会因为 prompt、RAG
文档或模型输出而自动启用。该集合只是允许工具进入候选面：每个远程 MCP tool，以及任何写入、
破坏性或 medium/high-risk tool，在 provider 调用前仍必须消费匹配 actor/action/target 的当前
`AuthorizationGrant`。scope 不等于授权，缺少 grant 时返回稳定的
`tool_authorization_required` Tool 结果。策略本身无权签发 grant。

```json
{
  "prompt": "执行已批准的外部检查",
  "capability_set_id": "chat.delegated.v1"
}
```

未知集合返回 404；调用方没有集合要求的 scope 时返回
`403/chat_capability_not_allowed`，并且不会先创建 Session、Message 或 Run。每个已建立的 Chat
Run 保存 `capability_set_id` 与 `capability_policy_hash`；Step metadata 同时保存实际生效的 scope。
当前 stream 路径尚不运行工具循环，但同样解析并冻结 capability decision，避免两条入口产生无标识
的权限语义。

Conversation import 的 preview/commit 闭包、支持格式与 candidate-only 边界见
[CONVERSATION_IMPORTS.md](CONVERSATION_IMPORTS.md)。

Policy proposal 只代表扩权请求。`PermissionDiff` 由服务端从规范化的 base/candidate policy
计算，客户端提交同名字段会因严格 schema 被拒绝；未知 constraint 语义无法证明为收紧时按扩权
处理。首版 proposal 永远停留在 `proposed`，不会改变 runtime policy。

`EnforcementReceipt` 只能由进程 composition root 注册的 issuer 通过内部 service seam 写入；普通
HTTP、Chat 和 MCP 均没有任意 workload 创建或 dispatch 入口。Tool Catalog 只有命中 host-owned
`ExternalToolExecutionBinding` 才会将调用交给外部 adapter，且绝不失败后回退本地 provider。外部
adapter 返回的 Ed25519 envelope 必须同时通过
签名、host-owned key、issuer identity、nonce/request hash、workspace/Run/Step/Proposal、policy 与
tool/argument digest、时间窗检查，才能写入 `signature_verified=true` 的 receipt。旧式内部
`record_receipt()` 与仅携带 `external_signature` 的记录明确保持 unverified。签名证明指定 key 对
指定 payload 的认证，不自动证明 attestation、runtime 实现或硬件测量本身可信；完整协议见
[ENFORCEMENT.md](ENFORCEMENT.md)。
