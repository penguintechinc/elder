"""Unit tests for the helpers behind ``/api/v1/integrations/internal``.

Pure helpers are called directly; the persistence helpers
(credential resolution, sync-mapping upserts, directory sync) run against the
real Postgres schema through the application database handle.
"""

# flake8: noqa: E501

import json
import uuid
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.api.modules.discovery.routes import integrations_internal as ii
from tests.unit._cov_d_helpers import fake_secret, in_db, now, vid

MOD = "apps.api.modules.discovery.routes.integrations_internal"


# ------------------------------------------------------------ pure helpers


def test_datetime_helpers():
    """Naive datetimes become UTC and ISO strings parse, including a Z suffix."""
    naive = datetime(2026, 1, 2, 3, 4, 5)
    assert ii._ensure_utc(naive).tzinfo is UTC
    east = datetime(2026, 1, 2, 3, 0, tzinfo=timezone(timedelta(hours=2)))
    assert ii._ensure_utc(east).hour == 1
    assert ii._utc_now().tzinfo is not None
    assert ii._parse_datetime(None) is None
    assert ii._parse_datetime(naive).tzinfo is UTC
    assert ii._parse_datetime("") is None and ii._parse_datetime("   ") is None
    assert ii._parse_datetime("2026-01-02T03:04:05Z").hour == 3
    assert ii._parse_datetime("not a date") is None
    assert ii._parse_datetime(12345) is None
    assert ii._iso_or_none(None) is None
    assert ii._iso_or_none(naive).endswith("+00:00")
    assert ii._iso_or_none("raw") == "raw"
    assert ii._iso_or_none(7) == "7"


def test_json_helpers():
    """JSON-ish columns decode safely to dicts or the raw value."""
    src = {"a": 1}
    copy = ii._parse_json_dict(src)
    assert copy == src and copy is not src
    assert ii._parse_json_dict('{"a": 2}') == {"a": 2}
    assert ii._parse_json_dict("[1, 2]") == {}
    assert ii._parse_json_dict("{bad") == {}
    assert ii._parse_json_dict(5) == {}
    assert ii._decode_raw_json({"k": 1}) == {"k": 1}
    assert ii._decode_raw_json('{"k": 1}') == {"k": 1}
    assert ii._decode_raw_json("plain") == "plain"
    assert ii._decode_raw_json(None) == {}
    assert ii._decode_raw_json(5) == 5


def test_int_helpers():
    """Integer parsing rejects bools/garbage; positive_int falls back to default."""
    assert ii._safe_int(None) is None and ii._safe_int(True) is None
    assert ii._safe_int("5") == 5 and ii._safe_int(3.9) == 3
    assert ii._safe_int("x") is None and ii._safe_int([]) is None
    assert ii._positive_int("7", 1) == 7
    assert ii._positive_int(0, 9) == 9 and ii._positive_int("x", 9) == 9


def test_has_column_variants():
    """Column detection supports DAL tables, field lists and plain attributes."""
    inner = SimpleNamespace(table=SimpleNamespace(columns=["a"]))
    assert ii._has_column(inner, "a") and not ii._has_column(inner, "b")

    class Fields:
        """Table exposing a ``fields`` list in its instance dict."""

        def __init__(self):
            """Populate the fields list."""
            self.fields = ["x"]

    assert ii._has_column(Fields(), "x") and not ii._has_column(Fields(), "y")
    assert ii._has_column(SimpleNamespace(z=1), "z")
    assert not ii._has_column(SimpleNamespace(), "z")


def test_safe_rollback_variants():
    """Rollback is attempted only when supported and never raises."""
    calls = []

    class Cls:
        """DB handle exposing rollback on the class."""

        def rollback(self):
            """Record the call then fail."""
            calls.append("cls")
            raise RuntimeError("x")

    ii._safe_rollback(Cls())
    ii._safe_rollback(SimpleNamespace(rollback=lambda: calls.append("inst")))
    ii._safe_rollback(SimpleNamespace())
    assert calls == ["cls", "inst"]


