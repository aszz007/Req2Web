"""Versioned AgentContextBundle -> Phase 4 canonical-B adapter."""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Mapping

from req2web_agent import AgentContextBundle

from .phase4_graph import validate_b_input


ADAPTER_SCHEMA_VERSION = "req2web.phase4.agent_context_to_canonical_b.v1"
ADAPTER_REVISION = "raw_requirement_actual_retriever_bridge_v1"


class Phase4CanonicalBAdapterError(ValueError):
    """Raised when the formal upstream cannot bind to Phase 4 canonical B."""


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
        raise Phase4CanonicalBAdapterError(
            "canonical-B adapter value is not canonical JSON"
        ) from exc


def _identity(value: object, *, revision: str) -> dict[str, object]:
    raw = value if type(value) is bytes else _canonical(value)
    return {
        "identity_kind": "raw_bytes" if type(value) is bytes else "canonical_json",
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "revision": revision,
    }


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise Phase4CanonicalBAdapterError(f"{name} must be canonical text")
    return value


def _text_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list):
        raise Phase4CanonicalBAdapterError(f"{name} must be an array")
    rows = [_text(item, name) for item in value]
    if len(rows) != len(set(rows)):
        raise Phase4CanonicalBAdapterError(f"{name} must be duplicate-free")
    return rows


@dataclass(frozen=True)
class Phase4CanonicalBAdaptation:
    b_input: dict[str, object]
    receipt: dict[str, object]

    def validate(
        self,
        *,
        context: AgentContextBundle,
        explicit_target_device: str,
        explicit_task_type: str,
        explicit_user_constraints: list[str],
    ) -> None:
        context.validate()
        b_input = validate_b_input(copy.deepcopy(self.b_input))
        receipt = self.receipt
        expected_keys = {
            "schema_version",
            "adapter_revision",
            "case_id",
            "request_id",
            "raw_requirement_identity",
            "explicit_user_input_identity",
            "agent_context_identity",
            "canonical_b_identity",
            "field_origins",
            "prewritten_b_fields_consumed",
            "receipt_identity",
        }
        if set(receipt) != expected_keys:
            raise Phase4CanonicalBAdapterError("adapter receipt keys drifted")
        if (
            receipt["schema_version"] != ADAPTER_SCHEMA_VERSION
            or receipt["adapter_revision"] != ADAPTER_REVISION
            or receipt["case_id"] != b_input["case_id"]
            or receipt["request_id"] != b_input["request_id"]
            or receipt["raw_requirement_identity"]
            != _identity(
                context.original_requirement.encode("utf-8"),
                revision="project_authored_raw_requirement.v1",
            )
            or receipt["explicit_user_input_identity"]
            != _identity(
                {
                    "target_device": explicit_target_device,
                    "task_type": explicit_task_type,
                    "constraints": explicit_user_constraints,
                },
                revision="project_authored_explicit_user_fields.v1",
            )
            or receipt["agent_context_identity"]
            != _identity(
                context.to_dict(),
                revision="req2web.agent.context.v1",
            )
            or receipt["canonical_b_identity"]
            != _identity(b_input, revision="canonical_b.p4.v1")
            or receipt["prewritten_b_fields_consumed"] is not False
        ):
            raise Phase4CanonicalBAdapterError("adapter receipt binding drifted")
        expected_origins = {
            "requirement": "raw_user_input",
            "target_device": "explicit_user_input_then_provider_validation",
            "task_type": "explicit_user_input_then_provider_validation",
            "requirement_summary": "RequirementUnderstandingProvider",
            "constraints": (
                "RequirementUnderstandingProvider_explicit_plus_detected"
            ),
            "use_cases": "RequirementUnderstandingProvider",
            "case_id": "runner_case_inventory",
            "request_id": "runner_case_inventory",
        }
        if receipt["field_origins"] != expected_origins:
            raise Phase4CanonicalBAdapterError("adapter field origins drifted")
        receipt_root = {
            key: value for key, value in receipt.items() if key != "receipt_identity"
        }
        if receipt["receipt_identity"] != _identity(
            receipt_root,
            revision=ADAPTER_SCHEMA_VERSION,
        ):
            raise Phase4CanonicalBAdapterError("adapter receipt identity drifted")
        if (
            b_input["requirement"] != context.original_requirement
            or b_input["requirement_summary"] != context.requirement_summary
            or b_input["target_device"] != context.target_device
            or b_input["task_type"] != context.task_type
            or b_input["constraints"] != context.constraints
            or b_input["use_cases"]
            != [asdict(item) for item in context.use_cases]
            or any(item not in context.constraints for item in explicit_user_constraints)
            or context.target_device != explicit_target_device
            or context.task_type != explicit_task_type
        ):
            raise Phase4CanonicalBAdapterError(
                "canonical B differs from the live AgentContext"
            )


