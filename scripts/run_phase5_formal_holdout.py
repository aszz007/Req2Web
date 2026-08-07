"""Run Phase 5 no-model validation under the inherited Phase 4 authority."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_runtime.phase4_remote_qwen_fresh_integrated import (  # noqa: E402
    RemoteFreshIntegratedProfile,
)
from req2web_runtime.phase5_action_authority import (  # noqa: E402
    Phase5FinalActionAuthority,
    validate_phase5_action_authority_against_package,
)
from req2web_runtime.phase5_formal_qwen_worker import (  # noqa: E402
    Phase5FormalQwenWorkerFactory,
)
from req2web_runtime.phase5_formal_runner import (  # noqa: E402
    PHASE5_FORMAL_EXECUTION_STATUS,
    Phase5FormalRunnerError,
    run_phase5_formal_runner,
    synthetic_phase5_worker_factory,
    validate_phase5_formal_result_root,
)
from req2web_runtime.phase5_sealed_action_package import (  # noqa: E402
    Phase5SealedActionPackage,
    load_phase5_sealed_action_package_file,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 5 inherits the accepted Phase 4 canonical authorities. "
            "Only the tracked synthetic/no-model validation mode is currently "
            "available; formal model/H1 action remains independently disabled."
        )
    )
    parser.add_argument("--action-package", required=True, type=Path)
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--runtime-profile", type=Path)
    parser.add_argument("--action-authority", type=Path)
    parser.add_argument("--hourly-rate-minor-units", type=int, default=0)
    parser.add_argument("--resume-existing", action="store_true")
    parser.add_argument(
        "--validate-result-only",
        action="store_true",
        help="Replay a terminal result root without worker, model, GPU, or writes.",
    )
    parser.add_argument(
        "--synthetic-validation-only",
        action="store_true",
        help="Use the tracked synthetic package and no-model fixture worker.",
    )
    parser.add_argument(
        "--confirm-formal-holdout-action",
        action="store_true",
        help=(
            "Reserved for a future separately authorized model/H1 action. It "
            "cannot override the current no-model gate."
        ),
    )
    return parser


def _outside_repository(path: Path, *, must_exist: bool, name: str) -> Path:
    try:
        resolved = path.resolve(strict=must_exist)
    except OSError as exc:
        raise ValueError(f"{name} is unavailable") from exc
    try:
        resolved.relative_to(ROOT.resolve(strict=True))
    except ValueError:
        return resolved
    raise ValueError(f"{name} must remain outside the repository")


def _offline_process() -> None:
    os.environ.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "LANGSMITH_TRACING": "0",
            "LANGCHAIN_TRACING_V2": "0",
            "CUDA_VISIBLE_DEVICES": "0",
            "PYTHONUNBUFFERED": "1",
        }
    )


def _load_package(path: Path, *, synthetic: bool) -> Phase5SealedActionPackage:
    if synthetic:
        return Phase5SealedActionPackage.from_dict(
            __import__("json").loads(path.read_text(encoding="utf-8"))
        )
    return load_phase5_sealed_action_package_file(
        _outside_repository(
            path,
            must_exist=True,
            name="real owner-sealed action package",
        ),
        owner_only_local_confirmed=True,
    )


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    value = completed.stdout.strip()
    if (
        completed.returncode != 0
        or len(value) != 40
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("formal runner cannot bind the source action commit")
    return value


def _load_action_authority(
    path: Path,
) -> Phase5FinalActionAuthority:
    return Phase5FinalActionAuthority.from_json_bytes(
        _outside_repository(
            path,
            must_exist=True,
            name="final action authority",
        ).read_bytes()
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _offline_process()
    try:
        if args.synthetic_validation_only is not True:
            raise Phase5FormalRunnerError(
                f"{PHASE5_FORMAL_EXECUTION_STATUS}: real Phase 5 package "
                "access and model/H1 action remain disabled"
            )
        package = _load_package(
            args.action_package,
            synthetic=args.synthetic_validation_only,
        )
        payload = package.to_dict()
        authority = None
        if args.validate_result_only:
            if any(
                value is not None
                for value in (
                    args.model_root,
                    args.runtime_profile,
                    args.action_authority,
                )
            ) or args.confirm_formal_holdout_action:
                parser.error(
                    "worker/model/action arguments are not used with "
                    "--validate-result-only"
                )
            result_root = (
                args.result_root.resolve(strict=True)
                if args.synthetic_validation_only
                else _outside_repository(
                    args.result_root,
                    must_exist=True,
                    name="formal result root",
                )
            )
            summary = validate_phase5_formal_result_root(
                package=package,
                result_root=result_root,
            )
            print(
                "[Phase 5] "
                f"validated=true run_id={summary['run_id']} "
                f"generate_started={summary['actual_generate_started_count']}",
                flush=True,
            )
            return 0
        if args.synthetic_validation_only:
            if payload["package_kind"] != "synthetic_validation_only":
                parser.error(
                    "--synthetic-validation-only requires a synthetic package"
                )
            if args.model_root is not None or args.runtime_profile is not None:
                parser.error(
                    "model/runtime arguments are prohibited in synthetic mode"
                )
            worker_factory = synthetic_phase5_worker_factory
            result_root = args.result_root.resolve(strict=False)
        else:
            if payload["package_kind"] not in {
                "owner_sealed_formal_h1",
                "project_authored_path2_model_pilot",
            }:
                parser.error("real execution requires an owner-sealed formal package")
            if args.confirm_formal_holdout_action is not True:
                parser.error("--confirm-formal-holdout-action is required")
            if (
                args.model_root is None
                or args.runtime_profile is None
                or args.action_authority is None
            ):
                parser.error(
                    "--model-root, --runtime-profile, and --action-authority "
                    "are required for formal action"
                )
            authority = _load_action_authority(args.action_authority)
            validate_phase5_action_authority_against_package(
                authority,
                package,
            )
            authority_payload = authority.to_dict()
            result_root = _outside_repository(
                args.result_root,
                must_exist=False,
                name="formal result root",
            )
            if str(result_root) != authority_payload["paths"]["result_root"]:
                raise ValueError("formal result root drifted from action authority")
            model_root = args.model_root.resolve(strict=True)
            if str(model_root) != authority_payload["paths"]["model_root"]:
                raise ValueError("formal model root drifted from action authority")
            if str(ROOT.resolve(strict=True)) != authority_payload["paths"][
                "repository_root"
            ]:
                raise ValueError("formal repository root drifted from action authority")
            if str(Path(sys.executable).resolve(strict=True)) != authority_payload[
                "paths"
            ]["python_executable"]:
                raise ValueError("formal Python executable drifted from action authority")
            if _git_head() != payload["source_action_commit"]:
                raise ValueError("formal source action commit drifted")
            if (
                args.hourly_rate_minor_units
                != authority_payload["limits"]["hourly_rate_minor_units"]
            ):
                raise ValueError("formal hourly rate drifted from action authority")
            profile_path = args.runtime_profile.resolve(strict=True)
            profile = RemoteFreshIntegratedProfile.from_bytes(
                profile_path.read_bytes()
            )
            if (
                profile.device_uuid
                != authority_payload["instance"]["gpu_uuid"]
                or profile.device_index
                != authority_payload["instance"]["device_index"]
            ):
                raise ValueError("formal GPU profile drifted from action authority")
            worker_factory = Phase5FormalQwenWorkerFactory(
                model_root=model_root,
                profile=profile,
                console=sys.stderr,
            )
        summary = run_phase5_formal_runner(
            package=package,
            result_root=result_root,
            worker_factory=worker_factory,
            action_authority=authority,
            allow_synthetic_validation_only=args.synthetic_validation_only,
            resume_existing=args.resume_existing,
            hourly_rate_minor_units=args.hourly_rate_minor_units,
            console=sys.stderr,
        )
    except (OSError, ValueError, Phase5FormalRunnerError) as exc:
        print(f"[Phase 5] failed closed: {exc}", file=sys.stderr, flush=True)
        return 2
    print(
        "[Phase 5] "
        f"status_counts={summary['status_counts']} "
        f"generate_started={summary['actual_generate_started_count']} "
        f"run_id={summary['run_id']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
