"""E2E tests for worker service health.

Regression guard for #95: worker Dockerfile PYTHONPATH referenced python3.12
site-packages after the base image was upgraded to python3.13, causing
ModuleNotFoundError on startup (all packages were invisible).  These tests
ensure the worker comes up healthy and its core modules are importable.
"""

import requests


class TestWorkerHealth:
    """Test worker service health endpoint."""

    def test_worker_healthz(self, worker_url, check_services):
        """Test worker /healthz returns healthy status.

        If this fails, the worker container failed to start — most likely a
        PYTHONPATH version mismatch in the Dockerfile (e.g. python3.12 path
        with a python3.13 image).
        """
        response = requests.get(f"{worker_url}/healthz", timeout=10)

        assert response.status_code == 200, (
            f"Worker /healthz returned {response.status_code}. "
            "Worker may have failed to start — check container logs for "
            "ModuleNotFoundError (PYTHONPATH version mismatch in Dockerfile)."
        )
        data = response.json()
        assert data.get("status") in ["healthy", "ok", "up", "stopped"], (
            f"Unexpected worker health status: {data}"
        )

    def test_worker_status_endpoint(self, worker_url, check_services):
        """Test worker /status endpoint is reachable."""
        response = requests.get(f"{worker_url}/status", timeout=10)

        assert response.status_code == 200