def test_is_due_rules():
    """Jobs are due when forced, never run, scheduled, or past their interval."""
    t = datetime(2026, 1, 1, tzinfo=UTC)
    assert ii._is_due(t, 60, t, True)
    assert ii._is_due(None, 60, t, False)
    assert ii._is_due(t, 3600, t + timedelta(seconds=10), False, next_run_at=t) is True
    assert not ii._is_due(
        t, 3600, t + timedelta(seconds=10), False, next_run_at=t + timedelta(days=1)
    )
    assert ii._is_due(t, 3600, t, False, next_run_at="garbage") is False
    assert ii._is_due("garbage", 60, t, False)
    assert ii._is_due(t, 60, t + timedelta(seconds=61), False)
    assert not ii._is_due(t, 60, t + timedelta(seconds=5), False)


def test_discovery_category_and_normalization():
    """Resource types map to categories and flat results group with their edges."""
    f = ii._infer_discovery_category
    assert f({"category": " Storage "}) == "storage"
    assert f({"resource_type": "aws_vpc"}) == "network"
    assert f({"resource_type": "s3_bucket"}) == "storage"
    assert f({"resource_type": "rds_db"}) == "database"
    assert f({"resource_type": "lambda"}) == "serverless"
    assert f({"resource_type": "iam_role"}) == "iam"
    assert f({"resource_type": "trivy_finding"}) == "security"
    assert f({"resource_type": "vm"}) == "compute" and f({}) == "compute"

    resources = [
        "skip-me",
        {"external_id": 1, "resource_type": "vm", "relationships": [{"x": 1}]},
        {"resource_id": "b", "provider": "keep", "resource_type": "bucket"},
        {"resource_type": "noid"},
    ]
    deps = [
        "skip",
        {"source_external_id": 1, "target_external_id": 2, "target_type": "entity"},
        {"source_external_id": "zzz", "target_external_id": "b"},
    ]
    grouped, remaining = ii._normalize_discovery_payload("aws", resources, deps)
    assert set(grouped) == {"compute", "storage"}
    vm = grouped["compute"][0]
    assert vm["resource_id"] == "1" and vm["provider"] == "aws"
    assert vm["relationships"][-1]["edge_type"] == "depends_on"
    assert vm["relationships"][-1]["target_kind"] == "entity"
    assert grouped["storage"][0]["provider"] == "keep"
    assert remaining == [deps[2]]


def test_issue_normalizers_and_serializer():
    """Free-form issue fields map to the enum values or documented defaults."""
    assert ii._normalize_issue_status(None) == "OPEN"
    assert ii._normalize_issue_status("resolved") == "RESOLVED"
    assert ii._normalize_issue_status("In Progress") == "IN_PROGRESS"
    assert ii._normalize_issue_status("weird") == "OPEN"
    assert ii._normalize_issue_priority(None) == "MEDIUM"
    assert ii._normalize_issue_priority("urgent") == "URGENT"
    assert ii._normalize_issue_priority("meh") == "MEDIUM"
    assert ii._normalize_issue_type(None) == "OPERATIONS"
    assert ii._normalize_issue_type("bug") == "BUG"
    assert ii._normalize_issue_type("story") == "FEATURE"
    assert ii._normalize_issue_type("zzz") == "OPERATIONS"

    t = now()
    issue = SimpleNamespace(id=4, title="t", metadata='{"k": 1}', created_at=t)
    out = ii._serialize_issue(issue, "ext-1")
    assert out["id"] == 4 and out["external_id"] == "ext-1"
    assert out["status"] == "OPEN" and out["metadata"] == {"k": 1}
    assert out["created_at"].endswith("+00:00") and out["updated_at"] is None


