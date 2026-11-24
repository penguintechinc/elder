"""End-to-end tests for the demo data seeder (``make seed-demo``).

Runs ``scripts/seed_demo.py`` as a real subprocess against a throwaway Postgres
database (created and dropped on the server ``DATABASE_URL`` points at), so the
demo rows never touch the shared unit-test database. Asserts per-feature counts
(3-4), universal unique ``village_id``, idempotency (second run creates nothing),
reserved-domain e-mails, no secret-looking strings, and that the documented demo
login authenticates and sees the seeded data.
"""

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from scripts.demo_seed import common

ROOT = Path(__file__).resolve().parents[2]

# feature -> (SQL returning one integer, min, max). Rows owned by the layered
# cloud-discovery / Kubernetes seeders (graph topologies) are excluded by filter.
FEATURE_COUNTS = {
    "staff identities (example.com)": (
        "SELECT count(*) FROM identities WHERE email LIKE '%@example.com'",
        3,
        4,
    ),
    "customer contacts": (
        "SELECT count(*) FROM identities WHERE username LIKE 'contact:%'",
        3,
        4,
    ),
    "teams": ("SELECT count(*) FROM identity_groups", 3, 4),
    "organizations": ("SELECT count(*) FROM organizations", 3, 4),
    "compute LXD/VM": (
        "SELECT count(*) FROM entities WHERE sub_type IN "
        "('lxd_container','lxd_vm','virtual_machine') AND name LIKE '%acme-%'",
        3,
        4,
    ),
    "networks": (
        "SELECT count(*) FROM networking_resources WHERE name IN "
        "('Acme Prod VPC','Web Subnet','App Subnet','Management VLAN')",
        3,
        4,
    ),
    "ipam prefixes": ("SELECT count(*) FROM ipam_prefixes", 3, 4),
    "ipam vlans": ("SELECT count(*) FROM ipam_vlans", 3, 4),
    "ipam addresses": ("SELECT count(*) FROM ipam_addresses", 3, 4),
    "data stores": (
        "SELECT count(*) FROM data_stores WHERE name IN "
        "('Orders Database','Build Artifacts Bucket','Analytics Warehouse')",
        3,
        4,
    ),
    "services": ("SELECT count(*) FROM services WHERE name LIKE 'Acme %'", 3, 4),
    "software": ("SELECT count(*) FROM software", 3, 4),
    "sbom scans": ("SELECT count(*) FROM sbom_scans", 3, 4),
    "sbom components": ("SELECT count(*) FROM sbom_components", 3, 4),
    "vulnerabilities": ("SELECT count(*) FROM vulnerabilities", 3, 4),
    "certificates": ("SELECT count(*) FROM certificates", 3, 4),
    "license policies": ("SELECT count(*) FROM license_policies", 3, 4),
    "secrets": ("SELECT count(*) FROM secrets", 3, 4),
    "api keys": ("SELECT count(*) FROM api_keys", 3, 4),
    "issues": ("SELECT count(*) FROM issues", 3, 4),
    "webhooks": ("SELECT count(*) FROM webhooks", 3, 4),
    "inbound webhooks": ("SELECT count(*) FROM inbound_webhooks", 3, 4),
    "inbound webhook events": ("SELECT count(*) FROM inbound_webhook_events", 3, 4),
    "stream playbooks": ("SELECT count(*) FROM stream_playbooks", 3, 4),
    "on-call rotations": ("SELECT count(*) FROM on_call_rotations", 3, 4),
    "diagrams": ("SELECT count(*) FROM dg_diagrams", 3, 4),
    "documents": ("SELECT count(*) FROM doc_documents", 3, 4),
    "pages": ("SELECT count(*) FROM pg_pages", 3, 4),
    "ai resources": ("SELECT count(*) FROM ai_resources", 3, 4),
    "run functions": ("SELECT count(*) FROM run_functions", 3, 4),
    "remote workers": ("SELECT count(*) FROM remote_workers", 3, 4),
    "access reviews": ("SELECT count(*) FROM access_reviews", 3, 4),
    "projects": ("SELECT count(*) FROM projects", 3, 4),
    "milestones": ("SELECT count(*) FROM milestones", 3, 4),
    "labels": ("SELECT count(*) FROM labels", 3, 4),
}

SECRET_PATTERNS = {
    "aws access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "github token": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    "api secret key": re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    "slack token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
}

