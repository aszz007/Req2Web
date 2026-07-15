# Demo v2 检索质量缺口诊断

诊断日期：2026-07-15
冻结输入：`fixtures/demo_v2_regression_cases_v1.json`、`outputs/demo_v2_regression_v1/`、既有 283 文档 TF-IDF 索引
范围：只诊断 15 个低影响单元（11 个 `validation`、4 个 `ui_reference`）；不读取 `data/raw`，不改生产链、fixture、门禁或 12 个结果包。

## 结论

`validation` 与 `ui_reference` 不是同一种主问题。前者的 11/11 都已经检索到 validation 文档，却只生成“证据引用 / regression_case / 当前不支持的异常值”，没有可在现有安全规则下变成结构指令的项；其中 3/11 还召回了恢复提示，但因它与明确需求的恢复类型不一致而被正确拒绝。后者的 3/4 是 UI 文档只被提取为 `reference_screen`，另 1/4 是提取到了控件值但与宠物识别需求不相符而被正确拒绝。

因此，不能把这 15 个零差异单元称为“RAG 未完成”，更不能以硬编码或放宽门禁抬高数字。当前双门禁没有误报：15 个单元均为 `adopted=0`、角色消融 `difference_count=0`，且没有 `verification_failed`。它的已知边界是只观察 PageSpec 结构，不能衡量“仅作引用的证据价值”或视觉相似度；但本批不存在“已采纳、却被指标漏检”的证据。

## 可复现方法

命令只读取冻结包，并通过既有 `create_retriever(... backend="tfidf")` 做两种反事实搜索：同一原查询的 `top_k=5`，以及“原需求 + constraint + 固定角色检索重点”的轻量改写 `top_k=5`。反事实结果不写回任何包，也不进入生产默认值。

```powershell
.\.venv\Scripts\python.exe .\scripts\diagnose_retrieval_quality.py `
  --output-dir .\outputs\retrieval_quality_diagnosis_v1
