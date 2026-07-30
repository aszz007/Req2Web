"""Bounded deterministic semantic closure for the Qwen3.5 recovery route.

The closure preserves the original Provider bytes and candidate identity.  It may
only complete relations that are uniquely implied by entities already present
in a valid semantic candidate.  It never creates components, states,
interactions, acceptance checks, or requirements.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from req2web_provider.semantic_candidate import ModelSemanticCandidate


QWEN27B_SEMANTIC_CLOSURE_SCHEMA = (
    "req2web.runtime.qwen27b_deterministic_semantic_closure.v1"
)
QWEN27B_SEMANTIC_CLOSURE_REPORT_PREFIX = (
    "qwen27b-deterministic-semantic-closure-v1-"
)
_CASE_IDS = frozenset(
    {"path3-commerce-checkout", "path3-media-analysis"}
)
_RULE_TRIGGER_VISIBILITY = "trigger_visibility_closure_v1"
_RULE_ACCEPTANCE_TRACE = "unique_acceptance_reachability_trace_v1"
_DECISIONS = frozenset({"unchanged", "repaired", "fail_closed"})


class Qwen27bSemanticClosureError(ValueError):
    """Raised when the bounded closure contract is violated."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _json_value(value: object) -> object:
    try:
        return json.loads(_canonical(value).decode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise Qwen27bSemanticClosureError(
            "semantic_closure_operation_value_invalid"
        ) from exc


@dataclass(frozen=True)
class Qwen27bSemanticClosureOperation:
    """One auditable, deterministic relation-closure operation."""

    rule_id: str
    field_path: str
    before: object
    after: object
    derivation_source: str

    def validate(self) -> None:
        if self.rule_id not in {
            _RULE_TRIGGER_VISIBILITY,
            _RULE_ACCEPTANCE_TRACE,
        }:
            raise Qwen27bSemanticClosureError(
                "semantic_closure_operation_rule_invalid"
            )
        if not isinstance(self.field_path, str) or not self.field_path:
            raise Qwen27bSemanticClosureError(
                "semantic_closure_operation_path_invalid"
            )
        if self.derivation_source not in {
            "existing_interaction_trigger_and_source_state",
            "existing_acceptance_target_with_unique_reaching_interaction",
        }:
            raise Qwen27bSemanticClosureError(
                "semantic_closure_operation_source_invalid"
            )
        before = _json_value(self.before)
        after = _json_value(self.after)
        if before == after:
            raise Qwen27bSemanticClosureError(
                "semantic_closure_operation_no_change"
            )

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "rule_id": self.rule_id,
            "field_path": self.field_path,
            "before": _json_value(self.before),
            "after": _json_value(self.after),
            "derivation_source": self.derivation_source,
        }


@dataclass(frozen=True)
class Qwen27bSemanticClosureReport:
    """Canonical receipt for one atomic closure attempt."""

    report_id: str
    case_id: str
    original_candidate_sha256: str
    repaired_candidate_sha256: str | None
    decision: str
    repair_attempted: int
    operations: tuple[Qwen27bSemanticClosureOperation, ...]
    failure_code: str | None
    schema_version: str = QWEN27B_SEMANTIC_CLOSURE_SCHEMA

    def _body(self) -> dict[str, object]:
        return {
            "report_id": QWEN27B_SEMANTIC_CLOSURE_REPORT_PREFIX + "0" * 64,
            "case_id": self.case_id,
            "original_candidate_sha256": self.original_candidate_sha256,
            "repaired_candidate_sha256": self.repaired_candidate_sha256,
            "decision": self.decision,
            "repair_attempted": self.repair_attempted,
            "operations": [item.to_dict() for item in self.operations],
            "failure_code": self.failure_code,
            "schema_version": self.schema_version,
        }

    def validate(self) -> None:
        if self.schema_version != QWEN27B_SEMANTIC_CLOSURE_SCHEMA:
            raise Qwen27bSemanticClosureError(
                "semantic_closure_report_schema_invalid"
            )
        if self.case_id not in _CASE_IDS:
            raise Qwen27bSemanticClosureError(
                "semantic_closure_report_case_invalid"
            )
        if not _is_sha256(self.original_candidate_sha256):
            raise Qwen27bSemanticClosureError(
                "semantic_closure_report_original_hash_invalid"
            )
        if self.repaired_candidate_sha256 is not None and not _is_sha256(
            self.repaired_candidate_sha256
        ):
            raise Qwen27bSemanticClosureError(
                "semantic_closure_report_repaired_hash_invalid"
            )
        if self.decision not in _DECISIONS:
            raise Qwen27bSemanticClosureError(
                "semantic_closure_report_decision_invalid"
            )
        if type(self.repair_attempted) is not int or self.repair_attempted not in {
            0,
            1,
        }:
            raise Qwen27bSemanticClosureError(
                "semantic_closure_report_attempt_invalid"
            )
        if type(self.operations) is not tuple:
            raise Qwen27bSemanticClosureError(
                "semantic_closure_report_operations_invalid"
            )
        for operation in self.operations:
            if type(operation) is not Qwen27bSemanticClosureOperation:
                raise Qwen27bSemanticClosureError(
                    "semantic_closure_report_operations_invalid"
                )
            operation.validate()
        paths = tuple(item.field_path for item in self.operations)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise Qwen27bSemanticClosureError(
                "semantic_closure_report_operation_order_invalid"
            )
        if self.decision == "unchanged":
            valid_state = (
                self.repair_attempted == 0
                and not self.operations
                and self.failure_code is None
                and self.repaired_candidate_sha256
                == self.original_candidate_sha256
            )
        elif self.decision == "repaired":
            valid_state = (
                self.repair_attempted == 1
                and bool(self.operations)
                and self.failure_code is None
                and self.repaired_candidate_sha256 is not None
                and self.repaired_candidate_sha256
                != self.original_candidate_sha256
            )
        else:
            valid_state = (
                self.repair_attempted == 1
                and self.repaired_candidate_sha256 is None
                and isinstance(self.failure_code, str)
                and bool(self.failure_code)
            )
        if not valid_state:
            raise Qwen27bSemanticClosureError(
                "semantic_closure_report_state_invalid"
            )
        expected_id = QWEN27B_SEMANTIC_CLOSURE_REPORT_PREFIX + _sha(
            _canonical(self._body())
        )
        if self.report_id != expected_id:
            raise Qwen27bSemanticClosureError(
                "semantic_closure_report_id_invalid"
            )

    def to_dict(self) -> dict[str, object]:
        self.validate()
        result = self._body()
        result["report_id"] = self.report_id
        return result

    def canonical_bytes(self) -> bytes:
        return _canonical(self.to_dict())

    def sha256(self) -> str:
        return _sha(self.canonical_bytes())


