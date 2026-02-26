#!/usr/bin/env bash
# Start the BASELINE combined API on port 8000.
# Both blocking and nonblocking share the same capacity pool.
#
# Usage:
#   ./scripts/run_baseline.sh              # warm mode (default)
#   MODEL_MODE=cold ./scripts/run_baseline.sh   # cold mode (worst case)

set -euo pipefail
cd "$(dirname "$0")/.."

export MODEL_MODE="${MODEL_MODE:-warm}"
export SEED="${SEED:-42}"
export BASELINE_SHARED_CONCURRENCY="${BASELINE_SHARED_CONCURRENCY:-24}"
export BASELINE_REQUEST_DEADLINE_S="${BASELINE_REQUEST_DEADLINE_S:-5}"

echo "=== Starting BASELINE combined_api on :8000 (MODEL_MODE=$MODEL_MODE) ==="
exec python -m uvicorn apps.combined_api:app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info