def adapt_agent_context_to_phase4_canonical_b(
    *,
    context: AgentContextBundle,
    case_id: str,
    request_id: str,
    explicit_target_device: str,
    explicit_task_type: str,
    explicit_user_constraints: list[str],
) -> Phase4CanonicalBAdaptation:
    """Adapt only live upstream output; no prewritten B field is accepted."""

    context.validate()
    case_id = _text(case_id, "case_id")
    request_id = _text(request_id, "request_id")
    target_device = _text(explicit_target_device, "explicit_target_device")
    task_type = _text(explicit_task_type, "explicit_task_type")
    constraints = _text_list(
        explicit_user_constraints,
        "explicit_user_constraints",
    )
    if any(item not in context.constraints for item in constraints):
        raise Phase4CanonicalBAdapterError(
            "explicit user constraints are not bound into AgentContext"
        )
    if context.target_device != target_device or context.task_type != task_type:
        raise Phase4CanonicalBAdapterError(
            "explicit target device or task type differs from AgentContext"
        )
    b_input = validate_b_input(
        {
            "case_id": case_id,
            "request_id": request_id,
            "requirement": context.original_requirement,
            "requirement_summary": context.requirement_summary,
            "target_device": context.target_device,
            "task_type": context.task_type,
            "constraints": copy.deepcopy(context.constraints),
            "use_cases": [asdict(item) for item in context.use_cases],
        }
    )
    receipt_root = {
        "schema_version": ADAPTER_SCHEMA_VERSION,
        "adapter_revision": ADAPTER_REVISION,
        "case_id": case_id,
        "request_id": request_id,
        "raw_requirement_identity": _identity(
            context.original_requirement.encode("utf-8"),
            revision="project_authored_raw_requirement.v1",
        ),
        "explicit_user_input_identity": _identity(
            {
                "target_device": target_device,
                "task_type": task_type,
                "constraints": constraints,
            },
            revision="project_authored_explicit_user_fields.v1",
        ),
        "agent_context_identity": _identity(
            context.to_dict(),
            revision="req2web.agent.context.v1",
        ),
        "canonical_b_identity": _identity(
            b_input,
            revision="canonical_b.p4.v1",
        ),
        "field_origins": {
            "requirement": "raw_user_input",
            "target_device": "explicit_user_input_then_provider_validation",
            "task_type": "explicit_user_input_then_provider_validation",
            "requirement_summary": "RequirementUnderstandingProvider",
            "constraints": (
                "RequirementUnderstandingProvider_explicit_plus_detected"
            ),
            "use_cases": "RequirementUnderstandingProvider",
            "case_id": "runner_case_inventory",
            "request_id": "runner_case_inventory",
        },
        "prewritten_b_fields_consumed": False,
    }
    result = Phase4CanonicalBAdaptation(
        b_input=copy.deepcopy(b_input),
        receipt={
            **receipt_root,
            "receipt_identity": _identity(
                receipt_root,
                revision=ADAPTER_SCHEMA_VERSION,
            ),
        },
    )
    result.validate(
        context=context,
        explicit_target_device=target_device,
        explicit_task_type=task_type,
        explicit_user_constraints=constraints,
    )
    return result


__all__ = [
    "ADAPTER_REVISION",
    "ADAPTER_SCHEMA_VERSION",
    "Phase4CanonicalBAdaptation",
    "Phase4CanonicalBAdapterError",
    "adapt_agent_context_to_phase4_canonical_b",
]
