# Req2Web 最小 Agent 链

## 1. 当前范围

当前链路只生成后续页面生成节点所需的结构化检索上下文：

```text
模糊需求
-> 确定性需求理解
-> 2-4 个核心用例
-> 五类分角色检索查询
-> 统一 Retriever
-> req2web.agent.context.v1 context bundle
```

本阶段不生成最终 HTML、前端页面、截图或一致性报告，也不依赖外部 LLM 服务。

该 context bundle 的下一节点是 `PageSpecBuilder`。PageSpec 契约、字段映射和 CLI 见 `docs/page_spec.md`；后续页面渲染器应消费 PageSpec，而不是直接消费原始需求。

## 2. 稳定输出结构

`AgentContextBundle` 位于 `src/req2web_agent/schema.py`，JSON schema 标识为 `req2web.agent.context.v1`。稳定字段包括：

| 字段 | 含义 |
|---|---|
| `original_requirement` | 用户原始模糊需求 |
| `requirement_summary` | 可供后续节点使用的需求摘要 |
| `target_device` | 确定性规则推断或 CLI 显式指定的目标设备 |
| `task_type` | 任务类型 |
| `constraints` | 去重后的显式与规则识别约束 |
| `use_cases` | 2-4 个核心用例，每项包含 ID、标题、角色、目标和预期结果 |
| `retrieval_queries` | requirement、ui_reference、interaction_flow、implementation、validation 五类查询 |
| `retrieval_results` | 与五类查询一一对应的统一检索结果 |

`bundle.to_dict()` 会先校验字段和角色顺序，再返回可由 `json.dumps` 直接序列化的字典。

## 3. 需求理解 Provider

`RequirementUnderstandingProvider` 是可替换接口。默认实现 `DeterministicRequirementProvider` 使用本地规则推断设备、任务类型、约束和核心用例，因此离线即可运行，也不会把外部 LLM 服务变成前置条件。

后续若接入其他 Provider，只需保持 `understand(...) -> RequirementUnderstanding` 契约，不需要修改 Agent 编排和 Retriever。

## 4. 统一 Retriever

`src/req2web_rag/retriever.py` 定义了统一接口：

```python
search(query, top_k, roles)
search_by_role(query, top_k)
```

当前注册表只有 `tfidf` 后端。`TfidfRetriever` 只是现有 `TfidfIndex` 的适配器，索引构建、分词、查询扩展、余弦评分和持久化格式均继续复用原实现。

`RetrieverRegistry` 和 `RetrieverConfig.options` 为未来后端预留注册与配置入口。例如未来经过单独选型后，可以注册一个语义后端并传入 `options={"embedding_backend": "bge_m3"}`。当前没有注册该后端，不会安装、下载或调用 `bge-m3`，也不把它设为默认模型。

## 5. CLI 使用

先确认现有索引位于 `data/processed/rag/`。如果统一语料发生变化，按 RAG 文档重新构建索引；运行 Agent 链本身不会重建索引。

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_agent_chain.py "我想做一个宠物情绪识别 App，用户拍照后系统识别宠物情绪并展示结果。"
```

默认每个角色返回 2 条结果。可显式设置结果数、设备、任务类型和附加约束：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_agent_chain.py `
  "做一个带地图和地址搜索的应用" `
  --top-k 3 `
  --target-device mobile `
  --constraint "权限被拒绝时给出可恢复提示"
```

CLI 将完整 context bundle 输出到标准输出。五类任一没有召回时退出码为 2；完整召回时退出码为 0。

## 6. 测试

运行全部回归测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Agent 链测试覆盖：

- 生成 2-4 个核心用例；
- 五类查询完整且顺序稳定；
- 五类检索都有结果且角色正确；
- context bundle 可 JSON 序列化；
- TF-IDF 可通过统一 `Retriever` 接口和默认注册表调用；
- 相同输入的本地需求理解结果确定一致；
- 注册表支持后端扩展，但默认只有 `tfidf`，`bge_m3` 未注册、未启用。
