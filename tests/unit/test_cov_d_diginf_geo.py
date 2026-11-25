"""Route tests for the infrastructure geo and entity-type APIs.

Real Quart app, real JWTs and real Postgres.  Only the outbound Nominatim
HTTP call (a true external boundary) is faked.
"""

# flake8: noqa: E501

import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from apps.api.modules.infrastructure.routes import geo
from tests.unit._cov_d_helpers import call, in_db, world  # noqa: F401

GEO = "/api/v1/geo"
ET = "/api/v1/entity-types"
RW = ["infrastructure:read", "infrastructure:write"]


class _Resp(io.BytesIO):
    """Minimal context-manager HTTP response used to fake urlopen."""

    def __enter__(self):
        """Enter the context manager and return self."""
        return self

    def __exit__(self, *a):
        """Leave the context manager without suppressing errors."""
        return False


def _resp(payload):
    """Build a fake urlopen response carrying a JSON payload."""
    return _Resp(json.dumps(payload).encode())


def test_helpers_pure():
    """Cover the pure helper functions: timestamps, coords, known lookup, query builder."""
    from datetime import UTC, datetime

    assert geo._format_ts(None) is None
    assert geo._format_ts(datetime(2024, 1, 1, tzinfo=UTC)).startswith("2024")
    assert geo._format_ts(5) == "5"
    assert geo._parse_coord("1.5", "latitude", -90, 90) == 1.5
    with pytest.raises(ValueError):
        geo._parse_coord("x", "latitude", -90, 90)
    with pytest.raises(ValueError):
        geo._parse_coord(500, "latitude", -90, 90)
    assert geo._lookup_known_location("   ") is None
    assert geo._lookup_known_location("Tokyo")["country"] == "Japan"
    assert geo._lookup_known_location("foo, London")["city"] == "London"
    assert geo._lookup_known_location("big dallas campus")["city"] == "Dallas"
    assert geo._lookup_known_location("nowhereville") is None
    assert geo._build_location_query("N", " a ", "c", None, "") == "a, c"
    assert geo._build_location_query(" nm ", None, None, None, None) == "nm"
    assert geo._build_location_query(None, None, None, None, None) == ""


def test_nominatim_sync_paths():
    """Exercise every branch of the synchronous Nominatim client."""
    good = [
        {
            "lat": "1",
            "lon": "2",
            "display_name": "X",
            "address": {"town": "T", "state": "S", "country": "C"},
        }
    ]
    with patch.object(geo.urllib.request, "urlopen", return_value=_resp(good)):
        r = geo._nominatim_geocode_sync("q")
    assert r["city"] == "T" and r["latitude"] == 1.0
    noaddr = [{"lat": 1, "lon": 2}]
    with patch.object(geo.urllib.request, "urlopen", return_value=_resp(noaddr)):
        r = geo._nominatim_geocode_sync("q")
    assert r["display_name"] == "q" and r["city"] is None
    with patch.object(geo.urllib.request, "urlopen", return_value=_resp([])):
        assert geo._nominatim_geocode_sync("q") is None
    with patch.object(geo.urllib.request, "urlopen", return_value=_resp({"a": 1})):
        assert geo._nominatim_geocode_sync("q") is None
    with patch.object(
        geo.urllib.request, "urlopen", return_value=_resp([{"lat": "bad", "lon": 1}])
    ):
        assert geo._nominatim_geocode_sync("q") is None
    with patch.object(geo.urllib.request, "urlopen", return_value=_resp([{}])):
        assert geo._nominatim_geocode_sync("q") is None
    with patch.object(
        geo.urllib.request, "urlopen", side_effect=urllib.error.URLError("down")
    ):
        assert geo._nominatim_geocode_sync("q") is None


@pytest.mark.asyncio
async def test_geocode_query_branches():
    """Resolve empty, known and remote-fallback queries."""
    assert await geo._geocode_query("  ") is None
    assert (await geo._geocode_query("Sydney"))["country"] == "Australia"
    with patch.object(
        geo, "_nominatim_geocode_sync", return_value={"latitude": 1.0, "longitude": 2.0}
    ):
        assert (await geo._geocode_query("zzz unknown"))["latitude"] == 1.0


