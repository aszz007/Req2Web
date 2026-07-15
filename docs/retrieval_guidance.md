# Req2Web 检索指导中间契约 v1

## 1. 位置与边界

第二阶段新增的 `req2web.retrieval.guidance.v1` 位于既有 Agent context 和未来的 PageSpec 检索驱动合成之间：

```text
req2web.agent.context.v1
-> RetrievalGuidanceBuilder
-> req2web.retrieval.guidance.v1
-> RetrievalGuidedPageSpecBuilder / req2web.guided.page_spec.build_result.v1
```

实现位于 `src/req2web_generation/retrieval_guidance.py`。`RetrievalGuidanceBuilder` 的唯一业务输入是已完成的 `AgentContextBundle`，尤其是其中已经生成的五类 `retrieval_results`。它不会：

- 重新运行需求理解或 Retriever；
- 打开 `data/raw`、统一语料、索引或检索结果所指向的资产；
- 调用外部服务、LLM，或安装/启用 `bge-m3`；
- 改动 `PageSpecBuilder`、渲染器、一致性检查器或结果包 schema。

该契约现在由独立 `RetrievalGuidedPageSpecBuilder` 显式消费；旧 `PageSpecBuilder.build(context)` 与 PageSpec v1 schema 保持不变。采用、拒绝和保守降级规则见 `docs/guided_page_spec.md`。

## 2. 稳定契约

`RetrievalGuidance` 的 `schema_version` 固定为 `req2web.retrieval.guidance.v1`。稳定顶层字段为：

| 字段 | 含义 |
|---|---|
| `guidance_bundle_id` | 由完整 context 与最终指导规范化 JSON 哈希得到的稳定 ID |
| `target_device` / `task_type` | 从 Agent context 原样保留的设备和任务类型 |
| `requirement_guidance` | 相似任务类型、可复用约束、业务/参考边界提示 |
| `ui_guidance` | 布局提示、组件提示和 UI 参考 URI |
| `interaction_guidance` | 操作模式和状态变化提示 |
| `implementation_guidance` | 实现结构及资源/实现参考约束 |
| `validation_guidance` | 异常、恢复和验收提示 |
| `use_case_traces` | 每个用例使用的 guidance ID 和 source doc ID |
| `source_context_schema_version` | 固定为 `req2web.agent.context.v1` |

每个 `GuidanceItem` 有稳定 `guidance_id`、`category`、受控 `value` 和 `source`。`source` 必含：

- `role` 和 `doc_id`；
- `source_fields`：实际使用的 compact retrieval-result 字段名；
- `extraction_rule`：稳定的适配器或 token-rule 名称；
- `reference_uris`：仅在白名单引用类型可用时保留，不复制资产或大型正文。

`validate()` 会校验 schema、五类非空指导、ID 唯一性、角色一致性、字段级来源、无绝对路径，以及“每项指导至少被一个用例 trace 使用”。signal-backed item 的可选 `source.adapter_evidence` 还保留 signal ID、原字段和值、白名单引用和 adapter rule；旧 guidance 不含该字段时保持兼容。`to_dict()` 先调用 `validate()`；相同输入以排序 JSON 计算哈希并输出逐字节一致的 JSON。

## 3. 显式角色 / 来源适配规则

当前 `TfidfRetriever` 的 compact result 不透传一般语料 `metadata` 或 `content`。它仍只消费每条已检索结果中的 `role`、`doc_id`、`dataset`、`subset`、`title`、`summary`、`references`；唯一的受控例外是 validation 结果可选的 `validation_signals`。该字段只由已加载 unified validation document 的结构化 `metadata.category` 和现存 Issue/PR 白名单引用确定性派生，不能触发 corpus 回读。不同数据来源的字段差异已经在 RAG 统一层归一；本层再按 role 使用以下可审计白名单。

