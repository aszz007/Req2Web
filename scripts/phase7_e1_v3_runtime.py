"""Run the bounded Phase 7 E1 v3 repair-informed holdout."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sys
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import phase7_e1_v2_runtime as _base


@contextmanager
def _configured_base() -> Iterator[None]:
    names = (
        "SCHEMA", "CASE_MODULE_NAME", "OBSERVER_MODULE_NAME", "SCHEDULE_SALT",
        "EXPECTED_DEVELOPMENT_CASES", "EXPECTED_MEASURED_CASES", "WORKER_SCRIPT",
        "RUNTIME_SOURCE", "CASES_SOURCE", "EXTRA_RUNTIME_SOURCES",
        "DEVELOPMENT_GATE_SCHEMA",
    )
    saved = {name: getattr(_base, name) for name in names}
    _base.SCHEMA = "req2web.phase7.e1_v3.runtime.v1"
    _base.CASE_MODULE_NAME = "phase7_e1_v3_cases"
    _base.OBSERVER_MODULE_NAME = "phase7_e1_v3_observe"
    _base.SCHEDULE_SALT = "e1-v3-20260918:"
    _base.EXPECTED_DEVELOPMENT_CASES = 4
    _base.EXPECTED_MEASURED_CASES = 12
    _base.WORKER_SCRIPT = Path(__file__)
    _base.RUNTIME_SOURCE = ROOT / "scripts/phase7_e1_v3_runtime.py"
    _base.CASES_SOURCE = ROOT / "scripts/phase7_e1_v3_cases.py"
    _base.EXTRA_RUNTIME_SOURCES = (
        ROOT / "scripts/phase7_e1_v2_runtime.py",
    )
    _base.DEVELOPMENT_GATE_SCHEMA = (
        "req2web.phase7.e1_v3.observer.v1.development_gate.v1"
    )
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(_base, name, value)


RuntimeErrorClosed = _base.RuntimeErrorClosed
sha = _base.sha
read = _base.read
write_once = _base.write_once
append = _base.append


def prepare(*args: Any, **kwargs: Any) -> Any:
    with _configured_base():
        return _base.prepare(*args, **kwargs)


def validate(*args: Any, **kwargs: Any) -> Any:
    with _configured_base():
        return _base.validate(*args, **kwargs)


def worker(*args: Any, **kwargs: Any) -> Any:
    with _configured_base():
        return _base.worker(*args, **kwargs)


def run(*args: Any, **kwargs: Any) -> Any:
    with _configured_base():
        return _base.run(*args, **kwargs)


def _schedule(*args: Any, **kwargs: Any) -> Any:
    with _configured_base():
        return _base._schedule(*args, **kwargs)


def _gate(*args: Any, **kwargs: Any) -> Any:
    with _configured_base():
        return _base._gate(*args, **kwargs)


def _inventory(*args: Any, **kwargs: Any) -> Any:
    return _base._inventory(*args, **kwargs)


def main(argv: list[str] | None = None) -> int:
    with _configured_base():
        return _base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
