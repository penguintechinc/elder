"""Unit tests for ``apps/api/utils`` helpers and ``apps/api/auth/web_auth``.

async_utils (threadpool retry/reconnect, task groups, batching) and
pydal_helpers run against the real Postgres of the test harness (``tenants``
table, unique rows per test); validation helpers cover every rejection branch.
"""

import datetime
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import g

from apps.api.auth import web_auth
from apps.api.utils import async_utils, pydal_helpers
from apps.api.utils import validation_helpers as vh


def _sfx():
    """Unique suffix so rows never collide across tests or reruns."""
    return uuid4().hex[:10]


def _new_tenant(db, **extra):
    """Insert a throwaway tenant row and return its id."""
    sfx = _sfx()
    tid = db.tenants.insert(
        name=f"cov-utils-{sfx}", slug=f"cov-utils-{sfx}", is_active=True, **extra
    )
    db.commit()
    return tid


async def _body(result):
    """Decode a (response, status) tuple into (json, status)."""
    resp, status = result
    return json.loads(await resp.get_data()), status


class TestRunInThreadpool:
    """run_in_threadpool retries connection errors only, and propagates context."""

    @pytest.mark.asyncio
    async def test_returns_result_and_passes_args(self):
        """Positional and keyword arguments reach the blocking function."""
        assert await async_utils.run_in_threadpool(lambda a, b=0: a + b, 2, b=3) == 5

    @pytest.mark.asyncio
    async def test_non_connection_error_is_not_retried(self):
        """A business error is raised immediately after a single attempt."""
        func = MagicMock(side_effect=ValueError("bad input"))
        with pytest.raises(ValueError, match="bad input"):
            await async_utils.run_in_threadpool(func)
        assert func.call_count == 1

    @pytest.mark.asyncio
    async def test_connection_error_retried_with_reconnect(self, app):
        """A dropped connection triggers adapter reconnect and a successful retry."""
        func = MagicMock(side_effect=[RuntimeError("Connection reset by peer"), "ok"])
        fake_app = SimpleNamespace(db=MagicMock())
        with patch("quart.current_app", fake_app):
            assert await async_utils.run_in_threadpool(func) == "ok"
        assert func.call_count == 2
        fake_app.db._adapter.close.assert_called_once_with()
        fake_app.db._adapter.reconnect.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_reconnect_failures_do_not_mask_retry(self):
        """Failing close()/reconnect() are tolerated; the operation is still retried."""
        func = MagicMock(side_effect=[RuntimeError("cursor already closed"), 7])
        fake_app = SimpleNamespace(db=MagicMock())
        fake_app.db._adapter.close.side_effect = RuntimeError("close failed")
        fake_app.db._adapter.reconnect.side_effect = RuntimeError("still down")
        with patch("quart.current_app", fake_app):
            assert await async_utils.run_in_threadpool(func) == 7
        assert func.call_count == 2

    @pytest.mark.asyncio
    async def test_app_without_db_still_retries(self):
        """An app object lacking ``db`` skips reconnect but retries the call."""
        func = MagicMock(side_effect=[RuntimeError("lost connection"), "done"])
        with patch("quart.current_app", SimpleNamespace()):
            assert await async_utils.run_in_threadpool(func) == "done"

    @pytest.mark.asyncio
    async def test_gives_up_after_two_retries(self):
        """A persistent connection error is re-raised after three attempts total."""
        func = MagicMock(side_effect=RuntimeError("connection refused"))
        with patch("quart.current_app", SimpleNamespace()):
            with pytest.raises(RuntimeError, match="connection refused"):
                await async_utils.run_in_threadpool(func)
        assert func.call_count == 3

    @pytest.mark.asyncio
    async def test_request_context_visible_in_worker(self, app):
        """The caller's request context is propagated into the worker thread."""
        from quart import request

        async with app.test_request_context("/x?marker=abc"):
            got = await async_utils.run_in_threadpool(
                lambda: request.args.get("marker")
            )
        assert got == "abc"


