# Req2Web 最小一致性检查器 v1

## 1. 链路位置与输入边界

`MinimalConsistencyChecker` 位于确定性页面渲染之后：

```text
PageSpec + RenderResult / 本地静态页面
-> MinimalConsistencyChecker
-> req2web.consistency.report.v1
```

公共接口：

```python
from req2web_generation import MinimalConsistencyChecker

report = MinimalConsistencyChecker().check(
    page_spec=page_spec,
    render_result=render_result,
)
```

检查器只消费以下输入：

- 已通过 `PageSpec.validate()` 的 `PageSpec`；
- `RenderResult`；
- `RenderResult.output_dir` 下的本地静态文件。

检查器不会读取原始模糊需求、`AgentContextBundle`、Retriever、RAG 索引或 `data/raw`，也不会重新运行 Agent、检索、`PageSpecBuilder` 或页面渲染器。CLI 位于检查器外层，只负责复用现有链路完成真实冒烟。

输入类型错误、PageSpec 非法或无法确定检查根目录时会抛出明确异常。缺文件、坏 manifest、哈希不一致、HTML 或运行时数据被篡改时，检查器会尽量返回 `passed=false` 的结构化报告，而不是无报告退出。

## 2. 报告契约

稳定 schema 为 `req2web.consistency.report.v1`。`ConsistencyReport` 字段如下：

| 字段 | 含义 |
|---|---|
| `schema_version` | 固定为 `req2web.consistency.report.v1` |
| `page_id` | 被检查的 PageSpec 页面 ID |
| `passed` | 只有不存在 `fail` 检查项时才为 `true` |
| `summary` | `total`、`pass`、`fail`、`warning` 四类计数 |
| `checks` | 按稳定检查顺序输出的检查项 |
| `warnings` | 按检查顺序提取的 warning 消息 |

每个检查项包含：

| 字段 | 含义 |
|---|---|
| `check_id` | 稳定且唯一的检查标识 |
| `category` | 检查类别 |
| `status` | `pass`、`fail` 或 `warning` |
| `message` | 不包含绝对机器路径的结果说明 |
| `related_ids` | 相关页面、文件、section、component、state、interaction、use case 或 acceptance ID |

`ConsistencyReport.validate()` 会校验 schema、检查 ID 唯一性、状态值、summary 计数、`passed` 语义以及 warning 列表。`to_dict()` 在校验后返回可直接交给 `json.dumps` 的字典。报告不写入时间、随机数或绝对机器路径；相同 PageSpec 和相同静态字节会得到完全相同的报告。

## 3. 状态语义

- `pass`：该项结构证据与 PageSpec 或渲染契约一致。
- `fail`：存在会破坏结构忠实度、离线边界或文件完整性的错误；任意 fail 都会令 `passed=false`。
- `warning`：当前产物仍可通过，但存在能力边界或非核心覆盖缺口；warning 不会单独令 `passed=false`。

当前 warning 包括：

- loading、error、empty 等非验收目标状态没有可达 interaction；
- PageSpec 使用当前渲染器只能以可见通用控件降级的组件类型；
- 输出目录存在 manifest 未声明的额外本地文件。

CLI 自己生成的 `consistency_report.json` 是检查器 sidecar，不作为额外文件 warning，保证对同一目录重复运行仍能生成相同报告。

## 4. 实际检查类别

### 4.1 `input_identity`

- PageSpec 已通过 v1 校验；
- `RenderResult.page_id` 与 PageSpec 一致；
- 四个 `RenderResult` 路径是 `output_dir` 下的规范文件；
- render manifest 是 JSON 对象，字段、schema、PageSpec schema、page_id 和文件声明符合 v1。

### 4.2 `file_integrity`

- `index.html`、`styles.css`、`app.js`、`render_manifest.json` 均存在且为 UTF-8；
- manifest 按稳定顺序声明三个页面文件；
- 每个声明文件都存在，实际字节 SHA-256 与 manifest 一致。