@pytest.mark.asyncio
async def test_geocode_route(app, world):  # noqa: F811
    """Geocode endpoint: auth, validation, 404 and success."""
    t = world["tok"]("a", ["infrastructure:read"])
    assert (await call(app, "post", f"{GEO}/geocode", json={"query": "Tokyo"}))[
        0
    ] == 401
    assert (
        await call(
            app, "post", f"{GEO}/geocode", world["tok"]("a", []), json={"query": "x"}
        )
    )[0] == 403
    assert (await call(app, "post", f"{GEO}/geocode", t, json=[]))[0] == 400
    assert (await call(app, "post", f"{GEO}/geocode", t, json={"query": "  "}))[
        0
    ] == 400
    with patch.object(geo, "_nominatim_geocode_sync", return_value=None):
        s, _ = await call(app, "post", f"{GEO}/geocode", t, json={"query": "zzqq"})
    assert s == 404
    s, d = await call(app, "post", f"{GEO}/geocode", t, json={"query": "Chicago"})
    assert s == 200 and d["city"] == "Chicago"
    with patch.object(geo, "_geocode_query", side_effect=RuntimeError("boom")):
        s, d = await call(app, "post", f"{GEO}/geocode", t, json={"query": "Chicago"})
    assert s == 500 and "boom" not in json.dumps(d)


@pytest.mark.asyncio
async def test_regions_crud(app, world):  # noqa: F811
    """Region create/list/update/delete including duplicates, overwrite and isolation."""
    ta = world["tok"]("a", RW)
    tb = world["tok"]("b", RW)
    assert (await call(app, "get", f"{GEO}/regions"))[0] == 401
    assert (await call(app, "post", f"{GEO}/regions", ta, json=[]))[0] == 400
    assert (await call(app, "post", f"{GEO}/regions", ta, json={"code": "r1"}))[
        0
    ] == 400
    assert (
        await call(
            app,
            "post",
            f"{GEO}/regions",
            ta,
            json={"code": "r1", "name": "R", "latitude": 99},
        )
    )[0] == 400
    s, d = await call(
        app,
        "post",
        f"{GEO}/regions",
        ta,
        json={
            "code": "r1",
            "name": "R",
            "latitude": 1,
            "longitude": 2,
            "is_active": False,
            "provider": "p",
        },
    )
    assert s == 201 and d["is_active"] is False
    rid = d["id"]
    s, d = await call(
        app, "post", f"{GEO}/regions", ta, json={"code": "r1", "name": "R"}
    )
    assert s == 409 and d["existing_id"] == rid
    s, d = await call(
        app,
        "post",
        f"{GEO}/regions",
        ta,
        json={"code": "r1", "name": "R2", "overwrite": True},
    )
    assert s == 200 and d["name"] == "R2" and d["is_active"] is True
    s, d = await call(app, "get", f"{GEO}/regions", ta)
    assert s == 200 and d["total"] == 1
    assert (await call(app, "get", f"{GEO}/regions", tb))[1]["total"] == 0
    # update
    assert (await call(app, "put", f"{GEO}/regions/{rid}", ta, json=[]))[0] == 400
    assert (await call(app, "put", f"{GEO}/regions/{rid}", tb, json={"name": "x"}))[
        0
    ] == 404
    assert (await call(app, "put", f"{GEO}/regions/{rid}", ta, json={"code": " "}))[
        0
    ] == 400
    assert (await call(app, "put", f"{GEO}/regions/{rid}", ta, json={"name": " "}))[
        0
    ] == 400
    assert (
        await call(app, "put", f"{GEO}/regions/{rid}", ta, json={"longitude": 999})
    )[0] == 400
    s, d = await call(
        app,
        "put",
        f"{GEO}/regions/{rid}",
        ta,
        json={
            "code": "r9",
            "name": "N",
            "provider": "q",
            "latitude": None,
            "longitude": None,
            "description": "d",
            "is_active": False,
        },
    )
    assert s == 200 and d["code"] == "r9" and d["latitude"] is None
    s, d = await call(
        app, "put", f"{GEO}/regions/{rid}", ta, json={"latitude": 5, "longitude": 6}
    )
    assert d["latitude"] == 5
    # delete
    assert (await call(app, "delete", f"{GEO}/regions/{rid}", tb))[0] == 404
    assert (await call(app, "delete", f"{GEO}/regions/{rid}", ta))[0] == 200
    assert (await call(app, "delete", f"{GEO}/regions/{rid}", ta))[0] == 404


