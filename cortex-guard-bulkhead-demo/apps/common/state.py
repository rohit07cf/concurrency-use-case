"""In-memory result store with TTL-based cleanup (no external DB)."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Optional

RESULT_TTL_SECONDS = int(os.environ.get("RESULT_TTL_SECONDS", "86400"))


@dataclass
class JobRecord:
    request_id: str
    status: str = "pending"           # pending | processing | done | violation | error
    verdict: Optional[str] = None     # allow | deny | None
    enqueued_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    scan_duration_ms: Optional[float] = None
    _created_ts: float = field(default_factory=time.monotonic)


class ResultStore:
    """Thread-safe-ish (single event loop) in-memory store."""

    def __init__(self) -> None:
        self._data: dict[str, JobRecord] = {}

    def put(self, record: JobRecord) -> None:
        self._data[record.request_id] = record

    def get(self, request_id: str) -> Optional[JobRecord]:
        return self._data.get(request_id)

    def remove(self, request_id: str) -> None:
        self._data.pop(request_id, None)

    @property
    def size(self) -> int:
        return len(self._data)

    async def ttl_cleanup_loop(self, interval: float = 60.0) -> None:
        """Periodically evict records older than RESULT_TTL_SECONDS."""
        while True:
            await asyncio.sleep(interval)
            now = time.monotonic()
            expired = [
                rid for rid, rec in self._data.items()
                if (now - rec._created_ts) > RESULT_TTL_SECONDS
            ]
            for rid in expired:
                self._data.pop(rid, None)
            if expired:
                print(f"[state] TTL cleanup: evicted {len(expired)} records")
