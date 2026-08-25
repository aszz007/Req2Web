"""Portable locations for Req2Web data that must stay outside Git."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path


LOCAL_DATA_ROOT_ENV = "REQ2WEB_LOCAL_DATA_ROOT"
LOCAL_DATA_DIRECTORY_NAME = "Req2Web_LocalData"
INSPECTOR_REPLAY_BUNDLE_NAME = "inspector_replay_bundle_v1"


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


__all__ = [
    "INSPECTOR_REPLAY_BUNDLE_NAME",
    "LOCAL_DATA_DIRECTORY_NAME",
    "LOCAL_DATA_ROOT_ENV",
    "inspector_release_candidate_root",
    "inspector_replay_bundle_root",
    "resolve_local_data_root",
]
