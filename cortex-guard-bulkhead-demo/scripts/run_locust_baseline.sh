#!/usr/bin/env bash
# Run Locust against the BASELINE combined_api (port 8000).
# Opens web UI at http://localhost:8089
#
# Usage:
#   ./scripts/run_locust_baseline.sh             # interactive web UI
#   ./scripts/run_locust_baseline.sh --headless   # headless (pass extra args)

set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== Locust → BASELINE (http://localhost:8000) ==="
echo "Open http://localhost:8089 to start the test"
exec locust -f locust/locustfile_baseline.py "$@"
