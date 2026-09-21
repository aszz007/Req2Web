# Req2Web

Req2Web turns a vague software requirement into a traceable frontend prototype
and a reviewable ResultPackage. The project combines retrieval, an optional
semantic requirement assistant, the canonical B-to-F1-F4 Agent chain, PageSpec
assembly, deterministic rendering, browser checks, semantic acceptance, and
evidence packaging behind one local Inspector.

> **Release status:** This repository is still private and pre-release. No
> software or data license has been selected, so the tracked content is not yet
> authorized for public redistribution. Internal provenance remains temporarily
> tracked during preparation; see
> [`docs/open_source_readiness.md`](docs/open_source_readiness.md) for the public
> inventory, removal ledger, and release gates.

The workspace directory may remain named `CrowdMEP`; the project and public
tool name is Req2Web.

## Current capabilities

- Accept a free-form requirement, infer basic device and task information, and
  create a model-free deterministic draft.
- Optionally run one advisory-only local Qwen requirement-assistance call. Its
  suggestions never rewrite canonical B or enter F1-F4 automatically.
- Explicitly run the existing canonical local-model flow from canonical B
  through F1-F4, PageSpec, rendering, delivery, browser evidence, and semantic
  evidence. The Inspector does not implement a second prompt or Agent loop.
- Import an existing ResultPackage v1/v2 ZIP with bounded path, size, schema,
  inventory, identity, and hash validation.
- Replay twelve frozen English publication rows without a GPU or hidden model
  call, inspect every intermediate artifact, and open the generated page.
- Compare BM25, RRF, and TF-IDF retrieval using the frozen method-blind pool and
  the owner-reviewed 420 relevance judgments. This remains secondary
  exploratory evidence and does not change the active TF-IDF default.
- Optionally prepare or import a GUISpector sidecar. It is disabled by default
  and cannot change canonical Req2Web acceptance or delivery facts.

The frozen publication evidence keeps separate ledgers: historical downstream
first-pass delivery is 9/12; zero-model policy replay provides 12/12 deliverable
packages without rewriting that history; objective Chrome and PageSpec checks
passed 12/12 with 36 recorded interactions and no console or page errors; the
separate semantic sidecar accepted 12/12 rows and supported 24/24 criteria.

## Phase 7 exploratory tool-effectiveness evidence

The completed Phase 7 experiments add two bounded comparisons without changing
the earlier frozen evidence:

- In three ordered-decision workflows, Req2Web passed 16/18 browser-behavior
  obligations, compared with 8/18 for direct HTML generation and 5/18 for a
  structured one-call baseline. The paired value is `p=0.25`, so this is an
  exploratory workflow-specific advantage rather than a general superiority
  claim.
- In six controlled cross-artifact trace cases, the integrated Req2Web check
  passed 18/18 criteria, compared with 6/18 for integrity/intra-artifact checks
  and the executed EVL-local condition. EVL-cross also passed 18/18 after
  equivalent cross-artifact rules were authored, showing that Req2Web's benefit
  is native integration rather than unique external-rule expressiveness.

Start with the concise status and method documents:

- [`docs/phase7_experiment_execution_status.md`](docs/phase7_experiment_execution_status.md)
- [`docs/phase7_e1_ordered_decision_acceptance_strategy.md`](docs/phase7_e1_ordered_decision_acceptance_strategy.md)
- [`docs/phase7_e2_integrated_trace_acceptance_strategy.md`](docs/phase7_e2_integrated_trace_acceptance_strategy.md)
- [`docs/phase7_demo_video_production_plan.md`](docs/phase7_demo_video_production_plan.md)

The experiment phase is closed. These results do not establish formal H1/gold
quality, general web-generation superiority, a user study, or production
readiness.

## Start the Inspector

For a clean GitHub checkout or a hosted maintenance environment, start the
tracked model-free surface without historical replay:

```bash
python scripts/run_req2web_inspector.py --repository-demo --port 8765
```

