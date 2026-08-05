"""Canonical synthetic commerce cases for the Phase 4 P4-05 stability run.

The case set is intentionally narrower than a general benchmark. Every case
belongs to the project-authored, Path 3 non-H1, single-page commerce family
currently supported by the P4-05 graph-bound delivery authority:

    search/filter -> product selection/cart -> checkout/submit -> inline
    validation recovery

This module owns only the versioned stability-case inventory and its validation.
It does not change the existing canonical B schema, F1-F4 schemas, PageSpec,
delivery authority, or model/runtime behavior.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

from req2web_orchestration.phase4_graph import Phase4ContractError, validate_b_input


CASE_SET_SCHEMA_VERSION = "req2web.phase4.p4_05.stability_case_set.v1"
CASE_SET_ID = "p4-05-commerce-stability-10"
CASE_COUNT = 10

_CASE_SET_KEYS = (
    "schema_version",
    "case_set_id",
    "case_order",
    "cases",
    "case_set_identity",
)
_B_INPUT_KEYS = (
    "case_id",
    "request_id",
    "requirement",
    "requirement_summary",
    "target_device",
    "task_type",
    "constraints",
    "use_cases",
)
_USE_CASE_KEYS = (
    "use_case_id",
    "title",
    "actor",
    "goal",
    "expected_outcome",
)
_CASE_ID_PATTERN = re.compile(r"^p4-05-stability-\d{2}-[a-z0-9-]+$")
_REQUEST_ID_PATTERN = re.compile(r"^p4-05-stability-request-\d{2}$")
_IDENTITY_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_ALLOWED_DEVICES = frozenset({"mobile", "desktop", "tablet"})
_ALLOWED_TASK_TYPES = frozenset({"ecommerce", "commerce"})
_FORBIDDEN_TERMS = (
    "rag",
    "evidence",
    "h1",
    "gold",
    "reference",
    "references",
    "asset",
    "assets",
    "screenshot",
    "screenshots",
    "retrieval",
    "ground-truth",
    "ground truth",
)
_REQUIRED_SIGNAL_GROUPS = (
    ("search", "filter"),
    ("select", "cart"),
    ("checkout", "submit"),
    ("inline", "validation"),
)


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Phase4ContractError("stability case value is not canonical JSON") from exc


def _identity(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _exact_object(value: object, keys: tuple[str, ...], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or tuple(value) != keys:
        raise Phase4ContractError(f"{name} exact keys are invalid")
    return value


def _text(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\ufffd" in value
    ):
        raise Phase4ContractError(f"{name} must be non-empty canonical text")
    return value


def _string_list(value: object, name: str, *, minimum: int = 1) -> list[str]:
    if not isinstance(value, list) or len(value) < minimum:
        raise Phase4ContractError(f"{name} must contain at least {minimum} rows")
    result = [_text(item, name) for item in value]
    if len(result) != len(set(result)):
        raise Phase4ContractError(f"{name} must be duplicate-free")
    return result


def _all_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for key, item in value.items():
            result.extend(_all_strings(key))
            result.extend(_all_strings(item))
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(_all_strings(item))
        return result
    return []


def _validate_project_authored_commerce_scope(case: dict[str, Any]) -> None:
    case_id = str(case["case_id"])
    request_id = str(case["request_id"])
    if not _CASE_ID_PATTERN.fullmatch(case_id):
        raise Phase4ContractError("stability case_id is not versioned and stable")
    if not _REQUEST_ID_PATTERN.fullmatch(request_id):
        raise Phase4ContractError("stability request_id is not versioned and stable")
    if case["target_device"] not in _ALLOWED_DEVICES:
        raise Phase4ContractError("stability target_device is not supported")
    if case["task_type"] not in _ALLOWED_TASK_TYPES:
        raise Phase4ContractError("stability task_type must remain commerce-compatible")

    text = " ".join(item.casefold() for item in _all_strings(case))
    for term in _FORBIDDEN_TERMS:
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text):
            raise Phase4ContractError(f"stability case contains forbidden term: {term}")
    for alternatives in _REQUIRED_SIGNAL_GROUPS:
        if not any(
            re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text)
            for term in alternatives
        ):
            raise Phase4ContractError(
                "stability case does not contain every required commerce signal"
            )


def validate_stability_case(value: object) -> dict[str, Any]:
    """Validate one canonical B input and return a defensive deep copy."""

    data = _exact_object(value, _B_INPUT_KEYS, "stability case")
    validate_b_input(data)
    _validate_project_authored_commerce_scope(data)
    if not 2 <= len(data["use_cases"]) <= 4:
        raise Phase4ContractError("stability case must contain two to four use cases")
    for index, use_case in enumerate(data["use_cases"]):
        _exact_object(use_case, _USE_CASE_KEYS, f"stability case use_cases[{index}]")
    return copy.deepcopy(data)


def _case_set_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": data["schema_version"],
        "case_set_id": data["case_set_id"],
        "case_order": copy.deepcopy(data["case_order"]),
        "cases": copy.deepcopy(data["cases"]),
    }


def validate_stability_case_set(value: object) -> dict[str, Any]:
    """Validate the complete versioned ten-case inventory and its identity."""

    data = _exact_object(value, _CASE_SET_KEYS, "stability case set")
    if data["schema_version"] != CASE_SET_SCHEMA_VERSION:
        raise Phase4ContractError("stability case-set schema version is invalid")
    if data["case_set_id"] != CASE_SET_ID:
        raise Phase4ContractError("stability case-set ID is invalid")
    if not isinstance(data["case_order"], list) or len(data["case_order"]) != CASE_COUNT:
        raise Phase4ContractError("stability case_order must contain exactly ten IDs")
    if not isinstance(data["cases"], list) or len(data["cases"]) != CASE_COUNT:
        raise Phase4ContractError("stability cases must contain exactly ten rows")
    if not isinstance(data["case_set_identity"], str) or not _IDENTITY_PATTERN.fullmatch(
        data["case_set_identity"]
    ):
        raise Phase4ContractError("stability case-set identity is invalid")

    validated_cases = [validate_stability_case(item) for item in data["cases"]]
    case_ids = [str(item["case_id"]) for item in validated_cases]
    request_ids = [str(item["request_id"]) for item in validated_cases]
    if len(set(case_ids)) != CASE_COUNT:
        raise Phase4ContractError("stability case IDs must be unique")
    if len(set(request_ids)) != CASE_COUNT:
        raise Phase4ContractError("stability request IDs must be unique")
    if data["case_order"] != case_ids:
        raise Phase4ContractError("stability case order does not match case rows")
    if set(item["target_device"] for item in validated_cases) != _ALLOWED_DEVICES:
        raise Phase4ContractError("stability case set must cover mobile, desktop, and tablet")
    if _identity(_case_set_payload(data)) != data["case_set_identity"]:
        raise Phase4ContractError("stability case-set identity drift")

    result = copy.deepcopy(data)
    result["cases"] = validated_cases
    return result


def canonical_case_bytes(value: object) -> bytes:
    """Return canonical UTF-8 bytes for one validated B input."""

    return _canonical_json_bytes(validate_stability_case(value))


def canonical_case_set_bytes(value: object | None = None) -> bytes:
    """Return canonical UTF-8 bytes excluding the derived set identity."""

    data = get_stability_case_set() if value is None else validate_stability_case_set(value)
    return _canonical_json_bytes(_case_set_payload(data))


def canonical_case_set_record_bytes(value: object | None = None) -> bytes:
    """Return the complete replayable case-set record including its identity."""

    data = get_stability_case_set() if value is None else validate_stability_case_set(value)
    return _canonical_json_bytes(data)


def get_stability_case_set() -> dict[str, Any]:
    """Return the complete case set as a defensive deep copy."""

    return copy.deepcopy(_CASE_SET)


def get_stability_cases() -> tuple[dict[str, Any], ...]:
    """Return cases in canonical order as defensive deep copies."""

    return tuple(copy.deepcopy(item) for item in _CASE_SET["cases"])


def get_stability_case(case_id: str) -> dict[str, Any]:
    """Return one case by stable ID as a defensive deep copy."""

    for case in _CASE_SET["cases"]:
        if case["case_id"] == case_id:
            return copy.deepcopy(case)
    raise KeyError(case_id)


def case_set_identity(value: object | None = None) -> str:
    """Return the derived identity of the canonical case set or a valid copy."""

    data = get_stability_case_set() if value is None else validate_stability_case_set(value)
    return _identity(_case_set_payload(data))


def _make_case(
    *,
    case_id: str,
    request_id: str,
    domain: str,
    target_device: str,
    requirement: str,
    requirement_summary: str,
    use_cases: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "request_id": request_id,
        "requirement": requirement,
        "requirement_summary": requirement_summary,
        "target_device": target_device,
        "task_type": "ecommerce",
        "constraints": [
            "Use project-authored synthetic catalog data only.",
            "Keep search, selection, cart, checkout, and submit feedback on one page.",
            "Preserve valid form fields when inline validation reports an error.",
        ],
        "use_cases": use_cases,
    }


_RAW_CASES = (
    _make_case(
        case_id="p4-05-stability-01-grocery",
        request_id="p4-05-stability-request-01",
        domain="grocery",
        target_device="mobile",
        requirement=(
            "Create a synthetic single-page grocery checkout flow where shoppers "
            "search or filter groceries, select items into a cart, complete checkout, "
            "and recover from inline validation errors."
        ),
        requirement_summary="Mobile grocery search, cart, checkout, and recoverable validation flow.",
        use_cases=[
            {
                "use_case_id": "UC-01",
                "title": "Find groceries",
                "actor": "Shopper",
                "goal": "Search or filter grocery products",
                "expected_outcome": "Matching groceries are visible and selectable",
            },
            {
                "use_case_id": "UC-02",
                "title": "Build grocery cart",
                "actor": "Shopper",
                "goal": "Select products and update quantities in a cart",
                "expected_outcome": "Chosen products and quantities remain visible",
            },
            {
                "use_case_id": "UC-03",
                "title": "Submit grocery order",
                "actor": "Shopper",
                "goal": "Complete checkout and submit the delivery form",
                "expected_outcome": "Inline errors appear without clearing valid fields",
            },
        ],
    ),
    _make_case(
        case_id="p4-05-stability-02-apparel",
        request_id="p4-05-stability-request-02",
        domain="apparel",
        target_device="desktop",
        requirement=(
            "Create a synthetic single-page apparel checkout flow where shoppers "
            "search or filter clothing, select size and color into a cart, complete "
            "checkout, and recover from inline validation errors."
        ),
        requirement_summary="Desktop apparel search, cart, checkout, and recoverable validation flow.",
        use_cases=[
            {
                "use_case_id": "UC-01",
                "title": "Find apparel",
                "actor": "Shopper",
                "goal": "Search or filter clothing products",
                "expected_outcome": "Matching clothing is visible and selectable",
            },
            {
                "use_case_id": "UC-02",
                "title": "Choose apparel options",
                "actor": "Shopper",
                "goal": "Select size and color and add apparel to a cart",
                "expected_outcome": "Selected options and cart items remain visible",
            },
            {
                "use_case_id": "UC-03",
                "title": "Submit apparel order",
                "actor": "Shopper",
                "goal": "Complete checkout and submit the shipping form",
                "expected_outcome": "Inline errors appear without clearing valid fields",
            },
        ],
    ),
    _make_case(
        case_id="p4-05-stability-03-electronics",
        request_id="p4-05-stability-request-03",
        domain="electronics",
        target_device="tablet",
        requirement=(
            "Create a synthetic single-page electronics checkout flow where shoppers "
            "search or filter devices, select a product into a cart, complete checkout, "
            "and recover from inline validation errors."
        ),
        requirement_summary="Tablet electronics search, cart, checkout, and recoverable validation flow.",
        use_cases=[
            {
                "use_case_id": "UC-01",
                "title": "Find devices",
                "actor": "Shopper",
                "goal": "Search or filter electronics products",
                "expected_outcome": "Matching devices are visible and selectable",
            },
            {
                "use_case_id": "UC-02",
                "title": "Select a device",
                "actor": "Shopper",
                "goal": "Select a device and add it to a cart",
                "expected_outcome": "The selected device and quantity remain visible",
            },
            {
                "use_case_id": "UC-03",
                "title": "Submit electronics order",
                "actor": "Shopper",
                "goal": "Complete checkout and submit the contact form",
                "expected_outcome": "Inline errors appear without clearing valid fields",
            },
        ],
    ),
    _make_case(
        case_id="p4-05-stability-04-books",
        request_id="p4-05-stability-request-04",
        domain="books",
        target_device="mobile",
        requirement=(
            "Create a synthetic single-page book checkout flow where shoppers search "
            "or filter books, select titles into a cart, complete checkout, and recover "
            "from inline validation errors."
        ),
        requirement_summary="Mobile book search, cart, checkout, and recoverable validation flow.",
        use_cases=[
            {
                "use_case_id": "UC-01",
                "title": "Find books",
                "actor": "Shopper",
                "goal": "Search or filter books by title or author",
                "expected_outcome": "Matching books are visible and selectable",
            },
            {
                "use_case_id": "UC-02",
                "title": "Build book cart",
                "actor": "Shopper",
                "goal": "Select book titles and add them to a cart",
                "expected_outcome": "Selected titles remain visible in the cart",
            },
            {
                "use_case_id": "UC-03",
                "title": "Submit book order",
                "actor": "Shopper",
                "goal": "Complete checkout and submit the delivery form",
                "expected_outcome": "Inline errors appear without clearing valid fields",
            },
        ],
    ),
    _make_case(
        case_id="p4-05-stability-05-pet",
        request_id="p4-05-stability-request-05",
        domain="pet",
        target_device="tablet",
        requirement=(
            "Create a synthetic single-page pet-supply checkout flow where shoppers "
            "search or filter supplies, select products into a cart, complete checkout, "
            "and recover from inline validation errors."
        ),
        requirement_summary="Tablet pet-supply search, cart, checkout, and recoverable validation flow.",
        use_cases=[
            {
                "use_case_id": "UC-01",
                "title": "Find pet supplies",
                "actor": "Shopper",
                "goal": "Search or filter pet supplies",
                "expected_outcome": "Matching supplies are visible and selectable",
            },
            {
                "use_case_id": "UC-02",
                "title": "Select pet products",
                "actor": "Shopper",
                "goal": "Select pet products and add them to a cart",
                "expected_outcome": "Selected products remain visible in the cart",
            },
            {
                "use_case_id": "UC-03",
                "title": "Submit pet-supply order",
                "actor": "Shopper",
                "goal": "Complete checkout and submit the delivery form",
                "expected_outcome": "Inline errors appear without clearing valid fields",
            },
        ],
    ),
    _make_case(
        case_id="p4-05-stability-06-home",
        request_id="p4-05-stability-request-06",
        domain="home",
        target_device="desktop",
        requirement=(
            "Create a synthetic single-page home-goods checkout flow where shoppers "
            "search or filter products, select room items into a cart, complete checkout, "
            "and recover from inline validation errors."
        ),
        requirement_summary="Desktop home-goods search, cart, checkout, and recoverable validation flow.",
        use_cases=[
            {
                "use_case_id": "UC-01",
                "title": "Find home goods",
                "actor": "Shopper",
                "goal": "Search or filter home-goods products",
                "expected_outcome": "Matching home goods are visible and selectable",
            },
            {
                "use_case_id": "UC-02",
                "title": "Build home-goods cart",
                "actor": "Shopper",
                "goal": "Select room items and add them to a cart",
                "expected_outcome": "Selected room items remain visible in the cart",
            },
            {
                "use_case_id": "UC-03",
                "title": "Submit home-goods order",
                "actor": "Shopper",
                "goal": "Complete checkout and submit the address form",
                "expected_outcome": "Inline errors appear without clearing valid fields",
            },
        ],
    ),
    _make_case(
        case_id="p4-05-stability-07-cosmetics",
        request_id="p4-05-stability-request-07",
        domain="cosmetics",
        target_device="mobile",
        requirement=(
            "Create a synthetic single-page cosmetics checkout flow where shoppers "
            "search or filter products, select shades into a cart, complete checkout, "
            "and recover from inline validation errors."
        ),
        requirement_summary="Mobile cosmetics search, cart, checkout, and recoverable validation flow.",
        use_cases=[
            {
                "use_case_id": "UC-01",
                "title": "Find cosmetics",
                "actor": "Shopper",
                "goal": "Search or filter cosmetics products",
                "expected_outcome": "Matching cosmetics are visible and selectable",
            },
            {
                "use_case_id": "UC-02",
                "title": "Choose cosmetics",
                "actor": "Shopper",
                "goal": "Select shades and add cosmetics to a cart",
                "expected_outcome": "Selected shades remain visible in the cart",
            },
            {
                "use_case_id": "UC-03",
                "title": "Submit cosmetics order",
                "actor": "Shopper",
                "goal": "Complete checkout and submit the delivery form",
                "expected_outcome": "Inline errors appear without clearing valid fields",
            },
        ],
    ),
    _make_case(
        case_id="p4-05-stability-08-sports",
        request_id="p4-05-stability-request-08",
        domain="sports",
        target_device="desktop",
        requirement=(
            "Create a synthetic single-page sports-equipment checkout flow where "
            "shoppers search or filter equipment, select items into a cart, complete "
            "checkout, and recover from inline validation errors."
        ),
        requirement_summary="Desktop sports-equipment search, cart, checkout, and recoverable validation flow.",
        use_cases=[
            {
                "use_case_id": "UC-01",
                "title": "Find sports equipment",
                "actor": "Shopper",
                "goal": "Search or filter sports equipment",
                "expected_outcome": "Matching equipment is visible and selectable",
            },
            {
                "use_case_id": "UC-02",
                "title": "Select sports items",
                "actor": "Shopper",
                "goal": "Select equipment and add it to a cart",
                "expected_outcome": "Selected equipment remains visible in the cart",
            },
            {
                "use_case_id": "UC-03",
                "title": "Submit sports order",
                "actor": "Shopper",
                "goal": "Complete checkout and submit the delivery form",
                "expected_outcome": "Inline errors appear without clearing valid fields",
            },
        ],
    ),
    _make_case(
        case_id="p4-05-stability-09-meal-kit",
        request_id="p4-05-stability-request-09",
        domain="meal-kit",
        target_device="tablet",
        requirement=(
            "Create a synthetic single-page meal-kit checkout flow where shoppers "
            "search or filter kits, select meals into a cart, complete checkout, and "
            "recover from inline validation errors."
        ),
        requirement_summary="Tablet meal-kit search, cart, checkout, and recoverable validation flow.",
        use_cases=[
            {
                "use_case_id": "UC-01",
                "title": "Find meal kits",
                "actor": "Shopper",
                "goal": "Search or filter meal kits by preference",
                "expected_outcome": "Matching kits are visible and selectable",
            },
            {
                "use_case_id": "UC-02",
                "title": "Build meal-kit cart",
                "actor": "Shopper",
                "goal": "Select meals and add kits to a cart",
                "expected_outcome": "Selected meals remain visible in the cart",
            },
            {
                "use_case_id": "UC-03",
                "title": "Submit meal-kit order",
                "actor": "Shopper",
                "goal": "Complete checkout and submit the delivery form",
                "expected_outcome": "Inline errors appear without clearing valid fields",
            },
        ],
    ),
    _make_case(
        case_id="p4-05-stability-10-office",
        request_id="p4-05-stability-request-10",
        domain="office",
        target_device="mobile",
        requirement=(
            "Create a synthetic single-page office-supply checkout flow where shoppers "
            "search or filter supplies, select bundles into a cart, complete checkout, "
            "and recover from inline validation errors."
        ),
        requirement_summary="Mobile office-supply search, cart, checkout, and recoverable validation flow.",
        use_cases=[
            {
                "use_case_id": "UC-01",
                "title": "Find office supplies",
                "actor": "Shopper",
                "goal": "Search or filter office supplies",
                "expected_outcome": "Matching supplies are visible and selectable",
            },
            {
                "use_case_id": "UC-02",
                "title": "Select office bundles",
                "actor": "Shopper",
                "goal": "Select bundles and add them to a cart",
                "expected_outcome": "Selected bundles remain visible in the cart",
            },
            {
                "use_case_id": "UC-03",
                "title": "Submit office-supply order",
                "actor": "Shopper",
                "goal": "Complete checkout and submit the delivery form",
                "expected_outcome": "Inline errors appear without clearing valid fields",
            },
        ],
    ),
)


_CASE_SET_PAYLOAD = {
    "schema_version": CASE_SET_SCHEMA_VERSION,
    "case_set_id": CASE_SET_ID,
    "case_order": [str(item["case_id"]) for item in _RAW_CASES],
    "cases": [copy.deepcopy(item) for item in _RAW_CASES],
}
_CASE_SET = {
    **copy.deepcopy(_CASE_SET_PAYLOAD),
    "case_set_identity": _identity(_CASE_SET_PAYLOAD),
}

validate_stability_case_set(_CASE_SET)


__all__ = [
    "CASE_COUNT",
    "CASE_SET_ID",
    "CASE_SET_SCHEMA_VERSION",
    "canonical_case_bytes",
    "canonical_case_set_bytes",
    "canonical_case_set_record_bytes",
    "case_set_identity",
    "get_stability_case",
    "get_stability_case_set",
    "get_stability_cases",
    "validate_stability_case",
    "validate_stability_case_set",
]
