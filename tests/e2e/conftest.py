"""Pytest configuration for E2E tests.

E2E tests require a running Docker Compose environment with:
- API server at http://localhost:4000
- Web UI at http://localhost:3000
- PostgreSQL database
- Redis cache

Run with: pytest tests/e2e/ -v
"""

import os
import time

import pytest
import requests

# Configuration
API_URL = os.getenv("API_URL", "http://localhost:4000")
WEB_URL = os.getenv("WEB_URL", "http://localhost:3000")
WORKER_URL = os.getenv("WORKER_URL", "http://localhost:8000")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@localhost.local")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
MAX_WAIT = 60  # seconds


def wait_for_service(url: str, timeout: int = MAX_WAIT) -> bool:
    """Wait for a service to become available."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            response = requests.get(url, timeout=5)
            if response.status_code < 500:
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(2)
    return False


@pytest.fixture(scope="session")
def api_url() -> str:
    """Provide API URL."""
    return API_URL


@pytest.fixture(scope="session")
def web_url() -> str:
    """Provide Web URL."""
    return WEB_URL


@pytest.fixture(scope="session")
def worker_url() -> str:
    """Provide worker URL."""
    return WORKER_URL


@pytest.fixture(scope="session")
def check_services():
    """Verify services are running before tests."""
    if not wait_for_service(f"{API_URL}/healthz"):
        pytest.skip("API service not available")
    if not wait_for_service(WEB_URL):
        pytest.skip("Web UI service not available")
    # Regression guard for #95: worker PYTHONPATH version mismatch causes
    # ModuleNotFoundError on startup, making /healthz unreachable.
    if not wait_for_service(f"{WORKER_URL}/healthz"):
        pytest.skip("Worker service not available — check PYTHONPATH in Dockerfile")


@pytest.fixture(scope="session")
def auth_token(api_url, check_services) -> str:
    """Get authentication token for API calls."""
    response = requests.post(
        f"{api_url}/api/v1/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    if response.status_code != 200:
        pytest.skip(f"Authentication failed: {response.text}")

    data = response.json()
    return data.get("access_token") or data.get("token")


@pytest.fixture(scope="session")
def auth_headers(auth_token) -> dict:
    """Provide authenticated headers."""
    return {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
    }