| role | 实际作用 | 白名单字段与规则 |
|---|---|---|
| `requirement` | 相似任务类型、可复用约束、业务边界 | `dataset` + `subset` 形成来源任务类型；`references.kind` 仅取 requirement/workflow/prototype/resource_directory；标题/摘要仅可命中受控 token map |
| `ui_reference` | 页面区域、组件、UI URI | 标题/摘要命中受控布局/组件 token；`references` 仅取 screenshot/semantic_image/view_hierarchy/semantic_annotation；可选 `ui_structure_signals` 仅重新校验并映射 `form_structure` |
| `interaction_flow` | 操作模式、状态变化 | 标题/摘要仅识别 mixed/swipe；step_screenshot/step_hierarchy 的数量决定 single/multi-step transition |
| `implementation` | 结构和资源约束 | 标题/摘要命中受控组件/布局 token；仅取 html/target_html/screenshot/target_screenshot/input_sketch/prototype 的引用 kind 组合 |
| `validation` | 异常、恢复、验收 | 固定 regression_case 仅表示当前记录可作验收回归案例；标题/摘要命中 permission/retry/loading/empty 等受控 token；可选 signal 仅将 `input_error -> retry_recovery`、`auth_access -> permission_recovery` 形成候选；仅取 issue/pull_request URI |

受控 token map 的输出是固定枚举（例如 `search_input`、`location_picker`、`permission_recovery`、`empty_state`），不会把标题、摘要或未知 metadata 自由拼接成“事实”。无匹配时输出角色专属的保守结构提示（例如 `reference_screen` 或 `reference_structure`），而不是猜测不存在的组件、流程或验收条件。

## 4. 使用方式

先用既有 Agent CLI 生成 context（这一步在 Builder 之外），再把已保存的 JSON 交给新 CLI。Windows PowerShell 下请显式指定 Python 标准输出为 UTF-8，并用 `Start-Process` 直接保存标准输出；普通文本管道可能按当前代码页转换中文：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
Start-Process -FilePath .\.venv\Scripts\python.exe `
  -ArgumentList @(
    '.\scripts\run_agent_chain.py',
    '做一个带搜索、筛选、购物车和结算的移动电商页面。',
    '--top-k', '5', '--target-device', 'mobile',
    '--constraint', '输入错误时应允许修改并重试。'
  ) `
  -RedirectStandardOutput .\outputs\retrieval_guidance_v1\ecommerce.context.json `
  -NoNewWindow -Wait

.\.venv\Scripts\python.exe .\scripts\run_retrieval_guidance.py `
  --context .\outputs\retrieval_guidance_v1\ecommerce.context.json `
  --output .\outputs\retrieval_guidance_v1\ecommerce.json
```

`outputs/` 已被 Git 忽略。CLI 不接受原始需求，因而不能在这个节点意外重跑需求理解或检索。

## 5. 固定案例基线

以下四个真实 context 均使用当前本地 TF-IDF index 生成，随后由本 Builder 生成 guidance；JSON 位于 `outputs/retrieval_guidance_v1/`：

| 文件 | 覆盖重点 |
|---|---|
| `ecommerce.json` | mobile、搜索/筛选、购物车/结算、输入错误修改重试 |
| `pet-recognition.json` | mobile、拍摄/上传、分析结果、相机权限恢复 |
| `map-address-search.json` | mobile、地点搜索、选择/确认位置、定位不可用时手动选择 |
| `desktop-dashboard.json` | desktop、指标/列表筛选/详情、空状态、清除筛选 |

四个文件是检索基线而不是 PageSpec 或页面产物：它们记录当前检索的结构化影响和来源，后续 PageSpec 集成任务必须显式消费它们，不能把这些基线误称为已生成的 UI。

## 6. 测试

`tests/test_retrieval_guidance.py` 覆盖：

- schema、`validate()`、`to_dict()` 与完整五类角色；
- 同一 AgentContextBundle 的规范 JSON 逐字节确定性；
- 替换 UI fixture 后，`layout_hint` 从搜索结构变为 `location_picker`，而不是只改变 doc_id；
- 未命中白名单 token 的文本不会成为指导；
- 任一角色为空、字段损坏或输出绝对路径时给出明确错误；
- 每项指导可以反查到 context 中现有的检索 doc；
- 通过禁止 `open()` 的测试确认 Builder 不读文件；Builder 没有网络、LLM 或 Retriever 调用点。

运行全部回归：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 7. 下游边界

检索驱动 PageSpec 已使用独立 build-result sidecar 接入，但尚未整合到正式 ResultPackage。Renderer、ConsistencyReport 和 ResultPackage schema 没有随之升级；截图、视觉比较和最终控制台仍在范围外。
