# Cortex-Guard Bulkhead Demo

Demonstrates how **shared-capacity queueing** causes upstream timeouts in an ML
scanning microservice, and how **bulkhead isolation + admission control** fixes it.

> **Read the blog post:** [Your ML Service Looks Fine — Until a Burst Kills It](blog/shared_capacity_queueing_bulkhead_fix.md) — a 5-minute walkthrough of the problem and fix.

## Problem Statement

A single "Cortex-Guard" service handles two types of traffic:

- **Non-blocking** (high volume, latency-tolerant) — e.g., async content scans
- **Blocking** (lower volume, latency-sensitive) — e.g., inline policy checks

When both share the same capacity pool (connection/concurrency limit), a burst of
slow blocking requests **starves** non-blocking requests. Non-blocking callers
queue behind blocking work, hit their client timeout, and get 504s — even though
their own work would be fast.

This is **head-of-line blocking** caused by shared-capacity queueing.

## Architecture

### Baseline (Bad) — Shared Capacity

```
                    ┌─────────────────────────────────┐
  Non-blocking ────►│                                 │
                    │   combined_api :8000             │
  Blocking ────────►│   Semaphore(24) ← SHARED        │
                    │                                 │
                    │   Both wait in same queue.      │
                    │   Blocking hogs slots →          │
                    │   Non-blocking starves → 504    │
                    └─────────────────────────────────┘
```

### Fixed (Good) — Bulkhead + Admission Control

```
                    ┌───────────────────────────────┐
  Non-blocking ────►│  nonblocking_api :8001        │
                    │  Accept fast (202) →           │
                    │  Background queue + workers    │
                    │  Backpressure: 503 if full     │
                    └───────────────────────────────┘

                    ┌───────────────────────────────┐
  Blocking ────────►│  blocking_api :8002            │
                    │  Semaphore(24) ← ISOLATED      │
                    │  Admission timeout: 100ms      │
                    │  → 429 fast if over capacity   │
                    │  → No more head-of-line block  │
                    └───────────────────────────────┘
```

## Quick Start

### 1. Install Dependencies

```bash
cd cortex-guard-bulkhead-demo
pip install -r requirements.txt
```

### 2. Run Baseline (Bad)

```bash
# Terminal 1 — start the combined service
./scripts/run_baseline.sh

# Terminal 2 — run Locust load test
./scripts/run_locust_baseline.sh
# Open http://localhost:8089, set 100 users / 20 spawn rate, start
```

### 3. Run Fixed (Good)

```bash
# Terminal 1 — start both fixed services
./scripts/run_fixed.sh

# Terminal 2 — run Locust load test
./scripts/run_locust_fixed.sh
# Open http://localhost:8089, set 100 users / 20 spawn rate, start
```

## Scenarios

### A) Baseline / Cold (Worst)

Every request simulates a cold model load (5-8 seconds per request).

```bash
MODEL_MODE=cold ./scripts/run_baseline.sh
```

### B) Baseline / Warm (Better, Still Shared)

Model loads once at startup (8s), per-request scans are fast (80-250ms).
But both endpoints still share capacity — bursts still cause starvation.

```bash
MODEL_MODE=warm ./scripts/run_baseline.sh   # default
```

### C) Fixed / Warm + Bulkhead (Best)

Separate services, warm model, admission control.

```bash
MODEL_MODE=warm ./scripts/run_fixed.sh   # default
```

## Reproducing "200 Blocking Calls in 2 Seconds"

Use the spike test to simulate an aggressive blocking burst:

```bash
# Against baseline (shows collapse):
TARGET_HOST=http://localhost:8000 \
  locust -f locust/locustfile_spike.py \
  --headless -u 200 -r 100 -t 30s

# Against fixed blocking service (shows fast-fail):
TARGET_HOST=http://localhost:8002 \
  locust -f locust/locustfile_spike.py \
  --headless -u 200 -r 100 -t 30s
```

The spike locust file uses an `AggressiveSpikeShape` class that ramps to 200 users
in 2 seconds, then holds — run it headless only. The baseline and fixed locust files
let you set users/ramp-up manually in the Locust web UI.

## Locust Commands Reference

