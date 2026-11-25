"""ProductionConfig must fail closed on the JWT/session signing secret.

`config.py` ships an in-repo dev fallback value for SECRET_KEY
("dev-secret-key-change-in-production"), and jwt_handler falls back to
SECRET_KEY whenever JWT_SECRET_KEY is unset. Before this fix,
ProductionConfig.init_app never checked either -- a production deploy that
forgot to set the secret would silently sign real JWTs with a value
visible in source control, letting anyone forge a token for any
tenant/identity. Regression: security review finding #3.
"""

import pytest

from apps.api.config import _DEV_SECRET_KEY_VALUE, ProductionConfig


class _FakeApp:
    """Minimal stand-in for a Quart app -- init_app only touches .config
    and (when SYSLOG_ENABLED) .logger."""

    def __init__(self, config: dict):
        self.config = config
        self.logger = None


class TestProductionSecretKeyFailsClosed:
    def test_missing_secret_key_raises(self):
        app = _FakeApp({"SECRET_KEY": None, "JWT_SECRET_KEY": "a-real-strong-secret"})
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            ProductionConfig.init_app(app)

    def test_dev_value_secret_key_raises(self):
        app = _FakeApp(
            {
                "SECRET_KEY": _DEV_SECRET_KEY_VALUE,
                "JWT_SECRET_KEY": "a-real-strong-secret",
            }
        )
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            ProductionConfig.init_app(app)

    def test_empty_string_secret_key_raises(self):
        app = _FakeApp({"SECRET_KEY": "", "JWT_SECRET_KEY": "a-real-strong-secret"})
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            ProductionConfig.init_app(app)


class TestProductionJwtSecretKeyFailsClosed:
    def test_missing_jwt_secret_falls_back_to_missing_secret_key_raises(self):
        """JWT_SECRET_KEY unset with a bad SECRET_KEY must still raise --
        checked via the SECRET_KEY branch since jwt_handler falls back to it."""
        app = _FakeApp({"SECRET_KEY": None, "JWT_SECRET_KEY": None})
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            ProductionConfig.init_app(app)

    def test_jwt_secret_key_equal_to_dev_value_raises_even_with_good_secret_key(self):
        """A real SECRET_KEY does not excuse an explicitly-set dev-valued
        JWT_SECRET_KEY -- jwt_handler prefers JWT_SECRET_KEY when present."""
        app = _FakeApp(
            {
                "SECRET_KEY": "a-real-strong-secret",
                "JWT_SECRET_KEY": _DEV_SECRET_KEY_VALUE,
            }
        )
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            ProductionConfig.init_app(app)

    def test_jwt_secret_key_unset_falls_back_to_good_secret_key_succeeds(self):
        """JWT_SECRET_KEY unset is fine as long as the SECRET_KEY it falls
        back to is a real, non-dev value."""
        app = _FakeApp(
            {
                "SECRET_KEY": "a-real-strong-secret",
                "JWT_SECRET_KEY": None,
                "SYSLOG_ENABLED": False,
            }
        )
        ProductionConfig.init_app(app)  # must not raise

    def test_both_secrets_set_to_strong_distinct_values_succeeds(self):
        app = _FakeApp(
            {
                "SECRET_KEY": "a-real-strong-secret",
                "JWT_SECRET_KEY": "a-different-real-strong-secret",
                "SYSLOG_ENABLED": False,
            }
        )
        ProductionConfig.init_app(app)  # must not raise


class TestDevelopmentAndTestingConfigsUnaffected:
    """Only ProductionConfig fails closed -- dev/test configs must keep
    working with the checked-in dev fallback, no env vars required."""

    def test_development_config_allows_dev_secret(self):
        from apps.api.config import DevelopmentConfig

        app = _FakeApp({"SECRET_KEY": _DEV_SECRET_KEY_VALUE, "SYSLOG_ENABLED": False})
        DevelopmentConfig.init_app(app)  # must not raise

    def test_testing_config_allows_dev_secret(self):
        from apps.api.config import TestingConfig

        app = _FakeApp({"SECRET_KEY": _DEV_SECRET_KEY_VALUE, "SYSLOG_ENABLED": False})
        TestingConfig.init_app(app)  # must not raise