class TestThreadpoolHelpers:
    """The no-retry runner, decorator, task group and batching helpers."""

    @pytest.mark.asyncio
    async def test_no_retry_runs_exactly_once(self):
        """run_in_threadpool_no_retry never retries, even on connection errors."""
        func = MagicMock(side_effect=RuntimeError("connection reset"))
        with pytest.raises(RuntimeError):
            await async_utils.run_in_threadpool_no_retry(func, 1, k=2)
        func.assert_called_once_with(1, k=2)

    @pytest.mark.asyncio
    async def test_no_retry_returns_value(self):
        """run_in_threadpool_no_retry returns the function result."""
        assert await async_utils.run_in_threadpool_no_retry(pow, 2, 5) == 32

    @pytest.mark.asyncio
    async def test_to_thread_decorator(self):
        """@to_thread makes a sync function awaitable and keeps its name."""

        @async_utils.to_thread
        def add(a, b):
            return a + b

        assert add.__name__ == "add"
        assert await add(2, 3) == 5

    @pytest.mark.asyncio
    async def test_run_parallel_preserves_order(self):
        """run_parallel returns results in task order."""
        tasks = [async_utils.run_in_threadpool(lambda v=v: v * 2) for v in (1, 2, 3)]
        assert await async_utils.run_parallel(*tasks) == [2, 4, 6]

    @pytest.mark.asyncio
    async def test_task_group_collects_results(self):
        """AsyncTaskGroup runs tasks to completion inside the context."""

        async def double(v):
            return v * 2

        async with async_utils.AsyncTaskGroup() as tg:
            t1 = tg.create_task(double(4))
            t2 = tg.create_task(double(5))
        assert (t1.result(), t2.result()) == (8, 10)

    @pytest.mark.asyncio
    async def test_task_group_propagates_failure(self):
        """A failing task surfaces as an ExceptionGroup when the group exits."""

        async def boom():
            raise ValueError("task failed")

        with pytest.raises(ExceptionGroup) as exc:
            async with async_utils.AsyncTaskGroup() as tg:
                tg.create_task(boom())
        assert isinstance(exc.value.exceptions[0], ValueError)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("batch_size, expected_batches", [(2, 3), (10, 1), (1, 5)])
    async def test_batch_execute_order_and_batching(self, batch_size, expected_batches):
        """batch_execute keeps item order and never exceeds the batch size."""
        active, peak = 0, 0

        async def work(item):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await __import__("asyncio").sleep(0)
            active -= 1
            return item * 10

        items = [1, 2, 3, 4, 5]
        out = await async_utils.batch_execute(items, work, batch_size=batch_size)
        assert out == [10, 20, 30, 40, 50]
        assert peak <= batch_size

    @pytest.mark.asyncio
    async def test_batch_execute_empty(self):
        """An empty item list yields an empty result without calling func."""
        func = MagicMock()
        assert await async_utils.batch_execute([], func) == []
        func.assert_not_called()

    def test_shutdown_thread_pool_waits(self):
        """shutdown_thread_pool shuts the executor down and waits for workers."""
        with patch.object(async_utils, "_executor") as executor:
            async_utils.shutdown_thread_pool()
        executor.shutdown.assert_called_once_with(wait=True)


