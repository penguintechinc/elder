"""Pytest configuration and fixtures for Elder tests.

This module provides fixtures for both unit tests and integration tests.
Unit tests should use mocks and not require external services.
Integration tests may use the actual database.

Migration Notes:
    * Elder uses PyDAL for database operations (not SQLAlchemy). The PyDAL
      instance lives on ``app.db`` and is independent of request/app context.
    * The application runs on **Quart** (ASGI/async), not Flask. Quart's test
      client and app context are asynchronous (``async with``/``await``).

    Historically the test suite was written for Flask's synchronous test
    client (``response = client.get(...)``; ``with app.app_context():``).
    To keep those tests working unchanged against Quart, the fixtures below
    expose **synchronous adapters** that drive Quart's coroutines on a
    dedicated background event loop. Because the coroutines run on a separate
    thread, the adapters block safely whether the calling test is a plain
    ``def`` test or an ``async def`` test running under pytest-asyncio — there
    is no "event loop is already running" problem.
"""

import asyncio
import os
import threading

import pytest

# Set testing environment before any app imports
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/elder_unit_test.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ---------------------------------------------------------------------------
# Background event loop: runs Quart coroutines synchronously from any thread.
# ---------------------------------------------------------------------------
class _BackgroundLoop:
    """A persistent asyncio event loop running on its own thread."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro):
        """Submit *coro* to the background loop and block for its result."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)
        self.loop.close()


@pytest.fixture(scope="session")
def _bg_loop():
    bg = _BackgroundLoop()
    yield bg
    bg.close()


# ---------------------------------------------------------------------------
# Synchronous adapters around Quart's async test client / app context.
# ---------------------------------------------------------------------------
class _SyncResponse:
    """Wrap a Quart Response so tests can read it synchronously."""

    def __init__(self, response, bg):
        self._response = response
        self._bg = bg
        self.status_code = response.status_code
        self.headers = response.headers

    @property
    def data(self):
        return self._bg.run(self._response.get_data())

    def get_data(self, as_text=False):
        data = self._bg.run(self._response.get_data())
        if as_text and isinstance(data, bytes):
            return data.decode("utf-8")
        return data

    def get_json(self, silent=False):
        return self._bg.run(self._response.get_json())

    @property
    def json(self):
        return self._bg.run(self._response.get_json())


class _SyncClient:
    """Synchronous facade over Quart's async ``QuartClient``."""

    def __init__(self, quart_client, bg):
        self._client = quart_client
        self._bg = bg

    def _request(self, method, *args, **kwargs):
        coro = getattr(self._client, method)(*args, **kwargs)
        response = self._bg.run(coro)
        return _SyncResponse(response, self._bg)

    def get(self, *a, **k):
        return self._request("get", *a, **k)

    def post(self, *a, **k):
        return self._request("post", *a, **k)

    def put(self, *a, **k):
        return self._request("put", *a, **k)

    def patch(self, *a, **k):
        return self._request("patch", *a, **k)

    def delete(self, *a, **k):
        return self._request("delete", *a, **k)

    def open(self, *a, **k):
        return self._request("open", *a, **k)


def _drive(coro):
    """Run a coroutine to completion synchronously in the *current* context.

    Unlike ``loop.run_until_complete`` (which runs the coroutine inside a Task
    with a copied contextvars context), stepping the coroutine directly keeps
    any ``ContextVar`` changes — such as Quart binding ``current_app`` — visible
    in the calling thread. Quart's ``AppContext.push`` performs only synchronous
    work, so it drives to completion without needing a running event loop.
    """
    try:
        while True:
            coro.send(None)
    except StopIteration as exc:
        return exc.value


