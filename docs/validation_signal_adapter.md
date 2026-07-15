# Validation 可追溯信号适配器

## 目标与边界

此适配器把**已加载的** 12 条 GitHub Issue/PR validation 派生文档中少量、受控的结构化事实带到紧凑检索结果，供 `RetrievalGuidanceBuilder` 形成候选 Guidance。它解决的不是召回数量，也不会把 Issue/PR 证据推断成新的 UI 行为。

```text
req2web.rag.document.v1 (已加载 validation 文档)
-> build_validation_signals()
-> TF-IDF compact retrieval result.validation_signals (可选字段)
-> RetrievalGuidanceBuilder
-> req2web.retrieval.guidance.v1 (可选 source.adapter_evidence)
-> 既有 explicit_context_gate / semantic_gate
```

不读取 `data/raw`，不重建 Retriever、Agent 链或默认 TF-IDF 查询/top-k；不修改 PageSpec v1、Renderer、Consistency、结果包 schema，也不启用 LLM、bge-m3 或视觉能力。

## 原有字段丢失边界与最小修复

统一 validation 文档本来保留 `metadata.category`、验收摘要、Issue/PR 白名单引用和 `doc_id`。旧 `TfidfIndex.search()` 的紧凑结果只传递 `doc_id`、身份字段、title、summary、references，未传递 `metadata`；因此 Guidance 层不能区分“输入校验”或“访问控制”这类已结构化来源，只能从 title/summary token 生成 `regression_case` 或不受支持值。

修复只在 `src/req2web_rag/index.py` 输出 validation 结果时增加可选 `validation_signals`。它由 `src/req2web_rag/validation_signals.py` 针对已在内存中的统一文档确定性生成；非 validation 结果和旧字段均不变。旧 persisted context 没有该字段时，`RetrievalGuidanceBuilder` 继续使用原有 `regression_case` / URI evidence-only 行为。

## 信号契约与受控映射

每个 `req2web.validation.signal.v1` 信号固定包含：

| 字段 | 含义 |
| --- | --- |
| `signal_id` | `doc_id + value + source_value + reference_uri + adapter_rule` 的稳定哈希 ID |
| `outcome` | `candidate` 或 `audit_only` |
| `value` | 候选仅可为现有集合的 `retry_recovery` / `permission_recovery`；审计记录为 `evidence_only` |
| `source_field` / `source_value` | 目前固定为 `metadata.category` 与其原值 |
| `reference_uri` | 当前文档中实际存在的 `issue` 或 `pull_request` 白名单引用 |
| `adapter_rule` | 固定规则名，例如 `github_validation_category_adapter:v1:input_error_to_retry_recovery` |

唯一候选映射为：

| 结构化 category | 受控候选 |
| --- | --- |
| `input_error` | `retry_recovery` |
| `auth_access` | `permission_recovery` |

其他 category 只产生 `audit_only/evidence_only`，明确记录为 `unsupported_category`，不能进入受控结构变换。缺失 `doc_id`、category 或 Issue/PR 白名单引用时不生成信号，原有 regression evidence 保持 fallback。紧凑记录里的信号若字段、规则、来源值、信号 ID 或引用与父结果不一致，也会被丢弃，不能成为候选。

Guidance 的 `source.adapter_evidence` 是向后兼容的可选字段，保留 signal ID、原字段/值、白名单引用、规则、outcome 和值。它不会改变 `req2web.retrieval.guidance.v1` 的必填字段；旧 JSON 反序列化时该字段默认为空列表。

## 采用与拒绝

适配器只负责“证据可否成为候选”。`RetrievalGuidedPageSpecBuilder` 仍是唯一可改变 PageSpec 的节点，且仍以原始需求/显式 constraint 为最高优先级：

- `retry_recovery` 只有已有错误 + 恢复/重试的显式边界时才可能 adopted；
- `permission_recovery` 只有已有权限拒绝 + 恢复的显式边界时才可能 adopted；
- `empty_state` 仍完全沿用既有门禁；本适配器不新增其映射；
- recovery 类型不一致继续以 `validation_guidance:v1:explicit_context_gate:*` ignored；
- 普通 `regression_case`、Issue/PR URI 和 audit-only 信号继续 evidence-only fallback。

因此，信号存在不表示必须产生页面差异。相同受控值来自不同 `doc_id` 时，既有稳定顺序的首条可以被采用，后续同值记录为 `duplicate:*` fallback；不按案例 ID、页面标题或固定 12 案例分支。

## 验证

专项测试 `tests/test_validation_signal_adapter.py` 覆盖来源字段、白名单引用、未知 category、缺字段、篡改来源/引用、旧紧凑结果兼容、不同 doc ID 的稳定去重、无 case-specific 常量与 recovery 语义门禁拒绝。完整回归还覆盖 RAG、Agent、Guidance、Guided Builder、影响双门禁、12 案例回归与 v1/v2 结果包。

真实 12 案例产物和诊断均写入 `outputs/`，默认不提交。诊断应如实比较“已检索但未转化”“已转化但门禁拒绝”和“adopted 且结构差异”；没有 adopted 或影响数提升仍可证明来源与拒绝链路已可审计。

## 本次 12 案例验证结果

以变更前冻结包和本实现重新构建的 12 案例包分别运行同一诊断，15 个低影响单元的结构性分布未被美化：

| 状态 | 变更前 | 变更后 |
| --- | ---: | ---: |
| 未检索到 validation/UI 证据 | 0 | 0 |
| 已检索但无可采纳结构转换 | 11 | 11 |
| 已转化候选但被显式/语义门禁拒绝 | 4 | 4 |
| adopted 且产生结构差异 | 0 | 0 |

变更后的 4 条门禁拒绝中，3 条 validation 分别以 `auth_access -> permission_recovery`（两条）和 `input_error -> retry_recovery`（一条）保留了 signal source evidence；它们仍因当前显式恢复类型不一致而 ignored。其余 audit-only 类别继续 evidence-only。12 个 v2 包均通过 Consistency / Retrieval Influence 双门禁，60 个角色单元为 44 `role_has_influence`、16 `guidance_not_applicable_or_ignored`、0 `verification_failed`。两次独立重建的所有产物文件逐字节一致。
