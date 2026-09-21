"""Frozen public cases for the Phase 7 E1 v4 repair holdout.

The public case content is unchanged from E1 v3. Only the experiment namespace
is advanced so new prompt and renderer identities cannot be confused with the
immutable v3 development evidence. Evaluator material remains local-only.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from phase7_e1_v3_cases import cases as _v3_cases
from phase7_e1_v3_cases import frozen_input_identity as _v3_input_identity


SCHEMA = "req2web.phase7.e1_v4.cases.v1"
SOURCE_INPUT_IDENTITY = _v3_input_identity()


def _build_cases() -> list[dict[str, Any]]:
    rows = _v3_cases()
    for row in rows:
        case_id = str(row["case_id"])
        if not case_id.startswith("e1v3-"):
            raise RuntimeError("E1 v4 source case namespace drifted")
        row["case_id"] = "e1v4-" + case_id.removeprefix("e1v3-")
    return rows


_CASES = _build_cases()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def cases() -> list[dict[str, Any]]:
    return copy.deepcopy(_CASES)


def frozen_input_identity() -> str:
    actual = "sha256:" + hashlib.sha256(
        _canonical({"schema_version": SCHEMA, "cases": _CASES})
    ).hexdigest()
    if actual != FROZEN_INPUT_SHA256:
        raise RuntimeError("E1 v4 public input packet drifted")
    return actual


FROZEN_INPUT_SHA256 = (
    "sha256:f478428b85bb83521647d05bcf176d2178507ecd7e5979b00d26b0d2cd926063"
)


__all__ = [
    "SCHEMA",
    "SOURCE_INPUT_IDENTITY",
    "cases",
    "frozen_input_identity",
]