@dataclass(frozen=True)
class Qwen27bSemanticClosureResult:
    """In-memory closure result; the candidate is never confused with raw bytes."""

    candidate: ModelSemanticCandidate | None
    report: Qwen27bSemanticClosureReport

    def validate(self) -> None:
        self.report.validate()
        if self.report.decision == "fail_closed":
            if self.candidate is not None:
                raise Qwen27bSemanticClosureError(
                    "semantic_closure_failed_candidate_present"
                )
            return
        if type(self.candidate) is not ModelSemanticCandidate:
            raise Qwen27bSemanticClosureError(
                "semantic_closure_candidate_type_invalid"
            )
        self.candidate.validate()
        if self.candidate.sha256() != self.report.repaired_candidate_sha256:
            raise Qwen27bSemanticClosureError(
                "semantic_closure_candidate_hash_invalid"
            )


def _make_report(
    *,
    case_id: str,
    original_hash: str,
    repaired_hash: str | None,
    decision: str,
    repair_attempted: int,
    operations: tuple[Qwen27bSemanticClosureOperation, ...],
    failure_code: str | None,
) -> Qwen27bSemanticClosureReport:
    placeholder = Qwen27bSemanticClosureReport(
        report_id=QWEN27B_SEMANTIC_CLOSURE_REPORT_PREFIX + "0" * 64,
        case_id=case_id,
        original_candidate_sha256=original_hash,
        repaired_candidate_sha256=repaired_hash,
        decision=decision,
        repair_attempted=repair_attempted,
        operations=operations,
        failure_code=failure_code,
    )
    report = Qwen27bSemanticClosureReport(
        report_id=QWEN27B_SEMANTIC_CLOSURE_REPORT_PREFIX
        + _sha(_canonical(placeholder._body())),
        case_id=case_id,
        original_candidate_sha256=original_hash,
        repaired_candidate_sha256=repaired_hash,
        decision=decision,
        repair_attempted=repair_attempted,
        operations=operations,
        failure_code=failure_code,
    )
    report.validate()
    return report