# ------------------------------------------------- service-worker identity


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "is_worker,sub,username,scope,ok",
    [
        (False, "svc-integrations", "svc-integrations", ["integrations:sync"], False),
        (True, "other", "svc-integrations", ["integrations:sync"], False),
        (True, "svc-integrations", "other", ["integrations:sync"], False),
        (True, "svc-integrations", "svc-integrations", "integrations:sync x", True),
        (True, "svc-integrations", "svc-integrations", "x y", False),
        (True, "svc-integrations", "svc-integrations", ("integrations:sync",), True),
        (True, "svc-integrations", "svc-integrations", {"integrations:sync"}, True),
        (True, "svc-integrations", "svc-integrations", None, False),
        (True, "svc-integrations", "svc-integrations", 7, False),
    ],
)
async def test_require_svc_integrations(app, is_worker, sub, username, scope, ok):
    """Only the svc-integrations system worker with the sync scope is admitted."""
    from quart import g

    async with app.test_request_context("/"):
        g.claims = {"sub": sub, "scope": scope}
        g.current_user = SimpleNamespace(username=username)
        with patch(f"{MOD}.is_system_service_worker", return_value=is_worker):
            result = ii._require_svc_integrations()
    if ok:
        assert result is None
    else:
        _, status = result
        assert status == 403


# ------------------------------------------------- credential resolution


async def _org(app):
    """Create a tenant + organization and return (tenant_id, org_id)."""

    def _mk():
        """Insert the tenant and organization rows."""
        db = app.db
        sfx = uuid.uuid4().hex[:8]
        tid = db.tenants.insert(name=f"ci-{sfx}", slug=f"ci-{sfx}", is_active=True)
        org = db.organizations.insert(
            name=f"ci-org-{sfx}", tenant_id=tid, created_at=now(), updated_at=now()
        )
        db.commit()
        return tid, org

    return await in_db(_mk)


@pytest.mark.asyncio
async def test_resolve_discovery_credentials(app):
    """Secret and cloud-account credentials merge into config, scoped to the org."""
    _, org = await _org(app)
    _, other_org = await _org(app)

    def _run():
        """Seed credential rows and exercise every resolution branch."""
        db = app.db
        t = now()
        secret_val = fake_secret("sv")
        sec = db.builtin_secrets.insert(
            name=fake_secret("s"),
            organization_id=org,
            secret_type="cloud",
            secret_json={"access_key": "ak", "region": "r1", "skip": None},
            is_active=True,
            created_at=t,
            updated_at=t,
        )
        raw_json = db.builtin_secrets.insert(
            name=fake_secret("s"),
            organization_id=org,
            secret_type="cloud",
            secret_value=json.dumps({"token": "tk"}),
            is_active=True,
            created_at=t,
            updated_at=t,
        )
        raw_plain = db.builtin_secrets.insert(
            name=fake_secret("s"),
            organization_id=org,
            secret_type="cloud",
            secret_value=secret_val,
            is_active=True,
            created_at=t,
            updated_at=t,
        )
        foreign = db.builtin_secrets.insert(
            name=fake_secret("s"),
            organization_id=other_org,
            secret_type="cloud",
            secret_json={"access_key": "nope"},
            is_active=True,
            created_at=t,
            updated_at=t,
        )
        acct = db.cloud_accounts.insert(
            name=fake_secret("a"),
            provider="aws",
            credentials_json={"key_id": "kid"},
            organization_id=org,
            enabled=True,
            village_id=vid(),
            created_at=t,
            updated_at=t,
        )
        acct_foreign = db.cloud_accounts.insert(
            name=fake_secret("a"),
            provider="aws",
            credentials_json={"key_id": "bad"},
            organization_id=other_org,
            enabled=True,
            village_id=vid(),
            created_at=t,
            updated_at=t,
        )
        db.commit()

        def job(cid, ctype=None, mapping=None):
            """Build a job-like object referencing a credential."""
            return SimpleNamespace(
                credential_id=cid, credential_type=ctype, credential_mapping=mapping
            )

        cfg = {"base": 1}
        r = ii._resolve_discovery_credentials
        assert r(db, job(None), org, cfg) == cfg
        assert r(db, job(sec), org, cfg) == {
            "base": 1,
            "access_key": "ak",
            "region": "r1",
        }
        mapped = r(
            db, job(sec, "secret", {"aws_key": "access_key", "x": "missing"}), org, cfg
        )
        assert mapped == {"base": 1, "aws_key": "ak"}
        assert r(db, job(raw_json, "builtin_secret"), org, cfg)["token"] == "tk"
        assert r(db, job(raw_plain), org, cfg)["secret_value"] == secret_val
        assert r(db, job(foreign), org, cfg) == cfg
        assert r(db, job(99999999), org, cfg) == cfg
        assert r(db, job(acct, "cloud_account"), org, cfg)["key_id"] == "kid"
        assert r(db, job(acct_foreign, "cloud_account"), org, cfg) == cfg
        assert r(db, job(99999999, "cloud_account"), org, cfg) == cfg
        assert r(db, job(sec, "vault"), org, cfg) == cfg
        no_tables = SimpleNamespace(tables=[])
        assert r(no_tables, job(sec), org, cfg) == cfg
        assert r(no_tables, job(acct, "cloud_account"), org, cfg) == cfg

    await in_db(_run)


