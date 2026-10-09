# Synthetic Public Demo Corpus

These five English text records were authored for release preparation on
2026-10-10. Each covers one of the existing retrieval roles: requirement,
UI reference, interaction flow, implementation, or validation.

There are no copied screenshots, dataset excerpts, issue bodies, model
responses, credentials, or third-party records. The UI reference is text only.
The examples use the existing RAG document schema and TF-IDF index builder;
they introduce no new retrieval, generation, or scoring authority.

This tiny corpus is only a portability and startup example. It is **not** the
283-record development corpus, the frozen regression set, E1/E2 data, an
evaluation benchmark, or evidence for any paper result. Do not substitute it
into those historical experiments or pool its generated outputs with them.

Build a separate local index and explicitly select it:

```bash
python scripts/build_public_demo_index.py
python scripts/run_req2web_inspector.py --repository-demo --index-dir data/public_demo_index
```

The builder refuses an existing output directory and refuses the frozen
`data/processed/rag` root. Existing private indexes and experiment evidence
remain unchanged. These authored text fixtures use the project's Apache-2.0
license. Their presence is not approval to publish the full development tree.