def apply_qwen27b_deterministic_semantic_closure(
    *,
    case_id: str,
    candidate: ModelSemanticCandidate,
) -> Qwen27bSemanticClosureResult:
    """Apply one atomic, bounded relation closure to an existing candidate."""

    if case_id not in _CASE_IDS:
        raise Qwen27bSemanticClosureError("semantic_closure_case_not_registered")
    if type(candidate) is not ModelSemanticCandidate:
        raise Qwen27bSemanticClosureError("semantic_closure_candidate_type_invalid")
    candidate.validate()
    original_hash = candidate.sha256()
    payload = deepcopy(candidate.to_dict())
    operations: list[Qwen27bSemanticClosureOperation] = []

    states = {item["stable_id"]: item for item in payload["states"]}
    for interaction in payload["interactions"]:
        source = states[interaction["source_state_stable_id"]]
        trigger_id = interaction["trigger_component_stable_id"]
        visible = list(source["visible_component_stable_ids"])
        if trigger_id not in visible:
            updated = [*visible, trigger_id]
            source["visible_component_stable_ids"] = updated
            operations.append(
                Qwen27bSemanticClosureOperation(
                    rule_id=_RULE_TRIGGER_VISIBILITY,
                    field_path=(
                        f"states[{source['stable_id']}]."
                        "visible_component_stable_ids"
                    ),
                    before=visible,
                    after=updated,
                    derivation_source=(
                        "existing_interaction_trigger_and_source_state"
                    ),
                )
            )

    interactions = {
        item["stable_id"]: item for item in payload["interactions"]
    }
    mappings = {
        item["use_case_id"]: item for item in payload["use_case_mappings"]
    }
    original_interaction_use_cases = {
        stable_id: list(item["use_case_ids"])
        for stable_id, item in interactions.items()
    }
    original_mapping_interactions = {
        use_case_id: list(item["interaction_stable_ids"])
        for use_case_id, item in mappings.items()
    }
    interaction_order = {
        item["stable_id"]: index
        for index, item in enumerate(payload["interactions"])
    }
    for acceptance in payload["acceptance_checks"]:
        reaching_ids = [
            item["stable_id"]
            for item in payload["interactions"]
            if item["target_state_stable_id"]
            == acceptance["state_stable_id"]
        ]
        for use_case_id in acceptance["use_case_ids"]:
            if any(
                use_case_id in interactions[interaction_id]["use_case_ids"]
                for interaction_id in reaching_ids
            ):
                continue
            if len(reaching_ids) != 1:
                report = _make_report(
                    case_id=case_id,
                    original_hash=original_hash,
                    repaired_hash=None,
                    decision="fail_closed",
                    repair_attempted=1,
                    operations=tuple(
                        sorted(operations, key=lambda item: item.field_path)
                    ),
                    failure_code="acceptance_target_interaction_not_unique",
                )
                result = Qwen27bSemanticClosureResult(
                    candidate=None,
                    report=report,
                )
                result.validate()
                return result
            interaction = interactions[reaching_ids[0]]
            if use_case_id not in interaction["use_case_ids"]:
                interaction["use_case_ids"].append(use_case_id)
            mapping = mappings[use_case_id]
            if interaction["stable_id"] not in mapping[
                "interaction_stable_ids"
            ]:
                mapping["interaction_stable_ids"] = sorted(
                    [
                        *mapping["interaction_stable_ids"],
                        interaction["stable_id"],
                    ],
                    key=interaction_order.__getitem__,
                )

    for stable_id, interaction in interactions.items():
        before = original_interaction_use_cases[stable_id]
        after = list(interaction["use_case_ids"])
        if before != after:
            operations.append(
                Qwen27bSemanticClosureOperation(
                    rule_id=_RULE_ACCEPTANCE_TRACE,
                    field_path=f"interactions[{stable_id}].use_case_ids",
                    before=before,
                    after=after,
                    derivation_source=(
                        "existing_acceptance_target_with_unique_reaching_interaction"
                    ),
                )
            )
    for use_case_id, mapping in mappings.items():
        before = original_mapping_interactions[use_case_id]
        after = list(mapping["interaction_stable_ids"])
        if before != after:
            operations.append(
                Qwen27bSemanticClosureOperation(
                    rule_id=_RULE_ACCEPTANCE_TRACE,
                    field_path=(
                        f"use_case_mappings[{use_case_id}]."
                        "interaction_stable_ids"
                    ),
                    before=before,
                    after=after,
                    derivation_source=(
                        "existing_acceptance_target_with_unique_reaching_interaction"
                    ),
                )
            )

    ordered_operations = tuple(
        sorted(operations, key=lambda item: item.field_path)
    )
    try:
        repaired = ModelSemanticCandidate.from_dict(payload)
    except Exception as exc:
        report = _make_report(
            case_id=case_id,
            original_hash=original_hash,
            repaired_hash=None,
            decision="fail_closed",
            repair_attempted=1,
            operations=ordered_operations,
            failure_code="repaired_candidate_reparse_failed",
        )
        result = Qwen27bSemanticClosureResult(candidate=None, report=report)
        result.validate()
        return result

    repaired_hash = repaired.sha256()
    decision = "repaired" if ordered_operations else "unchanged"
    report = _make_report(
        case_id=case_id,
        original_hash=original_hash,
        repaired_hash=repaired_hash,
        decision=decision,
        repair_attempted=1 if ordered_operations else 0,
        operations=ordered_operations,
        failure_code=None,
    )
    result = Qwen27bSemanticClosureResult(candidate=repaired, report=report)
    result.validate()
    return result


__all__ = (
    "QWEN27B_SEMANTIC_CLOSURE_SCHEMA",
    "Qwen27bSemanticClosureError",
    "Qwen27bSemanticClosureOperation",
    "Qwen27bSemanticClosureReport",
    "Qwen27bSemanticClosureResult",
    "apply_qwen27b_deterministic_semantic_closure",
)