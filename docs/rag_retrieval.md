# Req2Web 最小 RAG 检索骨架

## 1. 统一文档格式

### 1.1 现有来源字段差异

构建器没有假设现有 JSONL 已经同构，而是为每类来源设置显式适配器：

| 来源 | 主要身份字段 | 主要可检索字段 | 特有结构 |
|---|---|---|---|
| Vision2Web frontend / website | `sample_id`、`task_name` | `requirement_text`、workflow 摘要与测试用例 | 原型列表、资源数量、需求类型 |
| RICO combined | `screen_id` | 中文图片摘要、Screen Annotation、可见文本 | 控件计数、组件标签、图标语义、截图与层级路径 |
| RICO filtered_traces | `sample_id`、`review_id` | 流程模式、Activity、手势序列 | 多步骤截图、层级、手势坐标 |
| Design2Code | `sample_id`、`review_id` | 标题、中文摘要、HTML 文本预览 | HTML 结构统计、截图尺寸和哈希 |
| Sketch2Code | `sample_id`、`webpage_id`、`sketch_id` | 页面类型与中文摘要 | 草图、目标截图、目标 HTML |
| GitHub Issues / PRs | `sample_id`、Issue/PR 编号 | Issue 正文、中文问题摘要、验收重点 | PR 变更统计与来源限制 |
| Vision2Web webpage | `sample_id`、`task_name` | manifest 的页面概括、设备与响应式描述 | 独立 JSONL 保存三端原型、workflow、资源和设备元数据，不含 PRD |

关键差异包括：部分记录没有 `dataset` / `subset` / `role`，RICO combined 使用 `screen_id` 而非 `sample_id`，可检索正文的字段名各不相同，资产有单图、多图、多步骤或外部链接等形态。统一层只固定外层契约，特有结构继续保存在 `metadata`。

### 1.2 稳定外层契约

`data/processed/rag/documents.jsonl` 每行是一条 `req2web.rag.document.v1` 文档，稳定外层字段如下：

| 字段 | 含义 |
|---|---|
| `doc_id` | 由 role、dataset、subset、sample_id 组成的唯一键 |
| `role` | requirement、ui_reference、interaction_flow、implementation、validation |
| `dataset` / `subset` / `sample_id` | 与 selection_manifest.csv 对齐的来源身份 |
| `title` / `summary` / `content` | 用于展示与检索的统一文本 |
| `tags` | 类别、页面类型、设备或手势等短标签 |
| `references` | 截图、HTML、流程步骤、标注或外部 Issue/PR 链接 |
| `source` | manifest 来源路径与原 RAG 记录文件 |
| `metadata` | 保留各数据集特有的结构化字段 |

统一构建严格以 `selection_manifest.csv` 的 keep 行为准。当前 8 个来源 JSONL 共提供 283 条记录，与 manifest 完全一致。Vision2Web webpage 的 100 条 implementation 由 `scripts/build_vision2web_webpage_rag.py` 从 manifest 和轻量 inventory 确定性生成；这只是补齐派生文件，不是重新筛选数据。正常构建优先使用该 JSONL，旧 inventory fallback 暂时保留用于兼容和故障诊断。

## 2. 当前索引

第一版索引是离线稀疏 TF-IDF 向量索引，使用余弦相似度检索，并对常用中文需求词做少量中英查询扩展。它不需要下载模型，适合先验证统一格式、角色过滤、索引持久化和五类召回接口。

索引文件：

- `documents.jsonl`：统一文档清单。
- `tfidf_index.json.gz`：词项 IDF 与稀疏 postings。
- `index_manifest.json`：文档数、角色数、数据集数和词表大小。

这一后端是 Demo 骨架，不等于最终语义检索质量。后续接入 `bge-m3` 时保留统一文档和检索返回结构，只替换向量生成及存储层。

### 2.1 统一检索接口

`src/req2web_rag/retriever.py` 在具体索引之上定义 `Retriever` Protocol、`RetrieverConfig` 和 `RetrieverRegistry`。默认注册的 `tfidf` 后端通过 `TfidfRetriever` 复用现有 `TfidfIndex`；`scripts/search_rag.py` 和最小 Agent 链都通过该接口调用检索，不直接绑定具体索引类。

注册表只提供未来后端的接入位置。当前未注册语义向量后端，也未安装、下载或调用 `bge-m3`。完整的 Agent 链结构与 CLI 用法见 `docs/agent_chain.md`。

## 3. 运行方式

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\build_vision2web_webpage_rag.py
.\.venv\Scripts\python.exe .\scripts\build_rag_index.py
.\.venv\Scripts\python.exe .\scripts\search_rag.py
```

第一条命令可单独重建 webpage 派生记录；第二条命令也会在建立统一文档和索引前自动执行同一生成逻辑。

第二条命令默认使用一条移动电商模糊需求，并按五种 role 各返回 2 条。也可以指定查询、角色或混合排序：

```powershell
.\.venv\Scripts\python.exe .\scripts\search_rag.py "做一个带地图和地址搜索的移动应用" --role ui_reference --top-k 3
.\.venv\Scripts\python.exe .\scripts\search_rag.py "登录表单与权限异常" --all --top-k 5
```

运行回归测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试会验证 webpage JSONL 恰好 100 条且未伪造 requirement_text、统一文档正常使用该 JSONL、283 条 manifest 全覆盖、文档键唯一、五类数量正确，以及一条模糊需求能够在五个 role 中分别得到正分召回。当前 webpage JSONL、统一文档、压缩索引和构建清单已验证连续两次构建 SHA-256 一致。

最小 Agent 链入口：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_agent_chain.py "一句模糊软件需求"
```