| Scenario | Command |
|---|---|
| Baseline (web UI) | `locust -f locust/locustfile_baseline.py` |
| Baseline (headless, 60s) | `locust -f locust/locustfile_baseline.py --headless -u 100 -r 20 -t 60s` |
| Fixed (web UI) | `locust -f locust/locustfile_fixed.py` |
| Fixed (headless, 60s) | `locust -f locust/locustfile_fixed.py --headless -u 100 -r 20 -t 60s` |
| Spike baseline | `TARGET_HOST=http://localhost:8000 locust -f locust/locustfile_spike.py --headless -u 200 -r 100 -t 30s` |
| Spike fixed | `TARGET_HOST=http://localhost:8002 locust -f locust/locustfile_spike.py --headless -u 200 -r 100 -t 30s` |

## Expected Results

### Baseline Under Burst Load

- **Non-blocking p95 latency**: 3-5+ seconds (should be <300ms)
- **Blocking p95 latency**: 5+ seconds (hits deadline)
- **Failure rate**: 30-60% timeouts (504) under spike
- **Root cause**: blocking requests hold semaphore slots for seconds,
  non-blocking requests queue behind them

### Fixed Under Same Load

- **Non-blocking accept latency**: <10ms p95 (always fast 202)
- **Blocking success latency**: 80-250ms p95 (warm scan)
- **Blocking rejection**: 429 within 100ms when over capacity
- **Zero non-blocking starvation** — isolated services can't affect each other
- **Failure mode**: fast 429 instead of slow 504

## Configuration (Environment Variables)

| Variable | Default | Description |
|---|---|---|
| `MODEL_MODE` | `warm` | `cold` = per-request model load; `warm` = startup load once |
| `SEED` | `42` | RNG seed for reproducible results |
| `COLD_LOAD_MIN_S` | `5` | Min cold load delay (seconds) |
| `COLD_LOAD_MAX_S` | `8` | Max cold load delay (seconds) |
| `WARM_STARTUP_LOAD_S` | `8` | One-time warm startup delay |
| `WARM_SCAN_MIN_MS` | `80` | Min warm scan delay (ms) |
| `WARM_SCAN_MAX_MS` | `250` | Max warm scan delay (ms) |
| `BASELINE_SHARED_CONCURRENCY` | `24` | Shared semaphore size (baseline) |
| `BASELINE_REQUEST_DEADLINE_S` | `5` | Request deadline (baseline) |
| `MAX_BLOCKING_CONCURRENCY` | `24` | Blocking semaphore size (fixed) |
| `BLOCKING_ADMISSION_TIMEOUT_MS` | `100` | Max wait for semaphore (fixed blocking) |
| `BLOCKING_DEADLINE_SECONDS` | `10` | Scan deadline (fixed blocking) |
| `NONBLOCKING_WORKERS` | `4` | Background worker count (fixed nonblocking) |
| `MAX_QUEUE_DEPTH` | `2000` | Queue backpressure limit (fixed nonblocking) |
| `RESULT_TTL_SECONDS` | `86400` | How long to keep results in memory |
| `RANDOM_VIOLATION_RATE` | `0.02` | Fraction of scans that return "deny" |

## How This Maps to Real Cortex-Guard

| Demo Concept | Production Equivalent |
|---|---|
| `combined_api` shared semaphore | Single K8s deployment handling all traffic |
| `nonblocking_api` background queue | Separate deployment with async accept + job queue |
| `blocking_api` admission control | Separate deployment with concurrency limit + circuit breaker |
| `MODEL_MODE=cold` | No PVC warm-up; model downloaded per pod/request |
| `MODEL_MODE=warm` | PVC-cached model loaded once at startup |
| `asyncio.Semaphore` | K8s resource limits + app-level concurrency control |
| 429/503 fast-fail | Upstream retry with backoff or fallback |

## File Structure

```
cortex-guard-bulkhead-demo/
├── README.md
├── requirements.txt
├── apps/
│   ├── combined_api.py          # Baseline (bad) — shared capacity
│   ├── nonblocking_api.py       # Fixed — accept fast, background scan
│   ├── blocking_api.py          # Fixed — admission control + bulkhead
│   └── common/
│       ├── models.py            # Pydantic request/response models
│       ├── simulate.py          # Jittered sleep, cold/warm simulation
│       └── state.py             # In-memory result store with TTL
├── locust/
│   ├── locustfile_baseline.py   # 10:1 mix against combined_api
│   ├── locustfile_fixed.py      # 10:1 mix against split services
│   └── locustfile_spike.py      # Aggressive blocking-only burst
└── scripts/
    ├── run_baseline.sh          # Start combined_api on :8000
    ├── run_fixed.sh             # Start nonblocking :8001 + blocking :8002
    ├── run_locust_baseline.sh   # Locust wrapper for baseline
    └── run_locust_fixed.sh      # Locust wrapper for fixed
```
