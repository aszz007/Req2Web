"""Phase 5 single-owner metric and claim-scope amendment.

This module freezes evaluator-mode-specific definitions only. It does not read
holdout material, consume gold, calculate formal metrics, select thresholds, or
authorize H1/model/remote execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Mapping, Sequence


SCHEMA_VERSION = "req2web.phase5.single_owner.metric_claim_contract.v1"
FORMAL_AUTHORITY_SHA256 = (
    "d97461ba2d6e76b954081a336c972c878829bddd195379227b27d10a147bca23"
)
FORMAL_PLAN_SHA256 = (
    "d37a0cd7a21031295380ba0ed7dcfe7977d8f05a544915c2ae625dff88b9faf3"
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _record_id(prefix: str, value: object) -> str:
    return f"{prefix}-{sha256(_canonical(value)).hexdigest()}"


def _exact(value: object, keys: Sequence[str], name: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise ValueError(f"{name} has invalid keys")
    return dict(value)


def _digest(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _metric(
    *,
    name: str,
    family: str,
    denominator: str,
    missing_rule: str,
    applicability: str,
    owner_gold_required: bool,
    case_macro_first: bool = True,
) -> dict[str, object]:
    return {
        "name": name,
        "family": family,
        "denominator": denominator,
        "missing_rule": missing_rule,
        "applicability": applicability,
        "owner_gold_required": owner_gold_required,
        "case_macro_first": case_macro_first,
        "formal_value": None,
        "executed": False,
    }


def _expected_body() -> dict[str, object]:
    metrics = [
        _metric(
            name="candidate_attribution_coverage",
            family="evidence_control",
            denominator="all_candidate_decisions_including_invalid_or_unattributed",
            missing_rule="no_valid_candidate_case_value_zero",
            applicability="path1_all_formal_candidates",
            owner_gold_required=False,
        ),
        _metric(
            name="deep_label_attribution_correctness",
            family="evidence_control",
            denominator="preregistered_owner_gold_edges_in_deep_label_cases",
            missing_rule=(
                "no_valid_candidate_precision_undefined_recall_zero_and_gold_edges_fn"
            ),
            applicability="path1_deep_label_cases_only",
            owner_gold_required=True,
        ),
        _metric(
            name="controlled_influence_accuracy",
            family="evidence_control",
            denominator="preregistered_expected_change_units_in_paired_interventions",
            missing_rule="missing_or_incomplete_pair_is_incomplete_experiment",
            applicability="path1_deep_label_paired_interventions_only",
            owner_gold_required=True,
        ),
        _metric(
            name="intervention_locality",
            family="evidence_control",
            denominator="preregistered_expected_stable_units_in_paired_interventions",
            missing_rule="missing_or_incomplete_pair_is_incomplete_experiment",
            applicability="path1_deep_label_paired_interventions_only",
            owner_gold_required=True,
        ),
        _metric(
            name="artifact_provenance_completeness",
            family="evidence_control",
            denominator="all_required_identity_visibility_and_hash_chain_slots",
            missing_rule="missing_required_slot_is_fail",
            applicability="all_formal_rows",
            owner_gold_required=False,
        ),
        _metric(
            name="fault_localization_accuracy",
            family="error_recovery",
            denominator="preregistered_applicable_gold_fault_copies",
            missing_rule="natural_errors_without_gold_are_reported_separately",
            applicability="preregistered_fault_copies_only",
            owner_gold_required=True,
        ),
        _metric(
            name="one_repair_success",
            family="error_recovery",
            denominator="preregistered_gold_repairable_fault_copies",
            missing_rule="not_attempted_or_failed_repair_remains_fail",
            applicability="gold_repairable_fault_copies_only",
            owner_gold_required=True,
        ),
        _metric(
            name="repair_locality",
            family="error_recovery",
            denominator="preregistered_gold_allowed_scope_units",
            missing_rule="missing_delta_or_scope_evidence_is_unknown",
            applicability="executed_repair_rows_only",
            owner_gold_required=True,
        ),
        _metric(
            name="fallback_delivery",
            family="error_recovery",
            denominator="rows_requiring_frozen_same_case_g0_fallback",
            missing_rule="unavailable_or_invalid_fallback_is_failed_delivery",
            applicability="fallback_required_rows_only",
            owner_gold_required=False,
        ),
        _metric(
            name="independent_acceptance_coverage",
            family="functional_acceptance",
            denominator="all_applicable_owner_gold_criteria",
            missing_rule="unknown_and_not_supported_remain_in_denominator",
            applicability="all_core_cases",
            owner_gold_required=True,
        ),
        _metric(
            name="executable_pass_rate",
            family="functional_acceptance",
            denominator="criteria_actually_executed_by_the_frozen_executor",
            missing_rule="not_executed_criteria_are_not_silently_removed_from_coverage",
            applicability="executed_criteria_only",
            owner_gold_required=True,
        ),
        _metric(
            name="all_gold_criterion_success",
            family="functional_acceptance",
            denominator="all_applicable_owner_gold_criteria",
            missing_rule="fail_unknown_and_not_supported_remain_in_denominator",
            applicability="all_core_cases",
            owner_gold_required=True,
        ),
        _metric(
            name="requirement_outcome",
            family="functional_acceptance",
            denominator="all_core_cases",
            missing_rule=(
                "hard_fail_unmet_hard_unknown_or_not_supported_unknown_"
                "soft_nonpass_with_all_hard_pass_partial"
            ),
            applicability="all_core_cases",
            owner_gold_required=True,
        ),
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "single_owner_definitions_and_claim_scope_frozen_no_action",
        "authority_bindings": {
            "formal_authority_sha256": FORMAL_AUTHORITY_SHA256,
            "formal_plan_sha256": FORMAL_PLAN_SHA256,
            "historical_d07_dual_annotation_applies_to_this_run": False,
            "owner_level_single_evaluator_amendment_present": True,
        },
        "evaluator_contract": {
            "evaluator_mode": "single_owner_evaluator",
            "owner_is_only_plaintext_evaluator": True,
            "second_annotator_present": False,
            "inter_rater_agreement_applicable": False,
            "inter_rater_agreement_claim_allowed": False,
            "independent_adjudication_claim_allowed": False,
            "single_evaluator_limitation_required": True,
            "gold_commitment_before_candidate_required": True,
            "manager_or_developer_plaintext_access_allowed": False,
            "runtime_model_gold_access_allowed": False,
        },
        "outcome_accounting": {
            "criterion_statuses": [
                "fail",
                "not_supported",
                "pass",
                "unknown",
            ],
            "requirement_statuses": [
                "met",
                "partial",
                "unmet",
                "unknown",
            ],
            "not_applicable_excluded_only_with_preregistered_reason": True,
            "not_supported_retained_in_applicable_denominator": True,
            "unknown_retained_in_applicable_denominator": True,
            "no_valid_candidate_is_not_dropped": True,
            "incomplete_matrix_row_status": "incomplete_experiment",
            "candidate_output_cannot_define_gold_or_denominator": True,
        },
        "metrics": metrics,
        "claim_matrix": {
            "allowed_if_all_named_preconditions_hold": [
                {
                    "claim": "bounded_path1_formal_feasibility",
                    "preconditions": [
                        "licensed_minimal_real_material_receipts_complete",
                        "sealed_manifest_serializer_and_provider_parity_frozen",
                        "all_formal_rows_terminal_or_incomplete_experiment_reported",
                    ],
                },
                {
                    "claim": "single_owner_case_level_requirement_and_acceptance_results",
                    "preconditions": [
                        "owner_gold_committed_before_candidate_view",
                        "single_evaluator_limitation_reported",
                        "frozen_metric_code_and_applicability_rules_used",
                    ],
                },
                {
                    "claim": "deep_label_attribution_correctness",
                    "preconditions": [
                        "owner_gold_edges_committed_before_candidate_view",
                        "deep_label_case_manifest_frozen",
                        "identity_invalid_claimed_edges_retained",
                    ],
                },
                {
                    "claim": "controlled_influence_and_locality",
                    "preconditions": [
                        "expected_change_and_expected_stable_gold_prefrozen",
                        "paired_intervention_rows_complete",
                        "same_input_schema_config_seed_and_runtime_binding",
                    ],
                },
                {
                    "claim": "artifact_provenance_completeness",
                    "preconditions": [
                        "raw_semantic_assembled_package_hash_chain_complete",
                        "provider_visibility_receipts_complete",
                    ],
                },
            ],
            "always_forbidden": [
                "broad_generalization",
                "dual_annotator_or_inter_rater_reliability",
                "independent_adjudication",
                "all_283_internal_records_effectiveness",
                "production_quality_or_deployment_readiness",
                "real_browser_or_user_efficiency_without_separate_evidence",
                "license_scope_beyond_the_frozen_receipts",
                "formal_path3_h1",
                "result_driven_prompt_schema_metric_threshold_or_case_change",
            ],
            "causal_language_allowed": False,
            "controlled_influence_language_required": True,
        },
        "threshold_contract": {
            "metric_definitions_frozen": True,
            "single_evaluator_claim_scope_frozen": True,
            "numeric_minimum_gain_thresholds_frozen": False,
            "numeric_threshold_status": (
                "pending_owner_approval_after_independent_development_pilot"
            ),
            "formal_h1_may_set_or_tune_thresholds": False,
            "threshold_change_after_candidate_or_h1_result_allowed": False,
        },
        "action_state": {
            "real_h1_or_gold_read": False,
            "formal_metrics_executed": False,
            "model_action_authorized": False,
            "gpu_or_remote_action_authorized": False,
            "holdout_executed": False,
            "formal_quality_claimed": False,
        },
    }


def _validate_payload(value: object) -> dict[str, object]:
    record = _exact(
        value,
        ("contract_id", *_expected_body().keys()),
        "single owner metric claim contract",
    )
    body = {key: record[key] for key in record if key != "contract_id"}
    if body != _expected_body():
        raise ValueError("single owner metric claim contract content drifted")
    if record["contract_id"] != _record_id("phase5-single-owner-protocol", body):
        raise ValueError("single owner metric claim contract id drifted")
    _digest(
        body["authority_bindings"]["formal_authority_sha256"],
        "formal authority hash",
    )
    _digest(
        body["authority_bindings"]["formal_plan_sha256"],
        "formal plan hash",
    )
    return record


@dataclass(frozen=True)
class Phase5SingleOwnerProtocol:
    canonical_json: str
    digest_sha256: str

    @classmethod
    def from_dict(cls, value: object) -> "Phase5SingleOwnerProtocol":
        payload = _validate_payload(value)
        canonical = _canonical(payload)
        result = cls(canonical.decode("utf-8"), sha256(canonical).hexdigest())
        result.validate()
        return result

    @classmethod
    def from_json_bytes(cls, value: bytes) -> "Phase5SingleOwnerProtocol":
        if not isinstance(value, bytes) or not value:
            raise ValueError("single owner protocol JSON must be non-empty bytes")
        try:
            parsed = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("single owner protocol JSON is invalid") from exc
        if _canonical(parsed) != value:
            raise ValueError("single owner protocol JSON is not canonical")
        return cls.from_dict(parsed)

    def validate(self) -> None:
        if not isinstance(self.canonical_json, str) or not self.canonical_json:
            raise ValueError("stored single owner protocol must be non-empty text")
        try:
            parsed = json.loads(self.canonical_json)
        except json.JSONDecodeError as exc:
            raise ValueError("stored single owner protocol JSON is invalid") from exc
        canonical = _canonical(parsed)
        if canonical.decode("utf-8") != self.canonical_json:
            raise ValueError("stored single owner protocol JSON is not canonical")
        if sha256(canonical).hexdigest() != _digest(
            self.digest_sha256,
            "single owner protocol digest",
        ):
            raise ValueError("stored single owner protocol digest drifted")
        _validate_payload(parsed)

    def to_dict(self) -> dict[str, object]:
        self.validate()
        value = json.loads(self.canonical_json)
        if not isinstance(value, dict):
            raise ValueError("stored single owner protocol root is not an object")
        return value

    def canonical_json_bytes(self) -> bytes:
        self.validate()
        return self.canonical_json.encode("utf-8")

    def sha256(self) -> str:
        self.validate()
        return self.digest_sha256


def build_phase5_single_owner_protocol() -> Phase5SingleOwnerProtocol:
    """Build the owner-approved single-evaluator amendment without action."""

    body = _expected_body()
    return Phase5SingleOwnerProtocol.from_dict(
        {"contract_id": _record_id("phase5-single-owner-protocol", body), **body}
    )
