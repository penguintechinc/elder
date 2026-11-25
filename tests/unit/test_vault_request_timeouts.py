"""Tests for Vault HTTP timeouts and bounded read retries.

A Vault call without a timeout pins its worker forever when Vault hangs;
these tests prove every call carries an explicit (connect, read) timeout,
that only idempotent reads are retried, and that a real unresponsive socket
is abandoned at the configured read timeout.
"""

from __future__ import annotations

import socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from apps.api.common.providers import vault as vault_provider
from apps.api.common.providers.vault import vault_request, vault_timeout
from shared.utils.env import env_float, env_int


def _response(status_code: int = 200) -> MagicMock:
    """Build a mock Vault response with the given status."""
    response = MagicMock()
    response.status_code = status_code
    response.text = "{}"
    response.json.return_value = {}
    return response


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Record retry sleeps instead of waiting."""
    delays: list[float] = []
    monkeypatch.setattr(vault_provider, "_sleep", delays.append)
    return delays


def test_default_timeout_is_applied_to_every_request():
    """A call without a timeout gets connect=3s / read=10s."""
    session = MagicMock()
    session.request.return_value = _response()

    vault_request(session, "POST", "https://vault.example.com/v1/x", json={"a": 1})

    assert session.request.call_args.kwargs["timeout"] == (3.0, 10.0)
    assert session.request.call_args.kwargs["json"] == {"a": 1}


def test_timeout_is_env_configurable(monkeypatch):
    """VAULT_*_TIMEOUT_SECONDS override the defaults."""
    monkeypatch.setenv("VAULT_CONNECT_TIMEOUT_SECONDS", "1.5")
    monkeypatch.setenv("VAULT_READ_TIMEOUT_SECONDS", "25")

    assert vault_timeout() == (1.5, 25.0)


@pytest.mark.parametrize(
    "garbage", ["", "  ", "abc", "nan", "inf", "-inf", "1e999", "3s"]
)
def test_garbage_timeout_env_falls_back_to_default(monkeypatch, garbage):
    """An unparseable or non-finite value never raises and uses the default."""
    monkeypatch.setenv("VAULT_CONNECT_TIMEOUT_SECONDS", garbage)
    monkeypatch.setenv("VAULT_READ_TIMEOUT_SECONDS", garbage)

    assert vault_timeout() == (3.0, 10.0)


def test_timeout_env_is_clamped(monkeypatch):
    """Zero/negative/huge values are clamped into a sane range (never 'no timeout')."""
    monkeypatch.setenv("VAULT_CONNECT_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("VAULT_READ_TIMEOUT_SECONDS", "-5")
    assert vault_timeout() == (0.1, 0.1)

    monkeypatch.setenv("VAULT_CONNECT_TIMEOUT_SECONDS", "100000")
    monkeypatch.setenv("VAULT_READ_TIMEOUT_SECONDS", "100000")
    assert vault_timeout() == (60.0, 300.0)


def test_caller_timeout_is_respected():
    """An explicit per-call timeout is not overwritten."""
    session = MagicMock()
    session.request.return_value = _response()

    vault_request(session, "GET", "https://vault.example.com/v1/x", timeout=1)

    assert session.request.call_args.kwargs["timeout"] == 1


@pytest.mark.parametrize("method", ["GET", "LIST", "get"])
def test_idempotent_read_is_retried_then_succeeds(method, _no_real_sleep):
    """A read survives transient connection errors and 503s within the budget."""
    session = MagicMock()
    session.request.side_effect = [
        requests.exceptions.ConnectionError("down"),
        _response(503),
        _response(200),
    ]

    response = vault_request(session, method, "https://vault.example.com/v1/x")

    assert response.status_code == 200
    assert session.request.call_count == 3
    assert len(_no_real_sleep) == 2
    assert 0.1 <= _no_real_sleep[0] <= 0.2
    assert 0.2 <= _no_real_sleep[1] <= 0.4


def test_read_retries_are_bounded(_no_real_sleep):
    """A permanently failing read raises after exactly the attempt cap."""
    session = MagicMock()
    session.request.side_effect = requests.exceptions.ReadTimeout("slow")

    with pytest.raises(requests.exceptions.ReadTimeout):
        vault_request(session, "GET", "https://vault.example.com/v1/x")

    assert session.request.call_count == 3
    assert len(_no_real_sleep) == 2


def test_last_retryable_status_is_returned_not_swallowed():
    """When every attempt returns 503 the caller still sees the 503."""
    session = MagicMock()
    session.request.return_value = _response(503)

    response = vault_request(session, "GET", "https://vault.example.com/v1/x")

    assert response.status_code == 503
    assert session.request.call_count == 3


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_writes_are_never_retried(method, _no_real_sleep):
    """Non-idempotent calls get exactly one attempt on error and on 503."""
    session = MagicMock()
    session.request.side_effect = requests.exceptions.ConnectionError("down")
    with pytest.raises(requests.exceptions.ConnectionError):
        vault_request(session, method, "https://vault.example.com/v1/x")
    assert session.request.call_count == 1

    session = MagicMock()
    session.request.return_value = _response(503)
    assert (
        vault_request(session, method, "https://v.example.com/v1/x").status_code == 503
    )
    assert session.request.call_count == 1
    assert _no_real_sleep == []


def test_non_retryable_status_returns_immediately():
    """4xx and 500 responses on reads are not retried."""
    for status in (400, 403, 404, 500):
        session = MagicMock()
        session.request.return_value = _response(status)
        assert (
            vault_request(session, "GET", "https://v.example.com/x").status_code
            == status
        )
        assert session.request.call_count == 1


def test_read_attempt_env_is_safe(monkeypatch):
    """A garbage attempt cap falls back to 3; a valid one is honoured."""
    session = MagicMock()
    session.request.side_effect = requests.exceptions.ConnectionError("down")
    monkeypatch.setenv("VAULT_MAX_READ_ATTEMPTS", "many")
    with pytest.raises(requests.exceptions.ConnectionError):
        vault_request(session, "GET", "https://vault.example.com/v1/x")
    assert session.request.call_count == 3

    session.request.reset_mock()
    monkeypatch.setenv("VAULT_MAX_READ_ATTEMPTS", "1")
    with pytest.raises(requests.exceptions.ConnectionError):
        vault_request(session, "GET", "https://vault.example.com/v1/x")
    assert session.request.call_count == 1


@pytest.fixture
def silent_server():
    """A TCP server that accepts connections and never sends a byte."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    accepted: list[socket.socket] = []
    stop = threading.Event()

    def accept_loop() -> None:
        server.settimeout(0.1)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
                accepted.append(conn)
            except OSError:
                continue

    thread = threading.Thread(target=accept_loop, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.getsockname()[1]}"
    stop.set()
    thread.join(timeout=2)
    for conn in accepted:
        conn.close()
    server.close()