# Probe run in a subprocess: log in as the documented demo user, read the data.
_PROBE = r"""
import asyncio, json, sys
sys.path.insert(0, sys.argv[1])
from apps.api.main import create_app
from scripts.demo_seed.common import DEMO_ADMIN_EMAIL, DEMO_ADMIN_PASSWORD
app = create_app()

async def main():
    c = app.test_client()
    r = await c.post("/api/v1/portal-auth/login",
                     json={"email": DEMO_ADMIN_EMAIL, "password": DEMO_ADMIN_PASSWORD})
    out = {"login_status": r.status_code}
    body = await r.get_json()
    h = {"Authorization": "Bearer " + str(body.get("access_token"))}
    r = await c.get("/api/v1/issues", headers=h)
    out["issues_status"] = r.status_code
    out["issues"] = len((await r.get_json()).get("items", []))
    r = await c.get("/api/v1/entities?per_page=200", headers=h)
    out["entities_status"] = r.status_code
    out["entity_subtypes"] = sorted({e.get("sub_type") for e in (await r.get_json()).get("items", [])})
    print("PROBE " + json.dumps(out))

asyncio.run(main())
"""


def _run(args, env):
    """Run a command in the repo root and return the CompletedProcess."""
    return subprocess.run(  # noqa: S603
        args,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )


def _counts(engine):
    """Return {table: row count} for every public table."""
    with engine.connect() as conn:
        names = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1"
                )
            )
        ]
        return {
            n: conn.execute(text(f'SELECT count(*) FROM "{n}"')).scalar_one()  # noqa: S608
            for n in names
        }


@pytest.fixture(scope="module")
def seeded():
    """Seed a throwaway database twice and yield engine, outputs and counts."""
    base = os.environ.get("DATABASE_URL")
    assert (
        base
    ), "DATABASE_URL must point at the test Postgres (see elder-unit-test-recipe)"
    url = make_url(base)
    name = f"seed_demo_{uuid.uuid4().hex[:10]}"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    new_url = url.set(database=name).render_as_string(hide_password=False)
    env = {**os.environ, "DATABASE_URL": new_url, "DEMO_ADMIN_PASSWORD": ""}
    env.pop("DEMO_ADMIN_PASSWORD")  # exercise the documented dev default
    engine = create_engine(new_url)
    try:
        first = _run([sys.executable, "scripts/seed_demo.py"], env)
        before = _counts(engine)
        second = _run([sys.executable, "scripts/seed_demo.py"], env)
        after = _counts(engine)
        probe = _run([sys.executable, "-I", "-c", _PROBE, str(ROOT)], env)
        yield {
            "engine": engine,
            "first": first,
            "second": second,
            "before": before,
            "after": after,
            "probe": probe,
        }
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_first_run_succeeds_and_creates_rows(seeded):
    """The first run exits 0 and reports created rows (non-zero denominator)."""
    assert seeded["first"].returncode == 0, seeded["first"].stdout[-1500:]
    assert "DEMO SEED RESULT: created" in seeded["first"].stdout
    assert sum(seeded["before"].values()) > 100


def test_second_run_is_unchanged(seeded):
    """Re-running creates zero rows in every table and says so."""
    assert seeded["second"].returncode == 0, seeded["second"].stdout[-1500:]
    assert "DEMO SEED RESULT: unchanged" in seeded["second"].stdout
    diff = {
        t: (seeded["before"][t], n)
        for t, n in seeded["after"].items()
        if n != seeded["before"].get(t)
    }
    assert not diff, f"second run changed tables: {diff}"


@pytest.mark.parametrize("feature", sorted(FEATURE_COUNTS))
def test_feature_count_is_three_or_four(seeded, feature):
    """Each seeded feature has 3-4 items (graph topologies are filtered out)."""
    sql, low, high = FEATURE_COUNTS[feature]
    with seeded["engine"].connect() as conn:
        count = conn.execute(text(sql)).scalar_one()
    assert low <= count <= high, f"{feature}: {count} not in {low}..{high}"


def test_severity_and_status_mix(seeded):
    """Vulnerabilities, links and issues cover a mix of severities/statuses."""
    with seeded["engine"].connect() as conn:
        sev = {r[0] for r in conn.execute(text("SELECT severity FROM vulnerabilities"))}
        status = {
            r[0]
            for r in conn.execute(text("SELECT status FROM component_vulnerabilities"))
        }
        issue_status = {r[0] for r in conn.execute(text("SELECT status FROM issues"))}
    assert {"critical", "high", "medium", "low"} <= sev
    assert len(status) >= 3
    assert len(issue_status) >= 3