class TestPydalHelpers:
    """pydal_helpers against the real database."""

    @pytest.mark.asyncio
    async def test_query_select_orderby_and_limit(self, app):
        """query_select applies ordering and limitby to a real query."""
        db = app.db
        async with app.app_context():
            marker = f"cov-sel-{_sfx()}"
            ids = [
                await pydal_helpers.insert_record(
                    db.tenants,
                    name=marker,
                    slug=f"{marker}-{i}",
                    is_active=True,
                )
                for i in range(3)
            ]
            await pydal_helpers.commit_db(db)
            rows = await pydal_helpers.query_select(
                db(db.tenants.name == marker),
                orderby=~db.tenants.id,
                limitby=(0, 2),
            )
            assert [r.id for r in rows] == sorted(ids, reverse=True)[:2]
            all_rows = await pydal_helpers.query_select(db(db.tenants.name == marker))
            assert sorted(r.id for r in all_rows) == sorted(ids)

    @pytest.mark.asyncio
    async def test_update_record_changes_row(self, app):
        """update_record persists new values and reports success."""
        db = app.db
        async with app.app_context():
            tid = _new_tenant(db)
            ok = await pydal_helpers.update_record(db.tenants, tid, domain="x.test")
            await pydal_helpers.commit_db(db)
            assert ok is True
            assert db.tenants[tid].domain == "x.test"

    @pytest.mark.asyncio
    async def test_update_record_missing_row(self, app):
        """update_record returns False for an id that does not exist."""
        async with app.app_context():
            ok = await pydal_helpers.update_record(
                app.db.tenants, 2_000_000_000, domain="x.test"
            )
            assert ok is False

    @pytest.mark.asyncio
    async def test_update_record_without_data_is_noop_true(self, app):
        """update_record with no fields leaves the row untouched and returns True."""
        db = app.db
        async with app.app_context():
            tid = _new_tenant(db, domain="keep.test")
            assert await pydal_helpers.update_record(db.tenants, tid) is True
            assert db.tenants[tid].domain == "keep.test"

    @pytest.mark.asyncio
    async def test_delete_record_removes_existing_row(self, app):
        """delete_record removes an existing row and returns True."""
        db = app.db
        async with app.app_context():
            tid = _new_tenant(db)
            assert await pydal_helpers.delete_record(db.tenants, tid) is True
            await pydal_helpers.commit_db(db)
            assert db.tenants[tid] is None

    @pytest.mark.asyncio
    async def test_delete_record_missing_row(self, app):
        """delete_record returns False when the row does not exist."""
        async with app.app_context():
            assert (
                await pydal_helpers.delete_record(app.db.tenants, 2_000_000_005)
                is False
            )


