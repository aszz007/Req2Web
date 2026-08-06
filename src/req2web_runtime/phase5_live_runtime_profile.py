"""Create the action-time Phase 5 runtime profile without loading a model."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Mapping

from req2web_runtime import phase4_remote_qwen as _remote
from req2web_runtime.phase4_remote_qwen_fresh_integrated import (
    RemoteFreshIntegratedProfile,
)


GpuProbe = Callable[[], Mapping[str, object]]


def create_phase5_live_runtime_profile(
    *,
    base_profile_path: Path,
    output_path: Path,
    gpu_probe: GpuProbe = _remote._probe_remote_gpu_facts,
) -> RemoteFreshIntegratedProfile:
    """Rebind an accepted Phase 4 profile to the current GPU identity.

    The model inventory and runtime versions are inherited from the accepted
    base profile because the owner declared that unchanged environment out of
    scope for redundant revalidation. Only the current GPU facts are probed.
    """

    if (
        not isinstance(base_profile_path, Path)
        or not base_profile_path.is_file()
        or base_profile_path.is_symlink()
    ):
        raise ValueError("Phase 5 base runtime profile is invalid")
    if (
        not isinstance(output_path, Path)
        or not output_path.is_absolute()
        or output_path.exists()
        or not output_path.parent.is_dir()
        or output_path.parent.is_symlink()
    ):
        raise ValueError("Phase 5 live runtime profile output is invalid")
    base = RemoteFreshIntegratedProfile.from_bytes(
        base_profile_path.read_bytes()
    )
    profile = RemoteFreshIntegratedProfile.create(
        inventory={
            "model_root_identity": dict(base.model_root_identity),
            "inventory_identity": dict(base.model_inventory_identity),
            "file_count": base.model_file_count,
        },
        runtime_facts={
            "python_version": base.python_version,
            "transformers_version": base.transformers_version,
            "torch_version": base.torch_version,
            "accelerate_version": base.accelerate_version,
        },
        gpu_facts=dict(gpu_probe()),
        timeout_seconds=base.timeout_seconds,
    )
    raw = profile.canonical_bytes()
    descriptor = os.open(
        output_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            output_path.unlink()
        except OSError:
            pass
        raise
    return profile


__all__ = ["GpuProbe", "create_phase5_live_runtime_profile"]
