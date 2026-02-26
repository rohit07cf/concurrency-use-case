#!/usr/bin/env bash
# Start the FIXED services: nonblocking on 8001, blocking on 8002.
# Each has its own isolated capacity (bulkhead pattern).
#
# Usage:
#   ./scripts/run_fixed.sh
#   MODEL_MODE=cold ./scripts/run_fixed.sh

set -euo pipefail
cd "$(dirname "$0")/.."

export MODEL_MODE="${MODEL_MODE:-warm}"
export SEED="${SEED:-42}"

echo "=== Starting FIXED nonblocking_api on :8001 ==="
python -m uvicorn apps.nonblocking_api:app \
    --host 0.0.0.0 \
    --port 8001 \
    --log-level info &
PID_NB=$!

echo "=== Starting FIXED blocking_api on :8002 ==="
python -m uvicorn apps.blocking_api:app \
    --host 0.0.0.0 \
    --port 8002 \
    --log-level info &
PID_BLK=$!

echo "PIDs: nonblocking=$PID_NB, blocking=$PID_BLK"

# Wait for both; kill the other if one exits
trap "kill $PID_NB $PID_BLK 2>/dev/null" EXIT
wait
