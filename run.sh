#!/usr/bin/env bash
# OXI-CQ Bridge launcher (Mantra Productions)
# Core app needs only python3 (bundled on macOS via Command Line Tools).
# Pass any flags through, e.g.:  ./run.sh --output tcp:192.168.1.60
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
exec "$PY" -m oxi_cq_bridge "$@"
