"""Local-only browser observer for the frozen Phase 7 E1 v4 holdout."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sys
from typing import Any, Iterator


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import phase7_e1_v2_observe as _base
from phase7_e1_v4_cases import cases, frozen_input_identity


SCHEMA = "req2web.phase7.e1_v4.observer.v1"
ARMS = _base.ARMS
REPLAY_CASE_IDS = (
    "e1v4-public-program-approval",
    "e1v4-catalog-export",
    "e1v4-exhibit-publishing",
)
MAX_ACTIONS = _base.MAX_ACTIONS
MAX_SECONDS = _base.MAX_SECONDS

_ORDER_CHECKPOINT = {
    "e1v4-dev-permit-approval": "Verify attachments",
    "e1v4-public-program-approval": "Complete accessibility audit",
    "e1v4-equipment-loan-release": "Verify return condition",
    "e1v4-subtitle-signoff": "Check terminology",
}
_RECOVERY_PREPARATION = {
    "e1v4-dev-dataset-export": "Choose subset",
    "e1v4-catalog-export": "Select records",
    "e1v4-media-package-transfer": "Prepare package",
    "e1v4-kiosk-profile-apply": "Run validation",
}
_WIZARD_STEP_COUNT = {
    "e1v4-dev-tour-walkthrough": 3,
    "e1v4-visitor-intake": 3,
    "e1v4-exhibit-publishing": 4,
    "e1v4-safety-inspection": 5,
}
_BRANCH_CHECKPOINT = {
    "e1v4-dev-route-review": ("second", "Record rationale"),
    "e1v4-standard-expanded-review": ("first", "Complete evidence check"),
    "e1v4-local-remote-preview": ("second", "Confirm connection mode"),
    "e1v4-regular-exception-publish": ("second", "Record exception reason"),
}


def evaluator_checks() -> dict[str, tuple[str, ...]]:
    return {row["case_id"]: _base._CRITERIA[row["family"]] for row in cases()}


def obligations_for_case(case_id: str) -> tuple[str, ...]:
    try:
        family = next(row["family"] for row in cases() if row["case_id"] == case_id)
    except StopIteration as exc:
        raise KeyError(case_id) from exc
    return _base._CRITERIA[family]


def observer_identity() -> str:
    public_contract = {
        "schema": SCHEMA,
        "input_identity": frozen_input_identity(),
        "checks": evaluator_checks(),
        "case_execution_parameters": {
            "ordered_checkpoints": _ORDER_CHECKPOINT,
            "recovery_preparations": _RECOVERY_PREPARATION,
            "wizard_step_counts": _WIZARD_STEP_COUNT,
            "branch_checkpoints": _BRANCH_CHECKPOINT,
        },
        "arms": ARMS,
        "replay_case_ids": REPLAY_CASE_IDS,
        "limits": {"max_actions": MAX_ACTIONS, "max_seconds": MAX_SECONDS},
        "selector_policy": "visible exact role/name and exact visible state text only",
        "implementation_sha256": _base._sha256(Path(__file__).read_bytes()),
    }
    return _base._sha256(_base._canonical(public_contract))


@contextmanager
def _configured_base() -> Iterator[None]:
    names = (
        "SCHEMA", "REPLAY_CASE_IDS", "cases", "frozen_input_identity",
        "_ORDER_CHECKPOINT", "_RECOVERY_PREPARATION", "_WIZARD_STEP_COUNT",
        "_BRANCH_CHECKPOINT", "evaluator_checks", "obligations_for_case",
        "observer_identity",
    )
    saved = {name: getattr(_base, name) for name in names}
    _base.SCHEMA = SCHEMA
    _base.REPLAY_CASE_IDS = REPLAY_CASE_IDS
    _base.cases = cases
    _base.frozen_input_identity = frozen_input_identity
    _base._ORDER_CHECKPOINT = _ORDER_CHECKPOINT
    _base._RECOVERY_PREPARATION = _RECOVERY_PREPARATION
    _base._WIZARD_STEP_COUNT = _WIZARD_STEP_COUNT
    _base._BRANCH_CHECKPOINT = _BRANCH_CHECKPOINT
    _base.evaluator_checks = evaluator_checks
    _base.obligations_for_case = obligations_for_case
    _base.observer_identity = observer_identity
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(_base, name, value)


ObservationError = _base.ObservationError


def load_artifact_manifest(*args: Any, **kwargs: Any) -> Any:
    with _configured_base():
        return _base.load_artifact_manifest(*args, **kwargs)


def observe(*args: Any, **kwargs: Any) -> Any:
    with _configured_base():
        return _base.observe(*args, **kwargs)


def build_development_gate(*args: Any, **kwargs: Any) -> Any:
    with _configured_base():
        return _base.build_development_gate(*args, **kwargs)


def main(argv: list[str] | None = None) -> int:
    with _configured_base():
        return _base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
