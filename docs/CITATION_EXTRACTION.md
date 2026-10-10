# CitationExtractionProposal.v1

此接口把模型或规则解析器当作 *epistemic sensor*。它只接受可定位的候选，不判断论文间的逻辑支持，不签发资格，不改变当前知识使用绑定，也不授予执行权限。

## 合同与边界

管理员以 multipart/form-data 向 `POST /api/research/citation-extractions/preview` 上传 `file`（PDF）、`source_uri`（HTTPS）和可选的 `proposal_json`。不提供 proposal 时使用确定性正则 baseline。预览返回 `preview_hash`、PDF SHA-256、逐页文本摘要和候选。向 `POST /api/research/citation-extractions` 重传完全相同的 PDF、URI、proposal，并提交 `expected_preview_hash`，才追加一个 JSON Artifact。已有的 `GET /api/artifacts/{artifact_id}` 可读取它；没有新增 ClaimRelation、Citation/EvidenceEdge、QualificationReceipt、CurrentUseBinding 或 AuthorizationGrant。

`proposal_json` 固定为 `CitationExtractionProposal.v1`：

```json
{
  "contract_version": "CitationExtractionProposal.v1",
  "occurrences": [{
    "id": "o-1", "style": "numeric",
    "anchor": {"page": 3, "start": 612, "end": 616, "quote": "[ 1]"}
  }],
  "bibliographic_matches": [{
    "id": "b-1", "occurrence_id": "o-1",
    "reference_anchor": {"page": 10, "start": 2538, "end": 2657, "quote": "[1] ..."},
    "target_identifier": "arXiv:1607.06450"
  }],
  "claim_relation_proposals": [{
    "id": "r-1", "occurrence_id": "o-1",
    "context_anchor": {"page": 3, "start": 600, "end": 630, "quote": "... [ 1] ..."},
    "target_claim_revision_id": "candidate-revision-id",
    "relation_hint": "possibly_supports"
  }]
}
```

上面 `start/end` 只是字段示意，不能直接用于真实 PDF；它们必须精确匹配本服务从所上传字节提取的**该页 Unicode 文本**，不是 PDF 字节偏移。页号从 1 开始。错误 quote/越界/重复位置直接拒绝。服务端只把第一个可识别 `References` 标题之前的位置标成 `body_anchored`；标题之后的引用标记、或完全找不到可靠边界的论文，均标成 `abstained`。书目匹配只在服务端发现的 `References` 编号条目上给出 `reference_anchored`；arXiv/DOI 从条目中抽取完整标识符后规范化并整体比较，前缀/截断不能通过。编号或标识符不符时保留为 `abstained`，清空有效 `target_identifier`，另以 `submitted_target_identifier` 留作错误提议审计，不猜测 Crossref/work identity。同一个 occurrence 最多产生一条有效 match，其余重复提议弃答。作者—年份匹配在 v1 保持弃答。`ClaimRelationProposal` 即使有锚点也始终是 `unverified_semantics`，不能写入已接受的 `supports`、`entails` 等关系。

确定性 baseline 最多返回 200 个 occurrence；预览和 Artifact 的 `truncated=true` 表示扫描发现更多，`false` 表示没有触顶。对模型自提交的 proposal，该字段为 `null`，因为服务端无法据其输出断言召回完整。不能把 `len(occurrences)` 当作论文的完整引用数量。

Artifact 冻结原 PDF 的 SHA-256、大小、来源 URI 声明、`pypdf` 版本、逐页文本及其摘要、checker 源文件摘要、proposal 与提交者的服务端 principal。PDF 本身没有嵌入 Artifact，URI 不由服务端取回或鉴真；复核者需另取字节并核对 SHA-256。模型 route/prompt hash 如有填写，也只是提交方声明，不能当作可信 verifier identity 或独立性证明。对于扫描件没有 OCR 自动补造；复杂排版的提取顺序与 Unicode 规范化仍是限制。API 只接受不超过 4 MB、30 页、60 万字符的 PDF，但这不是独立 sandbox 的替代。

## 固定来源冒烟与排版留出检查

重放脚本：

```bash
python scripts/citation_paper_smoke.py --download
# 或使用自行下载且通过 SHA-256 校验的三份 PDF：
python scripts/citation_paper_smoke.py --pdf-dir tmp/pdfs
```

脚本通过真实 HTTP preview/commit、SQLite 持久化与 Artifact 回读完成验收；不提交 PDF 或运行时数据库。来源和预声明锚点固定在脚本里：

| 用途 | 固定论文 | 引用风格 / 预声明锚点 | PDF SHA-256 |
|---|---|---|---|
| 主冒烟 | [Attention Is All You Need](https://arxiv.org/pdf/1706.03762) | 单栏编号 `[ 1]`，参考文献含 `arXiv:1607.06450` | `bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697` |
| 排版/风格留出 | [Improving Evidence Retrieval with Claim-Evidence Entailment](https://aclanthology.org/2021.ranlp-1.174.pdf) | 双栏作者—年份 `(Parikh et al., 2016)` | `d8bab2c19af822c5477f545ce4a1121c39d249b04b56c8d547932b2fc82b12d8` |
| 排版/风格留出 | [CLAIM-BENCH](https://aclanthology.org/2025.ijcnlp-long.127.pdf) | 双栏复合作者—年份 `(Lu et al., 2023; Wei et al., 2023)` | `67eb7c07dee6dcd95d0a2745eb9a7b781a68f4c01f0594426912045d171589a1` |

2026-10-10 本地运行：主冒烟发现 74 个 occurrence，其中 54 个编号引用匹配到冻结参考文献条目；正文行首疑似参考文献条目的标记会保守弃答。两份留出论文分别发现 11、42 个 occurrence，书目匹配均为 0（主动弃答）。三次提交后 `qualification_receipts`、`current_use_bindings`、`authorization_grants` 的增量均为 0。这里只测了预声明锚点和权限不变量，**不是**全量 precision/recall 或真正的未知论文盲评；前两份双栏论文的版式在设计前已被人工查看，不能据此宣称统计泛化。独立盲评的冻结、人工标注与分层评分协议见 [CITATION_BLIND_EVAL.md](CITATION_BLIND_EVAL.md)。

## 后续队列策略

Topic、Taste、Novelty、Relevance 可以影响 *Discovery Priority*，不能改变 *Epistemic Qualification*。模型分数不得永久 early-reject 一个 family 或其其他 manuscript；低优先级队列应保留随机抽样，以估计漏检。此队列策略尚未在 v1 实现。
