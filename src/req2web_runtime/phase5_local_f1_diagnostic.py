"""One-call local F1 diagnostic for the immutable Phase 5 Core 2 baseline.

This module is component-only diagnostic infrastructure.  It reads the exact
preserved F1 input from the terminal publication result return, rebuilds the
prompt through the project-wide shared prompt authority, and reuses the
existing supervised local-Qwen worker.  It never rewrites the publication
result, retries a call, runs F2-F4, or claims publication or formal quality.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tarfile
from types import MappingProxyType
from typing import Mapping

from req2web_agent.prompt_authority import (
    PROMPT_AUTHORITY_IDENTITY,
    PROMPT_AUTHORITY_REVISION,
    build_canonical_f1_f4_prompt,
)
from req2web_generation.publication_language import (
    validate_english_publication_value,
)
from req2web_orchestration.phase4_graph import (
    phase4_create_portable_authority_state,
    phase4_validate_node_output,
    validate_b_input,
)
from req2web_runtime import phase4_local_qwen as _local
from req2web_runtime import phase4_local_qwen_f4 as _local_f4
from req2web_runtime.phase5_result_return import validate_phase5_result_return


SCHEMA_PREFIX = "req2web.phase5.local_f1_diagnostic.v1"
SOURCE_RUN_ID = "phase5-publication-path2-v3-20260809"
SOURCE_CASE_ID = "publication-core-case-02-service-request"
SOURCE_REQUEST_ID = "phase5-publication-request-02"
SOURCE_TAR_SHA256 = (
    "e14f30eec4f5490e167db05ef4a2a206aceebd459bc03f308d109ac544ee2d24"
)
SOURCE_MANIFEST_SHA256 = (
    "f47b731f35363e6ba7ce979a560b8e1b8d87acd6ef746dab0ccb1cc3a07db420"
)
SOURCE_UPSTREAM_IDENTITY = (
    "sha256:537e443cc4b7134b233db7d28bca4bf9976dbab08408e4825ad2879df0f53f03"
)
PROMPT_AUTHORITY_SHA256 = (
    "sha256:68de2486f92ccf20aa3c2b6ba3826c3f201ba2ba14e9355e1a86ae40aca4c66f"
)
RESULT_ROOT_MARKER_NAME = ".req2web-phase5-local-f1-diagnostic-root"


class Phase5LocalF1DiagnosticError(RuntimeError):
    """Raised when the diagnostic cannot preserve its narrow contract."""


@dataclass(frozen=True)
class SourceExpectation:
    tar_sha256: str
    manifest_sha256: str
    run_id: str
    case_id: str
    request_id: str
    condition_id: str
    execution_index: int
    row_id: str
    row_root: str
    input_sha256: str
    input_byte_length: int
    upstream_identity: str
    projection_identity: str


def _core2_expectation(
    *,
    condition_id: str,
    execution_index: int,
    row_suffix: str,
    input_sha256: str,
    input_byte_length: int,
    projection_identity: str,
) -> SourceExpectation:
    row_id = f"phase5-publication-candidate-row-{row_suffix}"
    return SourceExpectation(
        tar_sha256=SOURCE_TAR_SHA256,
        manifest_sha256=SOURCE_MANIFEST_SHA256,
        run_id=SOURCE_RUN_ID,
        case_id=SOURCE_CASE_ID,
        request_id=SOURCE_REQUEST_ID,
        condition_id=condition_id,
        execution_index=execution_index,
        row_id=row_id,
        row_root=f"rows/{execution_index:02d}-{row_id}",
        input_sha256=input_sha256,
        input_byte_length=input_byte_length,
        upstream_identity=SOURCE_UPSTREAM_IDENTITY,
        projection_identity=projection_identity,
    )


CORE2_REMOVE_CRITICAL_ROLE_EXPECTATION = _core2_expectation(
    condition_id="remove_critical_role",
    execution_index=2,
    row_suffix=(
        "426f765f972bf08243459cfac5c0be4549a5c61a94442dbf24013ce442fa4247"
    ),
    input_sha256=(
        "5b34b9ffc8e8f6e0555214f9a5626203c5cd0439c3285ed423049dd572e90ee6"
    ),
    input_byte_length=2664,
    projection_identity=(
        "sha256:bd9023021e0255d4c36d84cb099f4c6a5d951dc497bbcee30379b2ea5289f74e"
    ),
)
CORE2_IRRELEVANT_EVIDENCE_EXPECTATION = _core2_expectation(
    condition_id="irrelevant_evidence",
    execution_index=5,
    row_suffix=(
        "777152750b5142f29bf7afd68f261160ff85f25f8bc3a8094f40d9425a1e669b"
    ),
    input_sha256=(
        "a910db8800db1bab9a9b3958462ba4f419db8322ecfa3c21979618def9ba014d"
    ),
    input_byte_length=2883,
    projection_identity=(
        "sha256:43c1a510918ded1049db9dbff1cb35b7472d38b16a543b2143ee2b71ebe0860f"
    ),
)
CORE2_NONE_EXPECTATION = _core2_expectation(
    condition_id="none",
    execution_index=9,
    row_suffix=(
        "d07f81d94690955cc7b192d9dba453953ed475a94d19c811d1e2d298cf5ff2c7"
    ),
    input_sha256=(
        "5b34b9ffc8e8f6e0555214f9a5626203c5cd0439c3285ed423049dd572e90ee6"
    ),
    input_byte_length=2664,
    projection_identity=(
        "sha256:6ea390c782d54105e7e6d9dade8265ab5eab7de87366f4b269e919117cbfd2b2"
    ),
)
CORE2_EXPECTATIONS: Mapping[str, SourceExpectation] = MappingProxyType(
    {
        item.condition_id: item
        for item in (
            CORE2_NONE_EXPECTATION,
            CORE2_IRRELEVANT_EVIDENCE_EXPECTATION,
            CORE2_REMOVE_CRITICAL_ROLE_EXPECTATION,
        )
    }
)


@dataclass(frozen=True)
class SourceMaterial:
    input_bytes: bytes
    b_input: dict[str, object]
    source_binding: dict[str, object]


@dataclass(frozen=True)
class PreparedDiagnostic:
    result_root: Path
    input_bytes: bytes
    prompt_bytes: bytes
    config_bytes: bytes
    request_bytes: bytes
    b_input: dict[str, object]
    profile: _local.LocalQwenProfile


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Phase5LocalF1DiagnosticError("value is not canonical JSON") from exc


def _identity(raw: bytes, *, revision: str) -> dict[str, object]:
    return {
        "identity_kind": "raw_bytes",
        "sha256": f"sha256:{sha256(raw).hexdigest()}",
        "byte_length": len(raw),
        "revision": revision,
    }


def _strict_object(raw: bytes, *, name: str) -> dict[str, object]:
    if not isinstance(raw, bytes) or not raw or raw.startswith(b"\xef\xbb\xbf"):
        raise Phase5LocalF1DiagnosticError(f"{name} bytes are invalid")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise Phase5LocalF1DiagnosticError(
                    f"{name} contains duplicate JSON keys"
                )
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase5LocalF1DiagnosticError(f"{name} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise Phase5LocalF1DiagnosticError(f"{name} must be a JSON object")
    return value


def _rehydrate_b_input(value: Mapping[str, object]) -> dict[str, object]:
    """Restore the owning schema order after canonical on-disk serialization."""

    keys = (
        "case_id",
        "request_id",
        "requirement",
        "requirement_summary",
        "target_device",
        "task_type",
        "constraints",
        "use_cases",
    )
    use_case_keys = (
        "use_case_id",
        "title",
        "actor",
        "goal",
        "expected_outcome",
    )
    if set(value) != set(keys) or not isinstance(value.get("use_cases"), list):
        raise Phase5LocalF1DiagnosticError("source canonical B shape drifted")
    use_cases: list[dict[str, object]] = []
    for item in value["use_cases"]:  # type: ignore[index]
        if not isinstance(item, Mapping) or set(item) != set(use_case_keys):
            raise Phase5LocalF1DiagnosticError(
                "source canonical B use-case shape drifted"
            )
        use_cases.append({key: copy.deepcopy(item[key]) for key in use_case_keys})
    ordered = {key: copy.deepcopy(value[key]) for key in keys}
    ordered["use_cases"] = use_cases
    return validate_b_input(ordered)


def _write_once(path: Path, raw: bytes) -> None:
    if not isinstance(path, Path) or not path.is_absolute() or path.parent.is_symlink():
        raise Phase5LocalF1DiagnosticError("diagnostic output path is unsafe")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise Phase5LocalF1DiagnosticError(
            f"cannot persist diagnostic artifact: {path.name}"
        ) from exc


def _prepare_result_root(path: Path, *, diagnostic_run_id: str) -> tuple[Path, str]:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path.exists()
        or not path.parent.is_dir()
        or path.parent.is_symlink()
    ):
        raise Phase5LocalF1DiagnosticError("diagnostic result root is invalid")
    try:
        path.mkdir()
        prepared = path.resolve(strict=True)
    except OSError as exc:
        raise Phase5LocalF1DiagnosticError(
            "cannot create diagnostic result root"
        ) from exc
    if prepared.is_symlink():
        raise Phase5LocalF1DiagnosticError("diagnostic result root is unsafe")
    marker = "phase5-local-f1-diagnostic-" + sha256(
        f"{diagnostic_run_id}\0{prepared}".encode("utf-8")
    ).hexdigest()
    _write_once(
        prepared / RESULT_ROOT_MARKER_NAME,
        (marker + "\n").encode("ascii"),
    )
    return prepared, marker


def _archive_members(
    tar_path: Path,
    names: tuple[str, ...],
) -> dict[str, bytes]:
    observed: dict[str, bytes] = {}
    try:
        with tarfile.open(tar_path, mode="r:") as archive:
            for name in names:
                member = archive.getmember(name)
                if not member.isfile() or member.issym() or member.islnk():
                    raise Phase5LocalF1DiagnosticError(
                        f"source member is not a regular file: {name}"
                    )
                stream = archive.extractfile(member)
                if stream is None:
                    raise Phase5LocalF1DiagnosticError(
                        f"source member is unreadable: {name}"
                    )
                observed[name] = stream.read()
    except (KeyError, OSError, tarfile.TarError) as exc:
        raise Phase5LocalF1DiagnosticError(
            "preserved source archive is incomplete"
        ) from exc
    return observed


def load_core2_source_material(
    *,
    tar_path: Path,
    manifest_path: Path,
    expectation: SourceExpectation = CORE2_NONE_EXPECTATION,
) -> SourceMaterial:
    """Pure-read validation and exact-byte loading of the preserved F1 input."""

    if (
        not isinstance(tar_path, Path)
        or not tar_path.is_absolute()
        or not tar_path.is_file()
        or tar_path.is_symlink()
        or not isinstance(manifest_path, Path)
        or not manifest_path.is_absolute()
        or not manifest_path.is_file()
        or manifest_path.is_symlink()
    ):
        raise Phase5LocalF1DiagnosticError("source return paths are invalid")
    manifest_raw = manifest_path.read_bytes()
    tar_digest = sha256(tar_path.read_bytes()).hexdigest()
    if (
        tar_digest != expectation.tar_sha256
        or sha256(manifest_raw).hexdigest() != expectation.manifest_sha256
    ):
        raise Phase5LocalF1DiagnosticError("source return identity drifted")
    validate_phase5_result_return(
        tar_path=tar_path,
        manifest_path=manifest_path,
    )

    paths = {
        "input": f"{expectation.row_root}/attempts/F1/input.json",
        "pre_call": f"{expectation.row_root}/attempts/F1/pre_call_record.json",
        "b_input": f"{expectation.row_root}/b_input.json",
        "row_summary": f"row-summaries/{expectation.execution_index:02d}.json",
        "projection": (
            f"projection-receipts/{expectation.execution_index:02d}.json"
        ),
        "upstream": "baseline/02/upstream/upstream_receipt.json",
    }
    members = _archive_members(tar_path, tuple(paths.values()))
    input_bytes = members[paths["input"]]
    if (
        sha256(input_bytes).hexdigest() != expectation.input_sha256
        or len(input_bytes) != expectation.input_byte_length
    ):
        raise Phase5LocalF1DiagnosticError("preserved F1 input bytes drifted")

    pre_call = _strict_object(members[paths["pre_call"]], name="source pre-call")
    row = _strict_object(members[paths["row_summary"]], name="source row summary")
    projection = _strict_object(
        members[paths["projection"]],
        name="source projection receipt",
    )
    upstream = _strict_object(
        members[paths["upstream"]],
        name="source upstream receipt",
    )
    b_input = _rehydrate_b_input(
        _strict_object(members[paths["b_input"]], name="source canonical B")
    )
    input_value = _strict_object(input_bytes, name="source F1 input")

    pre_call_input = pre_call.get("input_identity")
    row_upstream = row.get("shared_case_upstream_identity")
    row_projection = row.get("provider_visible_projection_identity")
    upstream_receipt = upstream.get("receipt_identity")
    projection_identity = projection.get("provider_visible_projection_identity")
    if (
        pre_call_input
        != _identity(
            input_bytes,
            revision="req2web.phase4.p4_05.remote_fresh_integrated.input.v1",
        )
        or row.get("execution_index") != expectation.execution_index
        or row.get("row_id") != expectation.row_id
        or row.get("case_ref") != expectation.case_id
        or row.get("condition_id") != expectation.condition_id
        or not isinstance(row_upstream, Mapping)
        or row_upstream.get("sha256") != expectation.upstream_identity
        or not isinstance(row_projection, Mapping)
        or row_projection.get("sha256") != expectation.projection_identity
        or not isinstance(upstream_receipt, Mapping)
        or upstream_receipt.get("sha256") != expectation.upstream_identity
        or not isinstance(projection_identity, Mapping)
        or projection_identity.get("sha256") != expectation.projection_identity
        or projection.get("row_id") != expectation.row_id
        or projection.get("condition_id") != expectation.condition_id
        or b_input.get("case_id") != expectation.case_id
        or b_input.get("request_id") != expectation.request_id
        or input_value.get("case_id") != expectation.case_id
        or input_value.get("request_id") != expectation.request_id
        or input_value.get("node_id") != "F1"
    ):
        raise Phase5LocalF1DiagnosticError("preserved source bindings drifted")
    validate_english_publication_value(input_value, artifact_name="source F1 input")
    validate_english_publication_value(b_input, artifact_name="source canonical B")

    binding = {
        "schema_version": f"{SCHEMA_PREFIX}.source_binding",
        "source_kind": "immutable_publication_result_return",
        "source_run_id": expectation.run_id,
        "source_case_id": expectation.case_id,
        "source_request_id": expectation.request_id,
        "source_condition_id": expectation.condition_id,
        "source_execution_index": expectation.execution_index,
        "source_row_id": expectation.row_id,
        "source_tar_sha256": expectation.tar_sha256,
        "source_manifest_sha256": expectation.manifest_sha256,
        "source_input_identity": _identity(
            input_bytes,
            revision="req2web.phase4.p4_05.remote_fresh_integrated.input.v1",
        ),
        "shared_upstream_identity": expectation.upstream_identity,
        "provider_visible_projection_identity": expectation.projection_identity,
        "input_provenance": "exact_preserved_row_member_bytes",
        "source_result_mutated": False,
    }
    return SourceMaterial(
        input_bytes=input_bytes,
        b_input=copy.deepcopy(dict(b_input)),
        source_binding=binding,
    )


def load_core2_none_source_material(
    *,
    tar_path: Path,
    manifest_path: Path,
    expectation: SourceExpectation = CORE2_NONE_EXPECTATION,
) -> SourceMaterial:
    """Compatibility wrapper for the original Core 2 baseline diagnostic."""

    return load_core2_source_material(
        tar_path=tar_path,
        manifest_path=manifest_path,
        expectation=expectation,
    )


def _diagnostic_artifacts(
    *,
    source: SourceMaterial,
    profile: _local.LocalQwenProfile,
    diagnostic_run_id: str,
    expectation: SourceExpectation = CORE2_NONE_EXPECTATION,
) -> tuple[bytes, bytes, bytes]:
    prompt_bytes = build_canonical_f1_f4_prompt(
        node_id="F1",
        input_bytes=source.input_bytes,
    )
    if (
        PROMPT_AUTHORITY_REVISION
        != "f3_f4_explicit_actual_state_plan_a07a_direct_english_v16"
        or PROMPT_AUTHORITY_IDENTITY.get("sha256") != PROMPT_AUTHORITY_SHA256
    ):
        raise Phase5LocalF1DiagnosticError("shared prompt authority drifted")
    prompt_value = _strict_object(prompt_bytes, name="v16 F1 prompt")
    validate_english_publication_value(prompt_value, artifact_name="v16 F1 prompt")
    config = {
        "schema_version": f"{SCHEMA_PREFIX}.config",
        "node_id": "F1",
        "profile_id": profile.profile_id,
        "profile_name": profile.profile_name,
        "device_index": 0,
        "dtype": "bfloat16",
        "quantization": "4bit_nf4_double_quant",
        "compute_dtype": "bfloat16",
        "cpu_offload": False,
        "local_files_only": True,
        "offline": True,
        "network": False,
        "input_truncation": False,
        "output_truncation": False,
        "fixed_max_new_tokens": None,
        "output_limit_kind": "context_remaining",
        "stop_on_complete_json_object": True,
        "seed": 0,
        "automatic_retry": False,
    }
    request = {
        "schema_version": f"{SCHEMA_PREFIX}.request",
        "diagnostic_run_id": diagnostic_run_id,
        "source_run_id": SOURCE_RUN_ID,
        "source_execution_index": expectation.execution_index,
        "case_id": SOURCE_CASE_ID,
        "request_id": SOURCE_REQUEST_ID,
        "condition_id": expectation.condition_id,
        "node_id": "F1",
        "call_kind": "integrated",
        "b_aux_disposition": "absent/not_requested",
        "generate_call_cap": 1,
        "retry_count": 0,
        "prompt_revision": PROMPT_AUTHORITY_REVISION,
        "component_only_diagnostic": True,
    }
    return prompt_bytes, _canonical(config), _canonical(request)


def prepare_phase5_local_f1_diagnostic(
    *,
    tar_path: Path,
    manifest_path: Path,
    model_root: Path,
    integrity_evidence: Path,
    result_root: Path,
    diagnostic_run_id: str,
    expectation: SourceExpectation = CORE2_NONE_EXPECTATION,
) -> PreparedDiagnostic:
    """Prepare all persisted call inputs before the supervised model load."""

    if (
        not isinstance(diagnostic_run_id, str)
        or CORE2_EXPECTATIONS.get(expectation.condition_id) != expectation
        or not diagnostic_run_id.startswith(
            f"phase5-local-f1-v16-core2-{expectation.condition_id}-"
        )
    ):
        raise Phase5LocalF1DiagnosticError("diagnostic run ID is invalid")
    source = load_core2_source_material(
        tar_path=tar_path,
        manifest_path=manifest_path,
        expectation=expectation,
    )
    if PROMPT_AUTHORITY_IDENTITY.get("sha256") != PROMPT_AUTHORITY_SHA256:
        raise Phase5LocalF1DiagnosticError("v16 prompt authority identity drifted")
    provisional_prompt = build_canonical_f1_f4_prompt(
        node_id="F1",
        input_bytes=source.input_bytes,
    )
    validate_english_publication_value(
        _strict_object(provisional_prompt, name="v16 F1 prompt"),
        artifact_name="v16 F1 prompt",
    )

    prepared_root, marker = _prepare_result_root(
        result_root,
        diagnostic_run_id=diagnostic_run_id,
    )
    _write_once(prepared_root / "source_input.json", source.input_bytes)
    _write_once(prepared_root / "source_b_input.json", _canonical(source.b_input))
    _write_once(
        prepared_root / "source_binding.json",
        _canonical(source.source_binding),
    )
    _write_once(prepared_root / "prompt.json", provisional_prompt)

    profile, native_context_identity = _local_f4._build_f4_profile(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
    )
    prompt_bytes, config_bytes, request_bytes = _diagnostic_artifacts(
        source=source,
        profile=profile,
        diagnostic_run_id=diagnostic_run_id,
        expectation=expectation,
    )
    if prompt_bytes != provisional_prompt:
        raise Phase5LocalF1DiagnosticError("v16 prompt reconstruction drifted")
    runtime_preflight = {
        "schema_version": f"{SCHEMA_PREFIX}.component_runtime_preflight",
        "diagnostic_run_id": diagnostic_run_id,
        "result_root_marker": marker,
        "scope": "one_local_nf4_v16_core2_condition_f1_diagnostic",
        "pipeline_scope": "component_only",
        "node_scope": ["F1"],
        "unused_nodes": ["F2", "F3", "F4"],
        "prompt_revision": PROMPT_AUTHORITY_REVISION,
        "prompt_authority_identity": copy.deepcopy(PROMPT_AUTHORITY_IDENTITY),
        "profile_identity": _identity(
            profile.canonical_bytes(),
            revision=_local.LOCAL_QWEN_PROFILE_SCHEMA_VERSION,
        ),
        "profile": profile.to_dict(),
        "native_context_identity": copy.deepcopy(native_context_identity),
        "native_context_source": "config.json:/text_config/max_position_embeddings",
        "output_limit_kind": "context_remaining",
        "generate_call_cap": 1,
        "retry_count": 0,
        "historical_pilot_policy_bound": False,
        "model_loaded": False,
        "generation_started": False,
        "f2_f4_execution_allowed": False,
        "downstream_pipeline_in_scope": False,
        "browser_in_scope": False,
        "h1_or_gold_access": False,
        "formal_evaluation": False,
    }
    pre_call = {
        "schema_version": f"{SCHEMA_PREFIX}.pre_call",
        "diagnostic_run_id": diagnostic_run_id,
        "scope": "component_only_local_nf4_f1",
        "source_binding": source.source_binding,
        "input_identity": _identity(
            source.input_bytes,
            revision="req2web.phase4.p4_05.remote_fresh_integrated.input.v1",
        ),
        "prompt_identity": _identity(
            prompt_bytes,
            revision="req2web.agent.f1_f4_prompt.v1",
        ),
        "prompt_authority_identity": copy.deepcopy(PROMPT_AUTHORITY_IDENTITY),
        "config_identity": _identity(
            config_bytes,
            revision=f"{SCHEMA_PREFIX}.config",
        ),
        "request_identity": _identity(
            request_bytes,
            revision=f"{SCHEMA_PREFIX}.request",
        ),
        "generate_call_cap": 1,
        "retry_count": 0,
        "raw_first": True,
        "repair_allowed": False,
        "normalization_allowed": False,
        "fallback_allowed": False,
        "f2_f4_allowed": False,
        "publication_result_mutation_allowed": False,
        "h1_or_gold_access": False,
        "formal_evaluation": False,
        "formal_quality_claimed": False,
        "model_loaded": False,
        "generation_started": False,
    }
    for name, raw in (
        ("config.json", config_bytes),
        ("request.json", request_bytes),
        ("component_runtime_preflight.json", _canonical(runtime_preflight)),
        ("pre_call_record.json", _canonical(pre_call)),
    ):
        _write_once(prepared_root / name, raw)
    return PreparedDiagnostic(
        result_root=prepared_root,
        input_bytes=source.input_bytes,
        prompt_bytes=prompt_bytes,
        config_bytes=config_bytes,
        request_bytes=request_bytes,
        b_input=source.b_input,
        profile=profile,
    )


def _emit_delta(buffer: bytearray, delta: bytes) -> None:
    if not isinstance(delta, bytes) or not delta:
        raise Phase5LocalF1DiagnosticError("streamed token delta is invalid")
    buffer.extend(delta)
    target = getattr(sys.stderr, "buffer", None)
    if target is not None:
        target.write(delta)
        target.flush()
    else:
        sys.stderr.write(delta.decode("utf-8", errors="replace"))
        sys.stderr.flush()


def _validate_raw_f1(raw: bytes, b_input: Mapping[str, object]) -> dict[str, object]:
    output = _strict_object(raw, name="raw F1 response")
    validate_english_publication_value(output, artifact_name="raw F1 response")
    state = phase4_create_portable_authority_state(b_input)
    return phase4_validate_node_output("F1", output, state)


def run_phase5_local_f1_diagnostic(
    *,
    tar_path: Path,
    manifest_path: Path,
    model_root: Path,
    integrity_evidence: Path,
    result_root: Path,
    diagnostic_run_id: str,
    expectation: SourceExpectation = CORE2_NONE_EXPECTATION,
) -> dict[str, object]:
    """Run exactly one local NF4 F1 call and close the worker in all paths."""

    prepared = prepare_phase5_local_f1_diagnostic(
        tar_path=tar_path,
        manifest_path=manifest_path,
        model_root=model_root,
        integrity_evidence=integrity_evidence,
        result_root=result_root,
        diagnostic_run_id=diagnostic_run_id,
        expectation=expectation,
    )
    runtime: _local.P4D1SupervisedRuntime | None = None
    partial = bytearray()
    raw: bytes | None = None
    validated: dict[str, object] | None = None
    failure: dict[str, object] | None = None
    teardown: dict[str, object] | None = None
    stderr_bytes: bytes | None = None
    generation_facts: dict[str, object] | None = None
    generation_started = False
    raw_persisted = False
    try:
        print(
            "[Phase 5 local F1 diagnostic] Starting supervised local model load.",
            file=sys.stderr,
            flush=True,
        )
        runtime = _local.start_supervised_local_qwen_fresh_integrated_component_runtime(
            model_root=model_root,
            integrity_evidence=integrity_evidence,
            profile=prepared.profile,
            generate_call_cap=1,
        )
        load_receipt = {
            "schema_version": f"{SCHEMA_PREFIX}.component_load_receipt",
            "diagnostic_run_id": diagnostic_run_id,
            "scope": "component_only_local_nf4_f1",
            "profile_identity": _identity(
                prepared.profile.canonical_bytes(),
                revision=_local.LOCAL_QWEN_PROFILE_SCHEMA_VERSION,
            ),
            "loaded_facts": copy.deepcopy(runtime.loaded_facts),
            "fresh_runtime_facts": copy.deepcopy(
                runtime.backend.fresh_runtime_facts
            ),
            "generate_call_cap": 1,
            "retry_count": 0,
            "model_loaded": True,
            "generation_started": False,
            "historical_pilot_policy_bound": False,
            "f2_f4_executed": False,
            "downstream_pipeline_executed": False,
            "browser_executed": False,
            "h1_or_gold_access": False,
            "formal_evaluation": False,
        }
        _write_once(
            prepared.result_root / "load_receipt.json",
            _canonical(load_receipt),
        )
        print(
            "[Phase 5 local F1 diagnostic] Load completed; starting the one F1 call.",
            file=sys.stderr,
            flush=True,
        )
        raw = runtime.backend.generate_fresh_integrated(
            node_id="F1",
            input_bytes=prepared.input_bytes,
            prompt_bytes=prepared.prompt_bytes,
            config_bytes=prepared.config_bytes,
            request_bytes=prepared.request_bytes,
            emit_delta=lambda delta: _emit_delta(partial, delta),
        )
        generation_started = runtime.backend.generation_started
        generation_facts = runtime.backend.last_generation_facts
        _write_once(prepared.result_root / "raw_response.bin", raw)
        raw_persisted = True
        validated = _validate_raw_f1(raw, prepared.b_input)
        _write_once(
            prepared.result_root / "validated_f1_output.json",
            _canonical(validated),
        )
    except _local.SupervisedWorkerStartFailure as exc:
        failure = {
            "failure_code": exc.failure_code,
            "failure_type": type(exc).__name__,
            "message": str(exc),
            "failure_boundary": "before_generation",
        }
        teardown = copy.deepcopy(dict(exc.teardown_facts))
        stderr_bytes = exc.stderr_bytes
    except Exception as exc:
        if runtime is not None:
            generation_started = runtime.backend.generation_started
            generation_facts = runtime.backend.last_generation_facts
        failure = {
            "failure_code": getattr(exc, "failure_code", "strict_f1_failed_closed"),
            "failure_type": type(exc).__name__,
            "message": str(exc),
            "failure_boundary": (
                "after_generation_started"
                if generation_started
                else "before_generation"
            ),
        }
    finally:
        if runtime is not None:
            teardown = runtime.backend.close()
            stderr_bytes = runtime.backend.stderr_bytes
        if partial:
            _write_once(prepared.result_root / "partial_stream.bin", bytes(partial))
        if stderr_bytes:
            _write_once(prepared.result_root / "worker_stderr.bin", stderr_bytes)

    raw_identity = (
        None
        if raw is None or not raw_persisted
        else _identity(raw, revision="raw_response.bin")
    )
    partial_identity = (
        None
        if not partial
        else _identity(bytes(partial), revision="partial_stream.bin")
    )
    summary = {
        "schema_version": f"{SCHEMA_PREFIX}.summary",
        "diagnostic_run_id": diagnostic_run_id,
        "status": (
            "local_nf4_v16_core2_f1_raw_contract_pass"
            if validated is not None
            else "failed_closed"
        ),
        "scope": "one_local_nf4_v16_core2_condition_f1_diagnostic",
        "source_run_id": SOURCE_RUN_ID,
        "source_execution_index": expectation.execution_index,
        "source_condition_id": expectation.condition_id,
        "prompt_revision": PROMPT_AUTHORITY_REVISION,
        "prompt_authority_identity": copy.deepcopy(PROMPT_AUTHORITY_IDENTITY),
        "profile_name": prepared.profile.profile_name,
        "quantization": prepared.profile.quantization,
        "compute_dtype": prepared.profile.compute_dtype,
        "cpu_offload": prepared.profile.cpu_offload,
        "generate_call_cap": 1,
        "generate_started_count": 1 if generation_started else 0,
        "automatic_retry": False,
        "repair_attempted": False,
        "normalization_attempted": False,
        "fallback_attempted": False,
        "raw_response_identity": raw_identity,
        "raw_response_persisted": raw_persisted,
        "raw_contract_pass": validated is not None,
        "partial_stream_identity": partial_identity,
        "partial_stream_is_raw_response": False,
        "generation_facts": generation_facts,
        "failure": failure,
        "worker_teardown": teardown,
        "publication_result_mutated": False,
        "f2_f4_executed": False,
        "browser_executed": False,
        "h1_or_gold_access": False,
        "formal_evaluation": False,
        "formal_quality_claimed": False,
        "rtx5090_bf16_equivalence_claimed": False,
        "stability_claimed": False,
    }
    _write_once(
        prepared.result_root / "terminal_summary.json",
        _canonical(summary),
    )
    return summary


__all__ = [
    "CORE2_EXPECTATIONS",
    "CORE2_IRRELEVANT_EVIDENCE_EXPECTATION",
    "CORE2_NONE_EXPECTATION",
    "CORE2_REMOVE_CRITICAL_ROLE_EXPECTATION",
    "PROMPT_AUTHORITY_SHA256",
    "Phase5LocalF1DiagnosticError",
    "PreparedDiagnostic",
    "SourceExpectation",
    "SourceMaterial",
    "load_core2_source_material",
    "load_core2_none_source_material",
    "prepare_phase5_local_f1_diagnostic",
    "run_phase5_local_f1_diagnostic",
]
