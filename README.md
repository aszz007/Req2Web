# Req2Web

Req2Web is a research prototype that turns requirements into traceable web
prototypes. It connects requirement processing, retrieval, structured page
contracts, rendering, consistency checks, acceptance evidence, and delivery.
The Inspector lets users inspect these artifacts and their relationships.

This distribution retains the development commit history with private and
unapproved material excluded. It includes current and historical project
source, experiment-method definitions, and selected technical documentation.
Private annotation, owner-evaluation, and training-data preparation utilities
are kept outside this distribution.
It is not a complete dataset, model-weight, or historical-result release.

## Quick start: model-free demo

Use Python 3.12 in a fresh virtual environment:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/build_public_demo_index.py
python scripts/run_req2web_inspector.py --repository-demo --index-dir data/public_demo_index --run-root .local-runs
```

Open `http://127.0.0.1:8765/`. Enter an English requirement, create a
deterministic draft, inspect its trace, open the preview, and download its ZIP.
Example: "Build a responsive equipment service page where users submit a
problem and confirm its status."

The five newly authored text records are startup examples, not the historical
283-record retrieval corpus or a benchmark. The builder refuses to overwrite
existing data. This explicit demo route runs no model and does not execute
F1-F4, paid APIs, or browser acceptance. Do not substitute it for historical
experiment data or report its outputs as raw-model success.

## Architecture and optional runtimes

The canonical model flow starts with the raw requirement, builds a shared
requirement/context representation, and calls the shared F1-F4 orchestration.
Req2Web owns the contracts, stable IDs, composition, PageSpec assembly,
Renderer, checks, repair limit, fallback, and ResultPackage semantics.
LangGraph is the orchestration substrate, not a second domain authority.

The deterministic draft/G0 route is distinct from model generation. Optional
GPU/model, semantic, browser, and external-GUI verification routes need their
own configured runtimes and explicit operator actions. The lightweight
requirements do not install model weights or GPU packages. The public smoke
check verifies the model-free route, not every historical/optional entry point.
See [architecture](docs/public_architecture.md) and
[dependencies](docs/dependency_management.md).

## Experiments

The frozen E1 ordered-decision secondary analysis reports Req2Web 16/18,
direct HTML 8/18, and structured one-call 5/18 across three cases. This is an
exploratory post-hoc result; the paired exact p-value is 0.25, not confirmation
of a general advantage.

The frozen E2 trace-relation analysis reports Req2Web C2 18/18,
C0/C1/EVL-local 6/18, and EVL-cross 18/18. It supports native integrated
trace checking, not unique rule expressiveness: externally authored equivalent
cross-artifact rules restore parity. All six workflows are retained.

Scoring code, authored cases, and method definitions are included. Historical
observation/result payloads are excluded, so the startup examples do not
reproduce those numbers. See [methods and limitations](docs/experiments.md).
Experiments are closed; this release does not rerun or relabel them.

## Focused validation

```bash
python -m unittest discover -s tests -p test_public_demo_index.py
python -m unittest discover -s tests -p test_req2web_inspector_preview_security.py
python -m unittest discover -s tests -p test_repository_secret_hygiene.py
python -m unittest discover -s tests -p test_open_source_readiness_audit.py
python -m unittest discover -s tests -p test_dependency_hygiene.py
python scripts/run_req2web_inspector.py --repository-demo --index-dir data/public_demo_index --preflight-only
python scripts/audit_repository_secrets.py --source history
```

Legacy integration tests and historical runners may refer to deliberately
excluded private fixtures, corpus data, policies, or result packages. Their
presence is source provenance, not a claim that full-suite or old-run
reproduction works from this distribution. Do not enable a legacy run to
compensate for missing artifacts or silently manufacture passing evidence.

## Security, license, and citation

Run the Inspector on loopback only. Package hashes establish integrity, not
trust in executable content. Artifact previews use an opaque-origin sandbox
that blocks Inspector API/storage access and network-backed behavior while
retaining local scripts and client-side form interaction. Downloads opened
elsewhere do not inherit these HTTP restrictions. See [SECURITY.md](SECURITY.md).

Project-authored software and the five synthetic examples use
[Apache-2.0](LICENSE). [LICENSING.md](LICENSING.md) records excluded material;
third-party datasets are not relicensed. Please cite the accompanying paper
when available, or this software using [CITATION.cff](CITATION.cff).

Existing clones from before the history cleanup must not merge or push their
old ancestry into this repository. Use a fresh clone for maintenance. See
[contributing](CONTRIBUTING.md).