class _SyncAppContext:
    """Synchronous Quart app context bound on the calling (main) thread.

    ``__enter__`` pushes a real Quart app context so ``current_app`` (and hence
    helpers that call ``jsonify``/``current_app.db``) work inside the ``with``
    block. ``__exit__`` unbinds by resetting the context var directly rather
    than running Quart's async teardown (which would require a running event
    loop); unit-test app contexts hold no resources that need async teardown.
    """

    def __init__(self, app, bg):
        self._app = app
        self._ctx = app.app_context()

    def __enter__(self):
        _drive(self._ctx.push())
        return self._app

    def __exit__(self, exc_type, exc, tb):
        try:
            from quart.ctx import _cv_app

            tokens = getattr(self._ctx, "_cv_tokens", None)
            if tokens:
                _cv_app.reset(tokens[-1])
        except (LookupError, ValueError, ImportError):
            pass
        return False


class _SyncApp:
    """Proxy exposing a Quart app with synchronous helpers for tests.

    Attribute access falls through to the real app, so ``app.db``,
    ``app.config``, ``app.url_map`` etc. work unchanged; ``app_context()``
    and ``test_client()`` are overridden to return synchronous adapters.
    """

    def __init__(self, app, bg):
        object.__setattr__(self, "_app", app)
        object.__setattr__(self, "_bg", bg)

    def app_context(self):
        return _SyncAppContext(self._app, self._bg)

    def test_client(self, *args, **kwargs):
        return _SyncClient(self._app.test_client(*args, **kwargs), self._bg)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_app"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_app"), name, value)


@pytest.fixture(scope="session")
def app(_bg_loop):
    """Create the Quart application for testing (synchronous adapter)."""
    from apps.api.main import create_app

    asgi_app = create_app("testing")
    # create_app returns an ASGI adapter; unwrap to the underlying Quart app.
    quart_app = getattr(asgi_app, "wsgi_application", asgi_app)
    quart_app.config["TESTING"] = True

    sync_app = _SyncApp(quart_app, _bg_loop)
    yield sync_app

    # Cleanup: drop the SQLite test database file if present.
    db_url = str(quart_app.config.get("DATABASE_URL", ""))
    if "sqlite" in db_url:
        for candidate in ("test.db", "test.db.sqlite"):
            try:
                if os.path.exists(candidate):
                    os.remove(candidate)
            except OSError:
                pass


@pytest.fixture(scope="function")
def client(app):
    """Create a synchronous Quart test client."""
    return app.test_client()


@pytest.fixture(scope="function")
def db_session(app):
    """Provide the PyDAL database instance (``app.db``) for tests."""
    with app.app_context():
        yield app.db


@pytest.fixture(scope="function")
def db(app):
    """Alias for :func:`db_session` — PyDAL database instance."""
    with app.app_context():
        yield app.db


@pytest.fixture(scope="function")
def auth_headers(client):
    """Provide authenticated headers for API tests.

    Logs in with the admin user created during DB initialization and returns
    an ``Authorization`` header with the issued JWT. Falls back to empty
    headers if login is unavailable (tests may then skip or use mocks).
    """
    response = client.post(
        "/api/v1/portal-auth/login",
        json={
            "email": os.getenv("ADMIN_EMAIL", "admin@localhost.local"),
            "password": os.getenv("ADMIN_PASSWORD", "admin123"),
        },
    )

    if response.status_code == 200:
        data = response.get_json() or {}
        token = data.get("access_token") or data.get("token")
        if token:
            return {"Authorization": f"Bearer {token}"}

    return {}


@pytest.fixture
def mock_pydal_db(mocker):
    """Create a mock PyDAL database for unit tests that must not touch a DB."""
    from unittest.mock import MagicMock

    mock_db = MagicMock()
    mock_db.tables = []
    mock_db.commit = MagicMock()
    mock_db.rollback = MagicMock()

    return mock_db


# Markers for test categories
def pytest_configure(config):
    """Configure custom pytest markers."""
    config.addinivalue_line("markers", "unit: Unit tests (no external dependencies)")
    config.addinivalue_line(
        "markers", "integration: Integration tests (requires database)"
    )
    config.addinivalue_line(
        "markers", "e2e: End-to-end tests (requires full Docker environment)"
    )
    config.addinivalue_line("markers", "slow: Tests that take a long time to run")
