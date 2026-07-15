# Req2Web 确定性 UI 参考与 storyboard 交付侧车

## 1. 目的与边界

交付侧车把一个已经通过 Consistency 与 Retrieval Influence 双门禁的 `req2web.result.package.v2` 转换为独立、可离线查看的用户交付页。它补充两种呈现：已声明 UI 参考的可读卡片，以及由 PageSpec 状态与交互组成的分场景 storyboard。

它不是视觉理解器、参考图复刻器、截图生成器、视觉相似度检查器、最终 Demo 控制台，也不决定最终 Agent、API 或模型。它不重跑 Agent、RAG、Guidance、PageSpec、Renderer、Consistency 或 Influence，且绝不修改输入 v1/v2 包。

## 2. 公共接口与契约

```python
from req2web_generation import DeterministicDeliverySidecarBuilder

result = DeterministicDeliverySidecarBuilder().build(
    package_dir=package_dir,
    output_dir=output_dir,
    reference_root=None,  # 可选且必须显式提供
)
result.validate()
```

稳定 schema：

- `req2web.delivery.references.v1`
- `req2web.delivery.storyboard.v1`
- `req2web.delivery.sidecar.v1`

输入必须先通过 `RetrievalEnhancedResultPackage.validate()`：固定 v2 文件集、包内散列、PageSpec 关联、Consistency `passed=true/fail=0` 与 Retrieval Influence `passed=true` 均会重新从磁盘核验。v1、篡改包和损坏 PageSpec 会在创建输出前被拒绝。

## 3. 固定输出与发布

```text
sidecar/
├─ index.html
├─ styles.css
├─ ui_references.json
├─ storyboard.json
├─ delivery_manifest.json
└─ assets/                         # 仅有明确允许且实际复制的图片时出现
```

`delivery_manifest.json` 不自引用；它按稳定 POSIX 顺序列出其余全部文件的相对路径、字节数和 SHA-256。非空输出目录默认拒绝。构建先在同级 staging 写入并完整验证，再原子发布；失败会清理 staging，不会留下完整 manifest。

## 4. UI reference 与图片授权边界

每条 `ui_references.json.references` 都保留：`doc_id`、标题、原 URI、真实 reference kind、delivery mode、来源说明和本地相对文件（如有）。reference kind 只能从该包 `internal/agent_context.json` 中对应 `doc_id` 的真实 `references` 反查，不能按扩展名猜测。

默认不传 `reference_root`，所有条目都是 `reference_only`。只有同时满足以下条件才成为 `packaged_image`：

- 显式提供的 `reference_root` 是非符号链接目录；
- kind 是 `screenshot` 或 `semantic_image`；
- URI 是仓库相对 POSIX 路径，且扩展名是明确的位图格式；
- 解析后的真实文件仍位于该 root 内，路径链中没有符号链接；
- 文件在 staging 前后字节一致。

已声明但不允许、扩展名不匹配、root 未提供或文件缺失的引用会保留为 `reference_only`；绝对路径、`..`、外链 URI、符号链接或 PageSpec 父 doc/URI 追溯被篡改则结构化拒绝。不会读取 hierarchy 或 annotation JSON 内容。

参考页固定显示：**“参考图来自检索数据，只作设计参考，不代表生成页面截图或视觉理解。”** 复制的文件保持原字节，名称由 doc/kind/内容哈希稳定派生；同 URI 在一次输出中稳定去重。

## 5. Storyboard 语义

侧车只读取 `internal/page_spec.json` 的 `use_cases`、`states`、`components` 和 `interactions`。每个面板包含 interaction ID、源/目标状态、触发组件与标签、动作、反馈和关联用例。

输出先按 use case 分组，再按 `normal`、`error_entry`、`recovery` 或 `other` 场景分组。只有 interaction 的 `target_state_id -> source_state_id` 存在明确连接时才排成序列；无法形成唯一连接的项目放在 `independent_branches`。`storyboard.json.semantics.scenario_boards_not_single_global_journey=true` 明确禁止将其解读为唯一完整用户旅程。HTML 只渲染文字和状态面板，不伪造运行截图。

## 6. 离线与安全

HTML 全部使用转义文本/属性，不能把 URI 作为可执行内容；页面不包含脚本、`innerHTML`、外部 URL、CDN、网络字体或网络 API。侧车 JSON、HTML 与 manifest 均不得泄露工作区绝对路径。相同已验证包及相同显式资产在不同输出目录生成的文件逐字节一致。

## 7. CLI

单包（默认 `reference_only`）：

```powershell
.\.venv\Scripts\python.exe .\scripts\build_delivery_sidecar.py `
  --package-dir .\outputs\demo_v2_form_structure_run_a\packages\mobile-auth `
  --output-dir .\outputs\delivery_sidecar_run_a\mobile-auth
```

批量 12 包：

```powershell
.\.venv\Scripts\python.exe .\scripts\build_delivery_sidecar.py `
  --packages-dir .\outputs\demo_v2_form_structure_run_a\packages `
  --output-root .\outputs\delivery_sidecar_run_a
```

批量根目录另外生成 `aggregate_report.json` 与 `aggregate_manifest.json`；它们只引用每个独立侧车的 manifest，不改动原回归包。若项目随后对复制 RICO 图像作出明确许可，可在同一命令加 `--reference-root <显式根目录>`；当前默认验收不隐含该授权。

## 8. 测试与非目标

`tests/test_delivery_sidecar.py` 覆盖双门禁输入、v1/篡改拒绝、真实 kind 反查、图片复制与缺图降级、路径/外链/符号链接安全、HTML 注入、场景与独立分支语义、manifest、原子性、非空拒绝、绝对路径泄露、批量聚合与跨目录字节确定性。

本侧车不替代视觉比较、截图理解、无障碍检查、真实 API 行为、自动浏览器测试或最终 Demo 控制台。
