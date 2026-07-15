# Req2Web 端到端确定性结果包 v1

## 1. 链路位置

结果包是当前确定性链路的最后一个组合节点：

```text
AgentContextBundle
+ PageSpec
+ RenderResult / 静态页面
+ ConsistencyReport
-> DeterministicResultPackager
-> req2web.result.package.v1
```

公共接口：

```python
from pathlib import Path

from req2web_generation import DeterministicResultPackager

result = DeterministicResultPackager().package(
    context=context,
    page_spec=page_spec,
    render_result=render_result,
    consistency_report=report,
    package_dir=Path("outputs/result_package_v1/example"),
)
```

`ResultPackage.validate()` 会重新读取 `package_manifest.json`，核验目录文件集、相对路径、角色、大小、SHA-256、结果摘要身份和 manifest 不自引用等包级约束。

## 2. 输入边界

打包器只消费显式传入的四个既有对象，以及 `RenderResult` 指向的四个静态文件。它不会：

- 接收或理解原始模糊需求；
- 运行 Requirement Provider、Retriever 或 RAG；
- 运行 `PageSpecBuilder`、`DeterministicPageRenderer` 或 `MinimalConsistencyChecker`；
- 读取 `data/raw`；
- 调用外部服务；
- 自动修复上游失败。

外层 CLI 可以按现有模块顺序编排完整链路，但各节点的内部逻辑不复制到打包器。

## 3. 固定目录结构

```text
package_dir/
├─ page/
│  ├─ index.html
│  ├─ styles.css
│  ├─ app.js
│  └─ render_manifest.json
├─ internal/
│  ├─ agent_context.json
│  ├─ page_spec.json
│  └─ consistency_report.json
├─ result_summary.json
└─ package_manifest.json
```

所有包内引用使用 POSIX 风格相对路径。包内不写当前时间、随机数、机器信息或工作区绝对路径，也不复制 `data/raw`、截图、大型 RAG 正文或数据集文件。

目标目录已存在且非空时默认拒绝。正常构建先在目标同级 staging 目录写入并完整验证，最后原子发布；`package_manifest.json` 最后生成。门禁或写入失败不会在目标目录留下看似完整的 manifest。

## 4. 用户结果摘要

`result_summary.json` 的 schema 是 `req2web.result.summary.v1`，稳定字段包括：

| 字段 | 含义 |
|---|---|
| `schema_version` | 固定 schema 标识 |
| `package_id` / `page_id` | 包和页面的确定性身份 |
| `title` / `summary` | PageSpec 标题和摘要 |
| `target_device` / `page_type` | 目标设备和页面类型 |
| `entrypoint` | 固定为 `page/index.html` |
| `text_description` | 由 PageSpec 标题、摘要、核心用例和约束拼接的确定性说明 |
| `ui_references` | 仅保留 ui_reference 的 `doc_id`、`title`、`reference_uris` |
| `interaction_flow` | 按用例与 interaction 稳定排序的操作步骤 |
| `quality_gate` | consistency 的 passed、计数、warning、warning 文本和报告相对路径 |
| `artifacts` | 用户页面文件和内部追溯文件的路径、角色索引 |

`interaction_flow` 每一步包含：

- `step`
- `use_case_ids`
- `trigger_component_id`
- `action`
- `user_feedback`
- `target_state_id`

因此，PageSpec 中新增的异常触发与恢复交互会按稳定顺序直接进入结果摘要。例如电商包包含“模拟输入错误 -> state-error”和“恢复：修改输入并重试 -> state-initial”，宠物包包含“模拟相机权限拒绝 -> state-error”和“恢复：返回并改用示例输入 -> state-initial”。结果包不自行推断、补写或修复这些步骤。

`result_summary.json` 面向 Demo 使用者，用于快速找到离线页面、理解功能与流程、查看轻量 UI 参考和质量门禁。`internal/` 是 Agent context、PageSpec 和一致性报告的内部追溯材料，不是用户最终页面，也不能伪装成最终输出。

## 5. 包级 manifest

`package_manifest.json` 的 schema 是 `req2web.result.package.v1`，字段包括：

- `schema_version`
- `package_id`
- `page_id`
- `entrypoint`
- `result_summary`
- `files`

