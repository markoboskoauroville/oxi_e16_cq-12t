#!/usr/bin/env bash
# Run the Four Tests (no third-party dependencies required).
set -euo pipefail
cd "$(dirname "$0")"
exec "${PYTHON:-python3}" -m unittest discover -s tests -p 'test_*.py' "$@"