class TestValidationHelpers:
    """Pure validators: cron, timezone and shift configuration."""

    @pytest.mark.asyncio
    async def test_validate_tenant_exists(self, app):
        """An existing tenant is returned; a missing one yields a 404 error."""
        async with app.app_context():
            tid = _new_tenant(app.db)
            tenant, error = await vh.validate_tenant_exists(tid)
            assert tenant.id == tid and error is None
            tenant, error = await vh.validate_tenant_exists(2_000_000_004)
            assert tenant is None
            assert error[1] == 404

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "expr, valid",
        [
            ("0 0 * * *", True),
            ("*/5 9-17 * * 1-5", True),
            ("not a cron", False),
            ("61 0 * * *", False),
            ("", False),
        ],
    )
    async def test_validate_cron_expression(self, app, expr, valid):
        """Well-formed cron passes; malformed/out-of-range expressions get a 400."""
        async with app.app_context():
            result = vh.validate_cron_expression(expr)
        if valid:
            assert result is None
        else:
            data, status = await _body(result)
            assert status == 400
            assert "Invalid cron expression" in json.dumps(data)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tz, valid", [("UTC", True), ("Europe/London", True), ("Mars/Olympus", False)]
    )
    async def test_validate_timezone(self, app, tz, valid):
        """Known IANA zones pass; unknown names are rejected with 400."""
        async with app.app_context():
            result = vh.validate_timezone(tz)
        if valid:
            assert result is None
        else:
            data, status = await _body(result)
            assert status == 400
            assert tz in json.dumps(data)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "config",
        [
            {
                "timezones": [
                    {"timezone": "UTC", "shift_start_hour": 9, "shift_end_hour": 17}
                ]
            },
            {
                "timezones": [
                    {"timezone": "UTC", "shift_start_hour": 6, "shift_end_hour": 14},
                    {
                        "timezone": "US/Eastern",
                        "shift_start_hour": 14,
                        "shift_end_hour": 22,
                    },
                    {
                        "timezone": "Asia/Tokyo",
                        "shift_start_hour": 22,
                        "shift_end_hour": 6,
                    },
                ]
            },
        ],
    )
    async def test_validate_shift_config_accepts(self, app, config):
        """Non-overlapping shifts (including a wrap-around shift) are valid."""
        async with app.app_context():
            assert vh.validate_shift_config(config) is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "config, fragment",
        [
            ("nope", "must be a dictionary"),
            ({}, "non-empty timezones list"),
            ({"timezones": "UTC"}, "non-empty timezones list"),
            ({"timezones": []}, "non-empty timezones list"),
            ({"timezones": ["UTC"]}, "Each timezone entry must be a dictionary"),
            ({"timezones": [{"shift_start_hour": 1}]}, "must have a 'timezone' field"),
            (
                {
                    "timezones": [
                        {
                            "timezone": "No/Where",
                            "shift_start_hour": 1,
                            "shift_end_hour": 2,
                        }
                    ]
                },
                "Invalid timezone: No/Where",
            ),
            (
                {"timezones": [{"timezone": "UTC", "shift_start_hour": 1}]},
                "must have shift_start_hour and shift_end_hour",
            ),
            (
                {"timezones": [{"timezone": "UTC", "shift_end_hour": 1}]},
                "must have shift_start_hour and shift_end_hour",
            ),
            (
                {
                    "timezones": [
                        {
                            "timezone": "UTC",
                            "shift_start_hour": "9",
                            "shift_end_hour": 17,
                        }
                    ]
                },
                "shift hours must be integers",
            ),
            (
                {
                    "timezones": [
                        {"timezone": "UTC", "shift_start_hour": 24, "shift_end_hour": 3}
                    ]
                },
                "shift_start_hour must be 0-23",
            ),
            (
                {
                    "timezones": [
                        {"timezone": "UTC", "shift_start_hour": -1, "shift_end_hour": 3}
                    ]
                },
                "shift_start_hour must be 0-23",
            ),
            (
                {
                    "timezones": [
                        {"timezone": "UTC", "shift_start_hour": 3, "shift_end_hour": 24}
                    ]
                },
                "shift_end_hour must be 0-23",
            ),
            (
                {
                    "timezones": [
                        {"timezone": "UTC", "shift_start_hour": 5, "shift_end_hour": 5}
                    ]
                },
                "cannot equal shift_end_hour",
            ),
            (
                {
                    "timezones": [
                        {
                            "timezone": "UTC",
                            "shift_start_hour": 8,
                            "shift_end_hour": 16,
                        },
                        {
                            "timezone": "US/Eastern",
                            "shift_start_hour": 12,
                            "shift_end_hour": 20,
                        },
                    ]
                },
                "US/Eastern: shift hours overlap",
            ),
            (
                {
                    "timezones": [
                        {"timezone": "UTC", "shift_start_hour": 0, "shift_end_hour": 4},
                        {
                            "timezone": "Asia/Tokyo",
                            "shift_start_hour": 22,
                            "shift_end_hour": 2,
                        },
                    ]
                },
                "Asia/Tokyo: shift hours overlap",
            ),
        ],
    )
    async def test_validate_shift_config_rejects(self, app, config, fragment):
        """Each malformed shift configuration is rejected with a specific 400."""
        async with app.app_context():
            data, status = await _body(vh.validate_shift_config(config))
        assert status == 400
        assert fragment in json.dumps(data)


