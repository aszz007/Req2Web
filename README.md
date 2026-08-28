# Req2Web

Req2Web is an Agent + RAG prototype that turns a vague software requirement into a frontend prototype or webpage implementation, together with a concise explanation, UI references, and an interaction flow.

Private project backup: `https://github.com/aszz007/Req2Web`.

## Current Stage

The first traceable Demo corpus and the unified RAG indexing and retrieval skeleton are ready; the next step is the minimal Agent chain.

- Vision2Web: 100 responsive webpage implementations and 93 requirement tasks retained.
- RICO combined: 42 UI reference screens retained.
- RICO filtered traces: 12 interaction flows retained.
- Design2Code: 12 standard and 4 HARD implementation references retained.
- Sketch2Code: 8 sketch-to-webpage pairs retained.
- GitHub Issues / PRs: 12 validation cases retained from a 14,384-row inventory.
- Topcoder: not downloaded yet and does not block the first Demo.

The manifest currently contains 283 unique records across requirement, UI reference, interaction flow, implementation, and validation roles.

The authoritative screening decisions are stored in `data/processed/selection_manifest.csv`. Dataset audit and finalization scripts are in `scripts/`.

## Minimal RAG Retrieval

Build the unified 283-document corpus and the offline TF-IDF index, then run the five-role retrieval smoke test:

```powershell
.\.venv\Scripts\python.exe .\scripts\build_rag_index.py
.\.venv\Scripts\python.exe .\scripts\search_rag.py
```

The unified schema, index files, query options, and test command are documented in `docs/rag_retrieval.md`. The TF-IDF backend is intentionally dependency-free and can later be replaced by a multilingual embedding backend such as bge-m3 without changing the document envelope.

## Repository Scope

Raw public datasets and downloaded archives are intentionally excluded from Git. The repository stores project documentation, processing scripts, lightweight inventories, screening decisions, and RAG-ready metadata. Raw files must be downloaded separately according to the dataset documentation in `docs/`.

## Project Documents

- `docs/project_framework.md`: overall product and technical framework.
- `docs/demo_spec.md`: first Demo scope and acceptance criteria.
- `docs/dataset_selection_workflow.md`: authoritative dataset-screening procedure.
- `docs/project_memory.md`: current project state and decisions.
- `docs/repository_hygiene_checklist.md`: evidence-based local workspace and
  repository maintenance checklist.

