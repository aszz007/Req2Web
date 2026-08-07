"""Frozen raw-only cases for the canonical Phase 4 full-flow remediation."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Mapping


CASE_SET_SCHEMA_VERSION = "req2web.phase4.canonical_full_flow_cases.v1"
CASE_SET_ID = "p4-canonical-full-flow-raw-commerce-10"
CASE_COUNT = 10
CANARY_COUNT = 3
SUPPORTED_SCOPE = "single_page_single_primary_task"
_CASE_KEYS = (
    "case_id",
    "request_id",
    "requirement",
    "target_device",
    "task_type",
    "constraints",
)
_FORBIDDEN_PREWRITTEN_KEYS = frozenset(
    {
        "requirement_summary",
        "use_cases",
        "retrieval_queries",
        "retrieval_results",
        "agent_context",
        "retrieval_guidance",
        "b_input",
    }
)
_OUT_OF_SCOPE_MARKERS = (
    "complete enterprise system",
    "entire enterprise system",
    "multi-tenant platform",
    "all business modules",
    "\u5b8c\u6574\u4f01\u4e1a\u7cfb\u7edf",
    "\u5b8c\u6574\u533b\u9662\u7ba1\u7406\u7cfb\u7edf",
    "\u5b8c\u6574\u5b66\u6821\u7ba1\u7406\u7cfb\u7edf",
    "\u591a\u79df\u6237\u5e73\u53f0",
    "\u5168\u90e8\u4e1a\u52a1\u6a21\u5757",
)
_MAX_REQUIREMENT_CHARACTERS = 1_200


class Phase4CanonicalFullFlowCaseError(ValueError):
    """Raised when a remediation case is not raw-only or in project scope."""


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
        raise Phase4CanonicalFullFlowCaseError(
            "case value is not canonical JSON"
        ) from exc


def _identity(value: object) -> dict[str, object]:
    raw = _canonical(value)
    return {
        "identity_kind": "canonical_json",
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "revision": CASE_SET_SCHEMA_VERSION,
    }


def _case(
    number: int,
    slug: str,
    requirement: str,
    target_device: str,
) -> dict[str, object]:
    return {
        "case_id": f"p4-full-flow-{number:02d}-{slug}",
        "request_id": f"p4-full-flow-request-{number:02d}",
        "requirement": requirement,
        "target_device": target_device,
        "task_type": "ecommerce",
        "constraints": [
            "Use project-authored synthetic catalog data only.",
            "Keep search, selection, cart, checkout, and feedback on one page.",
            "Preserve valid form fields after inline validation errors.",
        ],
    }


_CASES = (
    _case(
        1,
        "grocery",
        "Create a single-page grocery checkout flow where shoppers search or "
        "filter groceries, select items into a cart, complete checkout, and "
        "recover from inline validation errors.",
        "mobile",
    ),
    _case(
        2,
        "apparel",
        "Create a single-page apparel checkout flow where shoppers search or "
        "filter clothing, select size and color into a cart, complete checkout, "
        "and recover from inline validation errors.",
        "desktop",
    ),
    _case(
        3,
        "electronics",
        "Create a single-page electronics checkout flow where shoppers search "
        "or filter devices, select a product into a cart, complete checkout, "
        "and recover from inline validation errors.",
        "tablet",
    ),
    _case(
        4,
        "books",
        "Create a single-page book checkout flow where shoppers search or "
        "filter books, select titles into a cart, complete checkout, and "
        "recover from inline validation errors.",
        "mobile",
    ),
    _case(
        5,
        "pet",
        "Create a single-page pet-supply checkout flow where shoppers search "
        "or filter supplies, select products into a cart, complete checkout, "
        "and recover from inline validation errors.",
        "tablet",
    ),
    _case(
        6,
        "home",
        "Create a single-page home-goods checkout flow where shoppers search "
        "or filter products, select room items into a cart, complete checkout, "
        "and recover from inline validation errors.",
        "desktop",
    ),
    _case(
        7,
        "cosmetics",
        "Create a single-page cosmetics checkout flow where shoppers search or "
        "filter products, select shades into a cart, complete checkout, and "
        "recover from inline validation errors.",
        "mobile",
    ),
    _case(
        8,
        "sports",
        "Create a single-page sports-equipment checkout flow where shoppers "
        "search or filter equipment, select items into a cart, complete "
        "checkout, and recover from inline validation errors.",
        "desktop",
    ),
    _case(
        9,
        "meal-kit",
        "Create a single-page meal-kit checkout flow where shoppers search or "
        "filter kits, select meals into a cart, complete checkout, and recover "
        "from inline validation errors.",
        "tablet",
    ),
    _case(
        10,
        "office",
        "Create a single-page office-supply checkout flow where shoppers search "
        "or filter supplies, select bundles into a cart, complete checkout, "
        "and recover from inline validation errors.",
        "mobile",
    ),
)


def validate_supported_requirement_scope(
    requirement: object,
) -> dict[str, object]:
    if (
        not isinstance(requirement, str)
        or not requirement
        or requirement != requirement.strip()
    ):
        raise Phase4CanonicalFullFlowCaseError(
            "raw requirement must be canonical non-empty text"
        )
    lowered = requirement.casefold()
    rejection_reason = None
    if len(requirement) > _MAX_REQUIREMENT_CHARACTERS:
        rejection_reason = "requirement_length_exceeds_research_scope"
    elif any(marker in lowered for marker in _OUT_OF_SCOPE_MARKERS):
        rejection_reason = "multi_system_or_multi_module_scope"
    return {
        "schema_version": "req2web.phase4.requirement_scope_check.v1",
        "scope": SUPPORTED_SCOPE,
        "supported": rejection_reason is None,
        "rejection_reason": rejection_reason,
        "requirement_identity": {
            "identity_kind": "raw_bytes",
            "sha256": "sha256:"
            + hashlib.sha256(requirement.encode("utf-8")).hexdigest(),
            "byte_length": len(requirement.encode("utf-8")),
            "revision": "project_authored_raw_requirement.v1",
        },
        "model_call_allowed": rejection_reason is None,
    }


def validate_raw_case(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or tuple(value) != _CASE_KEYS:
        raise Phase4CanonicalFullFlowCaseError("raw case exact keys drifted")
    if _FORBIDDEN_PREWRITTEN_KEYS & set(value):
        raise Phase4CanonicalFullFlowCaseError(
            "raw case contains a prewritten upstream result"
        )
    row = copy.deepcopy(dict(value))
    for key in (
        "case_id",
        "request_id",
        "requirement",
        "target_device",
        "task_type",
    ):
        if (
            not isinstance(row[key], str)
            or not row[key]
            or row[key] != row[key].strip()
        ):
            raise Phase4CanonicalFullFlowCaseError(
                f"raw case {key} is invalid"
            )
    constraints = row["constraints"]
    if (
        not isinstance(constraints, list)
        or not constraints
        or any(
            not isinstance(item, str)
            or not item
            or item != item.strip()
            for item in constraints
        )
        or len(constraints) != len(set(constraints))
    ):
        raise Phase4CanonicalFullFlowCaseError(
            "raw case constraints are invalid"
        )
    scope = validate_supported_requirement_scope(row["requirement"])
    if scope["supported"] is not True:
        raise Phase4CanonicalFullFlowCaseError(
            "frozen case is outside the supported research scope"
        )
    return row


def get_case_set() -> dict[str, object]:
    cases = [validate_raw_case(row) for row in _CASES]
    if len(cases) != CASE_COUNT:
        raise Phase4CanonicalFullFlowCaseError("case count drifted")
    if len({str(row["case_id"]) for row in cases}) != CASE_COUNT:
        raise Phase4CanonicalFullFlowCaseError("case IDs are duplicated")
    if len({str(row["request_id"]) for row in cases}) != CASE_COUNT:
        raise Phase4CanonicalFullFlowCaseError("request IDs are duplicated")
    root = {
        "schema_version": CASE_SET_SCHEMA_VERSION,
        "case_set_id": CASE_SET_ID,
        "case_count": CASE_COUNT,
        "canary_count": CANARY_COUNT,
        "supported_scope": SUPPORTED_SCOPE,
        "case_order": [row["case_id"] for row in cases],
        "prewritten_upstream_fields_present": False,
        "cases": cases,
    }
    return {**root, "case_set_identity": _identity(root)}


__all__ = [
    "CANARY_COUNT",
    "CASE_COUNT",
    "CASE_SET_ID",
    "CASE_SET_SCHEMA_VERSION",
    "Phase4CanonicalFullFlowCaseError",
    "SUPPORTED_SCOPE",
    "get_case_set",
    "validate_raw_case",
    "validate_supported_requirement_scope",
]
