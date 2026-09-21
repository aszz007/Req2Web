"""Analyze the Phase 7 E1 v4 prompt-and-renderer repair holdout."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sys
from typing import Any, Iterator


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import phase7_e1_v2_analyze as _base
import phase7_e1_v4_observe as _observer
from phase7_e1_v4_cases import cases, frozen_input_identity


@contextmanager
def _configured_base() -> Iterator[None]:
    names = (
        "SCHEMA", "STUDY_LABEL", "INFERENCE_STATUS", "cases",
        "frozen_input_identity", "ARMS", "REPLAY_CASE_IDS",
        "observer_identity", "obligations_for_case",
    )
    saved = {name: getattr(_base, name) for name in names}
    _base.SCHEMA = "req2web.phase7.e1_v4.analysis.v1"
    _base.STUDY_LABEL = "Phase 7 E1 v4 prompt-and-renderer repair holdout"
    _base.INFERENCE_STATUS = (
        "v3_failure_informed_development_repair_frozen_before_any_v21_model_execution"
    )
    _base.cases = cases
    _base.frozen_input_identity = frozen_input_identity
    _base.ARMS = _observer.ARMS
    _base.REPLAY_CASE_IDS = _observer.REPLAY_CASE_IDS
    _base.observer_identity = _observer.observer_identity
    _base.obligations_for_case = _observer.obligations_for_case
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(_base, name, value)


AnalysisError = _base.AnalysisError


def analyze(*args: Any, **kwargs: Any) -> Any:
    with _configured_base():
        return _base.analyze(*args, **kwargs)


def main(argv: list[str] | None = None) -> int:
    with _configured_base():
        return _base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