@pytest.mark.asyncio
async def test_locations_crud(app, world):  # noqa: F811
    """Location CRUD with geocoding fallback, duplicates, overwrite and validation."""
    ta = world["tok"]("a", RW)
    tb = world["tok"]("b", RW)
    org = world["a"]["org"]
    assert (await call(app, "get", f"{GEO}/locations"))[0] == 401
    assert (await call(app, "post", f"{GEO}/locations", ta, json=[]))[0] == 400
    assert (await call(app, "post", f"{GEO}/locations", ta, json={"name": " "}))[
        0
    ] == 400
    assert (
        await call(
            app,
            "post",
            f"{GEO}/locations",
            ta,
            json={"name": "L", "latitude": "x", "longitude": 1},
        )
    )[0] == 400
    with patch.object(geo, "_nominatim_geocode_sync", return_value=None):
        s, d = await call(
            app, "post", f"{GEO}/locations", ta, json={"name": "zzqq nowhere"}
        )
    assert s == 400
    # auto-geocode from known city; org-scoped
    s, d = await call(
        app,
        "post",
        f"{GEO}/locations",
        ta,
        json={"name": "DC1", "city": "Dallas", "organization_id": org},
    )
    assert (
        s == 201 and d["latitude"] == pytest.approx(32.7767) and d["state"] == "Texas"
    )
    lid = d["id"]
    s, d = await call(
        app,
        "post",
        f"{GEO}/locations",
        ta,
        json={"name": "DC1", "latitude": 1, "longitude": 2, "organization_id": org},
    )
    assert s == 409 and "organization" in d["error"]
    s, d = await call(
        app,
        "post",
        f"{GEO}/locations",
        ta,
        json={
            "name": "DC1",
            "city": "Tokyo",
            "organization_id": org,
            "overwrite": True,
            "region_code": "r",
        },
    )
    assert s == 200 and d["country"] == "Japan"
    # tenant-scoped duplicate (no org)
    s, d = await call(
        app,
        "post",
        f"{GEO}/locations",
        ta,
        json={"name": "T1", "latitude": 1, "longitude": 2, "is_active": False},
    )
    assert s == 201 and d["is_active"] is False
    s, d = await call(
        app,
        "post",
        f"{GEO}/locations",
        ta,
        json={"name": "T1", "latitude": 1, "longitude": 2},
    )
    assert s == 409 and "tenant" in d["error"]
    # only lat given -> geocode fills lon
    s, d = await call(
        app,
        "post",
        f"{GEO}/locations",
        ta,
        json={"name": "P", "latitude": 10, "country": "Singapore"},
    )
    assert s == 201 and d["latitude"] == 10
    s, d = await call(app, "get", f"{GEO}/locations", ta)
    assert d["total"] == 3
    assert (await call(app, "get", f"{GEO}/locations", tb))[1]["total"] == 0
    # update
    assert (await call(app, "put", f"{GEO}/locations/{lid}", ta, json=[]))[0] == 400
    assert (await call(app, "put", f"{GEO}/locations/{lid}", tb, json={"name": "x"}))[
        0
    ] == 404
    assert (await call(app, "put", f"{GEO}/locations/{lid}", ta, json={"name": " "}))[
        0
    ] == 400
    assert (
        await call(app, "put", f"{GEO}/locations/{lid}", ta, json={"latitude": 500})
    )[0] == 400
    s, d = await call(
        app,
        "put",
        f"{GEO}/locations/{lid}",
        ta,
        json={
            "name": "DC1b",
            "city": "x",
            "is_active": False,
            "latitude": 3,
            "longitude": 4,
        },
    )
    assert s == 200 and d["name"] == "DC1b" and d["latitude"] == 3
    # clear coords -> re-geocode from name/city; known city
    s, d = await call(
        app,
        "put",
        f"{GEO}/locations/{lid}",
        ta,
        json={"latitude": None, "longitude": None, "city": "London"},
    )
    assert s == 200 and d["latitude"] == pytest.approx(51.5072)
    # unresolvable on update
    with patch.object(geo, "_nominatim_geocode_sync", return_value=None):
        s, d = await call(
            app,
            "put",
            f"{GEO}/locations/{lid}",
            ta,
            json={
                "latitude": None,
                "city": "zzqq",
                "address": "zz",
                "state": "q",
                "country": "q",
                "name": "zzqq",
            },
        )
    assert s == 400
    # only lat cleared with lon retained
    s, d = await call(
        app, "put", f"{GEO}/locations/{lid}", ta, json={"longitude": None}
    )
    assert s == 200
    # delete
    assert (await call(app, "delete", f"{GEO}/locations/{lid}", tb))[0] == 404
    assert (await call(app, "delete", f"{GEO}/locations/{lid}", ta))[0] == 200
    assert (await call(app, "delete", f"{GEO}/locations/{lid}", ta))[0] == 404