# ------------------------------------------------------ dependency lookups


@pytest.mark.asyncio
async def test_resolve_db_record_for_dep(app):
    """Edges resolve to existing rows of the right kind in the same organization."""
    tid, org = await _org(app)
    _, other = await _org(app)

    def _run():
        """Seed an entity and a service and look both up."""
        db = app.db
        t = now()
        ent = db.entities.insert(
            name="e",
            type="vm",
            organization_id=org,
            external_id="ext-e",
            village_id=vid(),
            created_at=t,
            updated_at=t,
        )
        svc = db.services.insert(
            name="s",
            tenant_id=tid,
            organization_id=org,
            external_id="ext-s",
            is_public=False,
            village_id=vid(),
            created_at=t,
            updated_at=t,
        )
        db.commit()
        f = ii._resolve_db_record_for_dep
        assert f(db, org, None, None) is None
        assert f(db, org, "ext-e", "entity") == ("entity", ent)
        assert f(db, org, "ext-e", None) == ("entity", ent)
        assert f(db, org, "ext-s", "unknown-kind") == ("service", svc)
        assert f(db, org, "ext-s", "service") == ("service", svc)
        assert f(db, org, "ext-e", "service") is None
        assert f(db, other, "ext-e", None) is None
        assert f(SimpleNamespace(tables=[]), org, "ext-e", None) is None

    await in_db(_run)


# ------------------------------------------------------------ sync mappings


@pytest.mark.asyncio
async def test_upsert_sync_mapping(app):
    """Mappings insert once, then update by external id or by elder id."""
    cfg_id = None

    def _run():
        """Seed a config and upsert mappings through each lookup path."""
        nonlocal cfg_id
        db = app.db
        t = now()
        cfg_id = db.sync_configs.insert(
            name=fake_secret("cfg"),
            platform="trello",
            enabled=True,
            sync_interval=60,
            batch_fallback_enabled=True,
            batch_size=10,
            two_way_create=False,
            webhook_enabled=True,
            config_json={},
            village_id=vid(),
            created_at=t,
            updated_at=t,
        )
        db.commit()
        sc = SimpleNamespace(id=cfg_id, platform="trello")
        kw = dict(
            elder_type="issue",
            sync_method="batch",
            sync_status="synced",
            elder_updated_at=t,
            external_updated_at=None,
            now=t,
        )
        first = ii._upsert_sync_mapping(db, sc, elder_id=1, external_id="e1", **kw)
        again = ii._upsert_sync_mapping(db, sc, elder_id=1, external_id="e1", **kw)
        by_elder = ii._upsert_sync_mapping(
            db, sc, elder_id=1, external_id="e1-new", **{**kw, "elder_updated_at": None}
        )
        other = ii._upsert_sync_mapping(
            db, sc, elder_id=2, external_id="e2", **{**kw, "external_updated_at": t}
        )
        db.commit()
        assert first == again == by_elder != other
        row = db.sync_mappings[first]
        assert row.external_id == "e1-new"

    await in_db(_run)


