# Qualification and assurance

This guide describes how candidate research objects earn a versioned,
profile-specific qualification and how their evidence closure can be exported
for offline inspection. Qualification remains separate from execution
authorization.

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

执行 grant 的 `scope / conditions / budget` 不是说明性 metadata。Tool Catalog 在 provider dispatch
之前，用服务端派生的具体 invocation 逐项匹配，并只按匹配到的 grant ID 原子消费：

```json
{
  "scope": {
    "workspace_id": "workspace-a",
    "provider_id": "robot",
    "tool_name": "robot_navigate_to",
    "required_scopes": ["robot:motion"]
  },
  "conditions": {
    "arguments": {
      "frame_id": "map",
      "x": {"$gte": 0, "$lte": 5}
    }
  },
  "budget": {
    "arguments": {"action_timeout_seconds": 60}
  }
}
```

`scope` 和 `conditions` 使用递归子集匹配，并支持 `$eq / $in / $gte / $lte`；`budget` 的数字表示调用
实际请求的上限。无法从调用中证明的约束会失败关闭，不能用模型输出补齐。旧 grant 若把任意说明字段
放在这些对象里，升级后应由管理员按上述可执行合同重新签发。
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
导出前还会递归刷新每个 current-use binding；依赖资格已经换版或变 stale 时，Bundle 保存污染后的
状态并阻断 authority，而不是把数据库中尚未惰性刷新的旧 `current` 投影成可执行权。