@pytest.mark.asyncio
async def test_geo_generic_500s(app, world):  # noqa: F811
    """Unexpected DB errors map to a generic 500 without leaking internals."""
    ta = world["tok"]("a", RW)
    with patch.object(geo, "_get_tenant_id", side_effect=RuntimeError("boom")):
        for m, u, kw in [
            ("get", "/regions", {}),
            ("post", "/regions", {"json": {"code": "c", "name": "n"}}),
            ("put", "/regions/1", {"json": {"name": "n"}}),
            ("delete", "/regions/1", {}),
            ("get", "/locations", {}),
            (
                "post",
                "/locations",
                {"json": {"name": "n", "latitude": 1, "longitude": 2}},
            ),
            ("put", "/locations/1", {"json": {"name": "n"}}),
            ("delete", "/locations/1", {}),
        ]:
            s, _ = await call(app, m, f"{GEO}{u}", ta, **kw)
            assert s == 500, (m, u)


@pytest.mark.asyncio
async def test_entity_types_routes(app, world):  # noqa: F811
    """Entity-type catalog endpoints: listing, detail, subtypes, metadata, validate."""
    t = world["tok"]("a", RW)
    assert (await call(app, "get", f"{ET}/"))[0] == 401
    s, d = await call(app, "get", f"{ET}/", t)
    assert s == 200 and d["total"] > 0
    et = d["entity_types"][0]["type"]
    sub = (
        d["entity_types"][0]["subtypes"][0]
        if d["entity_types"][0]["subtypes"]
        else None
    )
    assert (await call(app, "get", f"{ET}/{et}", t))[0] == 200
    assert (await call(app, "get", f"{ET}/nope", t))[0] == 404
    assert (await call(app, "get", f"{ET}/{et}/subtypes", t))[0] == 200
    assert (await call(app, "get", f"{ET}/nope/subtypes", t))[0] == 404
    assert (await call(app, "get", f"{ET}/{et}/metadata", t))[0] == 200
    assert (await call(app, "get", f"{ET}/nope/metadata", t))[0] == 404
    assert (await call(app, "get", f"{ET}/nope/x/metadata", t))[0] == 404
    assert (await call(app, "get", f"{ET}/{et}/zzz/metadata", t))[0] == 404
    if sub:
        assert (await call(app, "get", f"{ET}/{et}/{sub}/metadata", t))[0] == 200
    # validate
    assert (await call(app, "post", f"{ET}/validate", t, json=None))[0] in (400, 415)
    assert (await call(app, "post", f"{ET}/validate", t, json={}))[0] == 400
    assert (await call(app, "post", f"{ET}/validate", t, json={"sub_type": "x"}))[
        0
    ] == 400
    s, d = await call(app, "post", f"{ET}/validate", t, json={"entity_type": et})
    assert d["valid"] is True
    s, d = await call(
        app, "post", f"{ET}/validate", t, json={"entity_type": "nope", "sub_type": "x"}
    )
    assert d["valid"] is False and d["sub_type_valid"] is False
    s, d = await call(
        app, "post", f"{ET}/validate", t, json={"entity_type": et, "sub_type": "zzz"}
    )
    assert d["valid"] is False
    if sub:
        s, d = await call(
            app, "post", f"{ET}/validate", t, json={"entity_type": et, "sub_type": sub}
        )
        assert d["valid"] is True
