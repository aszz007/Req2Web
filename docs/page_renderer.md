# Req2Web 确定性 PageSpec 页面渲染器

## 1. 输入边界

`DeterministicPageRenderer` 的唯一业务输入是已经构建完成的 `PageSpec`：

```python
from pathlib import Path

from req2web_generation import DeterministicPageRenderer, PageSpec

result = DeterministicPageRenderer().render(
    page_spec=page_spec,
    output_dir=Path("output/page"),
)
```

渲染器不接收原始模糊需求、`AgentContextBundle`、Retriever、RAG 索引或 `data/raw`。`render()` 在创建输出目录前调用 `PageSpec.validate()`；非法契约不会产生任何输出文件。

CLI 位于渲染器外层，可以依次编排现有 `MinimalAgentChain`、`PageSpecBuilder` 和 `DeterministicPageRenderer`，但没有复制需求理解、检索或 PageSpec 构建逻辑。

## 2. PageSpec 到 HTML 的映射

| PageSpec 字段 | 页面输出 |
|---|---|
| `page_id` | `body[data-page-id]`、页脚和 render manifest |
| `title` / `summary` | 页面标题、主标题和摘要 |
| `target_device` / `page_type` | 页面元信息与响应式 CSS 选择器 |
| `layout.pattern` | `main[data-layout-pattern]` |
| `layout.section_order` | section 的 DOM 顺序 |
| `section.component_ids` | section 内组件的 DOM 顺序 |
| section ID / use case ID | `id`、`data-section-id`、`data-use-case-ids` |
| component ID / type | `id`、`data-component-id`、`data-component-type` |
| `states` | `app.js` 状态表、可见组件集合和 `body` 状态属性 |
| `interactions` | 触发组件、源状态、目标状态、反馈文本和运行时转移表 |

每个组件都保留稳定 ID。未知 `component_type` 使用带 `data-renderer-kind="fallback"` 的可见通用控件，并显示原类型名称，不会静默丢弃。

## 3. 组件类型映射

| `component_type` | 确定性控件 |
|---|---|
| `primary_action` | 主操作按钮 |
| `media_input` | 本地文件输入和示例操作按钮 |
| `search_input` | 搜索输入框和搜索按钮 |
| `form` | 文本字段和提交按钮 |
| `data_view` | 本地静态指标摘要和刷新按钮 |
| `location_picker` | 离线位置占位图和选择按钮 |
| `status_panel` | `output` 反馈区域 |

这些控件只使用原生 HTML、CSS 和 JavaScript，不加载 CDN、网络字体、前端框架、外部服务或 Node 依赖。

## 4. 状态与交互

`app.js` 把 `PageState` 和 `InteractionSpec` 编码为确定性 JSON 数据。按钮、文件输入和表单通过 `data-interaction-trigger` 或 `data-interaction-form` 关联触发组件。触发后运行时：

1. 根据 component ID 选择 InteractionSpec；优先匹配当前 `source_state_id`。
2. 把同一 section 的 `status_panel` 文本更新为 `user_feedback`。
3. 切换到 `target_state_id`，更新 `body` 和状态栏的 `data-state-id` / `data-state-name`。
4. 根据目标状态的 `visible_component_ids` 更新组件可见性。

`success`、`error`、`empty` 和 `loading` 有独立可识别样式。交互允许源状态和目标状态相同，用于刷新、重试或不改变页面状态的原地反馈。

## 5. 安全与确定性

- HTML 文本和属性统一使用 HTML 转义。
- JavaScript 数据使用 JSON 编码，并额外转义 `<`、`>`、`&`、U+2028 和 U+2029。
- 运行时只使用 `textContent`、DOM 属性和事件监听，不把 PageSpec 内容传给 `innerHTML`。
- 相同 PageSpec 生成逐字节一致的四个文件。
- 产物不包含当前时间、随机数、绝对路径或机器信息。

## 6. 输出文件

| 文件 | 内容 |
|---|---|
| `index.html` | 可直接离线打开的页面结构 |
| `styles.css` | 控件、状态和 mobile / desktop / responsive 布局 |
| `app.js` | PageSpec 状态、交互和反馈逻辑 |
| `render_manifest.json` | schema、page_id、三个页面文件名和 SHA-256 |

`RenderResult` 返回 `page_id`、输出目录以及上述四个真实路径。manifest 不记录自身哈希，避免自引用；其余三个文件的哈希均由最终 UTF-8 字节计算。

## 7. CLI

先确保 `data/processed/rag/` 中已有现有 TF-IDF 索引，然后在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_page_renderer.py `
  "做一个带搜索、购物车和结算的移动电商页面" `
  --target-device mobile `
  --output-dir .\output\ecommerce
```

宠物识别示例：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_page_renderer.py `
  "做一个宠物情绪识别 App，用户拍照后展示识别结果" `
  --target-device mobile `
  --constraint "相机权限被拒绝时给出恢复提示" `
  --output-dir .\output\pet-recognition
```

`--output-dir` 必须显式提供。CLI 输出 `RenderResult` JSON；直接用浏览器打开对应 `index.html` 即可离线使用。

## 8. 当前不包含

当前渲染层不包含自动截图管线或视觉一致性比较，也不包含服务器、数据库、React / Vue / Vite 项目或最终 Demo 控制台。下游 `MinimalConsistencyChecker` 与 `req2web.consistency.report.v1` 见 `docs/consistency_checker.md`；静态页面和结构报告已由 `DeterministicResultPackager` 整合进端到端结果包，见 `docs/result_package.md`。