# ---------------------------------------------------------- directory sync


@pytest.mark.asyncio
async def test_unique_name_resolution(app):
    """Colliding usernames and group names are disambiguated deterministically."""
    tid, org = await _org(app)

    def _run():
        """Seed colliding identities/groups and resolve names."""
        db = app.db
        t = now()
        sfx = uuid.uuid4().hex[:8]
        base = f"dup-{sfx}"
        ident = db.identities.insert(
            identity_type="human",
            username=base,
            tenant_id=tid,
            auth_provider="local",
            is_active=True,
            is_superuser=False,
            mfa_enabled=False,
            must_change_password=False,
            portal_role="observer",
            created_at=t,
            updated_at=t,
        )
        db.identities.insert(
            identity_type="human",
            username=f"p:{org}:x1",
            tenant_id=tid,
            auth_provider="local",
            is_active=True,
            is_superuser=False,
            mfa_enabled=False,
            must_change_password=False,
            portal_role="observer",
            created_at=t,
            updated_at=t,
        )
        grp = db.identity_groups.insert(
            name=base,
            is_active=True,
            review_enabled=False,
            review_auto_apply=False,
            created_at=t,
            updated_at=t,
        )
        db.commit()
        u = ii._resolve_unique_identity_username
        assert u(db, f"free-{sfx}", "p", org, "x1") == f"free-{sfx}"
        assert u(db, base, "p", org, "x1", existing_id=ident) == base
        assert u(db, base, "p", org, "x2") == f"p:{org}:x2"
        assert (
            u(db, base, "p", org, "x1").startswith(f"p:{org}:")
            and u(db, base, "p", org, "x1") != f"p:{org}:x1"
        )
        g = ii._resolve_unique_group_name
        assert g(db, f"free-{sfx}", "p", org, "g1") == f"free-{sfx}"
        assert g(db, base, "p", org, "g1", existing_id=grp) == base
        assert g(db, base, "p", org, "g1") == f"{base} (p:{org}:g1)"

    await in_db(_run)


@pytest.mark.asyncio
async def test_upsert_directory_membership(app):
    """Membership links insert once and update on repeat; missing table is a no-op."""
    tid, org = await _org(app)

    def _run():
        """Create an identity and group, then link them twice."""
        db = app.db
        t = now()
        sfx = uuid.uuid4().hex[:8]
        ident = db.identities.insert(
            identity_type="human",
            username=f"m-{sfx}",
            tenant_id=tid,
            auth_provider="local",
            is_active=True,
            is_superuser=False,
            mfa_enabled=False,
            must_change_password=False,
            portal_role="observer",
            created_at=t,
            updated_at=t,
        )
        grp = db.identity_groups.insert(
            name=f"mg-{sfx}",
            is_active=True,
            review_enabled=False,
            review_auto_apply=False,
            created_at=t,
            updated_at=t,
        )
        db.commit()
        kw = dict(identity_id=ident, group_id=grp, now=t)
        assert ii._upsert_directory_membership(db, provider_member_id="pm", **kw)
        assert ii._upsert_directory_membership(db, provider_member_id=None, **kw)
        rows = db(db.identity_group_memberships.identity_id == ident).select()
        assert len(rows) == 1 and rows.first().provider_member_id == "pm"
        assert not ii._upsert_directory_membership(
            SimpleNamespace(tables=[]), provider_member_id=None, **kw
        )

    await in_db(_run)


