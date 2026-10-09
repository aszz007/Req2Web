# Dependency Management

## Recommended installation

Use Python 3.12 in a virtual environment and install the canonical lightweight
development environment from the repository root:

```bash
python -m pip install -r requirements.txt
```

This entry point is sufficient for the model-free Inspector, deterministic
framework code, data utilities, and the local LangGraph orchestration layer.
Do not install the four requirements files sequentially: the root entry point
already includes both installable layers.

For data-screening utilities alone, install `requirements-data.txt`. For an
Agent-only environment, install `requirements-phase4-agent-lock.txt`. Neither
specialized choice replaces the full lightweight Inspector setup. The direct
`requirements-phase4-agent.txt` declaration is not an additional installation
step; installing it alone lets pip resolve a different transitive closure.

## File roles

| File | Role | Maintenance rule |
| --- | --- | --- |
| `requirements.txt` | Canonical one-command lightweight development install | Keep as includes only; do not duplicate package pins here. |
| `requirements-data.txt` | Exact tested data-processing stack | Keep exact pins until a separate dependency-update task validates the data workflows. |
| `requirements-phase4-agent.txt` | Direct LangGraph declaration bound to the accepted Phase 4 acquisition receipt | Do not rename, move, reformat, comment, or otherwise change its bytes without updating and revalidating the owning runtime contract. |
| `requirements-phase4-agent-lock.txt` | Exact tested transitive closure for the LangGraph layer | Use for clean installs and remote experiment environments; update only with a reviewed lock refresh. |

The direct declaration and lock file intentionally coexist. The first records
the single dependency requested by the architecture. The second records the
complete resolved environment used by runtime integrity checks and historical
experiments.

## Why the four files are retained

The hygiene review on 2026-10-10 found no unused requirements file. The root
file contains includes only, and the two included layers have no duplicate
distribution pins. The apparent repetition of `langgraph` in the direct
declaration and lock is intentional: the declaration's path and canonical
bytes are checked against the acquisition receipt, while the lock must match
that receipt's complete resolved closure. Bundle builders and startup tests
also consume these paths. Deleting or relocating them would change setup or
reproduction behavior, rather than merely removing clutter.

Keep their paths and bytes unchanged during ordinary hygiene. Package updates
remain a separate reviewed change. The model-free dependency hygiene tests
check the include-only entry point, unique pins, declaration identity, and
local-artifact exclusions; the Inspector startup tests check the lock against
the receipt. These static checks do not prove a fresh installation succeeds.

Parallel environments such as `.venv-rebuild`, root build/coverage output, and
Python package metadata are ignored. Ignore rules prevent accidental staging;
they do not delete existing environments, evidence, or security backups.

## Optional platform-specific layers

The canonical file deliberately excludes large or task-specific dependencies:

- PyTorch, torchvision, Transformers, Accelerate, bitsandbytes, Safetensors,
  and Hugging Face Hub depend on the selected CPU/GPU platform and approved
  model profile.
- Playwright is needed only for browser-observation development and uses the
  installed Chrome channel in the current Windows profile.
- Matplotlib and python-docx are needed only to rebuild the owner-facing DOCX
  experiment report.
- Eclipse Epsilon and its Java dependencies are isolated experiment tooling,
  not Python runtime dependencies.

Historical machine-specific environment records are not distributed. The
[repository README](../README.md) describes the supported lightweight demo;
optional model and evaluation runtimes require a separately configured
environment. Do not add GPU packages to the lightweight root file, because a
platform-neutral `pip install -r requirements.txt` must not unexpectedly
download multi-gigabyte model runtimes.

## Update procedure

1. Change the smallest owning requirement layer.
2. Resolve and review the full closure in a fresh parallel environment.
3. Run `python -m pip check`.
4. Run the focused model-free tests for the affected code.
5. If the Phase 4 direct declaration or lock changes, update the owning
   acquisition/runtime evidence and its tests in the same reviewed change.
6. Keep the existing environment until the parallel environment passes.

Dependency updates are maintenance changes, not evidence that model quality or
tool effectiveness improved.
