# Req2Web 检索驱动 PageSpec 合成 v1

## 1. 位置与兼容策略

检索驱动合成使用独立 API，不改变 `req2web.page_spec.v1`：

```python
from req2web_generation import RetrievalGuidedPageSpecBuilder

result = RetrievalGuidedPageSpecBuilder().build(context, guidance)
page_spec = result.page_spec
```

完整链路为：

```text
req2web.agent.context.v1
+ req2web.retrieval.guidance.v1
-> RetrievalGuidedPageSpecBuilder
-> req2web.guided.page_spec.build_result.v1
   ├─ page_spec: req2web.page_spec.v1
   ├─ adopted
   ├─ ignored
   └─ fallback
```

旧 `PageSpecBuilder.build(context) -> PageSpec` 没有参数、字段或行为变更。未提供 guidance 时仍走旧路径，既有 PageSpec、Renderer、ConsistencyReport 和 ResultPackage 消费方不需要升级。guided build 先调用旧 builder，再做受门控的有限变换；没有给 `req2web.page_spec.v1` 偷加字段，也没有升级 Renderer、ConsistencyReport 或 ResultPackage schema。

## 2. build-result sidecar

`GuidedPageSpecBuildResult` 的 schema 是 `req2web.guided.page_spec.build_result.v1`，包含：

| 字段 | 含义 |
|---|---|
| `build_result_id` | 由 PageSpec 与三类决策的规范 JSON 计算出的稳定 ID |
| `guidance_bundle_id` | 被消费的 RetrievalGuidance 身份 |
| `page_spec` | 保持 v1 的完整 PageSpec |
| `adopted` | 实际改变 PageSpec 的检索指导 |
| `ignored` | 因不相关、冲突或缺少显式边界而拒绝的指导 |
| `fallback` | 弱/重复指导保守降级，以及直接来自 Agent context 的明确规则 |
| `source_context_schema_version` | 固定为 `req2web.agent.context.v1` |
| `source_guidance_schema_version` | 固定为 `req2web.retrieval.guidance.v1` |

每条检索决策保存 `guidance_id`、`doc_id`、role、采用/拒绝规则、原因和受影响的 PageSpec 实体/字段。每个 guidance item 必须且只能得到一个决策。`adopted` 必须有真实影响字段；`ignored` 和检索型 `fallback` 不能声称改变了 PageSpec。

`fallback` 中的 `source_kind=agent_context` 专门表示高优先级 context 规则。这类决策没有 `guidance_id` 或 `doc_id`，但必须列出其保证的 PageSpec 字段。因此宠物的 `media_input`、地图的手动地址恢复、看板指标与清除筛选不会被伪装成 RAG 发现。

## 3. 证据门控与优先级

guided build 在修改 PageSpec 前执行以下硬门禁：

1. context 必须是 `req2web.agent.context.v1`，guidance 必须是 `req2web.retrieval.guidance.v1`。
2. `source_context_schema_version`、`target_device` 和 `task_type` 必须一致。
3. 每个 guidance 的 `doc_id` 必须真实存在于当前 context 的同 role `retrieval_results`。
4. guidance 必须与 `RetrievalGuidanceBuilder` 从当前 context 确定性重建的结果逐字段一致，拒绝旁路篡改。
5. 所有引用 URI 只允许项目相对路径或 `https://`；Windows 绝对路径、UNC/根路径、`file://`、`file:///C:/...` 和 `file:C:/...` 均拒绝。
6. Builder 不打开引用资产、不读取 `data/raw`、不调用 Retriever、网络、外部 LLM 或 `bge-m3`。

优先级固定为：

```text
原始需求 / 显式 constraint
> context 的需求理解与用例
> 通过语义门控的检索指导
> PageSpecBuilder 旧保守默认
```

检索不能删除用例、修改设备、覆盖显式 constraint，不能把权限、支付等无关业务加入页面。requirement guidance 只补充证据边界，不把相似案例内容改写为用户要求。

## 4. use-case 相关性

`UseCaseGuidanceTrace` 当前由 RetrievalGuidanceBuilder 做确定性轮转覆盖分配，它只证明“指导可导航到用例”，不证明语义相关。

guided builder 不以该轮转关系作为采用条件。interaction guidance 必须根据用例标题、目标和预期结果与受控值做确定性匹配：

- `tap`：用例明确包含选择、确认、查看、提交等点击型动作；
- `swipe`：用例明确包含滑动、浏览、列表、结果等语义；
- `tap_and_swipe`：同一用例同时具备点击型和浏览/滑动型语义；
- `single_step_transition`：用例只有一个明确动作；
- `multi_step_transition`：用例含多个动作或明确组合表达。

轮转 trace 单独命中但语义不匹配时，决策进入 `ignored`，PageSpec 不变。

## 5. 五类 guidance 的实际影响

### 5.1 requirement

