"""Coverage tests for small pure helpers under ``shared`` (certificates, redaction, fleetdm, ids, api utils, events)."""

import sys
import types
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import redis as real_redis
from flask import Flask
from marshmallow import Schema, ValidationError, fields

from shared import api_utils, certificates, events, fleetdm_helpers, redaction
from shared.utils import village_id as vid


# --------------------------------------------------------------------------- certificates
@pytest.mark.parametrize(
    ("offset", "revoked", "expected"),
    [
        (10, True, "revoked"),
        (None, False, "pending"),
        (-1, False, "expired"),
        (5, False, "expiring_soon"),
        (30, False, "expiring_soon"),
        (31, False, "active"),
    ],
)
def test_certificate_status_matrix(offset, revoked, expected):
    """Every status branch of the certificate calculator is reached via a date offset."""
    exp = None if offset is None else date.today() + timedelta(days=offset)
    assert certificates.calculate_certificate_status(exp, 30, revoked) == expected


# --------------------------------------------------------------------------- redaction
def test_redact_credential_keys_replaces_only_known_keys():
    """Known credential keys are masked case-insensitively and other keys survive."""
    out = redaction.redact_credential_keys({"API_TOKEN": "x", "region": "eu"})
    assert out == {"API_TOKEN": redaction.REDACTED_PLACEHOLDER, "region": "eu"}


@pytest.mark.parametrize("empty", [None, {}])
def test_redact_credential_keys_passes_empty_through(empty):
    """Falsy configs are returned untouched rather than copied."""
    assert redaction.redact_credential_keys(empty) is empty


def test_redact_url_variants():
    """URLs without userinfo or password are unchanged; passwords are masked with port kept."""
    assert redaction.redact_url(None) == ""
    assert redaction.redact_url("") == ""
    assert redaction.redact_url("http://host/x") == "http://host/x"
    assert redaction.redact_url("http://user@host/x") == "http://user@host/x"
    pw = "pw" + "val"
    masked = redaction.redact_url(f"postgres://bob:{pw}@db:5432/app?x=1#f")
    assert masked == "postgres://bob:***@db:5432/app?x=1#f"
    assert pw not in masked
    assert redaction.redact_url(f"postgres://bob:{pw}@db/app") == (
        "postgres://bob:***@db/app"
    )


def test_redact_url_unparseable_inputs():
    """A bare '@' with no netloc, or a bad port, yields the placeholder and never echoes input."""
    assert redaction.redact_url("not-a-url@") == "<unparseable url>"
    assert redaction.redact_url("http://u:p@host:notaport/") == "<unparseable url>"


# --------------------------------------------------------------------------- fleetdm helpers
def test_fleetdm_sensitive_key_detection():
    """Exact and substring sensitive keys are flagged, innocuous ones are not."""
    assert fleetdm_helpers._is_sensitive_key("Password")
    assert fleetdm_helpers._is_sensitive_key("my_api_secret_value")
    assert not fleetdm_helpers._is_sensitive_key("hostname")


def test_fleetdm_sanitize_dict_recurses():
    """Sensitive keys are removed at any depth, including inside lists."""
    data = {"a": [{"token": 1, "ok": 2}], "secret": 3, "b": "v"}
    assert fleetdm_helpers._sanitize_dict(data) == {"a": [{"ok": 2}], "b": "v"}


def test_fleetdm_extract_safe_mdm():
    """Only whitelisted MDM keys are kept; non-dicts return None."""
    assert fleetdm_helpers._extract_safe_mdm("x") is None
    raw = {"name": "n", "enrollment_status": "on", "junk": 1}
    assert fleetdm_helpers._extract_safe_mdm(raw) == {
        "enrollment_status": "on",
        "name": "n",
    }


@pytest.mark.parametrize(
    ("score", "label"),
    [(None, "unknown"), (9.5, "critical"), (7, "high"), (4.0, "medium"), (1, "low")],
)
def test_fleetdm_cvss_to_severity(score, label):
    """Each CVSS band maps to its severity label."""
    assert fleetdm_helpers._cvss_to_severity(score) == label


def test_fleetdm_safe_json():
    """204, invalid JSON and valid JSON bodies are handled."""
    assert fleetdm_helpers._safe_json(httpx.Response(204)) is None
    assert fleetdm_helpers._safe_json(httpx.Response(200, content=b"nope")) is None
    assert fleetdm_helpers._safe_json(httpx.Response(200, json={"a": 1})) == {"a": 1}


