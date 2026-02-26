"""Fixed blocking service — bounded concurrency + admission control.

POST /scan/blocking  → inline scan with bulkhead + fast-fail (429/503)
GET  /metrics        → inflight, completed, rejected counts
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

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

MAX_BLOCKING_CONCURRENCY = int(os.environ.get("MAX_BLOCKING_CONCURRENCY", "24"))
BLOCKING_ADMISSION_TIMEOUT_MS = int(os.environ.get("BLOCKING_ADMISSION_TIMEOUT_MS", "100"))
BLOCKING_DEADLINE_SECONDS = float(os.environ.get("BLOCKING_DEADLINE_SECONDS", "10"))

# ── State ───────────────────────────────────────────────────────────────────

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
    _semaphore = asyncio.Semaphore(MAX_BLOCKING_CONCURRENCY)
    await warm_startup_load()
    print(
        f"[blocking_api] Ready — max_concurrency={MAX_BLOCKING_CONCURRENCY}, "
        f"admission_timeout={BLOCKING_ADMISSION_TIMEOUT_MS}ms, "
        f"deadline={BLOCKING_DEADLINE_SECONDS}s"
    )
    yield


app = FastAPI(title="Cortex-Guard Fixed (Blocking)", lifespan=lifespan)


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.post("/scan/blocking", response_model=BlockingScanResponse)
async def scan_blocking(req: ScanRequest):
    request_id = str(uuid.uuid4())
    started = now_iso()

    # Admission control: try to acquire within a short timeout
    admission_timeout = BLOCKING_ADMISSION_TIMEOUT_MS / 1000.0
    try:
        await asyncio.wait_for(_semaphore.acquire(), timeout=admission_timeout)
    except asyncio.TimeoutError:
        _metrics["rejected"] += 1
        raise HTTPException(
            status_code=429,
            detail=ErrorResponse(
                request_id=request_id,
                error="over_capacity",
                reason="over_capacity",
                started_at=started,
                finished_at=now_iso(),
            ).model_dump(),
        )

    _metrics["inflight"] += 1
    try:
        # Scan with a hard deadline
        with Timer() as t:
            await asyncio.wait_for(
                simulate_scan(),
                timeout=BLOCKING_DEADLINE_SECONDS,
            )

        verdict = decide_verdict()
        _metrics["completed"] += 1
        finished = now_iso()

        status_code = 200 if verdict == "allow" else 403
        response = BlockingScanResponse(
            request_id=request_id,
            verdict=ScanVerdict(verdict),
            started_at=started,
            finished_at=finished,
            scan_duration_ms=round(t.elapsed_ms, 2),
            model_mode=os.environ.get("MODEL_MODE", "warm"),
        )
        if verdict == "deny":
            raise HTTPException(status_code=403, detail=response.model_dump())
        return response

    except HTTPException:
        raise  # re-raise 403
    except asyncio.TimeoutError:
        _metrics["rejected"] += 1
        raise HTTPException(
            status_code=504,
            detail=ErrorResponse(
                request_id=request_id,
                error="deadline_exceeded",
                reason="scan_deadline_exceeded",
                started_at=started,
                finished_at=now_iso(),
            ).model_dump(),
        )
    except Exception as exc:
        _metrics["errors"] += 1
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                request_id=request_id,
                error=str(exc),
                reason="internal_error",
                started_at=started,
                finished_at=now_iso(),
            ).model_dump(),
        )
    finally:
        _metrics["inflight"] -= 1
        _semaphore.release()


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
    return {"status": "ok", "service": "blocking_fixed"}
