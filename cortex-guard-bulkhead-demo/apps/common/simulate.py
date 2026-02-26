"""Helpers for simulating scan work: jittered sleep, cold/warm model loading."""

from __future__ import annotations

import asyncio
import os
import random
import time

# ── Seeded RNG ──────────────────────────────────────────────────────────────

_SEED = int(os.environ.get("SEED", "42"))
_rng = random.Random(_SEED)


def get_rng() -> random.Random:
    """Return the seeded Random instance (shared, not thread-safe — fine for async)."""
    return _rng


# ── Config from env ─────────────────────────────────────────────────────────

COLD_LOAD_MIN_S = float(os.environ.get("COLD_LOAD_MIN_S", "5"))
COLD_LOAD_MAX_S = float(os.environ.get("COLD_LOAD_MAX_S", "8"))
WARM_STARTUP_LOAD_S = float(os.environ.get("WARM_STARTUP_LOAD_S", "8"))
WARM_SCAN_MIN_MS = float(os.environ.get("WARM_SCAN_MIN_MS", "80"))
WARM_SCAN_MAX_MS = float(os.environ.get("WARM_SCAN_MAX_MS", "250"))
MODEL_MODE = os.environ.get("MODEL_MODE", "warm")  # "cold" or "warm"
RANDOM_VIOLATION_RATE = float(os.environ.get("RANDOM_VIOLATION_RATE", "0.02"))


# ── Simulated model state ──────────────────────────────────────────────────

_model_loaded = False


async def warm_startup_load() -> None:
    """Simulate one-time model load at startup (warm mode only)."""
    global _model_loaded
    if MODEL_MODE == "warm" and not _model_loaded:
        print(f"[simulate] Warm startup: loading model ({WARM_STARTUP_LOAD_S}s) ...")
        await asyncio.sleep(WARM_STARTUP_LOAD_S)
        _model_loaded = True
        print("[simulate] Model loaded and warm.")


async def simulate_scan() -> float:
    """Simulate scan work. Returns wall-clock seconds spent scanning.

    Cold mode: each call pays full model-load + scan latency.
    Warm mode: fast jittered scan only (model already loaded at startup).
    """
    rng = get_rng()

    if MODEL_MODE == "cold":
        delay = rng.uniform(COLD_LOAD_MIN_S, COLD_LOAD_MAX_S)
        await asyncio.sleep(delay)
        return delay

    # warm: fast scan
    delay_ms = rng.uniform(WARM_SCAN_MIN_MS, WARM_SCAN_MAX_MS)
    await asyncio.sleep(delay_ms / 1000.0)
    return delay_ms / 1000.0


def decide_verdict() -> str:
    """Return 'allow' or 'deny' based on RANDOM_VIOLATION_RATE."""
    if get_rng().random() < RANDOM_VIOLATION_RATE:
        return "deny"
    return "allow"


# ── Timing helpers ──────────────────────────────────────────────────────────

def now_iso() -> str:
    """ISO-8601 UTC timestamp string."""
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


class Timer:
    """Simple wall-clock context-manager timer."""

    def __init__(self) -> None:
        self.start: float = 0
        self.end: float = 0

    def __enter__(self) -> "Timer":
        self.start = time.monotonic()
        return self

    def __exit__(self, *_: object) -> None:
        self.end = time.monotonic()

    @property
    def elapsed_ms(self) -> float:
        return (self.end - self.start) * 1000.0