def test_fleetdm_parse_retry_after():
    """Retry-After is clamped, bad values fall back to bounded exponential backoff."""
    ok = httpx.Response(429, headers={"Retry-After": "7"})
    assert fleetdm_helpers._parse_retry_after(ok, 0) == 7.0
    big = httpx.Response(429, headers={"Retry-After": "999"})
    assert fleetdm_helpers._parse_retry_after(big, 0) == 30.0
    neg = httpx.Response(429, headers={"Retry-After": "-4"})
    assert fleetdm_helpers._parse_retry_after(neg, 0) == 0.0
    bad = httpx.Response(429, headers={"Retry-After": "soon"})
    assert fleetdm_helpers._parse_retry_after(bad, 3) == 8.0
    none = httpx.Response(429)
    assert fleetdm_helpers._parse_retry_after(none, 9) == 10.0
    assert fleetdm_helpers._parse_retry_after(types.SimpleNamespace(), 1) == 2.0


# --------------------------------------------------------------------------- village id
def test_generate_village_id_validation_and_format():
    """Sync generation validates inputs and formats tenant + sequence as hex."""
    client = MagicMock()
    client.incr.return_value = 255
    assert vid.generate_village_id(42, client) == "0000002a-00000000000000ff"
    client.incr.assert_called_once_with("elder:vid:0000002a")
    with pytest.raises(ValueError):
        vid.generate_village_id(-1, client)
    with pytest.raises(ValueError):
        vid.generate_village_id("x", client)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        vid.generate_village_id(1, None)


@pytest.mark.asyncio
async def test_agenerate_village_id_validation_and_format():
    """Async generation mirrors the sync contract."""
    client = AsyncMock()
    client.incr.return_value = 1
    assert await vid.agenerate_village_id(1, client) == "00000001-0000000000000001"
    with pytest.raises(ValueError):
        await vid.agenerate_village_id(-5, client)
    with pytest.raises(ValueError):
        await vid.agenerate_village_id(1, None)


def test_parse_and_validate_village_id():
    """Valid IDs parse into components; malformed ones are rejected."""
    parsed = vid.parse_village_id("0000002a-000000000000f3c1")
    assert (parsed.tenant_id, parsed.object_seq) == (42, 0xF3C1)
    assert vid.is_valid_village_id("0000002a-000000000000f3c1")
    assert not vid.is_valid_village_id("BAD")
    assert not vid.is_valid_village_id(123)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        vid.parse_village_id("BAD")


# --------------------------------------------------------------------------- api utils
def _query(total=95, items=None):
    """Return a chainable query double with a fixed total and item list."""
    q = MagicMock()
    q.count.return_value = total
    q.limit.return_value = q
    q.offset.return_value = q
    q.all.return_value = items if items is not None else ["i"]
    q.filter.return_value = q
    return q


def test_paginate_metadata():
    """Pagination computes pages, offsets and prev/next flags."""
    q = _query()
    items, meta = api_utils.paginate(q, page=2, per_page=50)
    assert items == ["i"]
    q.offset.assert_called_with(50)
    assert meta == {
        "total": 95,
        "page": 2,
        "per_page": 50,
        "pages": 2,
        "has_prev": True,
        "has_next": False,
    }
    _, zero = api_utils.paginate(_query(), page=1, per_page=0)
    assert zero["pages"] == 0


def test_get_pagination_params_clamps():
    """Request args are clamped to sane bounds."""
    app = Flask(__name__)
    with app.test_request_context("/?page=-3&per_page=5000"):
        assert api_utils.get_pagination_params() == {"page": 1, "per_page": 1000}
    with app.test_request_context("/?per_page=0"):
        assert api_utils.get_pagination_params() == {"page": 1, "per_page": 1}
    with app.test_request_context("/"):
        assert api_utils.get_pagination_params() == {"page": 1, "per_page": 50}


class _Schema(Schema):
    """Tiny schema requiring an integer ``n``."""

    n = fields.Int(required=True)


