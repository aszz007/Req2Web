# Req2Web 检索增强结果包 v2

## 1. 位置与兼容性

v2 是独立的确定性发布接口，不修改 `req2web.result.package.v1`、`req2web.result.summary.v1`、`DeterministicResultPackager` 或 `scripts/build_result_package.py`。

```text
AgentContextBundle + RetrievalGuidance + GuidedPageSpecBuildResult
+ final guided PageSpec + RenderResult + ConsistencyReport
+ RetrievalInfluenceReport
-> DeterministicRetrievalEnhancedResultPackager
-> req2web.result.package.v2
```

公共接口为：

```python
from req2web_generation import DeterministicRetrievalEnhancedResultPackager

result = DeterministicRetrievalEnhancedResultPackager().package(
    context, guidance, guided_build_result, page_spec,
    render_result, consistency_report, retrieval_influence_report,
    package_dir,
)
```

打包器仅组合显式传入的既有对象与 `RenderResult` 的四个静态文件。它不会运行 Retriever、RAG、指导构建、PageSpec 构建、渲染、一致性/影响检查或自动修复。

## 2. 固定包结构

```text
package_dir/
├─ page/
│  ├─ index.html
│  ├─ styles.css
│  ├─ app.js
│  └─ render_manifest.json
├─ internal/
│  ├─ agent_context.json
│  ├─ retrieval_guidance.json
│  ├─ guided_page_spec_build_result.json
│  ├─ page_spec.json
│  ├─ consistency_report.json
│  └─ retrieval_influence_report.json
├─ result_summary.json
└─ package_manifest.json
```

不复制 baseline PageSpec 或五份完整角色消融目录：它们是影响报告形成时的内部受控对照，而 `retrieval_influence_report.json` 已保留结构差异、决策归因和每个角色的消融结论，足以支持离线审计。

## 3. 发布门禁与磁盘校验

发布前，v2 验证所有对象 schema，并交叉核对 Agent context、guidance、guided build-result、最终 PageSpec、渲染结果、ConsistencyReport 与 RetrievalInfluenceReport 的 `page_id`、`guidance_bundle_id`、build/report ID 和内容关系。最终 guided PageSpec 必须与 build-result 内 PageSpec 完全相同。

两个门禁都必须通过：

- consistency：`passed=true`、`summary.fail=0`，且 v1 静态文件/manifest 必要检查均为 pass；
- retrieval influence：`passed=true`，并且其复用的 renderer consistency 为通过且与传入 consistency summary 一致。

打包时重新计算三个页面静态文件 SHA-256 并核对 render manifest。`validate()` 从磁盘重新验证固定文件集、POSIX 相对路径、角色、大小、SHA-256、summary 身份、内部 JSON 关联、入口、manifest 不自引用和无工作区绝对路径。

目标目录非空即拒绝。写入在目标同级 staging 中完成，完整验证后以原子替换发布；失败时清理 staging，目标不会留下看似完整的 `package_manifest.json`。

## 4. 面向使用者的 summary v2

`result_summary.json` schema 为 `req2web.result.summary.v2`。它保留页面标题、说明、UI 参考、页面入口和质量门禁，并新增：

- 五角色 adopted / ignored / fallback 计数与角色消融结论；
- 结构影响分类计数与决策状态计数；
- consistency 与 retrieval-influence 双门禁；
- `interaction_flow_semantics`：`interaction_flow` 是稳定交互清单，覆盖正常与异常恢复场景，不表示严格单一路径执行顺序。

## 5. 端到端 CLI

新入口不会覆盖 v1：

```powershell
.\.venv\Scripts\python.exe .\scripts\build_retrieval_enhanced_result_package.py `
  "做一个带搜索、筛选、购物车和结算的移动电商页面" `
  --target-device mobile `
  --constraint "输入错误时允许修改并重试" `
  --output-dir .\outputs\result_package_v2\ecommerce
```

CLI 只按顺序复用 `MinimalAgentChain`、`RetrievalGuidanceBuilder`、`PageSpecBuilder` baseline、`RetrievalGuidedPageSpecBuilder`、五角色受控消融、Renderer、ConsistencyChecker、RetrievalInfluenceChecker 与 v2 Packager，不复制这些节点的业务逻辑。

## 6. 固定案例与边界

验收固定使用移动电商、宠物情绪识别、地图地址搜索和桌面数据看板四类需求。相同输入生成到不同目录时，包中每个文件（包括 package manifest）必须逐字节一致；可对 `package_manifest.json` 计算 SHA-256 作为包级确定性证据。

本版本仍不包含 bge-m3、外部 LLM、Topcoder 扩充、截图、像素视觉比较、浏览器自动化验收、最终 Demo 控制台、服务器、数据库、公开托管或失败页面自动修复。

## 7. 可选交付侧车（不修改 v2）

面向用户的 UI 参考与分场景 storyboard 由独立的 `req2web.delivery.sidecar.v1` 生成，说明见 [delivery_sidecar.md](delivery_sidecar.md)。它把一个已通过本章双门禁的 v2 包作为只读输入，生成自己的离线 HTML、JSON 与 manifest；不会把任何文件写回 v2 包，也不重跑既有节点。

默认输出 `reference_only` 卡片。只有项目提供显式 `reference_root` 且引用满足 kind、相对路径、扩展名、非符号链接与稳定性规则时，侧车才复制 `screenshot` / `semantic_image` 位图。该能力不是图像理解、生成页面截图或视觉比较。
