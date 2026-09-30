# aigc-lite

[![M8ven Score](https://m8ven.ai/badge/mcp/dooven-prime/aigc-lite)](https://m8ven.ai/mcp/dooven-prime/aigc-lite)

> M8ven 徽章表示第三方对公开 MCP 源码的扫描状态，不构成本项目
> Qualification Plane 的资格或授权结论。

一个可自托管、以可搜索执行记忆为核心的 AI 工作台与 Agent/MCP
运行时。它可以通过 OpenAI-compatible 上游调用模型，但不以重复建设通用
AI Gateway 为目标。

## 项目定位

aigc-lite 关注的是 bounded autonomous execution infrastructure：

```text
Agent / Scheduler / MCP
          ↓
Tool Catalog + permission + budget + cancellation
          ↓
Run / Step / Artifact / Citation / Receipt
          ↓
Search + Verification + Qualification + Authorization
```

模型的一次回答会消失，执行事实不应该消失。系统把会话、模型调用、工具调用、
失败、取消、来源和产物写入同一套可查询账本，并严格区分：

- candidate storage 与 qualified knowledge；
- epistemic qualification 与 execution authorization；
- Agent proposal 与确定性 Gate 决定；
- workflow stage 与 evidence strength。

企业私有业务、第三方平台凭据、组织专属连接器和运行时数据不属于公开核心，
通过独立扩展接入。

## 核心能力

- 同步/SSE Agent 对话，以及模型轮次、工具次数、墙钟和单工具执行预算；
- server-owned Chat capability sets；默认对话剥离管理员 scope，只向模型暴露低风险、
  只读、无副作用的闭世界能力，远程或写能力必须显式委托并经过窄授权；
- workspace-aware Tool Catalog，统一本地 Python tool、远程 MCP tool 和权限边界；可由
  部署者把精确 capability 绑定到独立 enforcer，失败时不回退本地执行；
- SQLite/PostgreSQL 执行账本，记录 Run、Step、Artifact、Citation 和稳定错误码；
- 可恢复计划任务、Run Explorer，以及跨会话、知识、步骤和产物的统一搜索；
- Research Registry、Claim Relation、Verification Runner 和 Promotion Gate；
- Qualification Plane、Lean/Coq kernel verifier、显式 Knowledge Admission、qualified-only retrieval 和窄授权；
- Review Profile Registry 与确定性的执行完整性 Finding 账本；
- 不可变 Policy Proposal、服务端 Permission Diff、独立 Enforcer Adapter 与 Ed25519 验签后的 Enforcement Receipt 账本；
- preview/commit 型 Conversation Import Registry，保留外部消息分支与来源 Artifact，并只进入 candidate search；
- 可离线校验的 Assurance Bundle；
- 独立安装的 ROS 2/Nav2 capability provider，核心不依赖 ROS 2。

完整功能和边界请从[文档导航](#文档导航)进入；HTTP 路由不再全部堆在首页。

## 快速开始

需要 Python 3.11+。默认使用 SQLite，适合本地单进程体验。

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

在 `.env` 中至少配置一个 OpenAI-compatible 模型：

```dotenv
AIGC_LITE_LLM_API_KEY=your-provider-key
AIGC_LITE_LLM_BASE_URL=https://api.openai.com/v1
AIGC_LITE_LLM_MODEL=gpt-4o-mini
```

启动：

```bash
python -m app.main
```

默认入口：

- 工作台：<http://127.0.0.1:8000/ui/>
- OpenAPI：<http://127.0.0.1:8000/docs>
- Liveness：<http://127.0.0.1:8000/health>
- Readiness：<http://127.0.0.1:8000/ready>

```bash
curl --fail http://127.0.0.1:8000/ready
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt":"用三句话介绍人工智能"}'
```

PostgreSQL 部署安装 `.[postgres]`；workspace Credential Store 还需要独立
Fernet master key。所有变量、MiniMax 示例和租户配置见
[配置索引](docs/CONFIGURATION.md)。

## 安全与运行边界

- 服务默认只监听 loopback；非 loopback 启动会强制检查认证、signup 策略和密钥强度。
- workspace 配置只接受本 workspace 的
  `encrypted-db://credential/UUID`，不能通过 `env://` 读取进程环境。
- 自定义模型 endpoint 必须原子绑定自己的 credential，绝不继承平台全局 LLM key。
- Python thread tool 只能停止等待，不能被 asyncio 强制终止；需要硬取消时使用 process
  backend、容器或外部 sandbox。
- Qualification 不等于 Authorization；任何高风险或物理动作仍需要独立授权与设备侧安全链。
- Kernel orthogonality 不等于 verifier independence；Qualification Receipt 也不会自动进入默认知识层。
- 假定被治理的 Agent 最终能理解 gate；理解 gate 不产生修改 gate 的权力，能力增长也不自动
  扩大有效权限。应用层 gate 不能替代外部 sandbox 或独立 watchdog。
- Prompt、RAG 文档和模型输出都不能选择或扩大 Chat capability set；策略选择只来自受认证的
  请求字段，且 Run/Step 会冻结实际生效的策略 ID、哈希和 scope。
- `/health` 仅表示进程存活，部署探针应使用 `/ready`。

公网部署、备份恢复和当前运维限制见
[生产部署清单](docs/PRODUCTION.md)，漏洞报告见 [SECURITY.md](SECURITY.md)。

## 文档导航

| 文档 | 内容 |
|---|---|
| [HTTP API](docs/API.md) | 接口分组、路由和最小调用示例 |
| [Execution Runtime](docs/EXECUTION.md) | Agent 边界、调度、统一搜索与 Run Explorer |
| [MCP and Tool Runtime](docs/MCP.md) | MCP、Tool Catalog、Credential Store 和执行 backend |
| [Qualification and Assurance](docs/QUALIFICATION.md) | Claim qualification、kernel verification、授权与离线 Bundle |
| [Review Workbench](docs/REVIEW.md) | 版本化审查 Profile、执行完整性规则与 Finding 账本 |
| [External Enforcement](docs/ENFORCEMENT.md) | 独立 Enforcer Adapter、签名 wire contract、key rotation 与可信边界 |
| [Conversation Imports](docs/CONVERSATION_IMPORTS.md) | ChatGPT/DeepSeek preview、不可变批次、原始 Artifact 与 candidate-only 搜索 |
| [Configuration](docs/CONFIGURATION.md) | 完整环境变量、模型、租户、MCP 与 formal kernel 配置 |
| [Production](docs/PRODUCTION.md) | 生产启动保护、备份恢复和已知运维限制 |
| [Threat Model](docs/THREAT_MODEL.md) | 两个 authority plane、三层 enforcement、系统假设和未实现边界 |
| [Authority Transitions](docs/AUTHORITY_TRANSITIONS.md) | authority mutation matrix、控制者、Gate 与敌意回归合同 |
| [Controlled Patch Runner](docs/PATCH_RUNNER.md) | 未来独立复现、窄路径修改授权与不可自改验证面的安全合同 |
| [Design](docs/DESIGN.md) | 产品边界、核心契约、架构决策和路线 |
| [ROS 2 Bridge](extensions/ros2-bridge/README.md) | Simulator/Nav2 provider、物理动作和安全边界 |
| [Changelog](CHANGELOG.md) | 版本变化 |
| [v0.6.0 release closure](docs/releases/v0.6.0.md) | 当前 authority separation 基线和验证范围 |

## 开发

后端检查：

```bash
python -m pytest -q
python -m ruff check .
python -m compileall -q app
```

前端：

```bash
cd frontend
npm ci
npm run dev
npm run build
python ../scripts/sync_frontend.py
```

`frontend/src` 是 UI 源码，`app/static` 是 wheel 和 Docker 使用的已构建产物。
发布验证要求见 [Production](docs/PRODUCTION.md)。

## 许可证

本项目使用 MIT License，见 [LICENSE](LICENSE)。