def test_validate_request_paths():
    """Validation uses explicit data, request JSON, or an empty body."""
    assert api_utils.validate_request(_Schema, {"n": "3"}) == {"n": 3}
    app = Flask(__name__)
    with app.test_request_context("/", method="POST", json={"n": 4}):
        assert api_utils.validate_request(_Schema) == {"n": 4}
    with app.test_request_context("/", method="POST", json={}):
        with pytest.raises(ValidationError) as exc:
            api_utils.validate_request(_Schema)
    resp, status = api_utils.handle_validation_error(exc.value)
    assert status == 400
    assert "n" in resp["validation_errors"]


def test_response_builders():
    """Error and success builders include optional fields only when given."""
    body, code = api_utils.make_error_response("bad", 422, extra=1)
    assert code == 422 and body["error"] is True and body["extra"] == 1
    body, code = api_utils.make_success_response()
    assert body == {"error": False, "status_code": 200} and code == 200
    body, _ = api_utils.make_success_response({"a": 1}, "ok", 201)
    assert body["data"] == {"a": 1} and body["message"] == "ok"


def test_apply_filters_branches():
    """Name filters use ILIKE, other set filters equality, None/unknown are skipped."""

    class Model:
        """Model double exposing two filterable columns."""

        name = MagicMock()
        kind = MagicMock()

    q = _query()
    api_utils.apply_filters(
        q, Model, {"name": "ab", "kind": "x", "skip": 1, "missing": None, "none": None}
    )
    Model.name.ilike.assert_called_once_with("%ab%")
    assert q.filter.call_count == 2
    q2 = _query()
    api_utils.apply_filters(q2, Model, {"kind": None})
    q2.filter.assert_not_called()


def _fake_db_modules(instance):
    """Build sys.modules entries providing ``apps.api.database.db`` returning ``instance``."""
    db_mod = types.ModuleType("apps.api.database")
    db_mod.db = MagicMock()
    db_mod.db.session.get.return_value = instance
    return {"apps.api.database": db_mod}


def test_get_or_404_found_and_missing():
    """A found instance is returned; a missing one aborts with 404 and a message."""

    class Thing:
        """Model double used only for its name."""

    obj = Thing()
    with patch.dict(sys.modules, _fake_db_modules(obj)):
        assert api_utils.get_or_404(Thing, 1) is obj
    from werkzeug.exceptions import NotFound

    with patch.dict(sys.modules, _fake_db_modules(None)):
        with pytest.raises(NotFound) as exc:
            api_utils.get_or_404(Thing, 7)
        assert "Thing with id 7" in exc.value.description
        with pytest.raises(NotFound) as exc2:
            api_utils.get_or_404(Thing, 7, "custom")
        assert exc2.value.description == "custom"


# --------------------------------------------------------------------------- events
@pytest.mark.asyncio
async def test_emit_event_connection_error_is_swallowed():
    """A Redis connection failure is logged, never raised."""
    fake = MagicMock()
    fake.ConnectionError = real_redis.ConnectionError
    fake.from_url.side_effect = real_redis.ConnectionError("down")
    with (
        patch.dict(sys.modules, {"redis": fake}),
        patch("quart.current_app", types.SimpleNamespace(config={})),
    ):
        await events.emit_event("d", "created", 1)
    fake.from_url.assert_called_once_with("redis://localhost:6379/0")


@pytest.mark.asyncio
async def test_emit_event_import_error_and_generic_error():
    """A missing redis package and an app-context failure are both swallowed."""
    with patch.dict(sys.modules, {"redis": None}):
        await events.emit_event("d", "a", 1)
    # Real redis, no app context: current_app access raises RuntimeError.
    await events.emit_event("d", "a", 1, tenant_id=3, payload={"k": 1})


@pytest.mark.asyncio
async def test_emit_event_publishes_to_stream():
    """A healthy Redis receives one XADD on the domain stream with the full envelope."""
    import json

    client = MagicMock()
    fake = MagicMock()
    fake.ConnectionError = real_redis.ConnectionError
    fake.from_url.return_value = client
    with (
        patch.dict(sys.modules, {"redis": fake}),
        patch(
            "quart.current_app",
            types.SimpleNamespace(config={"REDIS_URL": "redis://r:1/2"}),
        ),
    ):
        await events.emit_event("issues", "created", 9, tenant_id=4, payload={"a": 1})
    fake.from_url.assert_called_once_with("redis://r:1/2")
    key, fields_ = client.xadd.call_args.args
    assert key == "elder:events:issues"
    env = json.loads(fields_["event"])
    assert env["resource_id"] == "9" and env["tenant_id"] == 4
    assert env["payload"] == {"a": 1}
