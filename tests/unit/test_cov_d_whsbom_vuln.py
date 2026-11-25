"""Coverage tests for the SBOM vulnerability clients and matcher.

The HTTP boundary is replaced with ``httpx.MockTransport`` (real httpx client,
fake network); matcher persistence runs against the real Postgres schema.
"""

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from apps.api.services.sbom.vulnerability import nvd_client as nvd_mod
from apps.api.services.sbom.vulnerability.matcher import (
    MatchedVulnerability,
    VulnerabilityMatcher,
)
from apps.api.services.sbom.vulnerability.nvd_client import NVDClient, RateLimiter
from apps.api.services.sbom.vulnerability.osv_client import (
    OSVClient,
    OSVVulnerability,
)
from tests.unit import _cov_d_helpers as _cov_helpers
from tests.unit._cov_d_helpers import in_db, vid

#: Re-export the shared two-tenant fixture for pytest discovery in this module.
world = _cov_helpers.world


def _mock_client(handler) -> httpx.AsyncClient:
    """Build a real httpx client whose network is the given handler."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _osv_raw(vid_: str = "GHSA-aaaa-bbbb-cccc", score_vec: str | None = None) -> dict:
    """Build a raw OSV vulnerability payload."""
    raw = {
        "id": vid_,
        "aliases": ["CVE-2024-0001"],
        "summary": "sum",
        "details": "det",
        "affected": [
            {
                "package": {
                    "ecosystem": "PyPI",
                    "name": "flask",
                    "purl": "pkg:pypi/flask",
                },
                "ranges": [
                    {
                        "type": "ECOSYSTEM",
                        "events": [{"introduced": "0"}, {"fixed": "2.0.4"}],
                    }
                ],
                "versions": ["1.0.0"],
            }
        ],
        "references": [{"type": "WEB", "url": "https://example.com"}],
        "published": "2024-01-01T00:00:00Z",
        "modified": "2024-02-01T00:00:00Z",
    }
    if score_vec:
        raw["severity"] = [{"type": "CVSS_V3", "score": score_vec}]
    return raw


# ---------------------------------------------------------------- OSV client


@pytest.mark.asyncio
async def test_osv_parse_severity_branches() -> None:
    """Severity is derived from CVSS score, database_specific, or UNKNOWN."""
    async with OSVClient() as client:
        crit = client._parse_vulnerability(_osv_raw(score_vec="9.8/AV:N"))
        high = client._parse_vulnerability(_osv_raw(score_vec="7.5/AV:N"))
        med = client._parse_vulnerability(_osv_raw(score_vec="5.0/AV:N"))
        low = client._parse_vulnerability(_osv_raw(score_vec="2.0/AV:N"))
        bad = client._parse_vulnerability(_osv_raw(score_vec="xx:notanumber/AV:N"))
        none = client._parse_vulnerability(_osv_raw(score_vec="plain"))
        dbs = _osv_raw()
        dbs["database_specific"] = {"severity": "moderate"}
        fallback = client._parse_vulnerability(dbs)
        empty = client._parse_vulnerability({})
    assert (crit.severity, high.severity, med.severity, low.severity) == (
        "CRITICAL",
        "HIGH",
        "MEDIUM",
        "LOW",
    )
    assert crit.cvss_score == 9.8
    assert bad.severity == "UNKNOWN" and bad.cvss_score is None
    assert none.cvss_vector == "plain" and none.cvss_score is None
    assert fallback.severity == "MODERATE"
    assert empty.id == "UNKNOWN" and empty.affected_packages == []


@pytest.mark.xfail(
    strict=True,
    reason="bug: CVSS vector prefix 'CVSS:3.1' is parsed as a base score of 3.1",
)
@pytest.mark.asyncio
async def test_osv_cvss_vector_version_not_score() -> None:
    """A real CVSS vector string must not yield its version number as the score."""
    async with OSVClient() as client:
        parsed = client._parse_vulnerability(_osv_raw(score_vec="CVSS:3.1/AV:N/AC:L"))
    assert parsed.cvss_score is None


@pytest.mark.asyncio
async def test_osv_query_by_purl_paths() -> None:
    """query_by_purl returns parsed vulns and swallows HTTP/transport errors."""
    seen: list[dict] = []

    def ok(req: httpx.Request) -> httpx.Response:
        """Return one vulnerability."""
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"vulns": [_osv_raw()]})

    client = OSVClient()
    await client.client.aclose()
    client.client = _mock_client(ok)
    vulns = await client.query_by_purl("pkg:pypi/flask@2.0.3")
    assert [v.id for v in vulns] == ["GHSA-aaaa-bbbb-cccc"]
    assert seen[0] == {"package": {"purl": "pkg:pypi/flask@2.0.3"}}
    await client.client.aclose()

    client.client = _mock_client(lambda r: httpx.Response(500))
    assert await client.query_by_purl("pkg:x") == []
    await client.client.aclose()

    def boom(req: httpx.Request) -> httpx.Response:
        """Simulate a transport failure."""
        raise httpx.ConnectError("down")

    client.client = _mock_client(boom)
    assert await client.query_by_purl("pkg:x") == []
    await client.close()


@pytest.mark.asyncio
async def test_osv_query_batch_splits_and_maps() -> None:
    """Batch queries split by batch_size and map results back to PURLs."""
    calls: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        """Return one empty result per query, a vuln for the first."""
        queries = json.loads(req.content)["queries"]
        calls.append(len(queries))
        results = [{"vulns": [_osv_raw()]}] + [{} for _ in queries[1:]]
        return httpx.Response(200, json={"results": results})

    client = OSVClient()
    await client.client.aclose()
    client.client = _mock_client(handler)
    out = await client.query_batch(["p1", "p2", "p3"], batch_size=2)
    assert calls == [2, 1]
    assert len(out["p1"]) == 1 and out["p2"] == [] and len(out["p3"]) == 1
    await client.client.aclose()

    client.client = _mock_client(lambda r: httpx.Response(503))
    assert await client._query_batch_single(["a", "b"]) == {"a": [], "b": []}
    await client.client.aclose()

    def boom(req: httpx.Request) -> httpx.Response:
        """Simulate a transport failure."""
        raise httpx.ReadTimeout("slow")

    client.client = _mock_client(boom)
    assert await client.query_batch(["a"]) == {"a": []}
    await client.close()


@pytest.mark.asyncio
async def test_osv_query_by_package() -> None:
    """Package queries map ecosystem names, include version, and handle errors."""
    seen: list[dict] = []

    def ok(req: httpx.Request) -> httpx.Response:
        """Record the payload and return a vulnerability."""
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"vulns": [_osv_raw()]})

    client = OSVClient()
    await client.client.aclose()
    client.client = _mock_client(ok)
    assert len(await client.query_by_package("pypi", "flask", "2.0.3")) == 1
    assert len(await client.query_by_package("weird", "x")) == 1
    assert seen[0]["package"]["ecosystem"] == "PyPI" and seen[0]["version"] == "2.0.3"
    assert seen[1]["package"]["ecosystem"] == "weird" and "version" not in seen[1]
    await client.client.aclose()

    client.client = _mock_client(lambda r: httpx.Response(404))
    assert await client.query_by_package("npm", "x") == []
    await client.client.aclose()

    def boom(req: httpx.Request) -> httpx.Response:
        """Simulate a transport failure."""
        raise httpx.ConnectError("down")

    client.client = _mock_client(boom)
    assert await client.query_by_package("npm", "x") == []
    await client.close()


# ---------------------------------------------------------------- NVD client


def _nvd_cve(**over) -> dict:
    """Build a raw NVD CVE item with CVSS v3.1 data."""
    cve = {
        "id": "CVE-2024-0001",
        "descriptions": [
            {"lang": "es", "value": "hola"},
            {"lang": "en", "value": "english"},
        ],
        "metrics": {
            "cvssMetricV31": [
                {
                    "cvssData": {
                        "baseScore": 9.8,
                        "vectorString": "CVSS:3.1/AV:N",
                        "baseSeverity": "critical",
                    }
                }
            ]
        },
        "configurations": [
            {
                "nodes": [
                    {
                        "cpeMatch": [
                            {"vulnerable": True, "criteria": "cpe:a"},
                            {"vulnerable": False, "criteria": "cpe:b"},
                        ]
                    }
                ]
            }
        ],
        "references": [{"url": "https://example.com", "source": "src"}],
        "published": "2024-01-01T00:00:00.000",
        "lastModified": "2024-02-01T00:00:00.000",
    }
    cve.update(over)
    return cve


def test_nvd_parse_metric_fallbacks() -> None:
    """Parser prefers CVSS v3.1, then v3.0, then v2 (score-derived severity)."""
    client = NVDClient()
    v31 = client._parse_vulnerability(_nvd_cve())
    assert (v31.severity, v31.cvss_score, v31.description) == (
        "CRITICAL",
        9.8,
        "english",
    )
    assert v31.cpe_matches == ["cpe:a"]
    v30 = client._parse_vulnerability(
        _nvd_cve(
            metrics={
                "cvssMetricV30": [
                    {
                        "cvssData": {
                            "baseScore": 5.0,
                            "vectorString": "v",
                            "baseSeverity": "medium",
                        }
                    }
                ]
            }
        )
    )
    assert (v30.severity, v30.cvss_vector) == ("MEDIUM", "v")
    sevs = []
    for score in (8.0, 5.0, 1.0):
        v2 = client._parse_vulnerability(
            _nvd_cve(metrics={"cvssMetricV2": [{"cvssData": {"baseScore": score}}]})
        )
        sevs.append(v2.severity)
    assert sevs == ["HIGH", "MEDIUM", "LOW"]
    bare = client._parse_vulnerability({})
    assert (bare.id, bare.severity, bare.cvss_score, bare.description) == (
        "UNKNOWN",
        "UNKNOWN",
        None,
        "",
    )
    nodesc = client._parse_vulnerability(
        _nvd_cve(descriptions=[{"lang": "fr"}], metrics={})
    )
    assert nodesc.description == "" and nodesc.severity == "UNKNOWN"


def test_nvd_client_construction_rates() -> None:
    """Rate limit and auth header depend on the presence of an API key."""
    keyed = NVDClient(api_key="k-" + "x" * 6)
    plain = NVDClient()
    custom = NVDClient(requests_per_minute=12)
    assert keyed.rate_limiter.requests_per_minute == 50
    assert keyed.client.headers.get("apiKey") is not None
    assert plain.rate_limiter.requests_per_minute == nvd_mod.DEFAULT_RATE_LIMIT
    assert custom.rate_limiter.interval == 5.0


@pytest.mark.asyncio
async def test_rate_limiter_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second acquire inside the interval sleeps for the remainder."""
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        """Record the requested sleep without waiting."""
        sleeps.append(sec)

    monkeypatch.setattr(nvd_mod.asyncio, "sleep", fake_sleep)
    limiter = RateLimiter(requests_per_minute=60)
    await limiter.acquire()
    await limiter.acquire()
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 1.0