### 4.3 `html_structure`

- 使用 Python 标准库 `HTMLParser`，不依赖浏览器自动化；
- `body[data-page-id]` 和 PageSpec schema 标识正确；
- title、h1 和 summary 以解析后的文本安全呈现；
- section 恰好出现一次并遵循 `layout.section_order`；
- component 恰好出现一次且位于所属 section；
- `component_type` 与 `data-renderer-kind` 可追踪；
- 未知组件类型必须保留可见的 `fallback`；
- 不允许未被 PageSpec 追踪的 section 或 component。

### 4.4 `runtime_coverage`

- 通过固定 `const PAGE_DATA = Object.freeze(...)` 边界和 `json.JSONDecoder` 读取运行时 JSON；不执行 JavaScript，不使用 `eval`；
- page_id、初始状态和 component—section 映射与 PageSpec 一致；
- 每个 PageState 和 InteractionSpec 恰好进入运行时一次且字段一致；
- interaction 触发组件存在于 HTML，目标状态存在于运行时；
- 允许源状态与目标状态相同；
- 可执行 JavaScript 不使用 `innerHTML`；
- HTML、CSS 和 JavaScript 不包含外部 URL 依赖或网络 API。

### 4.5 `acceptance_coverage`

- 每个 AcceptanceCheck 引用的 use case 存在；
- 验收状态存在于渲染运行时；
- 至少一个相关的已渲染 interaction 能到达验收目标状态；
- 每个核心用例均有实际 section、component、interaction 和 acceptance check 覆盖。

### 4.6 `capability_boundary`

该类别保存上述 warning。它明确陈述当前结构检查的能力边界，不把未执行的视觉检查伪装为 pass。

## 5. CLI

移动电商示例：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_consistency_check.py `
  "做一个带搜索、购物车和结算的移动电商页面" `
  --target-device mobile `
  --constraint "输入错误时给出可恢复提示" `
  --output-dir .\outputs\consistency_v1\ecommerce
```

宠物识别示例：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_consistency_check.py `
  "做一个宠物情绪识别 App，用户拍照后展示识别结果" `
  --target-device mobile `
  --constraint "相机权限被拒绝时给出恢复提示" `
  --output-dir .\outputs\consistency_v1\pet-recognition
```

CLI 依次复用 `MinimalAgentChain`、`PageSpecBuilder`、`DeterministicPageRenderer` 和 `MinimalConsistencyChecker`，不会复制各模块内部逻辑。标准输出为完整报告 JSON，同时在输出目录写入确定性的 `consistency_report.json`。报告通过时退出码为 0，存在 fail 时退出码为 2。

## 6. 当前不检查的内容

当前版本只做确定性结构一致性检查，不检查：

- 截图或视觉相似度；
- 像素级布局、颜色、字体和响应式视觉质量；
- 浏览器实际绘制结果或可访问性树；
- LLM 主观评审；
- 服务端、数据库或真实外部 API 行为；
- 最终 Demo 控制台、结果页和用户结果包。

因此 `passed=true` 只表示静态产物通过当前结构、状态、交互、验收、manifest 和离线边界检查，不表示完整视觉一致性已经验证。

## 7. 测试与下一阶段

`tests/test_consistency_checker.py` 覆盖正常电商与宠物页面、JSON 序列化、确定性、文件删除、哈希篡改、RenderResult/manifest page_id 篡改、section/component 删除或错位、interaction/state 删除、目标状态修改、同状态交互、warning、未知类型降级、`innerHTML`、外部网络依赖和非法 PageSpec。

端到端结果包整合已经完成：`DeterministicResultPackager` 以现有 `AgentContextBundle`、PageSpec、`RenderResult`、静态页面和 `ConsistencyReport` 为输入，组合面向 Demo 的稳定结果目录与说明元数据，见 `docs/result_package.md`。它不会在一致性检查器内部重新生成或自动修复任何上游产物。下一工作对话先做 Demo 里程碑验收和缺口决策。
