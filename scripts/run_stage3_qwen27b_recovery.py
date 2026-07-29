"""Run the bounded Qwen3.5-27B recovery pilot on an approved Linux GPU host."""
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
    execute_qwen27b_recovery,
    terminate_process_group,
    wait_for_worker,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the fixed two-case Qwen3.5-27B recovery pilot. Partial stream "
            "output is observational; persisted final raw responses are authoritative."
        )
    )
    parser.add_argument("--predeploy-manifest", required=True)
    parser.add_argument("--predeploy-ready", required=True)
    parser.add_argument("--model-root", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--repository-archive", required=True)
    parser.add_argument("--repository-archive-manifest", required=True)
    parser.add_argument("--case-bundle-root", required=True)
    parser.add_argument("--result-root", required=True)
    parser.add_argument("--selected-copy", required=True)
    parser.add_argument(
        "--expected-gpu-name",
        required=True,
        help="Exact action-time nvidia-smi GPU name selected by the project owner.",
    )
    parser.add_argument(
        "--expected-gpu-uuid",
        required=True,
        help="Exact action-time GPU UUID reported by nvidia-smi.",
    )
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
    result = execute_qwen27b_recovery(
        args.predeploy_manifest,
        args.predeploy_ready,
        args.model_root,
        args.runtime_root,
        args.repository_archive,
        args.repository_archive_manifest,
        args.case_bundle_root,
        args.result_root,
        selected_copy=args.selected_copy,
        expected_gpu_name=args.expected_gpu_name,
        expected_gpu_uuid=args.expected_gpu_uuid,
        stream_output=args.stream_output,
    )
    sys.stdout.write(
        json.dumps(
            {
                "state": result["state"],
                "run_id": result["run_id"],
                "provider_call_count": result["execution"]["provider_call_count"],
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
        raise Qwen27BRecoveryRunnerError("linux_parent_runner_required")
    cancel = None if args.cancel_file is None else Path(args.cancel_file)
    if cancel is not None and (
        not cancel.is_absolute()
        or cancel.is_symlink()
        or not cancel.parent.is_dir()
        or cancel.parent.is_symlink()
    ):
        raise Qwen27BRecoveryRunnerError("cancel_request_path_invalid")
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--predeploy-manifest",
        args.predeploy_manifest,
        "--predeploy-ready",
        args.predeploy_ready,
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
        "--selected-copy",
        args.selected_copy,
        "--stream-output",
        args.stream_output,
        "--expected-gpu-name",
        args.expected_gpu_name,
        "--expected-gpu-uuid",
        args.expected_gpu_uuid,
    ]
    environment = {
        **os.environ,
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "PYTHONNOUSERSITE": "1",
    }
    for key in ("CUDA_VISIBLE_DEVICES", "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE"):
        environment.pop(key, None)
    process: subprocess.Popen[bytes] | None = None
    process_group_id: int | None = None
    succeeded = False
    previous_sigterm = None

    def _parent_sigterm(_signum: int, _frame: object) -> None:
        raise Qwen27BRecoveryRunnerError("recovery_parent_sigterm")

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
            raise Qwen27BRecoveryRunnerError(
                "recovery_worker_process_failed"
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
    args = _parser().parse_args(argv)
    try:
        return _worker(args) if args.worker else _parent(args)
    except Qwen27BRecoveryRunnerError as exc:
        sys.stderr.write(
            json.dumps(
                {"event": "recovery_failed_closed", "error_code": str(exc)},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