@pytest.mark.asyncio
async def test_apply_directory_sync_tokenized(app):
    """IAM-style sync persists tokenized users, groups, roles and memberships."""
    tid, org = await _org(app)
    sfx = uuid.uuid4().hex[:8]

    def _run():
        """Apply a rich payload twice and verify idempotent counts and rows."""
        db = app.db
        t = now()
        users = [
            "skip",
            {"id": ""},
            {"external_id": f"u1-{sfx}", "uuid": f"tok-{sfx}", "groups": [f"g1-{sfx}"]},
            {"id": f"u2-{sfx}", "username": "has@at.example", "suspended": True},
            {"user_id": f"u3-{sfx}", "is_service_account": True, "mfa_enabled": 1},
            {
                "arn": f"arn:u4-{sfx}",
                "identity_type": "SERVICE_ACCOUNT",
                "portal_role": "Editor",
            },
        ]
        groups = [
            "skip",
            {"id": ""},
            {"external_id": f"g1-{sfx}", "name": f"Group {sfx}", "description": "d"},
            {"group_id": f"g2-{sfx}", "is_active": False},
        ]
        roles = [
            "skip",
            {},
            {"name": f"role-{sfx}", "arn": f"arn:role-{sfx}", "description": "r"},
            {"role_name": f"role2-{sfx}"},
        ]
        memberships = [
            "skip",
            {"user_external_id": f"u1-{sfx}", "group_external_id": f"g2-{sfx}"},
            {
                "identity_external_id": f"u2-{sfx}",
                "group_id": f"Group {sfx}",
                "provider_member_id": "pm",
            },
            {"user_id": f"u3-{sfx}", "role_external_id": f"arn:role-{sfx}"},
            {"user_uuid": f"tok-{sfx}", "role_name": f"role-{sfx}"},
            {"user_id": f"u3-{sfx}", "role_id": f"role2-{sfx}"},
            {"user_id": f"u3-{sfx}", "role_id": "pre-existing-" + sfx},
            {"user_id": "nobody", "group_id": f"g1-{sfx}"},
            {},
            {"user_id": f"u1-{sfx}", "group_id": "unknown-group"},
        ]
        db.roles.insert(name="pre-existing-" + sfx, created_at=t, updated_at=t)
        db.commit()

        def apply():
            """Run one sync pass with fresh copies of the payload lists."""
            return ii._apply_directory_sync(
                db,
                provider_type="aws",
                organization_id=org,
                tenant_id=tid,
                users=list(users),
                groups=list(groups),
                roles=list(roles),
                memberships=list(memberships),
                tokenize_pii=True,
                now=t,
            )

        first = apply()
        second = apply()
        db.commit()
        assert first["users_synced"] == 4 and first["groups_synced"] == 2
        assert first["roles_synced"] == 2
        assert second == first or second["users_synced"] == 4
        ids = db(
            (db.identities.auth_provider == "aws")
            & (db.identities.organization_id == org)
        ).select()
        assert len(ids) == 4
        names = {r.username for r in ids}
        assert f"tok-{sfx}" in names and all("@" not in n for n in names)
        assert all(r.email is None for r in ids)
        assert {r.identity_type for r in ids} == {"human", "service_account"}
        assert any(not r.is_active for r in ids)

    await in_db(_run)


@pytest.mark.asyncio
async def test_apply_directory_sync_plain_google(app):
    """Directory sync without tokenization keeps email/name and skips role entities."""
    tid, org = await _org(app)
    sfx = uuid.uuid4().hex[:8]

    def _run():
        """Apply a Google-style payload and check stored identity fields."""
        db = app.db
        t = now()
        users = [
            {
                "primaryEmail": f"a-{sfx}@example.com",
                "id": f"gu1-{sfx}",
                "name": {"fullName": "Ann Example"},
                "groups": [f"gg-{sfx}"],
            },
            {
                "id": f"gu2-{sfx}",
                "email": f"b-{sfx}@example.com",
                "full_name": f"b-{sfx}@example.com",
            },
            {"id": f"gu3-{sfx}", "username": "named"},
            {"id": f"gu4-{sfx}"},
        ]
        groups = [{"id": f"gg-{sfx}", "email": f"grp-{sfx}@example.com"}]
        memberships = [
            {"member_email": f"b-{sfx}@example.com", "group_external_id": f"gg-{sfx}"}
        ]
        out = ii._apply_directory_sync(
            db,
            provider_type="google",
            organization_id=org,
            tenant_id=tid,
            users=users,
            groups=groups,
            roles=[],
            memberships=memberships,
            tokenize_pii=False,
            now=t,
        )
        db.commit()
        assert out["users_synced"] == 4 and out["groups_synced"] == 1
        assert out["memberships_synced"] == 2
        rows = {
            r.auth_provider_id: r
            for r in db(
                (db.identities.auth_provider == "google")
                & (db.identities.organization_id == org)
            ).select()
        }
        a = rows[f"gu1-{sfx}"]
        assert a.email == f"a-{sfx}@example.com" and a.full_name == "Ann Example"
        assert rows[f"gu2-{sfx}"].full_name is None
        assert rows[f"gu4-{sfx}"].username == f"google:{org}:gu4-{sfx}"
        # Existing member found through the organization-scoped identity lookup.
        again = ii._apply_directory_sync(
            db,
            provider_type="google",
            organization_id=org,
            tenant_id=tid,
            users=[],
            groups=[],
            roles=[],
            memberships=[
                {
                    "user_id": f"gu3-{sfx}",
                    "group_id": f"grp-{sfx}@example.com",
                    "role_id": "none",
                }
            ],
            tokenize_pii=False,
            now=t,
        )
        assert again["memberships_synced"] == 1

    await in_db(_run)


