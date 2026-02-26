"""Baseline (bad) combined API — both blocking and nonblocking share capacity.

This demonstrates the shared-capacity queueing failure: when blocking requests
hog the semaphore, nonblocking requests queue behind them and timeout.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

# Ensure common package is importable
sys.path.insert(0, os.path.dirname(__file__))

from common.models import (
    BlockingScanResponse,
    ErrorResponse,
    MetricsResponse,
    ScanRequest,
    ScanVerdict,
)
from common.simulate import (
    Timer,
    decide_verdict,
    now_iso,
    simulate_scan,
    warm_startup_load,
)

# ── Config ──────────────────────────────────────────────────────────────────

BASELINE_SHARED_CONCURRENCY = int(os.environ.get("BASELINE_SHARED_CONCURRENCY", "24"))
BASELINE_REQUEST_DEADLINE_S = float(os.environ.get("BASELINE_REQUEST_DEADLINE_S", "5"))

# ── Shared state ────────────────────────────────────────────────────────────

_semaphore: asyncio.Semaphore
_metrics = {
    "inflight": 0,
    "completed": 0,
    "rejected": 0,
    "errors": 0,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _semaphore
    _semaphore = asyncio.Semaphore(BASELINE_SHARED_CONCURRENCY)
    await warm_startup_load()
    print(
        f"[combined_api] Ready — shared concurrency={BASELINE_SHARED_CONCURRENCY}, "
        f"deadline={BASELINE_REQUEST_DEADLINE_S}s"
    )
    yield


app = FastAPI(title="Cortex-Guard Baseline (Combined)", lifespan=lifespan)


# ── Helpers ─────────────────────────────────────────────────────────────────

async def _acquire_or_timeout(deadline: float) -> bool:
    """Try to acquire the shared semaphore within *deadline* seconds."""
    try:
        await asyncio.wait_for(_semaphore.acquire(), timeout=deadline)
        return True
    except asyncio.TimeoutError:
        return False


async def _do_scan(request_id: str, content: str, endpoint: str):
    """Shared scan path used by BOTH endpoints — the root cause of the problem."""
    started = now_iso()

    # Try to acquire shared capacity within the deadline
    acquired = await _acquire_or_timeout(BASELINE_REQUEST_DEADLINE_S)
    if not acquired:
        _metrics["rejected"] += 1
        finished = now_iso()
        raise HTTPException(
            status_code=504,
            detail=ErrorResponse(
                request_id=request_id,
                error="timeout",
                reason="shared_capacity_exhausted",
                started_at=started,
                finished_at=finished,
            ).model_dump(),
        )

    _metrics["inflight"] += 1
    try:
        with Timer() as t:
            scan_seconds = await asyncio.wait_for(
                simulate_scan(),
                timeout=BASELINE_REQUEST_DEADLINE_S,
            )
        verdict = decide_verdict()
        _metrics["completed"] += 1
        finished = now_iso()
        return BlockingScanResponse(
            request_id=request_id,
            verdict=ScanVerdict(verdict),
            started_at=started,
            finished_at=finished,
            scan_duration_ms=t.elapsed_ms,
            model_mode=os.environ.get("MODEL_MODE", "warm"),
        )
    except asyncio.TimeoutError:
        _metrics["rejected"] += 1
        finished = now_iso()
        raise HTTPException(
            status_code=504,
            detail=ErrorResponse(
                request_id=request_id,
                error="timeout",
                reason="scan_deadline_exceeded",
                started_at=started,
                finished_at=finished,
            ).model_dump(),
        )
    except Exception as exc:
        _metrics["errors"] += 1
        finished = now_iso()
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                request_id=request_id,
                error=str(exc),
                reason="internal_error",
                started_at=started,
                finished_at=finished,
            ).model_dump(),
        )
    finally:
        _metrics["inflight"] -= 1
        _semaphore.release()


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.post("/scan/nonblocking", response_model=BlockingScanResponse)
async def scan_nonblocking(req: ScanRequest):
    """In baseline, nonblocking goes through the SAME shared capacity — bad."""
    request_id = str(uuid.uuid4())
    return await _do_scan(request_id, req.content, "nonblocking")


@app.post("/scan/blocking", response_model=BlockingScanResponse)
async def scan_blocking(req: ScanRequest):
    request_id = str(uuid.uuid4())
    return await _do_scan(request_id, req.content, "blocking")


@app.get("/metrics", response_model=MetricsResponse)
async def metrics():
    return MetricsResponse(
        inflight=_metrics["inflight"],
        completed=_metrics["completed"],
        rejected=_metrics["rejected"],
        errors=_metrics["errors"],
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "combined_baseline"}
