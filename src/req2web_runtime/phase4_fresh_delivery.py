"""No-model downstream delivery for a live Phase 4 fresh-integrated artifact.

The source of this route is a separately validated fresh-integrated assembled
PageSpec.  It is intentionally not a ``ModelRouteOutcome`` and never enters
the historical scripted-fixture registry.  The existing A-07a/A-07b factories
still own Renderer, Consistency, RequirementView, Acceptance, package,
one-repair, and same-case G0 decisions.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping

from req2web_agent import AgentContextBundle
from req2web_evaluation.frozen_g0_reference import (
    FrozenG0InventoryEntry,
    FrozenG0PackageReference,
    freeze_verified_g0_package_reference,
)
from req2web_faults.fallback_delivery import (
    FrozenG0FallbackRecord,
    freeze_g0_fallback_package,
)
from req2web_generation import (
    DeterministicPageRenderer,
    DeterministicRetrievalEnhancedResultPackager,
    MinimalConsistencyChecker,
    PageSpecBuilder,
    RetrievalEnhancedResultPackage,
    RetrievalGuidance,
    RetrievalGuidedPageSpecBuilder,
    RetrievalInfluenceChecker,
)
from req2web_orchestration.model_route import (
    PHASE4_FRESH_INTEGRATED_DISPOSITION,
    SCRIPTED_ACCEPTANCE_PASS_KEY,
    TIER_A_07A_GATE_DELIVERY_OUTCOME_SCHEMA_VERSION,
    TIER_A_07B_GATE_DELIVERY_OUTCOME_SCHEMA_VERSION,
    TierA07aGateDeliveryOutcome,
    TierA07bFieldGateReport,
    TierA07bGateDeliveryOutcome,
    TierA07bRepairPatch,
    ScriptedAcceptanceFixture,
    _build_phase4_fresh_delivery_runners,
    _validate_live_audit,
    _validate_live_manifest,
    _validate_live_preparation,
    _validate_live_request,
    _validate_live_selection,
    create_tier_a_07b_field_gate_report,
)
from req2web_provider.d17_audit import (
    create_d17_path3_pre_invocation_audit_record,
)
from req2web_provider.d17_input_view import select_d17_path3_provider_input
from req2web_provider.d17_manifest import D17Path3TierAManifest
from req2web_provider.d17_serializer import serialize_d17_path3_local_request
from req2web_provider.local_qwen_provider import (
    prepare_local_qwen_provider_interface,
)
from req2web_provider.semantic_candidate import (
    AssembledPageSpec,
    CanonicalPageSpecAssembler,
    ProviderRawResponse,
)
from req2web_rag.corpus import ROLE_ORDER


PHASE4_FRESH_DELIVERY_SOURCE_KIND = "phase4_fresh_integrated_assembled"
PHASE4_FRESH_ROUTE_OUTCOME_SCHEMA_VERSION = (
    "req2web.phase4.fresh_delivery.route_outcome.v1"
)
PHASE4_FRESH_DELIVERY_RECEIPT_SCHEMA_VERSION = (
    "req2web.phase4.fresh_delivery.receipt.v1"
)
PHASE4_FRESH_ROUTE_ID_PREFIX = "phase4-fresh-route-"
PHASE4_FRESH_RECEIPT_ID_PREFIX = "phase4-fresh-delivery-receipt-"
PHASE4_GRAPH_BOUND_DELIVERY_MATERIALS_SCHEMA_VERSION = (
    "req2web.phase4.graph_bound_delivery_materials.v1"
)
PHASE4_GRAPH_BOUND_DELIVERY_MATERIALS_ID_PREFIX = (
    "phase4-graph-bound-delivery-materials-"
)
PHASE4_COMMERCE_STRUCTURAL_SIGNALS = (
    "has_search",
    "has_filter_controls",
    "has_cart_panel",
    "has_checkout_form",
    "has_inline_validation",
)
PHASE4_FRESH_CLAIM_BOUNDARY = (
    "Phase 4 fresh-integrated assembled source with local downstream delivery "
    "evidence; not historical scripted ModelRouteOutcome, strict raw-model "
    "success, H1, browser, or formal-quality evidence"
)

_SOURCE_RESULT_FILENAMES = (
    "revalidation_result.json",
    "fresh_integrated_result.json",
    "p4_05_result.json",
)
_SOURCE_IDENTITY_KEYS = (
    "source_result",
    "candidate_composition",
    "candidate_bytes",
    "assembled_page_spec",
    "assembly_report",
    "context",
    "guidance",
)
_ROUTE_ARTIFACT_KEYS = (
    "model_semantic_candidate_sha256",
    "assembled_page_id",
    "assembly_report_id",
    "assembly_report_sha256",
    "assembled_page_spec_sha256",
)
_ROUTE_STEPS = (
    "source_result_live_validated",
    "candidate_composition_revalidated",
    "assembled_page_live_revalidated",
)


class Phase4FreshDeliveryError(ValueError):
    """Raised when a fresh-integrated source or receipt fails closed."""


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Phase4FreshDeliveryError("value is not canonical JSON") from exc


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _bare_sha256(value: str) -> str:
    return value[7:] if value.startswith("sha256:") else value


def _identity(
    raw: bytes,
    *,
    revision: str,
    identity_kind: str = "canonical_json",
) -> dict[str, object]:
    return {
        "identity_kind": identity_kind,
        "sha256": _sha256(raw),
        "byte_length": len(raw),
        "revision": revision,
    }


def _json_identity(
    value: object,
    *,
    revision: str,
) -> dict[str, object]:
    return _identity(_canonical_bytes(value), revision=revision)


def _strict_json(raw: bytes, name: str) -> object:
    if type(raw) is not bytes or not raw:
        raise Phase4FreshDeliveryError(f"{name} is empty")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise Phase4FreshDeliveryError(f"{name} has a UTF-8 BOM")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase4FreshDeliveryError(f"{name} is not JSON") from exc
    if _canonical_bytes(value) != raw:
        raise Phase4FreshDeliveryError(f"{name} is not canonical JSON")
    return value


def _safe_existing_root(value: Path, name: str) -> Path:
    root = Path(value)
    if root.is_symlink():
        raise Phase4FreshDeliveryError(f"{name} must not be a symlink")
    root = root.resolve(strict=False)
    if not root.is_dir():
        raise Phase4FreshDeliveryError(f"{name} must be an existing directory")
    return root


def _read_json(root: Path, filename: str) -> tuple[bytes, object]:
    path = root / filename
    if path.is_symlink() or not path.is_file():
        raise Phase4FreshDeliveryError(f"required source artifact is missing: {filename}")
    raw = path.read_bytes()
    return raw, _strict_json(raw, filename)


def _identity_matches(
    declared: object,
    actual: Mapping[str, object],
    name: str,
) -> None:
    if not isinstance(declared, Mapping):
        raise Phase4FreshDeliveryError(f"{name} identity is invalid")
    if (
        declared.get("sha256") != actual.get("sha256")
        or declared.get("byte_length") != actual.get("byte_length")
    ):
        raise Phase4FreshDeliveryError(f"{name} identity drifted")


def _optional_identity_matches(
    declared: object,
    actual: Mapping[str, object],
    name: str,
) -> None:
    if declared is not None:
        _identity_matches(declared, actual, name)


def _artifact_identity(
    source_root: Path,
    filename: str,
    *,
    revision: str,
) -> dict[str, object] | None:
    path = source_root / filename
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise Phase4FreshDeliveryError(f"source artifact is not a regular file: {filename}")
    return _identity(path.read_bytes(), revision=revision)


def _acceptance_fixture(value: object | None) -> object:
    if value is None:
        return ScriptedAcceptanceFixture.create(SCRIPTED_ACCEPTANCE_PASS_KEY)
    if isinstance(value, str):
        return ScriptedAcceptanceFixture.create(value)
    return value


def _reference_from_dict(value: object) -> FrozenG0PackageReference:
    if not isinstance(value, Mapping):
        raise Phase4FreshDeliveryError("frozen G0 reference is not an object")
    expected = {
        "schema_version",
        "package_schema_version",
        "context_schema_version",
        "guidance_schema_version",
        "package_id",
        "page_id",
        "context_id",
        "context_sha256",
        "guidance_bundle_id",
        "guidance_sha256",
        "package_manifest_sha256",
        "inventory_tree_sha256",
        "inventory",
        "reference_id",
        "declarations",
    }
    if set(value) != expected or not isinstance(value["inventory"], list):
        raise Phase4FreshDeliveryError("frozen G0 reference keys are invalid")
    try:
        reference = FrozenG0PackageReference(
            package_id=str(value["package_id"]),
            page_id=str(value["page_id"]),
            context_id=str(value["context_id"]),
            context_sha256=str(value["context_sha256"]),
            guidance_bundle_id=str(value["guidance_bundle_id"]),
            guidance_sha256=str(value["guidance_sha256"]),
            package_manifest_sha256=str(value["package_manifest_sha256"]),
            inventory_tree_sha256=str(value["inventory_tree_sha256"]),
            inventory=tuple(
                FrozenG0InventoryEntry(
                    relative_path=str(item["relative_path"]),
                    role=str(item["role"]),
                    size=int(item["size"]),
                    sha256=str(item["sha256"]),
                )
                for item in value["inventory"]
                if isinstance(item, Mapping)
            ),
            reference_id=str(value["reference_id"]),
            schema_version=str(value["schema_version"]),
            package_schema_version=str(value["package_schema_version"]),
            context_schema_version=str(value["context_schema_version"]),
            guidance_schema_version=str(value["guidance_schema_version"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise Phase4FreshDeliveryError("frozen G0 reference is invalid") from exc
    reference.validate()
    return reference


@dataclass(frozen=True)
class Phase4FreshDeliveryInput:
    source_root: Path
    source_result_filename: str
    source_result: Mapping[str, object]
    candidate_record: Mapping[str, object]
    raw_candidate: bytes
    assembled: AssembledPageSpec
    source_identities: Mapping[str, Mapping[str, object]]
    normalization_receipt: Mapping[str, object] | None

    @classmethod
    def from_result_root(
        cls,
        source_root: Path,
        *,
        context: AgentContextBundle,
        guidance: RetrievalGuidance,
    ) -> "Phase4FreshDeliveryInput":
        root = _safe_existing_root(source_root, "source root")
        if not isinstance(context, AgentContextBundle):
            raise Phase4FreshDeliveryError("context has the wrong type")
        if not isinstance(guidance, RetrievalGuidance):
            raise Phase4FreshDeliveryError("guidance has the wrong type")
        try:
            context.validate()
            guidance.validate()
        except Exception as exc:
            raise Phase4FreshDeliveryError("context or guidance is invalid") from exc

        candidate_raw, candidate_value = _read_json(
            root, "candidate_composition_record.json"
        )
        page_raw, page_value = _read_json(root, "assembled_page_spec.json")
        report_raw, report_value = _read_json(root, "assembly_report.json")
        result_filename = next(
            (
                filename
                for filename in _SOURCE_RESULT_FILENAMES
                if (root / filename).is_file()
            ),
            None,
        )
        if result_filename is None:
            raise Phase4FreshDeliveryError("fresh-integrated source result is missing")
        result_raw, result_value = _read_json(root, result_filename)
        if not isinstance(candidate_value, Mapping):
            raise Phase4FreshDeliveryError("candidate composition record is invalid")
        if not isinstance(result_value, Mapping):
            raise Phase4FreshDeliveryError("source result is invalid")
        if not isinstance(page_value, Mapping) or not isinstance(report_value, Mapping):
            raise Phase4FreshDeliveryError("assembled source artifacts are invalid")

        encoded = candidate_value.get("model_semantic_candidate_canonical_b64")
        if not isinstance(encoded, str) or not encoded:
            raise Phase4FreshDeliveryError("candidate bytes are unavailable")
        try:
            raw_candidate = base64.b64decode(encoded, validate=True)
            candidate_json = json.loads(raw_candidate.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise Phase4FreshDeliveryError("candidate bytes are invalid") from exc
        if _canonical_bytes(candidate_json) != raw_candidate:
            raise Phase4FreshDeliveryError("candidate bytes are not canonical JSON")
        if candidate_value.get("model_semantic_candidate_byte_length") != len(
            raw_candidate
        ):
            raise Phase4FreshDeliveryError("candidate byte length drifted")
        if candidate_value.get("model_semantic_candidate_sha256") != _sha256(
            raw_candidate
        ):
            raise Phase4FreshDeliveryError("candidate identity drifted")

        try:
            assembled = CanonicalPageSpecAssembler().assemble(
                ProviderRawResponse.from_bytes(raw_candidate),
                context,
                guidance,
            )
            assembled.validate()
        except Exception as exc:
            raise Phase4FreshDeliveryError(
                "candidate no longer assembles under the live authority"
            ) from exc
        if _canonical_bytes(assembled.page_spec.to_dict()) != page_raw:
            raise Phase4FreshDeliveryError("saved PageSpec differs from live assembly")
        if _canonical_bytes(assembled.report.to_dict()) != report_raw:
            raise Phase4FreshDeliveryError(
                "saved assembly report differs from live assembly"
            )

        booleans = (
            "raw_model_contract_success",
            "normalized_node_contract_success",
            "agent_chain_system_output_usable",
        )
        if any(type(result_value.get(key)) is not bool for key in booleans):
            raise Phase4FreshDeliveryError("source success accounting is invalid")
        if result_value["agent_chain_system_output_usable"] is not True:
            raise Phase4FreshDeliveryError(
                "source result does not establish system output usability"
            )
        if result_value.get("composition_status") not in {None, "composed"}:
            raise Phase4FreshDeliveryError("source composition is not composed")
        if result_value.get("assembler_status") not in {None, "assembled"}:
            raise Phase4FreshDeliveryError("source assembler is not assembled")
        case_id = result_value.get("case_id")
        request_id = result_value.get("request_id")
        if not isinstance(case_id, str) or not case_id:
            raise Phase4FreshDeliveryError("source case_id is invalid")
        if not isinstance(request_id, str) or not request_id:
            raise Phase4FreshDeliveryError("source request_id is invalid")

        normalization_receipt = None
        receipt_filename = next(
            (
                filename
                for filename in (
                    "core_f4_normalization_receipt.json",
                    "f4_normalization_receipt.json",
                )
                if (root / filename).is_file()
            ),
            None,
        )
        if receipt_filename is not None:
            _, receipt_value = _read_json(root, receipt_filename)
            if not isinstance(receipt_value, Mapping):
                raise Phase4FreshDeliveryError("normalization receipt is invalid")
            normalization_receipt = dict(receipt_value)

        if result_value["raw_model_contract_success"] is False:
            if result_value["normalized_node_contract_success"] is not True:
                raise Phase4FreshDeliveryError(
                    "raw failure requires normalized node success"
                )
            if normalization_receipt is None:
                raise Phase4FreshDeliveryError(
                    "raw failure requires a normalization receipt"
                )
            if normalization_receipt.get("raw_model_contract_success") is not False:
                raise Phase4FreshDeliveryError(
                    "normalization receipt raw success drifted"
                )
            if normalization_receipt.get("normalized_node_contract_success") is not True:
                raise Phase4FreshDeliveryError(
                    "normalization receipt normalized success drifted"
                )
            if normalization_receipt.get("normalization_counts_as_repair") is not False:
                raise Phase4FreshDeliveryError(
                    "normalization cannot be counted as one-repair"
                )
            if normalization_receipt.get("repair_attempted") not in {None, 0}:
                raise Phase4FreshDeliveryError(
                    "normalization receipt contains a repair attempt"
                )

        identities: dict[str, Mapping[str, object]] = {
            "source_result": _identity(
                result_raw,
                revision="req2web.phase4.fresh_delivery.source_result.v1",
            ),
            "candidate_composition": _identity(
                candidate_raw,
                revision="req2web.phase4.fresh_delivery.candidate_record.v1",
            ),
            "candidate_bytes": _identity(
                raw_candidate,
                revision="req2web.phase4.fresh_delivery.candidate_bytes.v1",
                identity_kind="raw_bytes",
            ),
            "assembled_page_spec": _identity(
                page_raw,
                revision="req2web.phase4.fresh_delivery.page_spec.v1",
            ),
            "assembly_report": _identity(
                report_raw,
                revision="req2web.phase4.fresh_delivery.assembly_report.v1",
            ),
            "context": _json_identity(
                context.to_dict(),
                revision="req2web.agent.context.v1",
            ),
            "guidance": _json_identity(
                guidance.to_dict(),
                revision="req2web.retrieval.guidance.v1",
            ),
        }
        root_markers = tuple(
            path
            for path in root.iterdir()
            if path.name.startswith(".req2web-")
            and path.is_file()
            and not path.is_symlink()
        )
        if len(root_markers) > 1:
            raise Phase4FreshDeliveryError("source root marker is ambiguous")
        if root_markers:
            identities["source_root_marker"] = _identity(
                root_markers[0].read_bytes(),
                revision="req2web.phase4.fresh_delivery.source_root_marker.v1",
            )
        if normalization_receipt is not None:
            receipt_path = root / receipt_filename
            identities["normalization_receipt"] = _identity(
                receipt_path.read_bytes(),
                revision="req2web.phase4.fresh_delivery.normalization_receipt.v1",
            )
        runtime_binding = _artifact_identity(
            root,
            "runtime_binding_receipt.json",
            revision="req2web.phase4.fresh_delivery.runtime_binding.v1",
        )
        if runtime_binding is not None:
            identities["runtime_binding"] = runtime_binding

        declared = result_value.get("candidate_composition_identity")
        _optional_identity_matches(
            declared, identities["candidate_composition"], "candidate composition"
        )
        _optional_identity_matches(
            result_value.get("assembled_page_spec_identity"),
            identities["assembled_page_spec"],
            "assembled PageSpec",
        )
        _optional_identity_matches(
            result_value.get("assembly_report_identity"),
            identities["assembly_report"],
            "assembly report",
        )
        output_identities = result_value.get("output_artifact_identities")
        if isinstance(output_identities, Mapping):
            if root_markers:
                _optional_identity_matches(
                    output_identities.get(root_markers[0].name),
                    identities["source_root_marker"],
                    f"output artifact {root_markers[0].name}",
                )
            for filename, key in (
                ("candidate_composition_record.json", "candidate_composition"),
                ("assembled_page_spec.json", "assembled_page_spec"),
                ("assembly_report.json", "assembly_report"),
            ):
                _optional_identity_matches(
                    output_identities.get(filename),
                    identities[key],
                    f"output artifact {filename}",
                )
            if normalization_receipt is not None:
                _optional_identity_matches(
                    output_identities.get(receipt_filename),
                    identities["normalization_receipt"],
                    f"output artifact {receipt_filename}",
                )

        return cls(
            source_root=root,
            source_result_filename=result_filename,
            source_result=dict(result_value),
            candidate_record=dict(candidate_value),
            raw_candidate=raw_candidate,
            assembled=assembled,
            source_identities=identities,
            normalization_receipt=normalization_receipt,
        )

    @property
    def case_id(self) -> str:
        return str(self.source_result["case_id"])

    @property
    def request_id(self) -> str:
        return str(self.source_result["request_id"])

    @property
    def raw_model_contract_success(self) -> bool:
        return bool(self.source_result["raw_model_contract_success"])

    @property
    def normalized_node_contract_success(self) -> bool:
        return bool(self.source_result["normalized_node_contract_success"])

    @property
    def agent_chain_system_output_usable(self) -> bool:
        return bool(self.source_result["agent_chain_system_output_usable"])

    def validate_context_guidance(
        self,
        context: AgentContextBundle,
        guidance: RetrievalGuidance,
    ) -> None:
        context.validate()
        guidance.validate()
        _identity_matches(
            _json_identity(context.to_dict(), revision="req2web.agent.context.v1"),
            self.source_identities["context"],
            "context",
        )
        _identity_matches(
            _json_identity(
                guidance.to_dict(),
                revision="req2web.retrieval.guidance.v1",
            ),
            self.source_identities["guidance"],
            "guidance",
        )


@dataclass(frozen=True)
class Phase4FreshRouteOutcome:
    """Truthful, path-free route outcome for a fresh assembled source."""

    case_id: str
    request_id: str
    source_identities: Mapping[str, Mapping[str, object]]
    artifacts: Mapping[str, object]
    frozen_g0_reference: FrozenG0PackageReference
    raw_model_contract_success: bool
    normalized_node_contract_success: bool
    agent_chain_system_output_usable: bool
    candidate_provenance: str
    outcome_id: str
    schema_version: str = PHASE4_FRESH_ROUTE_OUTCOME_SCHEMA_VERSION
    source_kind: str = PHASE4_FRESH_DELIVERY_SOURCE_KIND
    disposition: str = PHASE4_FRESH_INTEGRATED_DISPOSITION
    execution_branch: str = PHASE4_FRESH_DELIVERY_SOURCE_KIND
    completed_steps: tuple[str, ...] = _ROUTE_STEPS
    failure: None = None

    @classmethod
    def create(
        cls,
        source: Phase4FreshDeliveryInput,
        frozen_g0_reference: FrozenG0PackageReference,
    ) -> "Phase4FreshRouteOutcome":
        if not isinstance(frozen_g0_reference, FrozenG0PackageReference):
            raise Phase4FreshDeliveryError("frozen G0 reference has the wrong type")
        page = source.assembled.page_spec
        report = source.assembled.report
        artifacts = {
            "model_semantic_candidate_sha256": _bare_sha256(
                source.assembled.candidate.sha256()
            ),
            "assembled_page_id": page.page_id,
            "assembly_report_id": report.report_id,
            "assembly_report_sha256": _bare_sha256(report.sha256()),
            "assembled_page_spec_sha256": _bare_sha256(
                report.assembled_page_spec_sha256
            ),
        }
        root = {
            "schema_version": PHASE4_FRESH_ROUTE_OUTCOME_SCHEMA_VERSION,
            "source_kind": PHASE4_FRESH_DELIVERY_SOURCE_KIND,
            "disposition": PHASE4_FRESH_INTEGRATED_DISPOSITION,
            "execution_branch": PHASE4_FRESH_DELIVERY_SOURCE_KIND,
            "case_id": source.case_id,
            "request_id": source.request_id,
            "completed_steps": list(_ROUTE_STEPS),
            "failure": None,
            "frozen_g0_reference": frozen_g0_reference.to_dict(),
            "frozen_g0_reference_identity": _json_identity(
                frozen_g0_reference.to_dict(),
                revision="req2web.phase4.fresh_delivery.frozen_g0.v1",
            ),
            "source_identities": dict(source.source_identities),
            "artifacts": artifacts,
            "raw_model_contract_success": source.raw_model_contract_success,
            "normalized_node_contract_success": source.normalized_node_contract_success,
            "agent_chain_system_output_usable": source.agent_chain_system_output_usable,
            "candidate_provenance": (
                "raw_model_contract"
                if source.raw_model_contract_success
                else "normalized_receipt_bound"
            ),
            "claim_boundary": PHASE4_FRESH_CLAIM_BOUNDARY,
        }
        result = cls(
            case_id=source.case_id,
            request_id=source.request_id,
            source_identities=dict(source.source_identities),
            artifacts=artifacts,
            frozen_g0_reference=frozen_g0_reference,
            raw_model_contract_success=source.raw_model_contract_success,
            normalized_node_contract_success=source.normalized_node_contract_success,
            agent_chain_system_output_usable=source.agent_chain_system_output_usable,
            candidate_provenance=str(root["candidate_provenance"]),
            outcome_id=PHASE4_FRESH_ROUTE_ID_PREFIX + hashlib.sha256(
                _canonical_bytes(root)
            ).hexdigest(),
        )
        result.validate()
        return result

    def _root(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_kind": self.source_kind,
            "disposition": self.disposition,
            "execution_branch": self.execution_branch,
            "case_id": self.case_id,
            "request_id": self.request_id,
            "completed_steps": list(self.completed_steps),
            "failure": None,
            "frozen_g0_reference": self.frozen_g0_reference.to_dict(),
            "frozen_g0_reference_identity": _json_identity(
                self.frozen_g0_reference.to_dict(),
                revision="req2web.phase4.fresh_delivery.frozen_g0.v1",
            ),
            "source_identities": {
                key: dict(value) for key, value in self.source_identities.items()
            },
            "artifacts": dict(self.artifacts),
            "raw_model_contract_success": self.raw_model_contract_success,
            "normalized_node_contract_success": self.normalized_node_contract_success,
            "agent_chain_system_output_usable": self.agent_chain_system_output_usable,
            "candidate_provenance": self.candidate_provenance,
            "claim_boundary": PHASE4_FRESH_CLAIM_BOUNDARY,
        }

    def validate(self) -> None:
        if (
            self.schema_version != PHASE4_FRESH_ROUTE_OUTCOME_SCHEMA_VERSION
            or self.source_kind != PHASE4_FRESH_DELIVERY_SOURCE_KIND
            or self.disposition != PHASE4_FRESH_INTEGRATED_DISPOSITION
            or self.execution_branch != PHASE4_FRESH_DELIVERY_SOURCE_KIND
            or self.completed_steps != _ROUTE_STEPS
            or self.failure is not None
            or not self.case_id
            or not self.request_id
        ):
            raise Phase4FreshDeliveryError("fresh route outcome envelope is invalid")
        if type(self.raw_model_contract_success) is not bool:
            raise Phase4FreshDeliveryError("raw success flag is invalid")
        if type(self.normalized_node_contract_success) is not bool:
            raise Phase4FreshDeliveryError("normalized success flag is invalid")
        if type(self.agent_chain_system_output_usable) is not bool:
            raise Phase4FreshDeliveryError("system usability flag is invalid")
        if not self.agent_chain_system_output_usable:
            raise Phase4FreshDeliveryError("fresh route source is not usable")
        if self.raw_model_contract_success is False:
            if (
                self.normalized_node_contract_success is not True
                or "normalization_receipt" not in self.source_identities
                or self.candidate_provenance != "normalized_receipt_bound"
            ):
                raise Phase4FreshDeliveryError(
                    "raw failure is not bound to normalized output"
                )
        elif self.candidate_provenance != "raw_model_contract":
            raise Phase4FreshDeliveryError("raw source provenance is invalid")
        if set(self.artifacts) != set(_ROUTE_ARTIFACT_KEYS):
            raise Phase4FreshDeliveryError("fresh route artifact shape is invalid")
        if any(
            not isinstance(self.artifacts[key], str) or not self.artifacts[key]
            for key in _ROUTE_ARTIFACT_KEYS
        ):
            raise Phase4FreshDeliveryError("fresh route artifact value is invalid")
        if set(self.source_identities) != set(_SOURCE_IDENTITY_KEYS) and not set(
            _SOURCE_IDENTITY_KEYS
        ).issubset(self.source_identities):
            raise Phase4FreshDeliveryError("fresh source identity shape is invalid")
        self.frozen_g0_reference.validate()
        expected = PHASE4_FRESH_ROUTE_ID_PREFIX + hashlib.sha256(
            _canonical_bytes(self._root())
        ).hexdigest()
        if self.outcome_id != expected:
            raise Phase4FreshDeliveryError("fresh route outcome identity is invalid")

    def validate_against(
        self,
        *,
        frozen_g0_reference: FrozenG0PackageReference,
        package: RetrievalEnhancedResultPackage,
        context: AgentContextBundle,
        guidance: RetrievalGuidance,
        **_: object,
    ) -> None:
        self.validate()
        if self.frozen_g0_reference.to_dict() != frozen_g0_reference.to_dict():
            raise Phase4FreshDeliveryError("fresh route G0 reference drifted")
        self.validate_context_guidance(context, guidance)
        frozen_g0_reference.validate_against(package, context, guidance)

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"outcome_id": self.outcome_id, **self._root()}

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, value: object) -> "Phase4FreshRouteOutcome":
        if not isinstance(value, Mapping):
            raise Phase4FreshDeliveryError("fresh route outcome is not an object")
        expected = {
            "outcome_id",
            "schema_version",
            "source_kind",
            "disposition",
            "execution_branch",
            "case_id",
            "request_id",
            "completed_steps",
            "failure",
            "frozen_g0_reference",
            "frozen_g0_reference_identity",
            "source_identities",
            "artifacts",
            "raw_model_contract_success",
            "normalized_node_contract_success",
            "agent_chain_system_output_usable",
            "candidate_provenance",
            "claim_boundary",
        }
        if set(value) != expected or not isinstance(value["completed_steps"], list):
            raise Phase4FreshDeliveryError("fresh route outcome keys are invalid")
        if value["failure"] is not None or not isinstance(
            value["source_identities"], Mapping
        ) or not isinstance(value["artifacts"], Mapping):
            raise Phase4FreshDeliveryError("fresh route outcome payload is invalid")
        reference = _reference_from_dict(value["frozen_g0_reference"])
        if value["frozen_g0_reference_identity"] != _json_identity(
            reference.to_dict(),
            revision="req2web.phase4.fresh_delivery.frozen_g0.v1",
        ):
            raise Phase4FreshDeliveryError("fresh route G0 identity is invalid")
        if value["claim_boundary"] != PHASE4_FRESH_CLAIM_BOUNDARY:
            raise Phase4FreshDeliveryError("fresh route claim boundary is invalid")
        result = cls(
            case_id=str(value["case_id"]),
            request_id=str(value["request_id"]),
            source_identities={
                str(key): dict(item)
                for key, item in value["source_identities"].items()
                if isinstance(item, Mapping)
            },
            artifacts=dict(value["artifacts"]),
            frozen_g0_reference=reference,
            raw_model_contract_success=value["raw_model_contract_success"],
            normalized_node_contract_success=value[
                "normalized_node_contract_success"
            ],
            agent_chain_system_output_usable=value[
                "agent_chain_system_output_usable"
            ],
            candidate_provenance=str(value["candidate_provenance"]),
            outcome_id=str(value["outcome_id"]),
            schema_version=str(value["schema_version"]),
            source_kind=str(value["source_kind"]),
            disposition=str(value["disposition"]),
            execution_branch=str(value["execution_branch"]),
            completed_steps=tuple(value["completed_steps"]),
            failure=None,
        )
        result.validate()
        return result


def _fresh_route_validate(
    outcome: Phase4FreshRouteOutcome,
    *,
    frozen_g0_reference: FrozenG0PackageReference,
    package: RetrievalEnhancedResultPackage,
    context: AgentContextBundle,
    guidance: RetrievalGuidance,
    manifest: object,
    selected: object,
    local_request: object,
    pre_invocation_audit: object,
    local_qwen_preparation: object,
    **_: object,
) -> None:
    outcome.validate_against(
        frozen_g0_reference=frozen_g0_reference,
        package=package,
        context=context,
        guidance=guidance,
    )
    live_manifest = _validate_live_manifest(manifest)
    live_selected = _validate_live_selection(selected, context, live_manifest)
    live_request = _validate_live_request(local_request, context, live_manifest)
    live_audit = _validate_live_audit(
        pre_invocation_audit,
        context,
        live_manifest,
        live_selected,
        live_request,
    )
    _validate_live_preparation(
        local_qwen_preparation,
        context,
        live_manifest,
        live_selected,
        live_request,
        live_audit,
    )


@dataclass(frozen=True)
class Phase4FreshDeliveryReceipt:
    source_root: Path
    case_id: str
    request_id: str
    route_outcome: Phase4FreshRouteOutcome
    downstream_outcome: Mapping[str, object]
    downstream_kind: str
    downstream: Mapping[str, object]
    success_accounting: Mapping[str, object]
    source_identities: Mapping[str, Mapping[str, object]]
    receipt_id: str
    schema_version: str = PHASE4_FRESH_DELIVERY_RECEIPT_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        source: Phase4FreshDeliveryInput,
        route_outcome: Phase4FreshRouteOutcome,
        downstream_outcome: TierA07aGateDeliveryOutcome
        | TierA07bGateDeliveryOutcome,
        downstream_kind: str,
    ) -> "Phase4FreshDeliveryReceipt":
        if isinstance(downstream_outcome, TierA07aGateDeliveryOutcome):
            status = downstream_outcome.status
            repair = (
                "not_executed_first_pass_pass"
                if status == "first_pass_success"
                else "not_executed_a07a"
            )
            fallback = (
                "not_executed_first_pass_pass"
                if status == "first_pass_success"
                else (
                    "delivered"
                    if status == "fallback_delivery"
                    else "not_executed"
                )
            )
            result_package = (
                "result_package_v1"
                if status == "first_pass_success"
                else (
                    "result_package_v2"
                    if status == "fallback_delivery"
                    else "not_executed"
                )
            )
        else:
            status = downstream_outcome.status
            repair = (
                "recovered_success"
                if status == "recovered_success"
                else downstream_outcome.repair_status
            )
            fallback = (
                "delivered"
                if status == "fallback_delivery"
                else "not_executed_repair_success"
                if status == "recovered_success"
                else "not_executed"
            )
            result_package = (
                "result_package_v1"
                if status == "recovered_success"
                else (
                    "result_package_v2"
                    if status == "fallback_delivery"
                    else "not_executed"
                )
            )
        downstream = {
            "status": status,
            "authority": downstream_kind,
            "renderer": (
                "executed"
                if "deterministic_render" in downstream_outcome.completed_steps
                else "not_executed"
            ),
            "consistency": getattr(
                downstream_outcome, "consistency_status", "not_executed"
            ),
            "requirement_view": (
                "executed"
                if (
                    "independent_requirement_view"
                    in downstream_outcome.completed_steps
                    or "independent_requirement_view" in downstream_outcome.completed_steps
                )
                else "not_executed"
            ),
            "acceptance": getattr(
                downstream_outcome, "acceptance_status", "not_executed"
            ),
            "one_repair": repair,
            "same_case_g0_fallback": fallback,
            "result_package": result_package,
        }
        success = status in {"first_pass_success", "recovered_success", "fallback_delivery"}
        success_accounting = {
            "raw_model_contract_success": source.raw_model_contract_success,
            "normalized_node_contract_success": source.normalized_node_contract_success,
            "agent_chain_system_output_usable": source.agent_chain_system_output_usable,
            "delivery_success": success,
            "model_success": False,
            "repair_success": status == "recovered_success",
            "fallback_success": status == "fallback_delivery",
        }
        root = {
            "schema_version": PHASE4_FRESH_DELIVERY_RECEIPT_SCHEMA_VERSION,
            "source_kind": PHASE4_FRESH_DELIVERY_SOURCE_KIND,
            "source_root": source.source_root.as_posix(),
            "case_id": source.case_id,
            "request_id": source.request_id,
            "route_outcome": route_outcome.to_dict(),
            "downstream_kind": downstream_kind,
            "downstream_outcome": downstream_outcome.to_dict(),
            "downstream": downstream,
            "success_accounting": success_accounting,
            "source_identities": dict(source.source_identities),
            "claim_boundary": PHASE4_FRESH_CLAIM_BOUNDARY,
        }
        return cls(
            source_root=source.source_root,
            case_id=source.case_id,
            request_id=source.request_id,
            route_outcome=route_outcome,
            downstream_outcome=downstream_outcome.to_dict(),
            downstream_kind=downstream_kind,
            downstream=downstream,
            success_accounting=success_accounting,
            source_identities=dict(source.source_identities),
            receipt_id=PHASE4_FRESH_RECEIPT_ID_PREFIX
            + hashlib.sha256(_canonical_bytes(root)).hexdigest(),
        )

    def _root(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_kind": PHASE4_FRESH_DELIVERY_SOURCE_KIND,
            "source_root": self.source_root.as_posix(),
            "case_id": self.case_id,
            "request_id": self.request_id,
            "route_outcome": self.route_outcome.to_dict(),
            "downstream_kind": self.downstream_kind,
            "downstream_outcome": dict(self.downstream_outcome),
            "downstream": dict(self.downstream),
            "success_accounting": dict(self.success_accounting),
            "source_identities": {
                key: dict(value) for key, value in self.source_identities.items()
            },
            "claim_boundary": PHASE4_FRESH_CLAIM_BOUNDARY,
        }

    def validate(self) -> None:
        self.route_outcome.validate()
        if (
            self.schema_version != PHASE4_FRESH_DELIVERY_RECEIPT_SCHEMA_VERSION
            or self.source_root.is_absolute() is False
            or self.case_id != self.route_outcome.case_id
            or self.request_id != self.route_outcome.request_id
            or self.downstream_kind not in {"tier_a_07a", "tier_a_07b"}
            or set(self.success_accounting)
            != {
                "raw_model_contract_success",
                "normalized_node_contract_success",
                "agent_chain_system_output_usable",
                "delivery_success",
                "model_success",
                "repair_success",
                "fallback_success",
            }
        ):
            raise Phase4FreshDeliveryError("fresh delivery receipt envelope is invalid")
        if self.downstream_kind == "tier_a_07a":
            TierA07aGateDeliveryOutcome.from_dict(self.downstream_outcome)
        else:
            TierA07bGateDeliveryOutcome.from_dict(self.downstream_outcome)
        if self.success_accounting["model_success"] is not False:
            raise Phase4FreshDeliveryError("fresh delivery cannot claim model success")
        expected = PHASE4_FRESH_RECEIPT_ID_PREFIX + hashlib.sha256(
            _canonical_bytes(self._root())
        ).hexdigest()
        if self.receipt_id != expected:
            raise Phase4FreshDeliveryError("fresh delivery receipt identity is invalid")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"receipt_id": self.receipt_id, **self._root()}

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, value: object) -> "Phase4FreshDeliveryReceipt":
        if not isinstance(value, Mapping):
            raise Phase4FreshDeliveryError("fresh delivery receipt is not an object")
        expected = {
            "receipt_id",
            "schema_version",
            "source_kind",
            "source_root",
            "case_id",
            "request_id",
            "route_outcome",
            "downstream_kind",
            "downstream_outcome",
            "downstream",
            "success_accounting",
            "source_identities",
            "claim_boundary",
        }
        if set(value) != expected:
            raise Phase4FreshDeliveryError("fresh delivery receipt keys are invalid")
        if (
            value["schema_version"] != PHASE4_FRESH_DELIVERY_RECEIPT_SCHEMA_VERSION
            or value["source_kind"] != PHASE4_FRESH_DELIVERY_SOURCE_KIND
            or value["claim_boundary"] != PHASE4_FRESH_CLAIM_BOUNDARY
        ):
            raise Phase4FreshDeliveryError("fresh delivery receipt envelope is invalid")
        if not all(
            isinstance(value[key], Mapping)
            for key in (
                "downstream_outcome",
                "downstream",
                "success_accounting",
                "source_identities",
            )
        ):
            raise Phase4FreshDeliveryError("fresh delivery receipt payload is invalid")
        result = cls(
            source_root=Path(str(value["source_root"])),
            case_id=str(value["case_id"]),
            request_id=str(value["request_id"]),
            route_outcome=Phase4FreshRouteOutcome.from_dict(
                value["route_outcome"]
            ),
            downstream_outcome=dict(value["downstream_outcome"]),
            downstream_kind=str(value["downstream_kind"]),
            downstream=dict(value["downstream"]),
            success_accounting=dict(value["success_accounting"]),
            source_identities={
                str(key): dict(item)
                for key, item in value["source_identities"].items()
                if isinstance(item, Mapping)
            },
            receipt_id=str(value["receipt_id"]),
            schema_version=str(value["schema_version"]),
        )
        result.validate()
        return result


def _write_once(path: Path, raw: bytes) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != raw:
            raise Phase4FreshDeliveryError(f"write-once artifact drifted: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


@dataclass(frozen=True)
class Phase4GraphBoundDeliveryMaterials:
    material_root: Path
    case_id: str
    context: AgentContextBundle
    guidance: RetrievalGuidance
    live: Mapping[str, object]
    binding: Mapping[str, object]

    def validate(self) -> None:
        expected_live_keys = {
            "frozen_g0_reference",
            "package",
            "context",
            "guidance",
            "manifest",
            "selected",
            "local_request",
            "pre_invocation_audit",
            "local_qwen_preparation",
            "fallback_record",
            "fallback_snapshot_dir",
            "render_output_dir",
            "model_package_output_dir",
            "fallback_output_dir",
            "scripted_acceptance_fixture",
        }
        expected_binding_keys = {
            "schema_version",
            "source_kind",
            "case_id",
            "context_identity",
            "guidance_identity",
            "manifest_identity",
            "selected_identity",
            "local_request_identity",
            "pre_invocation_audit_identity",
            "local_qwen_preparation_identity",
            "frozen_g0_reference_identity",
            "fallback_record_identity",
            "package_id",
            "page_id",
            "graph_assembler_bindings_exact",
            "model_loaded",
            "run_occurred",
            "binding_id",
        }
        if (
            not isinstance(self.material_root, Path)
            or not self.material_root.is_absolute()
            or not self.case_id
            or set(self.live) != expected_live_keys
            or set(self.binding) != expected_binding_keys
            or self.live["context"] is not self.context
            or self.live["guidance"] is not self.guidance
        ):
            raise Phase4FreshDeliveryError(
                "graph-bound delivery material envelope is invalid"
            )
        self.context.validate()
        self.guidance.validate()
        if (
            self.binding["schema_version"]
            != PHASE4_GRAPH_BOUND_DELIVERY_MATERIALS_SCHEMA_VERSION
            or self.binding["source_kind"] != "phase4_graph_assembler_binding"
            or self.binding["case_id"] != self.case_id
            or self.binding["context_identity"]
            != _json_identity(
                self.context.to_dict(),
                revision="req2web.agent.context.v1",
            )
            or self.binding["guidance_identity"]
            != _json_identity(
                self.guidance.to_dict(),
                revision="req2web.retrieval.guidance.v1",
            )
            or self.binding["graph_assembler_bindings_exact"] is not True
            or self.binding["model_loaded"] is not False
            or self.binding["run_occurred"] is not False
        ):
            raise Phase4FreshDeliveryError(
                "graph-bound delivery material binding is invalid"
            )
        root = {
            key: value for key, value in self.binding.items() if key != "binding_id"
        }
        expected_id = PHASE4_GRAPH_BOUND_DELIVERY_MATERIALS_ID_PREFIX + hashlib.sha256(
            _canonical_bytes(root)
        ).hexdigest()
        if self.binding["binding_id"] != expected_id:
            raise Phase4FreshDeliveryError(
                "graph-bound delivery material identity is invalid"
            )


def _build_same_context_g0(
    *,
    case_id: str,
    context: AgentContextBundle,
    guidance: RetrievalGuidance,
    material_root: Path,
) -> tuple[
    RetrievalEnhancedResultPackage,
    FrozenG0PackageReference,
    FrozenG0FallbackRecord,
    Path,
]:
    baseline = PageSpecBuilder().build(context)
    guided = RetrievalGuidedPageSpecBuilder().build(context, guidance)
    ablations = {
        role: RetrievalGuidedPageSpecBuilder().build(
            context,
            guidance,
            disabled_roles=(role,),
        )
        for role in ROLE_ORDER
    }
    render = DeterministicPageRenderer().render(
        guided.page_spec,
        material_root / "g0-render",
    )
    consistency = MinimalConsistencyChecker().check(guided.page_spec, render)
    influence = RetrievalInfluenceChecker().check(
        context,
        guidance,
        baseline,
        guided,
        ablations,
        render,
    )
    package = DeterministicRetrievalEnhancedResultPackager().package(
        context,
        guidance,
        guided,
        guided.page_spec,
        render,
        consistency,
        influence,
        material_root / "g0-package-v2",
    )
    package.validate()
    reference = freeze_verified_g0_package_reference(
        package,
        context,
        guidance,
    )
    reference.validate_against(package, context, guidance)
    fallback_snapshot_dir = material_root / "fallback-snapshot"
    fallback_record = freeze_g0_fallback_package(
        case_id,
        package,
        fallback_snapshot_dir,
    )
    fallback_record.validate_against(fallback_snapshot_dir)
    if (
        fallback_record.case_id != case_id
        or fallback_record.package_id != reference.package_id
        or fallback_record.page_id != reference.page_id
    ):
        raise Phase4FreshDeliveryError(
            "graph-bound G0 fallback is not same-context and same-case"
        )
    return package, reference, fallback_record, fallback_snapshot_dir


def build_phase4_graph_bound_delivery_materials(
    *,
    graph_state: Mapping[str, object],
    material_root: Path,
) -> Phase4GraphBoundDeliveryMaterials:
    """Build the sole Phase 4 delivery authority from graph assembler bindings."""

    if not isinstance(graph_state, Mapping):
        raise Phase4FreshDeliveryError("graph state is invalid")
    b_input = graph_state.get("b_input")
    if not isinstance(b_input, Mapping):
        raise Phase4FreshDeliveryError("graph state B input is unavailable")
    case_id = b_input.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise Phase4FreshDeliveryError("graph state case identity is invalid")
    if not isinstance(material_root, Path) or not material_root.is_absolute():
        raise Phase4FreshDeliveryError("material root must be an absolute Path")
    if material_root.exists():
        raise Phase4FreshDeliveryError("material root must be new")
    material_root.mkdir(parents=True, exist_ok=False)

    from req2web_orchestration.phase4_graph import (
        phase4_synthetic_assembler_bindings,
    )

    context, guidance = phase4_synthetic_assembler_bindings(graph_state)
    context.validate()
    guidance.validate()

    package, reference, fallback_record, fallback_snapshot_dir = (
        _build_same_context_g0(
            case_id=case_id,
            context=context,
            guidance=guidance,
            material_root=material_root,
        )
    )
    manifest = D17Path3TierAManifest.create(
        structural_signal_names=PHASE4_COMMERCE_STRUCTURAL_SIGNALS
    )
    selected = select_d17_path3_provider_input(
        context,
        manifest,
        original_requirement_source_class="synthetic",
    )
    local_request = serialize_d17_path3_local_request(
        context,
        selected,
        manifest,
    )
    audit = create_d17_path3_pre_invocation_audit_record(
        context,
        manifest,
        selected,
        local_request,
    )
    preparation = prepare_local_qwen_provider_interface(
        context,
        manifest,
        selected,
        local_request,
        audit,
    )
    live_manifest = _validate_live_manifest(manifest)
    live_selected = _validate_live_selection(selected, context, live_manifest)
    live_request = _validate_live_request(local_request, context, live_manifest)
    live_audit = _validate_live_audit(
        audit,
        context,
        live_manifest,
        live_selected,
        live_request,
    )
    _validate_live_preparation(
        preparation,
        context,
        live_manifest,
        live_selected,
        live_request,
        live_audit,
    )

    scripted_acceptance_fixture = ScriptedAcceptanceFixture.create(
        SCRIPTED_ACCEPTANCE_PASS_KEY
    )
    live = {
        "frozen_g0_reference": reference,
        "package": package,
        "context": context,
        "guidance": guidance,
        "manifest": manifest,
        "selected": selected,
        "local_request": local_request,
        "pre_invocation_audit": audit,
        "local_qwen_preparation": preparation,
        "fallback_record": fallback_record,
        "fallback_snapshot_dir": fallback_snapshot_dir,
        "render_output_dir": material_root / "delivery-render",
        "model_package_output_dir": material_root / "delivery-result-package-v1",
        "fallback_output_dir": material_root / "delivery-fallback",
        "scripted_acceptance_fixture": scripted_acceptance_fixture,
    }
    binding_root = {
        "schema_version": PHASE4_GRAPH_BOUND_DELIVERY_MATERIALS_SCHEMA_VERSION,
        "source_kind": "phase4_graph_assembler_binding",
        "case_id": case_id,
        "context_identity": _json_identity(
            context.to_dict(),
            revision="req2web.agent.context.v1",
        ),
        "guidance_identity": _json_identity(
            guidance.to_dict(),
            revision="req2web.retrieval.guidance.v1",
        ),
        "manifest_identity": _identity(
            manifest.canonical_bytes(),
            revision="req2web.provider.d17_manifest.live.v1",
            identity_kind="raw_bytes",
        ),
        "selected_identity": _json_identity(
            {
                "provider_visible_input": (
                    selected.provider_visible_input.to_dict()
                ),
                "selection_record": selected.selection_record.to_dict(),
            },
            revision="req2web.provider.d17_selected_input.live.v1",
        ),
        "local_request_identity": _identity(
            local_request.canonical_bytes(),
            revision="req2web.provider.d17_local_request.live.v1",
            identity_kind="raw_bytes",
        ),
        "pre_invocation_audit_identity": _identity(
            audit.canonical_bytes(),
            revision="req2web.provider.d17_pre_invocation_audit.live.v1",
            identity_kind="raw_bytes",
        ),
        "local_qwen_preparation_identity": _identity(
            preparation.canonical_bytes(),
            revision="req2web.provider.local_qwen_preparation.live.v1",
            identity_kind="raw_bytes",
        ),
        "frozen_g0_reference_identity": _json_identity(
            reference.to_dict(),
            revision="req2web.evaluation.frozen_g0_reference.live.v1",
        ),
        "fallback_record_identity": _json_identity(
            fallback_record.to_dict(),
            revision="req2web.faults.frozen_g0_fallback.live.v1",
        ),
        "package_id": reference.package_id,
        "page_id": reference.page_id,
        "graph_assembler_bindings_exact": True,
        "model_loaded": False,
        "run_occurred": False,
    }
    binding = {
        **binding_root,
        "binding_id": PHASE4_GRAPH_BOUND_DELIVERY_MATERIALS_ID_PREFIX
        + hashlib.sha256(_canonical_bytes(binding_root)).hexdigest(),
    }
    materials = Phase4GraphBoundDeliveryMaterials(
        material_root=material_root,
        case_id=case_id,
        context=context,
        guidance=guidance,
        live=live,
        binding=binding,
    )
    materials.validate()
    _write_once(
        material_root / "phase4_graph_bound_delivery_materials_binding.json",
        _canonical_bytes(binding),
    )
    return materials


def run_phase4_fresh_delivery(
    *,
    source_root: Path,
    delivery_root: Path,
    context: AgentContextBundle,
    guidance: RetrievalGuidance,
    manifest: object,
    selected: object,
    local_request: object,
    pre_invocation_audit: object,
    local_qwen_preparation: object,
    package: RetrievalEnhancedResultPackage,
    frozen_g0_reference: FrozenG0PackageReference,
    fallback_record: FrozenG0FallbackRecord,
    fallback_snapshot_dir: Path,
    scripted_acceptance_fixture: object | None = None,
) -> Phase4FreshDeliveryReceipt:
    """Validate a fresh source and run existing no-model downstream gates."""

    source = Phase4FreshDeliveryInput.from_result_root(
        source_root,
        context=context,
        guidance=guidance,
    )
    source.validate_context_guidance(context, guidance)
    if source.case_id != fallback_record.case_id:
        raise Phase4FreshDeliveryError("fallback record is cross-case")
    frozen_g0_reference.validate_against(package, context, guidance)
    fallback_record.validate_against(fallback_snapshot_dir)
    if (
        fallback_record.package_id != frozen_g0_reference.package_id
        or fallback_record.page_id != frozen_g0_reference.page_id
    ):
        raise Phase4FreshDeliveryError("fallback record is not bound to same-case G0")

    route = Phase4FreshRouteOutcome.create(source, frozen_g0_reference)
    route.validate_against(
        frozen_g0_reference=frozen_g0_reference,
        package=package,
        context=context,
        guidance=guidance,
    )
    _fresh_route_validate(
        route,
        frozen_g0_reference=frozen_g0_reference,
        package=package,
        context=context,
        guidance=guidance,
        manifest=manifest,
        selected=selected,
        local_request=local_request,
        pre_invocation_audit=pre_invocation_audit,
        local_qwen_preparation=local_qwen_preparation,
    )

    def assembled_page_authority(
        outcome: Phase4FreshRouteOutcome,
        live_context: AgentContextBundle,
        live_guidance: RetrievalGuidance,
    ) -> AssembledPageSpec:
        if outcome.outcome_id != route.outcome_id:
            raise Phase4FreshDeliveryError("fresh route outcome is not source-bound")
        source.validate_context_guidance(live_context, live_guidance)
        return source.assembled

    first_pass, one_repair = _build_phase4_fresh_delivery_runners(
        model_route_type=Phase4FreshRouteOutcome,
        model_route_validate=_fresh_route_validate,
        model_route_structural_validate=Phase4FreshRouteOutcome.validate,
        model_route_canonical_bytes=Phase4FreshRouteOutcome.canonical_bytes,
        assembled_page_authority=assembled_page_authority,
    )
    fixture = _acceptance_fixture(scripted_acceptance_fixture)
    root = Path(delivery_root)
    if root.exists() and root.is_symlink():
        raise Phase4FreshDeliveryError("delivery root must not be a symlink")
    root = root.resolve(strict=False)
    common = {
        "model_route_outcome": route,
        "frozen_g0_reference": frozen_g0_reference,
        "package": package,
        "context": context,
        "guidance": guidance,
        "manifest": manifest,
        "selected": selected,
        "local_request": local_request,
        "pre_invocation_audit": pre_invocation_audit,
        "local_qwen_preparation": local_qwen_preparation,
        "execution_branch": route.execution_branch,
        "scripted_local_fixture": None,
        "case_id": source.case_id,
        "fallback_record": fallback_record,
        "fallback_snapshot_dir": Path(fallback_snapshot_dir),
        "render_output_dir": root / "render",
        "model_package_output_dir": root / "result_package_v1",
        "fallback_output_dir": root / "fallback",
        "scripted_acceptance_fixture": fixture,
    }
    try:
        field_gate_report = create_tier_a_07b_field_gate_report(
            case_id=source.case_id,
            page_spec=source.assembled.page_spec,
            local_request=local_request,
        )
        field_gate_report.validate_against(
            source.case_id,
            source.assembled.page_spec,
            field_gate_report.request_sha256,
        )
    except Exception as exc:
        raise Phase4FreshDeliveryError(
            "fresh delivery field gate failed closed"
        ) from exc

    if field_gate_report.decision == "pass":
        downstream = first_pass(object(), **common)
        downstream_kind = "tier_a_07a"
    elif field_gate_report.decision == "repair" and field_gate_report.repair_eligible:
        if (
            field_gate_report.reported_field is None
            or field_gate_report.expected is None
        ):
            raise Phase4FreshDeliveryError(
                "fresh delivery repair report is incomplete"
            )
        repair_patch = TierA07bRepairPatch.create(
            report_id=field_gate_report.report_id,
            report_sha256=field_gate_report.sha256(),
            first_page_id=source.assembled.page_spec.page_id,
            first_page_spec_sha256=field_gate_report.first_page_spec_sha256,
            attempt_index=1,
            operations=(
                (
                    field_gate_report.reported_field,
                    field_gate_report.expected,
                ),
            ),
        )
        downstream = one_repair(
            object(),
            **common,
            field_gate_report=field_gate_report.canonical_bytes(),
            repair_patch=repair_patch.canonical_bytes(),
        )
        downstream_kind = "tier_a_07b"
    else:
        raise Phase4FreshDeliveryError(
            "fresh delivery field gate returned a non-routable decision"
        )
    receipt = Phase4FreshDeliveryReceipt.create(
        source=source,
        route_outcome=route,
        downstream_outcome=downstream,
        downstream_kind=downstream_kind,
    )
    receipt.validate()
    _write_once(root / "phase4_fresh_route_outcome.json", route.canonical_bytes())
    _write_once(root / "phase4_fresh_delivery_receipt.json", receipt.canonical_bytes())
    return receipt


__all__ = [
    "PHASE4_FRESH_CLAIM_BOUNDARY",
    "PHASE4_FRESH_DELIVERY_RECEIPT_SCHEMA_VERSION",
    "PHASE4_FRESH_DELIVERY_SOURCE_KIND",
    "PHASE4_FRESH_ROUTE_OUTCOME_SCHEMA_VERSION",
    "PHASE4_GRAPH_BOUND_DELIVERY_MATERIALS_SCHEMA_VERSION",
    "Phase4FreshDeliveryError",
    "Phase4FreshDeliveryInput",
    "Phase4FreshDeliveryReceipt",
    "Phase4FreshRouteOutcome",
    "Phase4GraphBoundDeliveryMaterials",
    "build_phase4_graph_bound_delivery_materials",
    "run_phase4_fresh_delivery",
]