async def _nvd_client(handler) -> NVDClient:
    """Build an NVD client with a mocked network and a no-op rate limiter."""
    client = NVDClient()
    await client.client.aclose()
    client.client = _mock_client(handler)
    client.rate_limiter.acquire = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_nvd_query_by_cve_id_paths() -> None:
    """CVE lookups return a parsed vuln, None when missing, and None on errors."""
    client = await _nvd_client(
        lambda r: httpx.Response(200, json={"vulnerabilities": [{"cve": _nvd_cve()}]})
    )
    got = await client.query_by_cve_id("CVE-2024-0001")
    assert got is not None and got.id == "CVE-2024-0001"
    await client.client.aclose()

    client.client = _mock_client(lambda r: httpx.Response(200, json={}))
    assert await client.query_by_cve_id("CVE-1") is None
    await client.client.aclose()

    client.client = _mock_client(lambda r: httpx.Response(403))
    assert await client.query_by_cve_id("CVE-1") is None
    await client.client.aclose()

    def boom(req: httpx.Request) -> httpx.Response:
        """Simulate a transport failure."""
        raise httpx.ConnectError("down")

    client.client = _mock_client(boom)
    assert await client.query_by_cve_id("CVE-1") is None
    await client.close()


@pytest.mark.asyncio
async def test_nvd_query_by_cpe_and_keyword() -> None:
    """CPE and keyword searches parse pages and degrade to empty lists on error."""
    body = {
        "totalResults": 2,
        "vulnerabilities": [{"cve": _nvd_cve()}, {"cve": _nvd_cve(id="CVE-2")}],
    }
    client = await _nvd_client(lambda r: httpx.Response(200, json=body))
    assert len(await client.query_by_cpe("cpe:2.3:a:x:y")) == 2
    assert len(await client.query_by_keyword("flask")) == 2
    await client.client.aclose()

    client.client = _mock_client(lambda r: httpx.Response(500))
    assert await client.query_by_cpe("c") == []
    assert await client.query_by_keyword("k") == []
    await client.client.aclose()

    def boom(req: httpx.Request) -> httpx.Response:
        """Simulate a transport failure."""
        raise httpx.ReadTimeout("slow")

    client.client = _mock_client(boom)
    assert await client.query_by_cpe("c") == []
    assert await client.query_by_keyword("k") == []
    await client.close()


