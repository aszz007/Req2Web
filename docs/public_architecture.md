# Public Architecture

The canonical model route is:

```text
raw requirement -> deterministic requirement/use-case processing -> Retriever
-> AgentContextBundle/Guidance -> approved provider projection
-> F1 structure -> F2 states/visibility -> F3 interactions -> mappings
-> F4 acceptance semantics -> composition -> canonical PageSpec assembler
-> Renderer/Consistency -> acceptance -> bounded repair or same-case G0
-> ResultPackage and separate audit/evaluation sidecars
```

Owning modules include `req2web_agent`, `req2web_rag`,
`req2web_orchestration`, `req2web_generation`, `req2web_acceptance`,
`req2web_inspector`, and `req2web_runtime`. LangGraph schedules the shared
model route; Req2Web owns domain contracts and downstream decisions.

The five-role retrieval structure is requirement, UI reference, interaction
flow, implementation, and validation. The public examples are authored text,
not reference images or the private historical 283-record corpus.

The supported portable smoke route is the model-free Inspector with an
explicit authored index. It follows the deterministic baseline/draft path and
must not be described as a successful F1-F4 model run. The synthetic index
builder never replaces a historical dataset and refuses existing targets.

Model-success ResultPackage v1, deterministic guided/G0 ResultPackage v2,
repair, fallback, raw failures, browser checks and semantic judgements retain
their separate source/status accounting. A replay or repaired delivery cannot
retroactively turn an original model failure into raw-model success.

The optional GUI verifier is a separately configured, default-off downstream
component. Enabling a panel is not a model invocation; operator confirmation
and ephemeral credentials are required for external provider actions. It
cannot rewrite Req2Web's historical generation or acceptance evidence.

Historical source files and technical contracts remain available for reading.
Older runners are not alternate current full-flow authorities. Source presence
does not establish portability of every optional route, full historical
reproduction, formal H1 quality, generalization, or production readiness.