def test_every_village_id_present_and_unique(seeded):
    """Every row of every table with a village_id column has a unique non-null one."""
    engine = seeded["engine"]
    tables_checked = rows_checked = 0
    with engine.connect() as conn:
        tables = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND column_name='village_id'"
                )
            )
        ]
        for table in tables:
            nulls = conn.execute(
                text(f'SELECT count(*) FROM "{table}" WHERE village_id IS NULL')
            ).scalar_one()  # noqa: S608
            dupes = conn.execute(
                text(
                    f'SELECT count(*) FROM (SELECT village_id FROM "{table}" '  # noqa: S608
                    "GROUP BY village_id HAVING count(*) > 1) d"
                )
            ).scalar_one()
            rows_checked += conn.execute(
                text(f'SELECT count(*) FROM "{table}"')
            ).scalar_one()  # noqa: S608
            tables_checked += 1
            assert nulls == 0, f"{table}: {nulls} rows without village_id"
            assert dupes == 0, f"{table}: duplicate village_id values"
    assert tables_checked >= 30 and rows_checked > 100  # zero examined == fail


def test_all_emails_use_reserved_domains(seeded):
    """Every e-mail/username-with-@ in identities and portal_users is reserved."""
    with seeded["engine"].connect() as conn:
        values = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT email FROM identities WHERE email IS NOT NULL "
                    "UNION ALL SELECT email FROM portal_users "
                    "UNION ALL SELECT username FROM identities WHERE username LIKE '%@%'"
                )
            )
        ]
    assert len(values) >= 8
    bad = [v for v in values if not common.is_reserved_email(v)]
    assert not bad, f"non-reserved addresses: {bad}"


def test_no_secret_looking_strings(seeded):
    """No table row contains an AWS key, private key, or provider token pattern."""
    scanned_tables = scanned_rows = 0
    hits: list[str] = []
    with seeded["engine"].connect() as conn:
        for table, count in seeded["after"].items():
            if not count:
                continue
            scanned_tables += 1
            for (blob,) in conn.execute(text(f'SELECT t::text FROM "{table}" t')):  # noqa: S608
                scanned_rows += 1
                hits += [
                    f"{table}:{label}"
                    for label, rx in SECRET_PATTERNS.items()
                    if rx.search(blob)
                ]
    assert scanned_tables >= 40 and scanned_rows > 200
    assert not hits, hits


def test_demo_login_authenticates_and_sees_data(seeded):
    """The documented demo login signs in and reads seeded issues/compute."""
    probe = seeded["probe"]
    line = next(
        (ln for ln in probe.stdout.splitlines() if ln.startswith("PROBE ")), None
    )
    assert line, probe.stdout[-1500:] + probe.stderr[-500:]
    out = json.loads(line[6:])
    assert out["login_status"] == 200
    assert out["issues_status"] == 200 and out["issues"] >= 4
    assert out["entities_status"] == 200
    assert {"lxd_container", "lxd_vm"} <= set(out["entity_subtypes"])


def test_demo_login_env_file_matches_defaults():
    """scripts/demo_login.example.env mirrors the defaults in common.py."""
    parsed = dict(
        line.split("=", 1)
        for line in (ROOT / "scripts" / "demo_login.example.env")
        .read_text()
        .splitlines()
        if line and not line.startswith("#")
    )
    assert parsed["DEMO_ADMIN_EMAIL"] == common.DEFAULT_DEMO_ADMIN_EMAIL
    assert parsed["DEMO_ADMIN_PASSWORD"] == common.DEFAULT_DEMO_ADMIN_PASSWORD
    assert common.is_reserved_email(parsed["DEMO_ADMIN_EMAIL"])


@pytest.mark.parametrize("env_var", ["ELDER_ENV", "QUART_ENV", "FLASK_ENV", "APP_ENV"])
def test_default_password_refused_in_production(monkeypatch, env_var):
    """The dev-default password is refused when the environment is production."""
    for var in (
        "ELDER_ENV",
        "QUART_ENV",
        "FLASK_ENV",
        "APP_ENV",
        "DEMO_ADMIN_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(env_var, "production")
    with pytest.raises(RuntimeError):
        common.demo_admin_password()
    monkeypatch.setenv("DEMO_ADMIN_PASSWORD", "explicit-value")
    assert common.demo_admin_password() == "explicit-value"


def test_makefile_targets_point_at_seed_demo():
    """seed-demo runs scripts/seed_demo.py and seed-mock-data aliases it."""
    makefile = (ROOT / "Makefile").read_text()
    assert re.search(
        r"^seed-demo:.*\n(?:.*\n)*?.*scripts/seed_demo\.py", makefile, re.M
    )
    assert re.search(r"^seed-mock-data: seed-demo", makefile, re.M)
