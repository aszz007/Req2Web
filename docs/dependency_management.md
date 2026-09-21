# Dependency Management

## Recommended installation

Use Python 3.12 in a virtual environment and install the canonical lightweight
development environment from the repository root:

```bash
python -m pip install -r requirements.txt
```

This entry point is sufficient for the model-free Inspector, deterministic
framework code, data utilities, and the local LangGraph orchestration layer.

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

The tested local versions and the parallel environment rebuild procedure are
recorded in `local_environment_rebuild.md`. Do not add GPU packages to the
lightweight root file, because a platform-neutral `pip install -r
requirements.txt` must not unexpectedly download multi-gigabyte model
runtimes.

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