@pytest.mark.asyncio
async def test_collect_local_sync_items(app):
    """Mapped and recent in-scope issues are collected honoring tenant and project scope."""
    tid, org = await _org(app)
    other_tid, other_org = await _org(app)

    def _run():
        """Seed issues, mappings and a project link and collect items."""
        db = app.db
        t = now()

        def mk(tenant, org_id, title):
            """Insert an issue for the given tenant/organization."""
            return db.issues.insert(
                tenant_id=tenant,
                organization_id=org_id,
                resource_type="organization",
                resource_id=org_id,
                title=title,
                status="OPEN",
                priority="LOW",
                issue_type="BUG",
                is_incident=0,
                reporter_id=None,
                village_id=vid(),
                created_at=t,
                updated_at=t,
            )

        own = mk(tid, org, "own")
        recent = mk(tid, org, "recent")
        foreign = mk(other_tid, other_org, "foreign")
        proj = db.projects.insert(
            name="p",
            status="active",
            organization_id=org,
            village_id=vid(),
            created_at=t,
            updated_at=t,
        )
        db.issue_project_links.insert(issue_id=recent, project_id=proj, created_at=t)
        empty_proj = db.projects.insert(
            name="p2",
            status="active",
            organization_id=org,
            village_id=vid(),
            created_at=t,
            updated_at=t,
        )
        db.commit()
        mappings = [
            SimpleNamespace(elder_type="issue", elder_id=own, external_id="x1"),
            SimpleNamespace(elder_type="issue", elder_id=foreign, external_id="x2"),
            SimpleNamespace(elder_type="label", elder_id=9, external_id="x3"),
            SimpleNamespace(elder_type="issue", elder_id=None, external_id="x4"),
        ]
        collect = ii._collect_local_sync_items
        plain = SimpleNamespace(batch_size=None, two_way_create=False)
        got = collect(db, plain, {}, mappings, org, tid)
        assert [i["id"] for i in got] == [own]
        assert got[0]["external_id"] == "x1"
        got = collect(db, plain, {}, mappings, None, None)
        assert {i["id"] for i in got} == {own, foreign}
        two_way = SimpleNamespace(batch_size=5, two_way_create=True)
        got = collect(db, two_way, {}, [], org, tid)
        assert {i["id"] for i in got} == {own, recent}
        got = collect(db, plain, {"project_id": proj}, mappings, org, tid)
        assert {i["id"] for i in got} == {own, recent}
        got = collect(db, plain, {"elder_project_id": empty_proj}, mappings, org, tid)
        assert {i["id"] for i in got} == {own}
        got = collect(db, two_way, {}, [], None, tid)
        assert {i["id"] for i in got} == {own, recent}
        got = collect(db, two_way, {}, [], None, None)
        assert got == []
        assert collect(SimpleNamespace(tables=[]), plain, {}, [], org, tid) == []

    await in_db(_run)
