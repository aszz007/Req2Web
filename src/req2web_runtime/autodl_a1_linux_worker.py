"""Linux-only Real-A1 preparation validation; never executes or writes."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import sys
from typing import Mapping

from .autodl_a1_operational import (
    A1ModelInventory,
    A1OperationalControls,
    A1OperationalPlan,
    A1RootMarker,
    RealA1OperationalError,
)
from .autodl_a1_preflight import (
    AutoDLA1DeploymentPackageManifest,
    AutoDLA1PreflightError,
    AutoDLA1PreflightPlan,
)

_PLATFORM = sys.platform
_ROLES = ("control", "package", "model", "evidence")
_EXECUTOR_STAGING_DIRECTORY = "real-a1-executor-v1"
_COMMITTED_EVIDENCE_FILES = ("phase_events.json", "production_observation.json", "execution_receipt.json", "evidence_commit.json")


@dataclass(frozen=True)
class A1WorkerPreparationValidation:
    plan: A1OperationalPlan
    controls: A1OperationalControls
    root_bindings: Mapping[str, Mapping[str, object]]


@dataclass(frozen=True)
class A1CommittedEvidenceRootValidation:
    plan: A1OperationalPlan
    controls: A1OperationalControls
    root_bindings: Mapping[str, Mapping[str, object]]
    staging_root: Path


def _require_linux() -> None:
    if _PLATFORM != "linux":
        raise RealA1OperationalError("real_a1_linux_worker_required")


def _reject_symlink_components(value: str | Path, code: str) -> Path:
    absolute = Path(os.path.abspath(os.fspath(value)))
    current = Path(absolute.anchor)
    parts = absolute.parts[1:] if absolute.anchor else absolute.parts
    for part in parts:
        current = current / part
        try:
            item_stat = os.lstat(current)
        except FileNotFoundError:
            break
        except OSError as exc:
            raise RealA1OperationalError(code) from exc
        if stat.S_ISLNK(item_stat.st_mode):
            raise RealA1OperationalError(code)
    return absolute


def _resolve_existing(value: str | Path, kind: str, code: str) -> Path:
    raw = _reject_symlink_components(value, code)
    try:
        item_stat = os.lstat(raw)
    except OSError as exc:
        raise RealA1OperationalError(code) from exc
    if stat.S_ISLNK(item_stat.st_mode):
        raise RealA1OperationalError(code)
    if kind == "file" and not stat.S_ISREG(item_stat.st_mode):
        raise RealA1OperationalError(code)
    if kind == "dir" and not stat.S_ISDIR(item_stat.st_mode):
        raise RealA1OperationalError(code)
    return raw.resolve(strict=True)


def _scan_tree(root: Path, code: str) -> tuple[dict[str, Path], set[str]]:
    files: dict[str, Path] = {}
    directories: set[str] = set()
    pending = [root]
    while pending:
        current = pending.pop()
        for item in current.iterdir():
            try:
                item_stat = item.lstat()
            except OSError as exc:
                raise RealA1OperationalError(code) from exc
            relative = item.relative_to(root).as_posix()
            if stat.S_ISLNK(item_stat.st_mode):
                raise RealA1OperationalError(code)
            if stat.S_ISDIR(item_stat.st_mode):
                directories.add(relative)
                pending.append(item)
            elif stat.S_ISREG(item_stat.st_mode):
                files[relative] = item
            else:
                raise RealA1OperationalError(code)
    return files, directories


def _expected_directories(paths: set[str]) -> set[str]:
    result: set[str] = set()
    for value in paths:
        parent = Path(value).parent
        while parent != Path("."):
            result.add(parent.as_posix())
            parent = parent.parent
    return result


def _validate_exact_tree(root: Path, expected_paths: set[str], role: str) -> None:
    files, directories = _scan_tree(root, f"worker_{role}_symlink_or_nonregular_invalid")
    if set(files) != expected_paths or directories != _expected_directories(expected_paths):
        raise RealA1OperationalError(f"worker_{role}_regular_file_set_invalid")


def _resolve_evidence(value: str | Path) -> tuple[Path, str]:
    raw = _reject_symlink_components(value, "worker_evidence_root_invalid")
    if raw.exists():
        root = _resolve_existing(raw, "dir", "worker_evidence_root_invalid")
        files, directories = _scan_tree(root, "worker_evidence_symlink_or_nonregular_invalid")
        if files or directories:
            raise RealA1OperationalError("worker_evidence_root_not_empty")
        return root, "dedicated_empty_no_write"
    parent = _resolve_existing(raw.parent, "dir", "worker_evidence_parent_invalid")
    candidate = parent / raw.name
    if candidate.exists():
        raise RealA1OperationalError("worker_evidence_root_race_invalid")
    return candidate, "dedicated_missing_no_write"


def _is_same_or_within(left: Path, right: Path) -> bool:
    if left == right:
        return True
    try:
        left.relative_to(right)
        return True
    except ValueError:
        return False


def _validate_topology(roots: Mapping[str, Path]) -> None:
    roles = tuple(roots)
    if roles != _ROLES:
        raise RealA1OperationalError("worker_root_roles_invalid")
    for index, left_role in enumerate(roles):
        for right_role in roles[index + 1:]:
            left = roots[left_role]
            right = roots[right_role]
            if _is_same_or_within(left, right) or _is_same_or_within(right, left):
                raise RealA1OperationalError("worker_root_topology_invalid")


def _root_markers(controls: A1OperationalControls) -> dict[str, A1RootMarker]:
    markers = [A1RootMarker.from_dict(value) for value in controls.data["root_markers"]]
    result = {marker.data["role"]: marker for marker in markers}
    if tuple(result) != _ROLES:
        raise RealA1OperationalError("worker_root_markers_invalid")
    return result


def _root_binding(role: str, root: Path, marker: A1RootMarker, observed_state: str) -> dict[str, object]:
    normalized = os.path.normcase(str(root))
    return {
        "role": role,
        "redacted_path": f"<{role}-root>",
        "normalized_path_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "marker_id": marker.data["marker_id"],
        "marker_sha256": marker.data["marker_sha256"],
        "expected_state": marker.data["expected_state"],
        "observed_state": observed_state,
    }


def _require_production_inventory(plan) -> A1ModelInventory:
    try:
        inventory_data = plan.data["model_inventory"]
        mode = inventory_data["inventory_mode"]
    except (AttributeError, KeyError, TypeError) as exc:
        raise RealA1OperationalError("worker_model_inventory_mode_invalid") from exc
    if mode != "production_pinned":
        raise RealA1OperationalError("worker_model_inventory_mode_invalid")
    try:
        inventory = A1ModelInventory.from_dict(inventory_data)
    except RealA1OperationalError as exc:
        raise RealA1OperationalError("worker_model_inventory_invalid") from exc
    if inventory.data["inventory_mode"] != "production_pinned":
        raise RealA1OperationalError("worker_model_inventory_mode_invalid")
    return inventory


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            hasher.update(block)
    return hasher.hexdigest()


def _validate_test_inventory_root(root: str | Path, rows: list[dict[str, object]]) -> None:
    """Private small-file seam for filesystem validator tests only."""
    resolved = _resolve_existing(root, "dir", "worker_test_model_root_invalid")
    expected_paths = {str(row["relative_path"]) for row in rows}
    _validate_exact_tree(resolved, expected_paths, "test_model")
    files, _ = _scan_tree(resolved, "worker_test_model_symlink_or_nonregular_invalid")
    for row in rows:
        item = files[str(row["relative_path"])]
        if item.stat().st_size != row["bytes"] or _hash_file(item) != row["sha256"]:
            raise RealA1OperationalError("worker_test_model_identity_drift")


def _validate_model_root(root: Path, inventory: A1ModelInventory) -> None:
    if inventory.data["inventory_mode"] != "production_pinned":
        raise RealA1OperationalError("worker_model_inventory_mode_invalid")
    expected_model_paths = {row["relative_path"] for row in inventory.data["files"]}
    _validate_exact_tree(root, expected_model_paths, "model")
    inventory.validate_against_root(root)


def _validate_preparation_roots_linux(plan_file: str, controls_file: str, package_root: str, model_root: str, evidence_root: str) -> A1WorkerPreparationValidation:
    plan_path = _resolve_existing(plan_file, "file", "worker_plan_file_invalid")
    controls_path = _resolve_existing(controls_file, "file", "worker_controls_file_invalid")
    if plan_path.parent != controls_path.parent or plan_path == controls_path:
        raise RealA1OperationalError("worker_control_containment_invalid")
    try:
        plan = A1OperationalPlan.from_bytes(plan_path.read_bytes())
    except RealA1OperationalError as exc:
        if "inventory" in str(exc):
            raise RealA1OperationalError("worker_model_inventory_mode_invalid") from exc
        raise
    inventory = _require_production_inventory(plan)
    controls = A1OperationalControls.from_bytes(controls_path.read_bytes())

    control_root = _resolve_existing(plan_path.parent, "dir", "worker_control_root_invalid")
    control_files, control_directories = _scan_tree(control_root, "worker_control_symlink_or_nonregular_invalid")
    if set(control_files) != {plan_path.name, controls_path.name} or control_directories:
        raise RealA1OperationalError("worker_control_regular_file_set_invalid")
    if controls.data["plan_id"] != plan.plan_id:
        raise RealA1OperationalError("worker_control_plan_binding_invalid")
    markers = _root_markers(controls)
    if any(marker.data["plan_id"] != plan.plan_id for marker in markers.values()):
        raise RealA1OperationalError("worker_root_marker_plan_binding_invalid")

    package = _resolve_existing(package_root, "dir", "worker_package_root_invalid")
    model = _resolve_existing(model_root, "dir", "worker_model_root_invalid")
    evidence, evidence_state = _resolve_evidence(evidence_root)
    roots = {"control": control_root, "package": package, "model": model, "evidence": evidence}
    _validate_topology(roots)
    inventory = _require_production_inventory(plan)

    try:
        base = AutoDLA1PreflightPlan.from_dict(plan.data["no_action_plan"])
        manifest = AutoDLA1DeploymentPackageManifest.from_dict(base.data["deployment_package_manifest"])
    except AutoDLA1PreflightError as exc:
        raise RealA1OperationalError("worker_package_manifest_invalid") from exc
    disposition = base.data["approved_disposition"]
    if manifest.sha256() != plan.data["package_manifest_sha256"] or manifest.data["source_commit_sha"] != plan.data["action_time_git_sha"] or disposition["deployment_package_manifest_sha256"] != manifest.sha256() or disposition["action_time_git_sha"] != manifest.data["source_commit_sha"]:
        raise RealA1OperationalError("worker_package_source_binding_invalid")
    expected_package_paths = {row["path"] for row in manifest.data["files"]}
    _validate_exact_tree(package, expected_package_paths, "package")
    try:
        manifest.validate_against_root(package)
    except AutoDLA1PreflightError as exc:
        raise RealA1OperationalError("worker_package_identity_invalid") from exc
    inventory = _require_production_inventory(plan)
    if inventory.sha256() != plan.data["model_inventory_sha256"]:
        raise RealA1OperationalError("worker_model_inventory_binding_invalid")
    _validate_model_root(model, inventory)
    inventory = _require_production_inventory(plan)

    bindings = {
        "control": _root_binding("control", control_root, markers["control"], "existing_exact_control_inputs"),
        "package": _root_binding("package", package, markers["package"], "existing_exact_package_manifest"),
        "model": _root_binding("model", model, markers["model"], "existing_exact_model_inventory"),
        "evidence": _root_binding("evidence", evidence, markers["evidence"], evidence_state),
    }
    return A1WorkerPreparationValidation(plan=plan, controls=controls, root_bindings=bindings)


def validate_preparation_roots(plan_file: str, controls_file: str, package_root: str, model_root: str, evidence_root: str) -> A1WorkerPreparationValidation:
    _require_linux()
    return _validate_preparation_roots_linux(plan_file, controls_file, package_root, model_root, evidence_root)


def _build_committed_evidence_validator(*, _platform_override=None, _model_validator_override=None):
    """Capture the complete post-execution filesystem authority at definition time."""
    platform = _PLATFORM if _platform_override is None else _platform_override
    roles = tuple(_ROLES)
    staging_directory = _EXECUTOR_STAGING_DIRECTORY
    committed_files = tuple(_COMMITTED_EVIDENCE_FILES)
    error_type = RealA1OperationalError
    result_type = A1CommittedEvidenceRootValidation
    path_type = Path
    fspath = os.fspath
    abspath = os.path.abspath
    normcase = os.path.normcase
    lstat = os.lstat
    sha256_ctor = hashlib.sha256
    is_link = stat.S_ISLNK
    is_regular = stat.S_ISREG
    is_directory = stat.S_ISDIR
    plan_from_bytes = A1OperationalPlan.from_bytes
    controls_from_bytes = A1OperationalControls.from_bytes
    marker_from_dict = A1RootMarker.from_dict
    inventory_from_dict = A1ModelInventory.from_dict
    inventory_validate_root = A1ModelInventory.validate_against_root
    inventory_sha256 = A1ModelInventory.sha256
    preflight_plan_from_dict = AutoDLA1PreflightPlan.from_dict
    manifest_from_dict = AutoDLA1DeploymentPackageManifest.from_dict
    manifest_sha256 = AutoDLA1DeploymentPackageManifest.sha256
    manifest_validate_root = AutoDLA1DeploymentPackageManifest.validate_against_root
    operational_error = RealA1OperationalError
    preflight_error = AutoDLA1PreflightError
    os_error = OSError
    file_not_found = FileNotFoundError
    value_error = ValueError

    def reject_symlink_components(value, code):
        absolute = path_type(abspath(fspath(value)))
        current = path_type(absolute.anchor)
        parts = absolute.parts[1:] if absolute.anchor else absolute.parts
        for part in parts:
            current = current / part
            try:
                item_stat = lstat(current)
            except file_not_found:
                break
            except os_error as exc:
                raise error_type(code) from exc
            if is_link(item_stat.st_mode):
                raise error_type(code)
        return absolute

    def resolve_existing(value, kind, code):
        raw = reject_symlink_components(value, code)
        try:
            item_stat = lstat(raw)
        except os_error as exc:
            raise error_type(code) from exc
        if is_link(item_stat.st_mode):
            raise error_type(code)
        if kind == "file" and not is_regular(item_stat.st_mode):
            raise error_type(code)
        if kind == "dir" and not is_directory(item_stat.st_mode):
            raise error_type(code)
        try:
            return raw.resolve(strict=True)
        except os_error as exc:
            raise error_type(code) from exc

    def scan_tree(root, code):
        files = {}
        directories = set()
        pending = [root]
        while pending:
            current = pending.pop()
            try:
                children = list(current.iterdir())
            except os_error as exc:
                raise error_type(code) from exc
            for item in children:
                try:
                    item_stat = item.lstat()
                except os_error as exc:
                    raise error_type(code) from exc
                try:
                    relative = item.relative_to(root).as_posix()
                except value_error as exc:
                    raise error_type(code) from exc
                if is_link(item_stat.st_mode):
                    raise error_type(code)
                if is_directory(item_stat.st_mode):
                    directories.add(relative)
                    pending.append(item)
                elif is_regular(item_stat.st_mode):
                    files[relative] = item
                else:
                    raise error_type(code)
        return files, directories

    def expected_directories(paths):
        result = set()
        for value in paths:
            parent = path_type(value).parent
            while parent != path_type("."):
                result.add(parent.as_posix())
                parent = parent.parent
        return result

    def validate_exact_tree(root, expected_paths, role, prefix="committed_worker"):
        files, directories = scan_tree(root, f"{prefix}_{role}_symlink_or_nonregular_invalid")
        if set(files) != expected_paths or directories != expected_directories(expected_paths):
            raise error_type(f"{prefix}_{role}_regular_file_set_invalid")

    def same_or_within(left, right):
        if left == right:
            return True
        try:
            left.relative_to(right)
            return True
        except value_error:
            return False

    def validate_topology(roots):
        ordered_roles = tuple(roots)
        if ordered_roles != roles:
            raise error_type("committed_worker_root_roles_invalid")
        for index, left_role in enumerate(ordered_roles):
            for right_role in ordered_roles[index + 1:]:
                left = roots[left_role]
                right = roots[right_role]
                if same_or_within(left, right) or same_or_within(right, left):
                    raise error_type("committed_worker_root_topology_invalid")

    def root_markers(controls):
        markers = [marker_from_dict(value) for value in controls.data["root_markers"]]
        result = {marker.data["role"]: marker for marker in markers}
        if tuple(result) != roles:
            raise error_type("committed_worker_root_markers_invalid")
        return result

    def root_binding(role, root, marker, observed_state):
        normalized = normcase(str(root))
        return {
            "role": role,
            "redacted_path": f"<{role}-root>",
            "normalized_path_sha256": sha256_ctor(normalized.encode("utf-8")).hexdigest(),
            "marker_id": marker.data["marker_id"],
            "marker_sha256": marker.data["marker_sha256"],
            "expected_state": marker.data["expected_state"],
            "observed_state": observed_state,
        }

    attribute_error = AttributeError
    key_error = KeyError
    type_error = TypeError

    def require_production_inventory(plan):
        try:
            inventory_data = plan.data["model_inventory"]
            mode = inventory_data["inventory_mode"]
        except (attribute_error, key_error, type_error) as exc:
            raise error_type("committed_worker_model_inventory_mode_invalid") from exc
        if mode != "production_pinned":
            raise error_type("committed_worker_model_inventory_mode_invalid")
        try:
            inventory = inventory_from_dict(inventory_data)
        except operational_error as exc:
            raise error_type("committed_worker_model_inventory_invalid") from exc
        if inventory.data["inventory_mode"] != "production_pinned":
            raise error_type("committed_worker_model_inventory_mode_invalid")
        return inventory

    def validate_production_model(root, inventory):
        if inventory.data["inventory_mode"] != "production_pinned":
            raise error_type("committed_worker_model_inventory_mode_invalid")
        expected_paths = {row["relative_path"] for row in inventory.data["files"]}
        validate_exact_tree(root, expected_paths, "model")
        inventory_validate_root(inventory, root)

    model_validator = validate_production_model if _model_validator_override is None else _model_validator_override

    def validate_linux(plan_file, controls_file, package_root, model_root, evidence_root):
        plan_path = resolve_existing(plan_file, "file", "committed_worker_plan_file_invalid")
        controls_path = resolve_existing(controls_file, "file", "committed_worker_controls_file_invalid")
        if plan_path.parent != controls_path.parent or plan_path == controls_path:
            raise error_type("committed_worker_control_containment_invalid")
        try:
            plan = plan_from_bytes(plan_path.read_bytes())
        except operational_error as exc:
            if "inventory" in str(exc):
                raise error_type("committed_worker_model_inventory_mode_invalid") from exc
            raise
        inventory = require_production_inventory(plan)
        controls = controls_from_bytes(controls_path.read_bytes())

        control_root = resolve_existing(plan_path.parent, "dir", "committed_worker_control_root_invalid")
        control_files, control_directories = scan_tree(control_root, "committed_worker_control_symlink_or_nonregular_invalid")
        if set(control_files) != {plan_path.name, controls_path.name} or control_directories:
            raise error_type("committed_worker_control_regular_file_set_invalid")
        if controls.data["plan_id"] != plan.plan_id:
            raise error_type("committed_worker_control_plan_binding_invalid")
        markers = root_markers(controls)
        if any(marker.data["plan_id"] != plan.plan_id for marker in markers.values()):
            raise error_type("committed_worker_root_marker_plan_binding_invalid")

        package = resolve_existing(package_root, "dir", "committed_worker_package_root_invalid")
        model = resolve_existing(model_root, "dir", "committed_worker_model_root_invalid")
        evidence = resolve_existing(evidence_root, "dir", "committed_worker_evidence_root_invalid")
        roots = {"control": control_root, "package": package, "model": model, "evidence": evidence}
        validate_topology(roots)

        try:
            base = preflight_plan_from_dict(plan.data["no_action_plan"])
            manifest = manifest_from_dict(base.data["deployment_package_manifest"])
        except preflight_error as exc:
            raise error_type("committed_worker_package_manifest_invalid") from exc
        disposition = base.data["approved_disposition"]
        manifest_hash = manifest_sha256(manifest)
        if manifest_hash != plan.data["package_manifest_sha256"] or manifest.data["source_commit_sha"] != plan.data["action_time_git_sha"] or disposition["deployment_package_manifest_sha256"] != manifest_hash or disposition["action_time_git_sha"] != manifest.data["source_commit_sha"]:
            raise error_type("committed_worker_package_source_binding_invalid")
        expected_package_paths = {row["path"] for row in manifest.data["files"]}
        validate_exact_tree(package, expected_package_paths, "package")
        try:
            manifest_validate_root(manifest, package)
        except preflight_error as exc:
            raise error_type("committed_worker_package_identity_invalid") from exc
        if inventory_sha256(inventory) != plan.data["model_inventory_sha256"]:
            raise error_type("committed_worker_model_inventory_binding_invalid")
        model_validator(model, inventory)
        require_production_inventory(plan)

        evidence_files, evidence_directories = scan_tree(evidence, "committed_worker_evidence_symlink_or_nonregular_invalid")
        expected_files = {f"{staging_directory}/{name}" for name in committed_files}
        if set(evidence_files) != expected_files or evidence_directories != {staging_directory}:
            raise error_type("committed_worker_evidence_regular_file_set_invalid")
        staging = resolve_existing(evidence / staging_directory, "dir", "committed_worker_evidence_staging_invalid")

        bindings = {
            "control": root_binding("control", control_root, markers["control"], "existing_exact_control_inputs"),
            "package": root_binding("package", package, markers["package"], "existing_exact_package_manifest"),
            "model": root_binding("model", model, markers["model"], "existing_exact_model_inventory"),
            "evidence": root_binding("evidence", evidence, markers["evidence"], "committed_exact_executor_evidence"),
        }
        return result_type(plan=plan, controls=controls, root_bindings=bindings, staging_root=staging)

    def validate_committed_evidence_roots(plan_file: str, controls_file: str, package_root: str, model_root: str, evidence_root: str) -> A1CommittedEvidenceRootValidation:
        if platform != "linux":
            raise error_type("real_a1_committed_evidence_linux_required")
        return validate_linux(plan_file, controls_file, package_root, model_root, evidence_root)

    return validate_committed_evidence_roots


def _make_committed_evidence_validator_for_tests(model_validator):
    """Private synthetic filesystem seam; never manager-consumable."""
    return _build_committed_evidence_validator(_platform_override="linux", _model_validator_override=model_validator)


validate_committed_evidence_roots = _build_committed_evidence_validator()



def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Real-A1 local preparation without execution")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--controls", required=True)
    parser.add_argument("--package-root", required=True)
    parser.add_argument("--model-root", required=True)
    parser.add_argument("--evidence-root", required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    _require_linux()
    args = _parser().parse_args(argv)
    validate_preparation_roots(args.plan, args.controls, args.package_root, args.model_root, args.evidence_root)
    raise RealA1OperationalError("real_a1_executor_not_ready")


if __name__ == "__main__":
    main()