`files` 按相对路径稳定排序。每项包含：

- `path`
- `size`
- `sha256`
- `role`

manifest 声明除自身以外的全部 8 个包内文件，不包含自身哈希。SHA-256 根据最终文件字节计算。`package_id` 由已校验的 context、PageSpec、ConsistencyReport 和四个渲染文件的稳定字节派生，因此相同输入输出到不同目录时，整个包逐字节一致。

## 6. 一致性门禁

发布前依次验证：

1. `AgentContextBundle.validate()`、`PageSpec.validate()` 和 `ConsistencyReport.validate()` 通过。
2. context、PageSpec、render manifest 和 consistency report 都使用当前 v1 schema。
3. PageSpec 的摘要、设备、类型、核心用例、Agent 约束和五类轻量证据与传入 context 一致。
4. PageSpec、RenderResult、ConsistencyReport 的 `page_id` 一致。
5. consistency report 的 `passed=true`、`summary.fail=0`，且打包所需的输入、manifest、文件存在和三个静态 SHA 检查均为 pass。
6. RenderResult 的四个路径都是其输出目录下的规范文件，文件存在且为 UTF-8。
7. render manifest schema、PageSpec schema、`page_id`、三文件稳定声明正确。
8. 重新计算 `index.html`、`styles.css`、`app.js` 的 SHA-256，并与 render manifest 比较。
9. 将 warning 原样写入 `result_summary.quality_gate`，warning 不单独阻止发布。
10. 写完 staging 后再执行包级文件集、相对路径、大小、SHA-256、schema 和身份核验。

明确错误恢复约束若未通过检查器的异常入口、反馈、恢复路径或 acceptance 门禁，会产生 consistency fail，从而阻止结果包发布。loading、empty 的非验收不可达 warning 仍原样进入 `quality_gate`，不单独阻止发布。

因此，报告生成后若静态文件被修改而 render manifest 未同步，打包器会因重新计算的 SHA-256 不匹配而拒绝发布。

## 7. CLI

入口为 `scripts/build_result_package.py`。移动电商示例：

```powershell
.\.venv\Scripts\python.exe .\scripts\build_result_package.py `
  "做一个带搜索、购物车和结算的移动电商页面" `
  --target-device mobile `
  --constraint "输入错误时给出可恢复提示" `
  --output-dir .\outputs\result_package_v1\ecommerce
```

宠物情绪识别示例：

```powershell
.\.venv\Scripts\python.exe .\scripts\build_result_package.py `
  "做一个宠物情绪识别 App，用户拍照后展示识别结果" `
  --target-device mobile `
  --constraint "相机权限被拒绝时给出恢复提示" `
  --output-dir .\outputs\result_package_v1\pet-recognition
```

CLI 依次复用 `MinimalAgentChain`、`PageSpecBuilder`、`DeterministicPageRenderer`、`MinimalConsistencyChecker` 和 `DeterministicResultPackager`。成功时向标准输出写 `ResultPackage` JSON 并返回 0；失败时向标准错误写明确错误、返回 2，且不发布完整包。

## 8. 当前仍不包含

v1 不包含：

- 自动截图管线或截图文件复制；
- 像素级视觉比较或浏览器绘制质量评分；
- 最终 Demo 控制台或新的网页外壳；
- 外部 LLM、`bge-m3`、服务器、数据库或真实 API；
- 失败页面自动修复。

如需检查结果包中 `page/` 的实际浏览器表现，按 [本地页面可视化验收手册](local_visual_acceptance.md) 执行临时、可清理的人工验收。该操作不向包内增加文件，也不改变结果包 schema。

端到端确定性结果包完成后，下一工作对话应先按 `docs/demo_spec.md` 做 Demo 里程碑验收和缺口决策，而不是自动扩展截图、控制台或视觉评估功能。

当前两个真实验收包继续固定为 9 个文件，位于 `outputs/result_package_v1/ecommerce` 与 `outputs/result_package_v1/pet-recognition`。它们不包含截图；相同输入独立生成到不同目录时，PageSpec、页面、报告、摘要和包级 manifest 均逐字节一致。

检索增强交付使用独立且向后兼容的 [结果包 v2](result_package_v2.md)；本文件描述的 v1 接口、目录、摘要和验证规则保持不变。
