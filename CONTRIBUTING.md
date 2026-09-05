# Contributing to Req2Web

Req2Web is currently a private, pre-release research tool. No public license has
been selected, so the tracked content is not yet authorized for redistribution.
This guide defines the intended engineering workflow while the future public
release is prepared.

## Development setup

Use Python 3.12 from a clean checkout:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-data.txt
python -m pip install -r requirements-phase4-agent-lock.txt
python scripts/run_req2web_inspector.py --repository-demo --port 8765
```

Repository demo mode is the portable, model-free development surface. It must
work without model weights, API credentials, historical run bundles, or a
sibling local-data directory.

## Change scope

Keep each change narrow and explain the user-visible or maintenance problem it
solves. Preserve the single canonical requirement-to-result flow and reuse the
existing PageSpec, Renderer, acceptance, and ResultPackage contracts. Do not
create a parallel implementation of an existing stage to simplify a patch.

Changes to schemas, frozen evidence, metrics, model prompts, generated cases, or
scientific claims require explicit project-owner review. Keep optional model and
external-verifier integrations disabled unless the caller supplies their local
runtime and deliberately enables them.

## Tests

Run checks that cover the changed behavior. The usual model-free checks are:

```bash
python -m unittest discover -s tests -p 'test_req2web_inspector*.py'
python -m unittest discover -s tests -p test_phase4_graph.py
python scripts/run_demo_v2_regression.py --output-root outputs/work-demo
python scripts/run_demo_v2_regression.py --output-root outputs/work-demo --validate-only
```

Do not report deterministic, mock, component, or replay success as a live model
run or as formal quality evidence.

## Data and generated artifacts

Do not commit credentials, model files, raw datasets, runtime caches, generated
ResultPackages, local browser runs, or machine-specific output. Small fixtures
must have a clear test reader, provenance, and redistribution decision. Treat
RICO and other unresolved third-party material as reference-only.

The future public inventory and all material requiring removal or review are
tracked in `docs/open_source_readiness.md`.

## Release hygiene

Run the tracked-tree readiness audit when a change adds documentation, data,
fixtures, generated output, deployment code, or a new dependency:

```bash
python scripts/audit_open_source_readiness.py
```

The private repository is expected to remain blocked while its cleanup ledger
is unresolved. The audit must not be used to delete or rewrite evidence. Update
`docs/open_source_readiness.md` when a new artifact changes the future public
inventory.

## Documentation and commits

Keep maintained repository documentation and commit messages in English. Use a
Conventional Commit subject such as `docs: clarify clean-checkout setup` or
`fix: reject malformed result package paths`.

A reviewable change should state:

- why the change is needed;
- what behavior or documentation changed;
- which focused checks ran;
- which relevant behavior remains unverified.

Before submitting a change, inspect the diff for private paths, credentials,
unrelated generated files, and accidental changes to frozen evidence.
