"""Portable locations for Req2Web data that must stay outside Git."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path


LOCAL_DATA_ROOT_ENV = "REQ2WEB_LOCAL_DATA_ROOT"
LOCAL_DATA_DIRECTORY_NAME = "Req2Web_LocalData"
INSPECTOR_REPLAY_BUNDLE_NAME = "inspector_replay_bundle_v1"
INSPECTOR_DRAFT_RUNS_NAME = "deterministic_draft_runs_v1"
SEMANTIC_ASSIST_RUNS_NAME = "semantic_assist_runs_v1"
CANONICAL_FLOW_RUNS_NAME = "canonical_flow_runs_v1"
GUISPECTOR_RUNTIME_NAME = "guispector_runtime_v1"
COMPONENT_EXAMPLES_NAME = "component_examples_v1"
COMPONENT_REPORT_RUNS_NAME = "component_report_runs_v1"
FRAMEWORK_EVIDENCE_NAME = "framework_evidence_v1"
RETRIEVAL_WORK_RUNS_NAME = "retrieval_work_runs_v1"


def resolve_local_data_root(
    repository_root: Path,
    explicit_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the untracked local-data root with an explicit override first."""

    values = os.environ if environment is None else environment
    configured = values.get(LOCAL_DATA_ROOT_ENV, "").strip()
    candidate = (
        explicit_root
        if explicit_root is not None
        else Path(configured)
        if configured
        else repository_root.resolve().parent / LOCAL_DATA_DIRECTORY_NAME
    )
    return candidate.expanduser().resolve(strict=False)


def inspector_replay_bundle_root(
    repository_root: Path,
    local_data_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the stable default location of the Inspector replay bundle."""

    root = resolve_local_data_root(
        repository_root,
        explicit_root=local_data_root,
        environment=environment,
    )
    return root / "replay" / INSPECTOR_REPLAY_BUNDLE_NAME


def inspector_release_candidate_root(
    repository_root: Path,
    local_data_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the default location for generated reviewer candidates."""

    root = resolve_local_data_root(
        repository_root,
        explicit_root=local_data_root,
        environment=environment,
    )
    return root / "release_candidates" / "inspector_release_candidate_v1"


def inspector_draft_runs_root(
    repository_root: Path,
    local_data_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the local history root for deterministic Inspector drafts."""

    root = resolve_local_data_root(
        repository_root,
        explicit_root=local_data_root,
        environment=environment,
    )
    return root / "runs" / INSPECTOR_DRAFT_RUNS_NAME


def semantic_assist_runs_root(
    repository_root: Path,
    local_data_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the local history root for advisory requirement assistance."""

    root = resolve_local_data_root(
        repository_root,
        explicit_root=local_data_root,
        environment=environment,
    )
    return root / "runs" / SEMANTIC_ASSIST_RUNS_NAME


def canonical_flow_runs_root(
    repository_root: Path,
    local_data_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the local history root for complete canonical flow runs."""

    root = resolve_local_data_root(
        repository_root,
        explicit_root=local_data_root,
        environment=environment,
    )
    return root / "runs" / CANONICAL_FLOW_RUNS_NAME


def guispector_runtime_root(
    repository_root: Path,
    local_data_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the pinned optional GUISpector checkout location."""

    root = resolve_local_data_root(
        repository_root,
        explicit_root=local_data_root,
        environment=environment,
    )
    return root / "optional_tools" / GUISPECTOR_RUNTIME_NAME / "upstream"


def component_examples_root(
    repository_root: Path,
    local_data_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the stable replay root for generated component examples."""

    root = resolve_local_data_root(
        repository_root,
        explicit_root=local_data_root,
        environment=environment,
    )
    return root / "replay" / COMPONENT_EXAMPLES_NAME


def component_report_runs_root(
    repository_root: Path,
    local_data_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the stable root for newly generated component reports."""

    root = resolve_local_data_root(
        repository_root,
        explicit_root=local_data_root,
        environment=environment,
    )
    return root / "runs" / COMPONENT_REPORT_RUNS_NAME


def framework_evidence_root(
    repository_root: Path,
    local_data_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the stable replay root for retained framework evidence."""

    root = resolve_local_data_root(
        repository_root,
        explicit_root=local_data_root,
        environment=environment,
    )
    return root / "replay" / FRAMEWORK_EVIDENCE_NAME


def retrieval_work_runs_root(
    repository_root: Path,
    local_data_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the writable root for fresh retrieval comparison runs."""

    root = resolve_local_data_root(
        repository_root,
        explicit_root=local_data_root,
        environment=environment,
    )
    return root / "runs" / RETRIEVAL_WORK_RUNS_NAME


__all__ = [
    "CANONICAL_FLOW_RUNS_NAME",
    "COMPONENT_EXAMPLES_NAME",
    "COMPONENT_REPORT_RUNS_NAME",
    "FRAMEWORK_EVIDENCE_NAME",
    "GUISPECTOR_RUNTIME_NAME",
    "INSPECTOR_DRAFT_RUNS_NAME",
    "INSPECTOR_REPLAY_BUNDLE_NAME",
    "LOCAL_DATA_DIRECTORY_NAME",
    "LOCAL_DATA_ROOT_ENV",
    "RETRIEVAL_WORK_RUNS_NAME",
    "SEMANTIC_ASSIST_RUNS_NAME",
    "canonical_flow_runs_root",
    "component_examples_root",
    "component_report_runs_root",
    "framework_evidence_root",
    "guispector_runtime_root",
    "inspector_draft_runs_root",
    "inspector_release_candidate_root",
    "inspector_replay_bundle_root",
    "resolve_local_data_root",
    "retrieval_work_runs_root",
    "semantic_assist_runs_root",
]
