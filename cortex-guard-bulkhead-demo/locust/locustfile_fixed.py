"""Locust load test targeting the FIXED services.

Nonblocking → port 8001 (nonblocking_api)
Blocking    → port 8002 (blocking_api)

Traffic mix: 10:1 nonblocking:blocking.

Run two separate Locust instances or use environment variables:

  # Terminal 1 — nonblocking traffic
  FIXED_HOST=http://localhost:8001 FIXED_MODE=nonblocking locust -f locustfile_fixed.py

  # Terminal 2 — blocking traffic
  FIXED_HOST=http://localhost:8002 FIXED_MODE=blocking locust -f locustfile_fixed.py --web-port 8092

Or run both with the combined user below (default):
  locust -f locustfile_fixed.py
"""

import os

from locust import HttpUser, LoadTestShape, between, task

SCAN_PAYLOAD = {"content": "This is a sample text that needs to be scanned for policy violations."}
CLIENT_TIMEOUT_NB = 3   # nonblocking accept should be fast
CLIENT_TIMEOUT_BLK = 10  # blocking has a longer deadline

NONBLOCKING_HOST = os.environ.get("NONBLOCKING_HOST", "http://localhost:8001")
BLOCKING_HOST = os.environ.get("BLOCKING_HOST", "http://localhost:8002")


class FixedNonblockingUser(HttpUser):
    """Sends only nonblocking requests to the nonblocking service."""
    host = NONBLOCKING_HOST
    wait_time = between(0.1, 0.5)
    weight = 10  # 10x more of these users

    @task
    def scan_nonblocking(self):
        with self.client.post(
            "/scan/nonblocking",
            json=SCAN_PAYLOAD,
            timeout=CLIENT_TIMEOUT_NB,
            catch_response=True,
            name="[NB] /scan/nonblocking",
        ) as resp:
            if resp.status_code == 202:
                resp.success()
            elif resp.status_code == 503:
                resp.failure("Queue full (backpressure working)")
            else:
                resp.failure(f"Unexpected: {resp.status_code}")


class FixedBlockingUser(HttpUser):
    """Sends only blocking requests to the blocking service."""
    host = BLOCKING_HOST
    wait_time = between(0.1, 0.5)
    weight = 1  # 1x

    @task
    def scan_blocking(self):
        with self.client.post(
            "/scan/blocking",
            json=SCAN_PAYLOAD,
            timeout=CLIENT_TIMEOUT_BLK,
            catch_response=True,
            name="[BLK] /scan/blocking",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code == 429:
                resp.failure("Over capacity (admission control working)")
            elif resp.status_code == 403:
                resp.success()  # violation detected — valid response
            elif resp.status_code == 504:
                resp.failure("Deadline exceeded")
            else:
                resp.failure(f"Unexpected: {resp.status_code}")


class BurstShape(LoadTestShape):
    """Same burst shape as baseline for apples-to-apples comparison."""
    stages = [
        (10, 50, 25),
        (30, 150, 50),
        (60, 200, 100),
        (90, 200, 100),
        (120, 50, 20),
    ]

    def tick(self):
        run_time = self.get_run_time()
        for stage_end, users, spawn_rate in self.stages:
            if run_time < stage_end:
                return users, spawn_rate
        return None
