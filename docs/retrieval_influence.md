# 检索影响检查与消融报告 v1

## 1. 位置与边界

本节点为已有检索指导合成增加可机器验证的影响证据：

```text
AgentContextBundle + RetrievalGuidance
-> unguided PageSpec / full guided PageSpec / five role ablations
-> DeterministicPageRenderer + MinimalConsistencyChecker (full guided only)
-> req2web.retrieval.influence.report.v1
```

实现位于 `src/req2web_generation/retrieval_influence.py`，公共接口为：

```python
report = RetrievalInfluenceChecker().check(
    context,
    guidance,
    baseline_page_spec,
    guided_result,
    ablations,
    render_result,
)
```

它只消费已经存在的 `AgentContextBundle`、`RetrievalGuidance`、旧版无指导 `PageSpec`、`GuidedPageSpecBuildResult`、受控角色消融结果和 full guided 的 `RenderResult`。它不读取 `data/raw`，不重建 RAG 索引，不调用 Retriever、LLM 或网络。既有 PageSpec、Renderer、ConsistencyReport 和 ResultPackage schema 均未修改。

## 2. 报告契约

报告 schema 固定为 `req2web.retrieval.influence.report.v1`。稳定字段包括：

- `report_id`：除自身外完整报告规范 JSON 的 SHA-256 前缀。
- `page_id`、`guidance_bundle_id`、`passed` 和结构化 `checks`。
- `structural_differences`：baseline 与 full guided PageSpec 的规范化、按实体 ID 和字段输出的差异；不含机器路径。
- `decision_traceability`：每条 adopted / ignored / fallback 的 disposition、`decision_id`、`guidance_id`、`doc_id`、role 和受影响字段；agent-context fallback 明确保留空 guidance/doc ID。
- `role_summaries`：五角色 adopted / ignored / fallback 计数和消融结论。
- `field_category_counts`：layout、section、component、state、interaction、constraint、acceptance_check 的影响计数。
- `decision_status_counts`：adopted、ignored、fallback 计数。
- `ablations`：每个角色的 full-vs-role-removed 差异和结论。
- `renderer_consistency`：复用既有一致性报告的 schema、通过状态和计数，不复制输出目录路径。

报告在 `decision_traceability` 中保留 `guidance_id`、`doc_id` 与 `decision_id` 作为关联 ID；不会写入工作区绝对路径、原始文档正文或引用资产。

## 3. 校验规则

开始时会严格验证 context、guidance、baseline、full result 和五份 ablation 的类型、schema 和身份：baseline 必须等于 `PageSpecBuilder` 从当前 context 的确定性输出；full result 和每份 role-removed result 必须分别等于 `RetrievalGuidedPageSpecBuilder` 的重新计算结果。由此可拒绝篡改 guidance、decision、身份关联、虚构 `affected_fields` 或把错误结果当作消融结果。

对于 full guided 输出，检查器会：

- 计算 baseline 到 guided 的规范化字段差异。
- 要求每条 adopted 的 `affected_fields` 都对应真实差异；任何“声称影响但字段未变”均为 fail。
- 要求重要结构实体都有 adopted、ignored 或 fallback 决策归因；不允许无决策的结构增量。
- 要求 ignored / retrieval fallback 不声明 `affected_fields`，避免把仅证据、无关或被拒绝的指导伪装为结构影响。
- 重新执行既有 guided-builder 身份和安全引用门禁，因此显式需求 / constraint 优先级及相对路径或 HTTPS 引用边界不能被报告绕过。
- 对完整 guided 页面复用 `MinimalConsistencyChecker`；渲染文件或静态结构不一致会使影响报告 `passed=false`。

## 4. 受控角色消融

`RetrievalGuidedPageSpecBuilder.build(context, guidance, disabled_roles=(role,))` 是专门的离线消融模式。它先完整校验原 guidance 的确定性身份，再把指定角色的所有 guidance 决策稳定标记为 `ignored`（`ablation_disabled_role:v1:<role>`），并阻止该角色进入既有变换规则。它不改变 guidance、不重跑检索，也不删除 context 的显式需求/constraint fallback。

报告将 full guided 与每份角色移除 PageSpec 比较：

- `role_has_influence`：移除后存在结构差异。
- `guidance_not_applicable_or_ignored`：该案例没有该角色的 adopted 决策且页面结构不变；这不是失败。
- `verification_failed`：存在 adopted 决策却移除后没有可验证差异；该项使报告失败。

因此，报告不会强行要求每个角色在每个案例都产生 UI 变化。

## 5. CLI 与真实固定案例

CLI 只接受已经保存的 context 和 guidance：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_retrieval_influence.py `
  --context .\outputs\retrieval_guidance_v1\ecommerce.context.json `
  --guidance .\outputs\retrieval_guidance_v1\ecommerce.json `
  --output-dir .\outputs\retrieval_influence_v1\ecommerce
```

它会稳定写出：`baseline/page_spec.json`、`full/page_spec.json`、`full/build_result.json`、full guided 的 `rendered/` 与 `consistency_report.json`、五个 `ablations/<role>/` 对照，以及 `retrieval_influence_report.json` 和对应 `.sha256` 文件。`outputs/` 被 Git 忽略。

对以下四个已有固定输入分别执行即可生成真实产物：

- `ecommerce.context.json` / `ecommerce.json`
- `pet-recognition.context.json` / `pet-recognition.json`
- `map-address-search.context.json` / `map-address-search.json`
- `desktop-dashboard.context.json` / `desktop-dashboard.json`

同一输入下，报告和 SHA-256 文件逐字节一致。完整回归：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 6. 不在范围内

本节点不扩展 bge-m3、外部 LLM、Topcoder、自动截图、像素级视觉比较、最终 Demo 控制台、ResultPackage v2 或自动修复。`passed=true` 仅说明当前确定性结构、归因、角色消融、静态渲染和既有一致性门禁通过。
