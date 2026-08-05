"""Regression tests for run_in_threadpool's Quart request-context propagation.

regression: run_in_threadpool must propagate Quart request context to worker thread
"""

import pytest

from apps.api.utils.async_utils import run_in_threadpool


@pytest.mark.asyncio
async def test_run_in_threadpool_propagates_request_args(app):
    """A threadpooled function must be able to read request.args.

    Previously ``run_in_threadpool`` copied Flask's request context via
    ``flask.copy_current_request_context``, which is always empty under
    Quart (Quart's request/g/current_app are contextvar-backed proxies, not
    Flask's). ``has_request_context()`` returned False and the copy was
    silently skipped, so any threadpooled function touching ``request``
    raised ``RuntimeError: Not within a request context`` (e.g. GET
    /api/v1/issues 500'd). The fix copies the caller's
    ``contextvars.Context`` into the executor thread instead.
    """

    async with app.test_request_context(
        "/api/v1/issues?status=open", method="GET"
    ):

        def read_status():
            from quart import request

            return request.args.get("status")

        result = await run_in_threadpool(read_status)

    assert result == "open"


@pytest.mark.asyncio
async def test_run_in_threadpool_propagates_g(app):
    """A threadpooled function must be able to read values set on Quart's ``g``."""

    async with app.test_request_context("/api/v1/issues", method="GET"):
        from quart import g

        g.foo = "bar"

        def read_g_foo():
            from quart import g as thread_g

            return thread_g.foo

        result = await run_in_threadpool(read_g_foo)

    assert result == "bar"
