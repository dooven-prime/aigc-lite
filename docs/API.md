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
- `GET /api/reviews/profiles`：列出代码注册、内容哈希固定的 Review Profile
- `POST /api/reviews/runs/{run_id}`：管理员对冻结的 Run 视图执行确定性审查并写入 ReviewRun/Finding
- `GET /api/reviews` 与 `GET /api/reviews/{review_run_id}`：查询 workspace 审查历史及其发现
- `GET /api/review-findings` 与 `GET /api/review-findings/{finding_id}`：按 subject、severity、status 查询证据定位后的建议
- `GET /api/conversation-importers`：列出版本化的 ChatGPT/DeepSeek 导入器
- `POST /api/conversation-imports/preview`：管理员解析上传的 JSON，返回 source/preview hash、告警和有界样本，不创建导入记录
- `POST /api/conversation-imports`：管理员用相同文件和 `expected_preview_hash` 原子提交 Artifact、不可变 ImportBatch 与完整消息图
- `GET /api/conversation-imports` 与 `GET /api/conversation-imports/{batch_id}`：查询 workspace 导入批次；详情可显式传 `include_messages=true`
- `POST /api/enforcement/policy-proposals`：管理员提交 base/candidate execution policy；服务端冻结两份 snapshot 并确定性计算 `PermissionDiff`，不批准或应用策略
- `GET /api/enforcement/policy-proposals` 与 `GET /api/enforcement/policy-proposals/{proposal_id}`：查询 workspace 的不可变策略提案与扩权/收权明细
- `GET /api/enforcement/issuers`：列出主机代码注册、能够签发 enforcement evidence 的 backend identity；请求不能注册 issuer
- `GET /api/enforcement/receipts` 与 `GET /api/enforcement/receipts/{receipt_id}`：只读查询外部 backend 写入的不可变 `EnforcementReceipt`；没有公共 Receipt 写入口
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
- `POST /mcp`：官方 MCP Streamable HTTP Server
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
HTTP、Chat 和 MCP 均没有创建入口。Receipt 绑定 workspace、Run/Step、policy revision/hash、
execution envelope hash、tool/argument digest 与 issuer trust domain。`external_signature` 与
attestation 在当前层是由已认证 adapter 提供的证据载体；是否具备密码学或硬件保证取决于具体
adapter，核心不会仅凭字段存在把它升级成独立信任域。