class TestValidateNoOverlap:
    """validate_no_overlap against real on-call override rows."""

    @pytest.mark.asyncio
    async def test_overlap_detection_and_exclusion(self, app):
        """Overlaps are found per rotation+identity; the excluded id is ignored."""
        db = app.db
        sfx = _sfx()
        now = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
        hour = datetime.timedelta(hours=1)
        async with app.app_context():
            tid = _new_tenant(db)
            idents = [
                db.identities.insert(
                    identity_type="human",
                    username=f"cov-ov-{i}-{sfx}",
                    email=f"cov-ov-{i}-{sfx}@example.com",
                    tenant_id=tid,
                    auth_provider="local",
                    is_active=True,
                    is_superuser=False,
                    mfa_enabled=False,
                    must_change_password=False,
                    portal_role="observer",
                    created_at=now,
                    updated_at=now,
                )
                for i in range(2)
            ]
            rotation_fields = {
                "name": f"cov-rot-{sfx}",
                "is_active": True,
                "scope_type": "organization",
                "schedule_type": "weekly",
            }
            rotation_fields["tenant_id"] = tid
            rot = db.on_call_rotations.insert(**rotation_fields)
            override_fields = {
                "rotation_id": rot,
                "original_identity_id": idents[0],
                "override_identity_id": idents[1],
                "start_datetime": now,
                "end_datetime": now + 4 * hour,
            }
            ov = db.on_call_overrides.insert(**override_fields)
            db.commit()

            overlap = await vh.validate_no_overlap(
                db, rot, idents[0], now + hour, now + 2 * hour
            )
            adjacent = await vh.validate_no_overlap(
                db, rot, idents[0], now + 4 * hour, now + 5 * hour
            )
            other_identity = await vh.validate_no_overlap(
                db, rot, idents[1], now + hour, now + 2 * hour
            )
            excluded = await vh.validate_no_overlap(
                db, rot, idents[0], now + hour, now + 2 * hour, exclude_override_id=ov
            )
            data, status = await _body(overlap)
        assert status == 400
        assert "overlaps with an existing override" in json.dumps(data)
        assert adjacent is None
        assert other_identity is None
        assert excluded is None


class TestWebAuth:
    """Cookie-based SSR auth helpers."""

    @pytest.mark.asyncio
    async def test_no_cookie_is_anonymous_and_cached(self, app):
        """Without the session cookie the user is None and the result is cached."""
        async with app.test_request_context("/x"):
            with patch.object(web_auth, "verify_token") as verify:
                assert web_auth.get_web_user() is None
                assert web_auth.get_web_user() is None
            verify.assert_not_called()
            assert g._web_user is None

    @pytest.mark.asyncio
    async def test_cookie_is_verified_once(self, app):
        """A cookie token is verified once and the payload cached on ``g``."""
        token = "-".join(["fake", "session", _sfx()])
        headers = {"Cookie": f"{web_auth.COOKIE_NAME}={token}"}
        payload = {"sub": "42"}
        async with app.test_request_context("/x", headers=headers):
            with patch.object(web_auth, "verify_token", return_value=payload) as verify:
                assert web_auth.get_web_user() == payload
                assert web_auth.get_web_user() == payload
            verify.assert_called_once_with(token, web_auth.IDENTITY_ACCESS_TOKEN_TYPES)

    @pytest.mark.asyncio
    async def test_invalid_cookie_is_anonymous(self, app):
        """A tampered/invalid cookie resolves to no user."""
        headers = {"Cookie": f"{web_auth.COOKIE_NAME}=garbage-{_sfx()}"}
        async with app.test_request_context("/x", headers=headers):
            assert web_auth.get_web_user() is None

    @pytest.mark.asyncio
    async def test_web_login_required_redirects_anonymous(self, app):
        """Anonymous SSR requests are redirected to the login page."""
        handler = MagicMock()

        @web_auth.web_login_required
        async def view():
            handler()
            return "page"

        async with app.test_request_context("/x"):
            with patch.object(web_auth, "url_for", return_value="/login") as url_for:
                resp = await view()
        url_for.assert_called_once_with("web.login")
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/login"
        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_web_login_required_allows_authenticated(self, app):
        """A request with a valid session reaches the wrapped view."""

        @web_auth.web_login_required
        async def view(value):
            return f"page-{value}"

        async with app.test_request_context("/x"):
            g._web_user = {"sub": "1"}
            assert await view(3) == "page-3"

    def test_session_cookie_set_and_cleared(self):
        """set/clear use the hardened cookie attributes on the response."""
        resp = MagicMock()
        token = "-".join(["fake", "jwt", _sfx()])
        web_auth.set_session_cookie(resp, token, secure=False)
        resp.set_cookie.assert_called_once_with(
            web_auth.COOKIE_NAME,
            token,
            max_age=web_auth.COOKIE_MAX_AGE,
            httponly=True,
            secure=False,
            samesite="Strict",
            path="/",
        )
        assert web_auth.clear_session_cookie(resp) is resp
        resp.delete_cookie.assert_called_once_with(web_auth.COOKIE_NAME, path="/")
