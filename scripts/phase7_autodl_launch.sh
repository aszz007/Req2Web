#!/usr/bin/env bash
set -euo pipefail
if [[ "$#" -ne 8 ]]; then echo "expected 8 arguments" >&2; exit 64; fi
exec "$1" "$2" supervise --python-bin "$1" --runner "$3" --preparation "$4" --run-root "$5" --model-root "$6" --integrity-evidence "$7" --evidence-root "$8"
