#!/usr/bin/env python3
"""Run the single unchanged-contract Qwen3.6-27B diagnostic on Linux."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.model_text_stream import MODEL_TEXT_STREAM_MODES  # noqa: E402
from req2web_runtime.qwen27b_recovery_runner import (  # noqa: E402
    DEFAULT_TIMEOUT_SECONDS,
    Qwen27BRecoveryRunnerError,
    terminate_process_group,
    wait_for_worker,
)
from req2web_runtime import qwen36_diagnostic as diagnostic  # noqa: E402


ENTRYPOINT_STATUS = "historical"
ENTRYPOINT_MODE = "diagnostic_only"
ACTIVE_DEFAULT_ENTRY = False
_ENTRYPOINT_HELP = (
    "HISTORICAL STAGE 3 ENTRYPOINT; NOT THE ACTIVE DEFAULT FULL-FLOW ENTRY. "
    "entrypoint_status=historical active_default_entry=false "
    "entrypoint_mode=diagnostic_only."
)


def _emit_entrypoint_notice() -> None:
    print(
        json.dumps(
            {
                "active_default_entry": ACTIVE_DEFAULT_ENTRY,
                "entrypoint_mode": ENTRYPOINT_MODE,
                "entrypoint_status": ENTRYPOINT_STATUS,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=sys.stderr,
        flush=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            _ENTRYPOINT_HELP
            + " Run exactly two unchanged-contract Qwen3.6-27B diagnostic calls. "
            "Stream output is observational; persisted raw bytes are authoritative."
        )
    )
    parser.add_argument("--plan", required=True)
    parser.add_argument("--action-binding", required=True)
    parser.add_argument("--model-inventory", required=True)
    parser.add_argument("--runtime-inventory", required=True)
    parser.add_argument("--model-root", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--repository-archive", required=True)
    parser.add_argument("--repository-archive-manifest", required=True)
    parser.add_argument("--case-bundle-root", required=True)
    parser.add_argument("--result-root", required=True)
    parser.add_argument(
        "--stream-output", choices=MODEL_TEXT_STREAM_MODES, default="off"
    )
    parser.add_argument(
        "--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS
    )
    parser.add_argument("--cancel-file")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser


def _worker(args: argparse.Namespace) -> int:
    result = diagnostic.execute_qwen36_diagnostic(
        args.plan,
        args.action_binding,
        args.model_inventory,
        args.runtime_inventory,
        args.model_root,
        args.runtime_root,
        args.repository_archive,
        args.repository_archive_manifest,
        args.case_bundle_root,
        args.result_root,
        stream_output=args.stream_output,
    )
    sys.stdout.write(
        json.dumps(
            {
                "state": result["state"],
                "run_id": result["run_id"],
                "provider_call_count": result["execution"][
                    "provider_call_count"
                ],
                "retry_count": result["execution"]["retry_count"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    sys.stdout.flush()
    return 0


def _parent(args: argparse.Namespace) -> int:
    if sys.platform != "linux":
        raise diagnostic.Qwen36DiagnosticError(
            "linux_parent_runner_required"
        )
    action_binding = Path(args.action_binding)
    if action_binding.is_symlink() or not action_binding.is_file():
        raise diagnostic.Qwen36DiagnosticError(
            "action_binding_path_invalid"
        )
    diagnostic.Qwen36DiagnosticActionBinding.from_bytes(
        action_binding.read_bytes()
    )
    action_binding = action_binding.resolve(strict=True)
    cancel = None if args.cancel_file is None else Path(args.cancel_file)
    if cancel is not None and (
        not cancel.is_absolute()
        or cancel.is_symlink()
        or not cancel.parent.is_dir()
        or cancel.parent.is_symlink()
    ):
        raise diagnostic.Qwen36DiagnosticError(
            "cancel_request_path_invalid"
        )
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--plan",
        args.plan,
        "--action-binding",
        str(action_binding),
        "--model-inventory",
        args.model_inventory,
        "--runtime-inventory",
        args.runtime_inventory,
        "--model-root",
        args.model_root,
        "--runtime-root",
        args.runtime_root,
        "--repository-archive",
        args.repository_archive,
        "--repository-archive-manifest",
        args.repository_archive_manifest,
        "--case-bundle-root",
        args.case_bundle_root,
        "--result-root",
        args.result_root,
        "--stream-output",
        args.stream_output,
    ]
    environment = {
        **os.environ,
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "PYTHONNOUSERSITE": "1",
    }
    for key in (
        "CUDA_VISIBLE_DEVICES",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONUSERBASE",
    ):
        environment.pop(key, None)
    process: subprocess.Popen[bytes] | None = None
    process_group_id: int | None = None
    succeeded = False
    previous_sigterm = None

    def _parent_sigterm(_signum: int, _frame: object) -> None:
        raise diagnostic.Qwen36DiagnosticError(
            "diagnostic_parent_sigterm"
        )

    try:
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, _parent_sigterm)
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=None,
            start_new_session=True,
        )
        process_group_id = process.pid
        stdout, _ = wait_for_worker(
            process,
            args.timeout_seconds,
            cancel,
            process_group_id=process_group_id,
        )
        if process.returncode != 0:
            raise diagnostic.Qwen36DiagnosticError(
                "diagnostic_worker_process_failed"
            )
        sys.stdout.buffer.write(stdout)
        sys.stdout.buffer.flush()
        succeeded = True
        return 0
    finally:
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)
        if process is not None and not succeeded:
            terminate_process_group(
                process, process_group_id=process_group_id
            )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _emit_entrypoint_notice()
    try:
        return _worker(args) if args.worker else _parent(args)
    except (
        diagnostic.Qwen36DiagnosticError,
        Qwen27BRecoveryRunnerError,
    ) as exc:
        sys.stderr.write(
            json.dumps(
                {
                    "event": "qwen36_diagnostic_failed_closed",
                    "error_code": str(exc),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
