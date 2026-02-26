"""Aggressive spike test — demonstrates collapse (baseline) vs stability (fixed).

Use TARGET_HOST env var to point at either service:
  TARGET_HOST=http://localhost:8000 locust -f locustfile_spike.py   # baseline
  TARGET_HOST=http://localhost:8002 locust -f locustfile_spike.py   # fixed blocking

This is a BLOCKING-ONLY test to reproduce the "200 blocking calls in 2 seconds"
burst scenario described in the problem statement.
"""

import os

from locust import HttpUser, LoadTestShape, constant, task

SCAN_PAYLOAD = {"content": "Scan this content for compliance violations immediately."}
TARGET_HOST = os.environ.get("TARGET_HOST", "http://localhost:8000")
CLIENT_TIMEOUT = 5


class SpikeBlockingUser(HttpUser):
    """Sends only blocking scan requests — pure blocking burst."""
    host = TARGET_HOST
    wait_time = constant(0)  # fire as fast as possible

    @task
    def scan_blocking(self):
        with self.client.post(
            "/scan/blocking",
            json=SCAN_PAYLOAD,
            timeout=CLIENT_TIMEOUT,
            catch_response=True,
            name="/scan/blocking (spike)",
        ) as resp:
            if resp.status_code in (200, 403):
                resp.success()
            elif resp.status_code in (429, 503, 504):
                resp.failure(f"Rejected/timeout: {resp.status_code}")
            else:
                resp.failure(f"Unexpected: {resp.status_code}")


class AggressiveSpikeShape(LoadTestShape):
    """Ramp to 200 users in 2 seconds, hold, then crash test.

    This reproduces the "200 blocking calls in 2 seconds" scenario.
    """
    stages = [
        # (duration_s, users, spawn_rate)
        (2, 200, 100),    # 200 users in 2 seconds
        (30, 200, 100),   # hold at 200 for 28s
        (40, 400, 200),   # spike harder to 400
        (60, 400, 200),   # hold
        (70, 50, 50),     # cool down
    ]

    def tick(self):
        run_time = self.get_run_time()
        for stage_end, users, spawn_rate in self.stages:
            if run_time < stage_end:
                return users, spawn_rate
        return None