# ------------------------------------------------------------------- matcher


def _osv_vuln(**over) -> OSVVulnerability:
    """Build an OSVVulnerability with an ECOSYSTEM range 0..2.0.4."""
    base = {
        "id": "GHSA-1",
        "aliases": ["CVE-1"],
        "summary": "s",
        "details": "d",
        "severity": "HIGH",
        "cvss_score": 7.5,
        "cvss_vector": "v",
        "affected_packages": [
            {
                "ranges": [
                    {
                        "type": "ECOSYSTEM",
                        "events": [{"introduced": "0"}, {"fixed": "2.0.4"}],
                    }
                ],
                "versions": [],
            }
        ],
        "references": [],
        "published": "2024-01-01T00:00:00Z",
        "modified": "2024-02-01T00:00:00Z",
    }
    base.update(over)
    return OSVVulnerability(**base)


def test_matcher_version_affected_matrix() -> None:
    """Version range logic covers ECOSYSTEM, SEMVER, explicit versions and bad input."""
    m = VulnerabilityMatcher()
    eco = [
        {
            "ranges": [
                {
                    "type": "ECOSYSTEM",
                    "events": [{"introduced": "1.0"}, {"fixed": "2.0"}],
                }
            ]
        }
    ]
    assert m._is_version_affected("1.5", eco) is True
    assert m._is_version_affected("2.0", eco) is False
    assert m._is_version_affected("0.5", eco) is False
    zero = [
        {
            "ranges": [
                {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.0"}]}
            ]
        }
    ]
    assert m._is_version_affected("1.0", zero) is True
    junk = [
        {
            "ranges": [
                {
                    "type": "ECOSYSTEM",
                    "events": [{"introduced": "zzz"}, {"fixed": "yyy"}],
                }
            ]
        }
    ]
    assert m._is_version_affected("1.0", junk) is False
    semver = [{"ranges": [{"type": "SEMVER", "events": [{"introduced": "1.0"}]}]}]
    assert m._is_version_affected("9.9", semver) is True
    semver_empty = [{"ranges": [{"type": "SEMVER", "events": [{"other": 1}]}]}]
    assert m._is_version_affected("1.0", semver_empty) is False
    explicit = [{"ranges": [{"type": "GIT"}], "versions": ["1.0"]}]
    assert m._is_version_affected("1.0", explicit) is True
    assert m._is_version_affected("1.1", explicit) is False
    assert m._is_version_affected("not-a-version", []) is True
    assert m._is_version_affected("1.0", [{}]) is False


def test_matcher_extract_fixed_versions() -> None:
    """Fixed versions are collected and de-duplicated."""
    m = VulnerabilityMatcher()
    pkgs = [
        {"ranges": [{"events": [{"fixed": "1.1"}, {"introduced": "0"}]}]},
        {"ranges": [{"events": [{"fixed": "1.1"}, {"fixed": "2.0"}]}]},
        {},
    ]
    assert sorted(m._extract_fixed_versions(pkgs)) == ["1.1", "2.0"]


@pytest.mark.asyncio
async def test_matcher_match_component_filters_by_version() -> None:
    """Single-component matching honours version affectedness and empty purl."""
    async with VulnerabilityMatcher(nvd_api_key="k-" + "y" * 5) as m:
        m.osv_client.query_by_purl = AsyncMock(return_value=[_osv_vuln()])
        hit = await m.match_component("pkg:pypi/flask@1.0", "pypi", "flask", "1.0")
        miss = await m.match_component("pkg:pypi/flask@3.0", "pypi", "flask", "3.0")
        noversion = await m.match_component("pkg:pypi/flask", "pypi", "flask")
        nopurl = await m.match_component("", "pypi", "flask", "1.0")
    assert [h.cve_id for h in hit] == ["GHSA-1"] and hit[0].fixed_versions == ["2.0.4"]
    assert hit[0].source == "osv" and miss == [] and len(noversion) == 1
    assert nopurl == []


@pytest.mark.asyncio
async def test_matcher_batch() -> None:
    """Batch matching maps by component id, skips unaffected and purl-less rows."""
    m = VulnerabilityMatcher()
    try:
        assert await m.match_components_batch([{"id": 1, "name": "x"}]) == {}
        m.osv_client.query_batch = AsyncMock(
            return_value={
                "pkg:a": [_osv_vuln()],
                "pkg:b": [_osv_vuln()],
                "pkg:c": [],
            }
        )
        comps = [
            {"id": 1, "purl": "pkg:a", "version": "1.0"},
            {"id": 2, "purl": "pkg:b", "version": "9.0"},
            {"id": 3, "purl": "pkg:c"},
            {"id": 4, "name": "nopurl"},
        ]
        out = await m.match_components_batch(comps)
        assert list(out) == [1]
        m.osv_client.query_batch = AsyncMock(return_value={"pkg:a": [_osv_vuln()]})
        nover = await m.match_components_batch([{"id": 7, "purl": "pkg:a"}])
        assert list(nover) == [7]
    finally:
        await m.close()


def _matched(cve: str, **over) -> MatchedVulnerability:
    """Build a MatchedVulnerability for persistence tests."""
    base = {
        "cve_id": cve,
        "aliases": ["A"],
        "severity": "HIGH",
        "cvss_score": 7.5,
        "cvss_vector": "v",
        "title": "t",
        "description": "d",
        "affected_packages": [],
        "fixed_versions": ["1"],
        "references": [],
        "published_at": "2024-01-01T00:00:00Z",
        "modified_at": "2024-02-01T00:00:00Z",
        "source": "osv",
    }
    base.update(over)
    return MatchedVulnerability(**base)


def _on_db_thread(coro):
    """Run a coroutine to completion on the calling (DB) thread."""
    return asyncio.run(coro)


@pytest.mark.asyncio
async def test_matcher_db_insert_and_link(world) -> None:
    """New vulnerabilities insert once; component links are idempotent."""
    db = world["db"]
    tenant = world["a"]["tenant"]
    m = VulnerabilityMatcher()
    try:
        first = await in_db(
            lambda: _on_db_thread(
                m.sync_vulnerability_to_db(db, _matched("CVE-" + vid()[:10]), tenant)
            )
        )
        nodates = await in_db(
            lambda: _on_db_thread(
                m.sync_vulnerability_to_db(
                    db,
                    _matched("CVE-" + vid()[:10], published_at=None, modified_at=None),
                    tenant,
                )
            )
        )

        def _component() -> int:
            """Insert a component row to link against."""
            cid = db.sbom_components.insert(
                tenant_id=tenant,
                parent_type="entity",
                parent_id=1,
                name="flask",
                package_type="pypi",
                direct=True,
                is_active=True,
            )
            db.commit()
            return cid

        comp = await in_db(_component)
        link1 = await in_db(
            lambda: _on_db_thread(
                m.create_component_vulnerability_link(db, comp, first, tenant)
            )
        )
        link2 = await in_db(
            lambda: _on_db_thread(
                m.create_component_vulnerability_link(db, comp, first, tenant)
            )
        )
    finally:
        await m.close()
    assert nodates != first
    assert link1 == link2


@pytest.mark.xfail(
    strict=True,
    reason="bug: update path uses item assignment, unsupported by the DAL table proxy",
)
@pytest.mark.asyncio
async def test_matcher_db_update_existing(world) -> None:
    """Re-syncing an existing CVE updates the row and returns the same id."""
    db = world["db"]
    tenant = world["a"]["tenant"]
    cve = "CVE-" + vid()[:10]
    m = VulnerabilityMatcher()
    try:
        first = await in_db(
            lambda: _on_db_thread(m.sync_vulnerability_to_db(db, _matched(cve), tenant))
        )
        again = await in_db(
            lambda: _on_db_thread(
                m.sync_vulnerability_to_db(
                    db, _matched(cve, severity="LOW", modified_at=None), tenant
                )
            )
        )
    finally:
        await m.close()
    assert first == again
