"""Fixed nonblocking service — accepts fast (202), processes in background.

POST /scan/nonblocking  → 202 immediately, job enqueued
GET  /scan/status/{id}  → poll for result
GET  /metrics            → queue depth, throughput stats
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
    ErrorResponse,
    JobStatus,
    MetricsResponse,
    NonblockingAcceptResponse,
    NonblockingStatusResponse,
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
from common.state import JobRecord, ResultStore

# ── Config ──────────────────────────────────────────────────────────────────

NONBLOCKING_WORKERS = int(os.environ.get("NONBLOCKING_WORKERS", "4"))
MAX_QUEUE_DEPTH = int(os.environ.get("MAX_QUEUE_DEPTH", "2000"))

# ── State ───────────────────────────────────────────────────────────────────

_store = ResultStore()
_queue: asyncio.Queue
_metrics = {
    "completed": 0,
    "rejected": 0,
    "errors": 0,
    "total_processing_ms": 0.0,
    "processed_jobs": 0,
}
_worker_tasks: list[asyncio.Task] = []


# ── Background worker ──────────────────────────────────────────────────────

async def _worker(worker_id: int) -> None:
    """Drain the queue and process scan jobs."""
    while True:
        request_id: str = await _queue.get()
        record = _store.get(request_id)
        if record is None:
            _queue.task_done()
            continue

        record.status = "processing"
        record.started_at = now_iso()
        try:
            with Timer() as t:
                await simulate_scan()
            verdict = decide_verdict()
            record.verdict = verdict
            record.status = "violation" if verdict == "deny" else "done"
            record.finished_at = now_iso()
            record.scan_duration_ms = t.elapsed_ms
            _metrics["completed"] += 1
            _metrics["processed_jobs"] += 1
            _metrics["total_processing_ms"] += t.elapsed_ms
        except Exception as exc:
            record.status = "error"
            record.finished_at = now_iso()
            _metrics["errors"] += 1
            print(f"[worker-{worker_id}] Error processing {request_id}: {exc}")
        finally:
            _queue.task_done()


# ── Lifespan ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _queue
    _queue = asyncio.Queue(maxsize=MAX_QUEUE_DEPTH)
    await warm_startup_load()

    # Start background workers
    for i in range(NONBLOCKING_WORKERS):
        task = asyncio.create_task(_worker(i))
        _worker_tasks.append(task)
    print(
        f"[nonblocking_api] Ready — workers={NONBLOCKING_WORKERS}, "
        f"max_queue={MAX_QUEUE_DEPTH}"
    )

    # Start TTL cleanup
    cleanup_task = asyncio.create_task(_store.ttl_cleanup_loop())

    yield

    # Shutdown
    cleanup_task.cancel()
    for task in _worker_tasks:
        task.cancel()


app = FastAPI(title="Cortex-Guard Fixed (Nonblocking)", lifespan=lifespan)


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.post("/scan/nonblocking", response_model=NonblockingAcceptResponse, status_code=202)
async def scan_nonblocking(req: ScanRequest):
    request_id = str(uuid.uuid4())
    started = now_iso()

    # Backpressure: reject if queue is full
    if _queue.qsize() >= MAX_QUEUE_DEPTH:
        _metrics["rejected"] += 1
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                request_id=request_id,
                error="queue_full",
                reason="queue_full",
                started_at=started,
                finished_at=now_iso(),
            ).model_dump(),
        )

    record = JobRecord(request_id=request_id, status="pending", enqueued_at=started)
    _store.put(record)

    try:
        _queue.put_nowait(request_id)
    except asyncio.QueueFull:
        _store.remove(request_id)
        _metrics["rejected"] += 1
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                request_id=request_id,
                error="queue_full",
                reason="queue_full",
                started_at=started,
                finished_at=now_iso(),
            ).model_dump(),
        )

    return NonblockingAcceptResponse(
        request_id=request_id,
        started_at=started,
    )


@app.get("/scan/status/{request_id}", response_model=NonblockingStatusResponse)
async def scan_status(request_id: str):
    record = _store.get(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="request_id not found")

    return NonblockingStatusResponse(
        request_id=record.request_id,
        status=JobStatus(record.status),
        verdict=ScanVerdict(record.verdict) if record.verdict else None,
        enqueued_at=record.enqueued_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        scan_duration_ms=record.scan_duration_ms,
    )


@app.get("/metrics", response_model=MetricsResponse)
async def metrics():
    avg = 0.0
    if _metrics["processed_jobs"] > 0:
        avg = _metrics["total_processing_ms"] / _metrics["processed_jobs"]
    return MetricsResponse(
        queue_depth=_queue.qsize(),
        completed=_metrics["completed"],
        rejected=_metrics["rejected"],
        errors=_metrics["errors"],
        processed_jobs=_metrics["processed_jobs"],
        avg_processing_ms=round(avg, 2),
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "nonblocking_fixed"}
