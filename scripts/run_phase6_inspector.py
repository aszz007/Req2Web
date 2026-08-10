"""Validate or serve the precomputed Phase 6 Inspector."""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_inspector.phase6_replay import (  # noqa: E402
    Phase6ReplayError,
    validate_phase6_reviewer_bundle,
)


class _InspectorHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        print(f"[P6-INSPECTOR] {format % args}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate or serve the read-only Phase 6 precomputed Inspector. "
            "Serving performs no model, GPU, browser-automation, remote, or "
            "H1/gold action."
        )
    )
    parser.add_argument(
        "--bundle-root",
        type=Path,
        default=ROOT / "release" / "phase6_reviewer_v8",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the precomputed bundle and exit without starting a server.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = validate_phase6_reviewer_bundle(args.bundle_root)
    except (OSError, ValueError, Phase6ReplayError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    summary = {
        "status": "validated_precomputed_replay",
        "counts": manifest["counts"],
        "bundle_root": str(args.bundle_root.resolve()),
    }
    if args.validate_only:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print(
            "[P6-INSPECTOR] failed closed: the default reviewer server is local-only",
            file=sys.stderr,
        )
        return 2
    handler = partial(_InspectorHandler, directory=str(args.bundle_root.resolve()))
    try:
        server = ThreadingHTTPServer((args.host, args.port), handler)
    except OSError as exc:
        print(f"[P6-INSPECTOR] failed closed: {exc}", file=sys.stderr)
        return 2
    print(
        f"[P6-INSPECTOR] validated {manifest['counts']['row_count']} rows; "
        f"open http://{args.host}:{args.port}/",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
