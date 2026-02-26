#!/usr/bin/env bash
# Run Locust against the FIXED services (ports 8001 + 8002).
# Opens web UI at http://localhost:8089
#
# Usage:
#   ./scripts/run_locust_fixed.sh
#   ./scripts/run_locust_fixed.sh --headless

set -euo pipefail
cd "$(dirname "$0")/.."

export NONBLOCKING_HOST="${NONBLOCKING_HOST:-http://localhost:8001}"
export BLOCKING_HOST="${BLOCKING_HOST:-http://localhost:8002}"

echo "=== Locust → FIXED (NB=$NONBLOCKING_HOST, BLK=$BLOCKING_HOST) ==="
echo "Open http://localhost:8089 to start the test"
exec locust -f locust/locustfile_fixed.py "$@"
