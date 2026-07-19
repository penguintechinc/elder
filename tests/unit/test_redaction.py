"""Tests for URL credential redaction.

Regression: the worker logged its Redis and database URLs verbatim, writing the
password into container logs on every startup.
"""

import pytest

from shared.redaction import redact_url


class TestRedactUrl:
    def test_password_is_removed(self):
        out = redact_url("redis://:hunter2@redis:6379/0")
        assert "hunter2" not in out
        assert out == "redis://:***@redis:6379/0"

    def test_username_is_preserved(self):
        out = redact_url("postgres://elder:s3cret@postgres:5432/elder")
        assert "s3cret" not in out
        assert out == "postgres://elder:***@postgres:5432/elder"

    def test_url_without_password_is_unchanged(self):
        url = "postgres://postgres:5432/elder"
        assert redact_url(url) == url

    def test_non_default_port_is_preserved(self):
        out = redact_url("postgres://u:p@db.example.com:55432/elder")
        assert out == "postgres://u:***@db.example.com:55432/elder"

    def test_query_and_path_are_preserved(self):
        out = redact_url("postgres://u:p@db:5432/elder?sslmode=require")
        assert out == "postgres://u:***@db:5432/elder?sslmode=require"

    @pytest.mark.parametrize("value", ["", None])
    def test_empty_values_are_safe(self, value):
        assert redact_url(value) == ""

    def test_password_never_leaks_via_error_path(self):
        # An unparseable URL must not fall back to echoing the raw value.
        out = redact_url("://:supersecret@[")
        assert "supersecret" not in out
