"""Pydantic request/response models for the Cortex-Guard bulkhead demo."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Requests ────────────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=100_000, description="Text to scan")
    metadata: Optional[dict] = Field(default=None, description="Optional metadata")


# ── Enums ───────────────────────────────────────────────────────────────────

class ScanVerdict(str, Enum):
    allow = "allow"
    deny = "deny"


class JobStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    done = "done"
    violation = "violation"
    error = "error"


# ── Responses ───────────────────────────────────────────────────────────────

class BlockingScanResponse(BaseModel):
    request_id: str
    verdict: ScanVerdict
    started_at: str
    finished_at: str
    scan_duration_ms: float
    model_mode: str


class NonblockingAcceptResponse(BaseModel):
    request_id: str
    status: str = "accepted"
    started_at: str
    message: str = "Job enqueued for scanning"


class NonblockingStatusResponse(BaseModel):
    request_id: str
    status: JobStatus
    verdict: Optional[ScanVerdict] = None
    enqueued_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    scan_duration_ms: Optional[float] = None


class ErrorResponse(BaseModel):
    request_id: str
    error: str
    reason: str
    started_at: str
    finished_at: str


class MetricsResponse(BaseModel):
    """Generic metrics envelope — each app adds its own fields."""
    inflight: int = 0
    completed: int = 0
    rejected: int = 0
    errors: int = 0
    queue_depth: int = 0
    processed_jobs: int = 0
    avg_processing_ms: float = 0.0