This mode accepts requirements, creates deterministic drafts and ResultPackage
ZIPs, imports validated packages, and keeps local run history. Replay-only
sections stay hidden so generated examples cannot be mistaken for the frozen
publication evidence.

The precomputed replay is local evidence and is intentionally not tracked in
Git. By default it is read from the sibling directory
`../Req2Web_LocalData/replay/inspector_replay_bundle_v1`. Set
`REQ2WEB_LOCAL_DATA_ROOT` to use another local-data root.

Validate the replay and start the model-free local Inspector:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_req2web_inspector.py --validate-only
.\.venv\Scripts\python.exe .\scripts\run_req2web_inspector.py --port 8768
```

The supported convenience launcher performs a model-free preflight and uses
the repository virtual environment:

```powershell
.\scripts\start_req2web_inspector.ps1 `
  -ReplayBundleRoot ..\Req2Web_LocalData\replay\inspector_replay_bundle_v1 `
  -Port 8768
```

Local semantic assistance and the complete canonical model flow stay disabled
unless the launcher or CLI receives the exact local model root, integrity
evidence, and explicit enable flags. See
`docs/phase6_release_foundation.md` for the low-GPU NF4 and high-GPU BF16
profiles.

For a clean hosted checkout, use the short setup and validation sequence in
`docs/work_maintenance.md`.

## Repository readiness audit

The release-readiness audit reads the tracked tree, reports private cleanup
candidates, missing public metadata, absolute machine paths, large or binary
artifacts, and suspected credential locations without printing matched secret
values:

```bash
python scripts/audit_open_source_readiness.py
python scripts/audit_open_source_readiness.py --json
```

The current private tree is expected to report `blocked`. The final sanitized
public tree must pass `--fail-on-blockers` before repository visibility changes.

## Repository and local data

Git contains the current source, tests, contracts, lightweight fixtures,
processed RAG corpus, frozen retrieval judgments, and mechanical metrics. Raw
datasets, model weights, ResultPackages, browser runs, release candidates,
Docker state, and other machine-specific outputs remain outside Git under
`Req2Web_LocalData` or their documented owner-controlled locations.

The current local-data roots include:

- `replay/inspector_replay_bundle_v1`: frozen Inspector replay;
- `replay/framework_evidence_v1`: retained deterministic, Phase 4, Phase 5,
  and retrieval evidence used by optional replay tests;
- `runs/`: new draft, semantic-assist, canonical-flow, report, and retrieval
  runs;
- `exports/`: handoff archives;
- `optional_tools/`: pinned optional verifier runtimes.

The repository does not include public-release license closure. RICO and other
assets with unresolved redistribution rights remain reference-only. H1/gold,
formal quality evaluation, training, LoRA, data expansion, production claims,
and user studies are not part of the completed engineering evidence.

## Main documents

- `docs/README.md`: short guide to current, evaluation, maintenance, and
  historical documentation.
- `docs/project_framework.md`: product and technical framework.
- `docs/active_flow_authority_registry.md`: current flow, prompt, and evidence
  authorities.
- `docs/phase6_release_foundation.md`: final Inspector behavior and runtime
  commands.
- `docs/phase7_experiment_execution_status.md`: frozen Phase 7 experiment
  results and limitations.
- `docs/phase7_demo_video_production_plan.md`: the current ICSE demonstration
  video storyboard and production workflow.
- `docs/dataset_selection_workflow.md`: dataset screening and corpus lineage.
- `docs/repository_hygiene_checklist.md`: current local and Git maintenance
  boundary.
- `docs/open_source_readiness.md`: future public inventory, removal ledger,
  history-sanitization rule, and release gates.
- `docs/phase6_license_and_material_decision_checklist.md`: owner decisions
  required before redistribution.
- `CONTRIBUTING.md`: clean-checkout development and review workflow.
- `docs/repository_output_reference_manifest.md`: retained and archived output
  roots.
- `docs/local_environment_rebuild.md`: safe parallel virtual-environment
  rebuild procedure.
- `docs/project_memory.md`: chronological project decisions and evidence.

The private backup remote is `https://github.com/aszz007/Req2Web`.
