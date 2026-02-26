"""Locust load test targeting the BASELINE combined_api (port 8000).

Traffic mix: 10:1 nonblocking:blocking.
Demonstrates shared-capacity queueing failure under burst load.
"""

from locust import HttpUser, between, task


SCAN_PAYLOAD = {"content": "This is a sample text that needs to be scanned for policy violations."}
CLIENT_TIMEOUT = 5  # seconds — typical upstream timeout


class BaselineUser(HttpUser):
    host = "http://localhost:8000"
    wait_time = between(0.1, 0.5)

    @task(10)
    def scan_nonblocking(self):
        with self.client.post(
            "/scan/nonblocking",
            json=SCAN_PAYLOAD,
            timeout=CLIENT_TIMEOUT,
            catch_response=True,
            name="/scan/nonblocking",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code in (504, 503, 429):
                resp.failure(f"Overload: {resp.status_code}")
            else:
                resp.failure(f"Unexpected: {resp.status_code}")

    @task(1)
    def scan_blocking(self):
        with self.client.post(
            "/scan/blocking",
            json=SCAN_PAYLOAD,
            timeout=CLIENT_TIMEOUT,
            catch_response=True,
            name="/scan/blocking",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code in (504, 503, 429):
                resp.failure(f"Overload: {resp.status_code}")
            else:
                resp.failure(f"Unexpected: {resp.status_code}")


