# Req2Web PageSpec v1

## 1. 在完整链路中的位置

`PageSpec` 是 Agent 与页面渲染器之间的内部契约：

```text
模糊需求
-> 最小 Agent 链
-> req2web.agent.context.v1
-> PageSpecBuilder
-> req2web.page_spec.v1
-> DeterministicPageRenderer
-> 离线静态页面
```

当前实现位于 `src/req2web_generation/`。`PageSpecBuilder` 只消费已经完成需求理解和五类检索的 `AgentContextBundle`，不会重新理解需求、重复检索、调用外部 LLM 或读取 `data/raw`。

## 2. v1 字段

| 字段 | 含义 |
|---|---|
| `schema_version` | 固定为 `req2web.page_spec.v1` |
| `page_id` | 由完整 context bundle 的规范化 JSON 哈希生成的稳定页面 ID |
| `title` / `summary` | 页面短标题和 Agent 需求摘要 |
| `target_device` / `page_type` | 目标设备和任务类型 |
| `layout` | 页面布局模式以及 section 的稳定顺序 |
| `use_cases` | 从 context bundle 原样保留的 2–4 个核心用例 |
| `sections` | 按用例组织的页面区域，包含稳定 ID、用途和组件引用 |
| `components` | 渲染器可消费的组件类型、标签、用途和所属 section |
| `states` | 与任务相关的 `initial`、`loading`、`error`、`success`，以及需要时的 `empty` |
| `interactions` | 触发组件、源状态、动作、目标状态、用户反馈和关联用例 |
| `constraints` | Agent 约束和构建器补充的设备、结构边界 |
| `acceptance_checks` | 每个核心用例的可验证成功条件和目标状态 |
| `traceability` | context schema、轻量 RAG 证据和用例到页面结构的追溯关系 |

section、component 和 interaction 均使用由用例 ID 派生的稳定 ID。相同 `AgentContextBundle` 会产生完全一致的字典和 ID。

## 3. context bundle 到 PageSpec 的映射

| `req2web.agent.context.v1` | `req2web.page_spec.v1` |
|---|---|
| `original_requirement` | `title` 的确定性来源，并参与 `page_id` 哈希 |
| `requirement_summary` | `summary` |
| `target_device` | `target_device` 和设备约束 |
| `task_type` | `page_type` |
| `constraints` | `constraints` |
| `use_cases` | `use_cases`、sections、components、interactions 和 acceptance checks |
| 五类 `retrieval_results` | `traceability.evidence` 与每个 `UseCaseTrace` 的 `evidence_doc_ids` |

RAG 证据只保留 `role`、`doc_id`、`title` 和最多 3 个必要的 `reference_uris`。检索分数、摘要、正文、metadata 和原始大文件都不复制到 PageSpec。每个用例都至少关联 requirement、ui_reference、interaction_flow、implementation、validation 五类证据。

## 4. 校验边界

`PageSpec.validate()` 会检查：

- schema 版本和必要文本字段；
- 2–4 个核心用例；
- section、component、state、interaction、constraint 和 acceptance check ID 的唯一性；
- layout、section、component、state 和 interaction 的引用完整性；
- 每个组件必须被其所属 section 的 `component_ids` 收录，且 section 内不得重复；
- interaction 的触发组件、源状态和目标状态真实存在；
- interaction 允许源状态和目标状态相同，以表达刷新、重试或原地反馈；
- acceptance checks 覆盖全部核心用例；
- 五类轻量证据齐全；
- 每个用例对 section、component、interaction 和 RAG `doc_id` 的追溯有效。

`PageSpec.to_dict()` 会先执行校验，再返回可直接交给 `json.dumps` 的字典。

## 5. CLI

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_page_spec.py "做一个宠物情绪识别 App，用户拍照后展示识别结果"
```

可复用最小 Agent 链的既有参数：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_page_spec.py `
  "做一个带搜索、购物车和结算的移动电商页面" `
  --top-k 2 `
  --target-device mobile `
  --constraint "输入错误时给出可恢复提示"
```

CLI 先通过现有 `MinimalAgentChain` 产生 `AgentContextBundle`，再把该对象交给 `PageSpecBuilder`，不会复制 Agent 链内部逻辑。

## 6. 当前不包含的内容

PageSpec 构建器本身不生成 HTML、CSS、JavaScript 或截图。下游确定性页面渲染器见 `docs/page_renderer.md`；PageSpec 仍是内部中间产物，不是最终用户页面。

渲染器以 `req2web.page_spec.v1` 为唯一业务输入，不直接消费原始模糊需求，也不重复执行 Agent 或 RAG。下游最小结构一致性检查器见 `docs/consistency_checker.md`；当前仍未实现自动截图或视觉一致性比较。