- 允许的 requirement/workflow 等参考只形成“检索案例不覆盖当前需求”的 builder constraint。
- `reusable_constraint` 只有在当前 context 已明确同一受控值时才可补充证据边界。
- `similar_task_type` 单独不足以改变页面，进入 fallback。

### 5.2 ui_reference

- `search_input`：影响搜索组件 purpose，并在相关页面选择 search/commerce/location/dashboard guided layout。
- `filter_control`：形成可由现有 Renderer 消费的筛选输入组件。
- `result_list`、`metric_summary`：形成 `data_view` 组件。
- `location_picker`：形成或加强 `location_picker` 组件与位置搜索布局。
- `empty_state`：只有当前需求或 constraint 明确空状态时，才形成空结果组件和状态表达。
- UI URI 只保留为证据，不复制资产。

同值多次召回只采用第一条稳定来源；跨业务召回进入 ignored。例如电商中的位置 UI、宠物中的搜索 UI、看板中的位置 UI 都不能越过 context 门禁。

### 5.3 interaction_flow

相关的 `tap`、`swipe`、`tap_and_swipe` 和单/多步状态提示会改变既有 `InteractionSpec.action` 的流程表达，但不增加新的业务目标。采用规则基于用例语义，不使用轮转 trace 作为证明。

### 5.4 implementation

`reference_structure`、受 context 支持的实现结构 token 和资源 kind 组合会形成来源明确的 builder constraint。约束只规定单页结构/资源组织和相对路径或 HTTPS 边界，不复制参考页面业务，也不读取 HTML、截图或草图文件。

### 5.5 validation

- `empty_state`：仅在需求明确无匹配/无数据时增加可达 empty 状态与 acceptance。
- `retry_recovery`：仅在显式 constraint 同时声明错误和恢复/重试时补充 acceptance。
- `permission_recovery`：仅在显式 constraint 同时声明权限拒绝与恢复时补充 acceptance。
- 通用 `regression_case` 和 Issue/PR URI 只作为证据；不相关的 permission/retry/loading 不污染 PageSpec。

检索 validation 只能补充已建立的业务边界。若 context 自身明确“定位不可用时手动选择地址”或“空状态后清除筛选”，guided builder 会以 `agent_context` fallback 建立恢复闭环，而不是等待或伪造 RAG 证明。

## 6. 四个真实 TF-IDF 案例

输入沿用 `outputs/retrieval_guidance_v1/` 的 context 与 guidance，生成物位于 `outputs/guided_page_spec_v1/`，不提交 Git。每个案例目录包含 `page_spec.json`、`build_result.json`，以及 Renderer 静态冒烟的 `rendered/` 四文件。

| 案例 | PageSpec 差异与来源边界 |
|---|---|
| `ecommerce` | mobile；guided commerce 搜索布局；搜索、筛选、购物车摘要、结算组件；tap+swipe 与多步流程表达；输入错误与重试 acceptance。购物车/结算首先由原需求保证，implementation guidance 只补充结构 constraint。 |
| `pet-recognition` | mobile；拍照/上传 `media_input`、分析结果；相机权限拒绝与恢复。弱 UI 召回没有证明 `media_input`，build-result 明确把它记录为 `agent_context` fallback；permission validation 只有在显式 constraint 下才采用。 |
| `map-address-search` | mobile；guided location search 布局、搜索结果与 `location_picker`；“定位不可用 -> 手动选择地址 -> initial”闭环直接来自显式 constraint。 |
| `desktop-dashboard` | desktop；guided dashboard 布局；关键指标、列表/筛选、详情、空结果提示；“查看空结果 -> 清除筛选 -> initial”闭环直接来自显式 constraint。无关权限/位置召回被拒绝。 |

四份 PageSpec 均通过 `PageSpec.validate()`，并由未修改的 `DeterministicPageRenderer` 生成 `index.html`、`styles.css`、`app.js` 和 `render_manifest.json`。本任务不把 guided PageSpec 整合到正式 ResultPackage。

## 7. CLI 与测试

生成一个案例并执行 Renderer 静态冒烟：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_guided_page_spec.py `
  --context .\outputs\retrieval_guidance_v1\ecommerce.context.json `
  --guidance .\outputs\retrieval_guidance_v1\ecommerce.json `
  --output-dir .\outputs\guided_page_spec_v1\ecommerce `
  --render-dir .\outputs\guided_page_spec_v1\ecommerce\rendered
```

完整回归：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

专项测试覆盖旧 build 兼容、逐字节确定性、schema/device/task/doc 门禁、UI 与 interaction 替换、validation 门控、无关恢复污染、轮转 trace 不足、宠物 context 来源、决策审计、本地 URI 拒绝、不读文件/网络/Retriever，以及现有 Renderer 兼容。

## 8. 边界

本版本不修改 Renderer 行为、ConsistencyReport 或 ResultPackage schema；不实现截图、视觉比较、最终控制台、外部 LLM、语义向量后端、数据扩充或 Agent 链重构。guided build-result 是独立内部 sidecar，不是最终用户结果包。