def _real_client(cls_path: str, url: str):
    """Instantiate a Vault client class against ``url`` with a real session."""
    module_path, cls_name = cls_path.rsplit(".", 1)
    module = __import__(module_path, fromlist=[cls_name])
    return getattr(module, cls_name)({"url": url, "token": "s.test-token-value"})


@pytest.mark.parametrize(
    "cls_path",
    [
        "apps.api.services.secrets.vault_client.HashicorpVaultClient",
        "apps.api.services.keys.vault_client.VaultTransitClient",
    ],
)
def test_real_unresponsive_vault_times_out_with_clear_error(
    silent_server, monkeypatch, caplog, cls_path
):
    """Against a socket that never answers, the client fails fast and clearly."""
    monkeypatch.setenv("VAULT_CONNECT_TIMEOUT_SECONDS", "0.5")
    monkeypatch.setenv("VAULT_READ_TIMEOUT_SECONDS", "0.3")
    monkeypatch.setenv("VAULT_MAX_READ_ATTEMPTS", "2")
    client = _real_client(cls_path, silent_server)

    started = time.monotonic()
    with caplog.at_level("DEBUG"), pytest.raises(Exception) as excinfo:
        client._make_request("GET", "/v1/sys/health")
    elapsed = time.monotonic() - started

    # Two attempts at ~0.3s each; without a timeout this would hang forever.
    assert 0.5 <= elapsed < 5.0
    message = str(excinfo.value)
    assert "timed out" in message
    assert "read timeout 0.3s" in message
    assert "/v1/sys/health" in message
    assert "s.test-token-value" not in message
    assert "s.test-token-value" not in caplog.text


def test_real_unresponsive_vault_write_is_not_retried(silent_server, monkeypatch):
    """A hung write is abandoned after one read timeout, not retried."""
    monkeypatch.setenv("VAULT_READ_TIMEOUT_SECONDS", "0.3")
    client = _real_client(
        "apps.api.services.keys.vault_client.VaultTransitClient", silent_server
    )

    started = time.monotonic()
    with pytest.raises(Exception, match="timed out"):
        client._make_request(
            "POST", "/v1/transit/encrypt/k", json={"plaintext": "eA=="}
        )
    elapsed = time.monotonic() - started

    assert 0.25 <= elapsed < 0.9


def test_vault_clients_route_through_vault_request():
    """Both clients call vault_request, so neither can skip the timeout."""
    for cls_path in (
        "apps.api.services.secrets.vault_client.HashicorpVaultClient",
        "apps.api.services.keys.vault_client.VaultTransitClient",
    ):
        session = MagicMock()
        session.request.return_value = _response()
        with patch(
            "apps.api.common.providers.vault.create_vault_session", return_value=session
        ):
            client = _real_client(cls_path, "https://vault.example.com")
        client._make_request("GET", "/v1/sys/health")
        assert session.request.call_args.kwargs["timeout"] == (3.0, 10.0)


def test_env_parsers_never_raise(monkeypatch):
    """env_int/env_float clamp and fall back for every malformed input."""
    monkeypatch.delenv("ELDER_TEST_KNOB", raising=False)
    assert env_int("ELDER_TEST_KNOB", 5, minimum=1, maximum=9) == 5
    assert env_float("ELDER_TEST_KNOB", 2.5, minimum=1, maximum=9) == 2.5
    for raw, expected in (("7", 7), ("0", 1), ("99", 9), ("4.5", 5), ("x", 5), ("", 5)):
        monkeypatch.setenv("ELDER_TEST_KNOB", raw)
        assert env_int("ELDER_TEST_KNOB", 5, minimum=1, maximum=9) == expected
    for raw, expected in (("7.5", 7.5), ("-1", 1.0), ("1e9", 9.0), ("nan", 2.5)):
        monkeypatch.setenv("ELDER_TEST_KNOB", raw)
        assert env_float("ELDER_TEST_KNOB", 2.5, minimum=1, maximum=9) == expected