```

机器报告为 `outputs/retrieval_quality_diagnosis_v1/diagnosis.json` 与 `target_units.csv`。前者逐单元保留**完整原查询**、原 top-2 的 `doc_id/score/dataset/subset/role/title/summary/relevance_judgment`、每条 Guidance 的 disposition/rule/reason、消融字段差异及两组反事实候选；CSV 是审计索引。脚本不复制 Retriever 或上游链路实现。

## 根因分布

| 主根因 | 单元 | 比例 | 结论 |
| --- | ---: | ---: | --- |
| 检索相关，但指导提取无法形成可用指令 | 11 | 73.3% | 8 个 validation 受 `evidence_only` / `supported_gate` 限制；3 个 UI 只提取到 `reference_screen`。 |
| 证据/安全/显式需求优先门禁拒绝采纳 | 4 | 26.7% | ecommerce、mobile-auth、pet-recognition 的 validation 恢复类型不匹配；pet-recognition UI 控件不匹配。 |
| 语料覆盖不足 | 0 | 0% | 本批无“无文档或无 Guidance”证据；不能据零影响反推覆盖不足。 |
| TF-IDF 查询词汇错配或排序问题 | 0（主因） | 0% | 轻量改写都会改变前二候选，说明排序敏感；但没有证据显示替换候选可越过现有指导/门禁。 |
| builder fallback / 显式需求掩盖 | 0 | 0% | 没有 adopted 决策，故不存在已采纳影响被显式需求掩盖的证据。 |
| PageSpec / 消融指标盲区 | 0 | 0% | 没有 adopted 且零差异的矛盾组合。 |

“排序敏感”是反事实观察，不是根因归类：两种反事实均未在本诊断中经过 Guidance、Builder 或门禁，所以不能据此声称会改善结果。

## 逐例证据

下表的“原查询”列指向机器报告中的原样 `original_query`；它来自各包的 `internal/agent_context.json`，没有重新生成。括号为 TF-IDF score；`F/I` 分别表示 fallback / ignored。所有来源角色均与本行角色相同。

### UI reference（4 个）

| 案例 | 原查询 | 原 top-2：doc_id（分数，来源/相关性） | Guidance 与处置 | 消融观察 | 主因与置信度 |
| --- | --- | --- | --- | --- | --- |
| mobile-appointment | `units[0].original_query` | `ui_reference:rico:combined:38216`（0.102323，RICO combined，表单与输入）; `...:44688`（0.096874，弹窗与浮层） | 10；两文档均为 `reference_screen` + URI，10 F（`controlled_value_required`/`reference_only`） | 0 字段差异 | 指导提取不可用，高 |
| mobile-auth | `units[1].original_query` | `...:18889`（0.158595，登录与认证）; `...:16725`（0.131704，表单与输入） | 10；两文档均为 `reference_screen` + URI，10 F | 0 | 指导提取不可用，高 |
| pet-recognition | `units[2].original_query` | `...:7005`（0.100050，空/异常状态）; `...:10234`（0.097373，搜索/筛选/结果） | 12；8 F（URI），`empty_state/search_input/filter_control/result_list` 4 I（`semantic_gate`） | 0 | 显式语义门禁，高 |
| profile-settings | `units[3].original_query` | `...:68476`（0.140644，社交与消息）; `...:44688`（0.097616，弹窗与浮层） | 10；`reference_screen` + URI，10 F | 0 | 指导提取不可用，高 |

UI 的 top-5 与改写候选均在机器报告中。四例改写均改变原前二，故存在词汇/排序敏感性；但前三例的决定性证据分别是“无受控值”和“语义门禁”，不能把排序敏感误写成主要缺陷。

### Validation（11 个）

| 案例 | 原查询 | 原 top-2：doc_id（分数，来源/相关性） | Guidance 与处置 | 消融观察 | 主因与置信度 |
| --- | --- | --- | --- | --- | --- |
| desktop-dashboard | `units[4].original_query` | `...12820/pr-12887`（0.167531，GHPR，持续加载）; `...12154/pr-12437`（0.133520，GHPR，浏览器导航） | 8；6 F `evidence_only`，`detail_view/loading_completion` 2 I `supported_gate` | 0 | 指导提取不可用，高 |
| desktop-media-library | `units[5].original_query` | `...12820/pr-12887`（0.154687，GHPR，持续加载）; `...12154/pr-12437`（0.153992，GHPR，浏览器导航） | 同上，8 / 6 F / 2 I | 0 | 指导提取不可用，高 |
| desktop-project-admin | `units[6].original_query` | `...12820/pr-12887`（0.160287，GHPR，持续加载）; `...12154/pr-12437`（0.149972，GHPR，浏览器导航） | 同上，8 / 6 F / 2 I | 0 | 指导提取不可用，高 |
| desktop-search-catalog | `units[7].original_query` | `...12820/pr-12887`（0.157393，GHPR，持续加载）; `...12154/pr-12437`（0.152216，GHPR，浏览器导航） | 同上，8 / 6 F / 2 I | 0 | 指导提取不可用，高 |
| ecommerce | `units[8].original_query` | `...12820/pr-12887`（0.143203，GHPR，持续加载）; `...9749/pr-9834`（0.127528，GHPR，权限角色） | 9；6 F，`detail_view/loading_completion/permission_recovery` 3 I；权限恢复与“输入错误重试”不一致 | 0 | 显式语义门禁，高 |
| map-address-search | `units[9].original_query` | `...12820/pr-12887`（0.140879，GHPR，持续加载）; `...5859/pr-5865`（0.134468，GHPR，服务不可用） | 8；6 F，2 I `supported_gate` | 0 | 指导提取不可用，高 |
| mobile-auth | `units[10].original_query` | `...9749/pr-9834`（0.169826，GHPR，权限角色）; `...12820/pr-12887`（0.162130，GHPR，持续加载） | 9；6 F，权限恢复及两项不支持值 3 I；需求是“登录失败后重试” | 0 | 显式语义门禁，高 |
| mobile-messages | `units[11].original_query` | `...12820/pr-12887`（0.154636，GHPR，持续加载）; `...12154/pr-12437`（0.148830，GHPR，浏览器导航） | 8；6 F / 2 I | 0 | 指导提取不可用，高 |
| pet-recognition | `units[12].original_query` | `...64301/pr-64305`（0.152305，GHPR，校验错误）; `...12820/pr-12887`（0.142116，GHPR，持续加载） | 9；6 F，`retry_recovery` 等 3 I；需求是“权限拒绝恢复” | 0 | 显式语义门禁，高 |
| profile-settings | `units[13].original_query` | `...12820/pr-12887`（0.163264，GHPR，持续加载）; `...12154/pr-12437`（0.160369，GHPR，浏览器导航） | 8；6 F / 2 I | 0 | 指导提取不可用，高 |
| task-empty-recovery | `units[14].original_query` | `...12820/pr-12887`（0.162529，GHPR，持续加载）; `...12154/pr-12437`（0.142796，GHPR，浏览器导航） | 8；6 F / 2 I | 0 | 指导提取不可用，高 |

11 个 validation 单元的同一模式很强：原前二和扩大 top-5 都大多是跨业务 GHPR 问题；轻量改写会改变排序，却没有证明候选能形成与当前页面需求一致、且在支持集合内的指令。故当前证据优先支持“转换契约缺口”，不支持立即更换检索器、放大 top-k 或扩充语料。

## 双门禁判断

- 没有误报：`RetrievalInfluenceChecker` 没有把 fallback/ignored 说成结构影响；15 行均报告 `guidance_not_applicable_or_ignored`，不是失败。
- 没有本批可证实的漏报：若出现 adopted 且角色消融为零，报告应为 `verification_failed`；本批 adopted 都是 0。
- 有意的测量边界：PageSpec 消融不会衡量“外部 Issue/PR URL 作为审计引用”或截图的视觉质量。这是当前 Demo 边界，不应把这种非结构价值计入“检索影响”。

## 唯一推荐的下一实现任务

**实现“既有 validation 记录的可采纳信号适配器”，并保持现有显式语义门禁。**

任务只处理当前 12 条 GHPR 派生记录和它们已存在的摘要/结构化字段：为紧凑检索结果公开稳定、来源可追溯的 validation signal，再由 `RetrievalGuidanceBuilder` 仅把该信号映射到已有的受支持集合。不得增加业务规则、不得按 case_id 分支、不得放宽 `explicit_context_gate`，不得改 PageSpec schema、Renderer、TF-IDF 默认查询或数据集。

预期收益是把“只得到 regression_case/URI 或未支持值”的 8 个 validation 单元变成可审计的候选转换，并把 3 个被门禁拒绝的单元保留为拒绝；**不以提升影响数量为验收目标**。风险是从 Issue 摘要过度推断 UI 行为；因此信号必须精确反查 `doc_id` 和来源字段，且缺证据时继续 fallback。完成标准：新增适配器的单元测试、12 案例完整回归、双门禁继续全绿、机器报告比较前后原因分布，并证明没有 case-specific 常量或放宽门禁。

备选但不推荐现在实施：调大 top-k/改写查询（有排序敏感性但无可采纳收益证据）、扩充 validation 语料（尚未证明覆盖不足）、为 `reference_screen` 增加 UI 提取规则（只影响 3 个 UI 单元，收益较窄）。

## 后续实现状态（2026-07-15）

本诊断推荐的 validation signal adapter 已在不读取 raw 数据的前提下实现。实际 field boundary、稳定 signal schema、映射及兼容说明见 `docs/validation_signal_adapter.md`。它只将已加载 validation 文档的 `metadata.category` 与已存在 Issue/PR 白名单引用映射成受控候选；`input_error -> retry_recovery`、`auth_access -> permission_recovery`，其余类别仍是 audit-only/evidence-only。所有 adopted 判断继续由既有 `explicit_context_gate` / semantic gate 处理，不能因适配器而推断新的页面行为。
