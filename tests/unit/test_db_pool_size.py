"""Tests for shared/database/__init__.py's _get_pool_size().

Regression coverage: DB_POOL_SIZE was read into app config/env by callers
but never consulted here -- every penguin-dal DAL instance was hardcoded to
pool_size=10 regardless of what was configured.
"""

from __future__ import annotations

import pytest

from shared.database import (
    _DEFAULT_DB_POOL_SIZE,
    _MAX_DB_POOL_SIZE,
    _MIN_DB_POOL_SIZE,
    _get_pool_size,
)


class _FakeApp:
    """Minimal stand-in for a Flask/Quart app's .config mapping."""

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}


class TestGetPoolSize:
    def test_no_config_or_env_returns_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DB_POOL_SIZE", raising=False)
        app = _FakeApp({})
        assert _get_pool_size(app) == _DEFAULT_DB_POOL_SIZE

    def test_app_config_value_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DB_POOL_SIZE", raising=False)
        app = _FakeApp({"DB_POOL_SIZE": "25"})
        assert _get_pool_size(app) == 25

    def test_env_var_is_used_when_config_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DB_POOL_SIZE", "30")
        app = _FakeApp({})
        assert _get_pool_size(app) == 30

    def test_app_config_takes_precedence_over_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DB_POOL_SIZE", "30")
        app = _FakeApp({"DB_POOL_SIZE": "15"})
        assert _get_pool_size(app) == 15

    def test_invalid_value_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DB_POOL_SIZE", raising=False)
        app = _FakeApp({"DB_POOL_SIZE": "not-a-number"})
        assert _get_pool_size(app) == _DEFAULT_DB_POOL_SIZE

    def test_value_above_max_is_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DB_POOL_SIZE", raising=False)
        app = _FakeApp({"DB_POOL_SIZE": str(_MAX_DB_POOL_SIZE + 500)})
        assert _get_pool_size(app) == _MAX_DB_POOL_SIZE

    def test_value_below_min_is_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DB_POOL_SIZE", raising=False)
        app = _FakeApp({"DB_POOL_SIZE": "0"})
        assert _get_pool_size(app) == _MIN_DB_POOL_SIZE

    def test_negative_value_is_clamped_to_min(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DB_POOL_SIZE", raising=False)
        app = _FakeApp({"DB_POOL_SIZE": "-5"})
        assert _get_pool_size(app) == _MIN_DB_POOL_SIZE
