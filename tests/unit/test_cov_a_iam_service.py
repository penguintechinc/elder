"""Unit tests for IAMService against the real test Postgres (iam_providers) with provider clients mocked.

Covers provider CRUD with secret redaction/preservation, org/tenant scoping of listings, the
provider-client factory, test/sync wrappers and the thin delegation methods.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from apps.api.services.iam import service as svc_mod
from apps.api.services.iam.service import (
    IAMProviderNotFoundError,
    IAMService,
    IAMTenantRequiredError,
    _is_blank_or_redacted_secret,
    _parse_json_dict,
)

REDACTED = "***REDACTED***"


def _tenant_org(db, label="iam"):
    """Create a fresh tenant plus organization and return (tenant_id, org_id)."""
    uniq = uuid4().hex[:8]
    tid = db.tenants.insert(
        name=f"{label}-{uniq}", slug=f"{label}-{uniq}", is_active=True
    )
    now = datetime.now(UTC)
    oid = db.organizations.insert(
        name=f"{label}-org-{uniq}", tenant_id=tid, created_at=now, updated_at=now
    )
    db.commit()
    return tid, oid


def _extra_org(db, tenant_id, label="iam-extra"):
    """Create another organization inside an existing tenant and return its id."""
    now = datetime.now(UTC)
    oid = db.organizations.insert(
        name=f"{label}-{uuid4().hex[:8]}",
        tenant_id=tenant_id,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return oid


def _fake(*parts):
    """Build a fake secret value at runtime."""
    return "-".join(["fake", *parts, uuid4().hex[:6]])


@pytest.fixture
def cleanup(app):
    """Collect provider ids and delete them after the test."""
    ids = []
    yield ids
    db = app.db
    for pid in ids:
        db(db.iam_providers.id == pid).delete()
    db.commit()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ({"a": 1}, {"a": 1}),
        ('{"a": 1}', {"a": 1}),
        ("[1, 2]", {}),
        ("not json", {}),
        (None, {}),
        (5, {}),
    ],
)
def test_parse_json_dict(raw, expected):
    """_parse_json_dict returns a dict copy for dict/JSON-object input and {} otherwise."""
    assert _parse_json_dict(raw) == expected


def test_parse_json_dict_returns_copy():
    """The parsed dict is a copy, not the caller's object."""
    src = {"a": 1}
    out = _parse_json_dict(src)
    out["b"] = 2
    assert "b" not in src


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, True),
        ("", True),
        ("  ", True),
        (REDACTED, True),
        ("***masked***", True),
        ({}, True),
        ("real", False),
        ({"k": "v"}, False),
        (0, False),
    ],
)
def test_is_blank_or_redacted_secret(value, expected):
    """Blank/None/redaction markers/empty dict count as blank; real values do not."""
    assert _is_blank_or_redacted_secret(value) is expected


@pytest.mark.asyncio
async def test_create_get_and_redact_provider(app, cleanup):
    """create_provider stores config, lower-cases type, and responses redact secrets."""
    async with app.app_context():
        tid, org = _tenant_org(app.db)
        secret = _fake("tok")
        out = IAMService(app.db).create_provider(
            tid,
            "P-" + uuid4().hex[:6],
            "KUBERNETES",
            {"namespace": "x", "token": secret},
            description="my desc",
            organization_id=org,
        )
        cleanup.append(out["id"])
        assert out["provider_type"] == "kubernetes"
        assert out["organization_id"] == org
        assert out["enabled"] is True
        assert out["config_json"]["token"] == REDACTED
        assert out["config_json"]["namespace"] == "x"
        assert out["description"] == "my desc"
        raw = app.db(app.db.iam_providers.id == out["id"]).select().first()
        assert raw.config_json["token"] == secret
        assert raw.village_id


@pytest.mark.asyncio
async def test_create_provider_rejects_invalid_type(app):
    """An unknown provider type is rejected before touching the DB."""
    async with app.app_context():
        with pytest.raises(Exception, match="Invalid provider type: azure"):
            IAMService(app.db).create_provider(1, "p", "azure", {}, organization_id=1)


def test_create_provider_requires_org_when_none_exists():
    """With no organization_id and no organizations, a ValueError is raised."""
    db = MagicMock()
    db.return_value.select.return_value = []
    with pytest.raises(ValueError, match="organization_id is required"):
        IAMService(db).create_provider(1, "p", "aws_iam", {})


@pytest.mark.parametrize("tenant_id", [None, 0])
def test_service_requires_verified_tenant(tenant_id):
    """Every entry point fails closed without a tenant id."""
    svc = IAMService(MagicMock())
    with pytest.raises(IAMTenantRequiredError):
        svc.list_providers(tenant_id)
    with pytest.raises(IAMTenantRequiredError):
        svc.get_provider(tenant_id, 1)
    with pytest.raises(IAMTenantRequiredError):
        svc.create_provider(tenant_id, "p", "aws_iam", {}, organization_id=1)
    with pytest.raises(IAMTenantRequiredError):
        svc.list_users(tenant_id, 1)


@pytest.mark.asyncio
async def test_create_provider_defaults_to_first_organization(app, cleanup):
    """Without organization_id the caller tenant's lowest-id organization is used."""
    async with app.app_context():
        tid, first_org = _tenant_org(app.db)
        _extra_org(app.db, tid)
        out = IAMService(app.db).create_provider(
            tid, "P-" + uuid4().hex[:6], "aws_iam", {}
        )
        cleanup.append(out["id"])
        assert out["organization_id"] == first_org
        assert out["description"] is None


@pytest.mark.asyncio
async def test_get_provider_not_found(app):
    """get_provider raises for an unknown id."""
    async with app.app_context():
        with pytest.raises(Exception, match="Provider not found: 987654321"):
            IAMService(app.db).get_provider(1, 987654321)


@pytest.mark.asyncio
async def test_list_providers_scoped_by_organization_across_tenants(app, cleanup):
    """Each tenant lists only its own providers; foreign ids and orgs are not found."""
    async with app.app_context():
        svc = IAMService(app.db)
        tid_a, org_a = _tenant_org(app.db, "a")
        tid_b, org_b = _tenant_org(app.db, "b")
        a = svc.create_provider(
            tid_a, "A-" + uuid4().hex[:6], "aws_iam", {}, organization_id=org_a
        )
        b = svc.create_provider(
            tid_b, "B-" + uuid4().hex[:6], "gcp_iam", {}, organization_id=org_b
        )
        cleanup.extend([a["id"], b["id"]])
        ids_a = {p["id"] for p in svc.list_providers(tid_a, organization_id=org_a)}
        ids_b = {p["id"] for p in svc.list_providers(tid_b, organization_id=org_b)}
        assert ids_a == {a["id"]}
        assert ids_b == {b["id"]}
        assert svc.list_providers(tid_a, organization_id=org_b) == []
        with pytest.raises(IAMProviderNotFoundError):
            svc.get_provider(tid_a, b["id"])
        with pytest.raises(IAMProviderNotFoundError):
            svc.update_provider(tid_a, b["id"], name="hijack")
        with pytest.raises(IAMProviderNotFoundError):
            svc.delete_provider(tid_a, b["id"])
        with pytest.raises(IAMProviderNotFoundError):
            svc.update_provider(tid_a, a["id"], organization_id=org_b)
        with pytest.raises(IAMProviderNotFoundError):
            svc.create_provider(tid_a, "x", "aws_iam", {}, organization_id=org_b)
        assert svc.get_provider(tid_b, b["id"])["name"].startswith("B-")


@pytest.mark.asyncio
async def test_list_providers_enabled_only_filters_disabled(app, cleanup):
    """enabled_only hides disabled providers; results are ordered by name."""
    async with app.app_context():
        svc = IAMService(app.db)
        tid, org = _tenant_org(app.db)
        p1 = svc.create_provider(
            tid, "b-" + uuid4().hex[:6], "aws_iam", {}, organization_id=org
        )
        p2 = svc.create_provider(
            tid, "a-" + uuid4().hex[:6], "aws_iam", {}, organization_id=org
        )
        cleanup.extend([p1["id"], p2["id"]])
        svc.update_provider(tid, p1["id"], enabled=False)
        all_rows = svc.list_providers(tid, organization_id=org)
        assert [r["id"] for r in all_rows] == [p2["id"], p1["id"]]
        enabled = svc.list_providers(tid, enabled_only=True, organization_id=org)
        assert [r["id"] for r in enabled] == [p2["id"]]


@pytest.mark.asyncio
async def test_update_provider_not_found(app):
    """update_provider raises for an unknown id."""
    async with app.app_context():
        with pytest.raises(Exception, match="Provider not found: 987654321"):
            IAMService(app.db).update_provider(1, 987654321, name="x")


@pytest.mark.asyncio
async def test_update_provider_basic_fields(app, cleanup):
    """name, enabled and organization_id updates persist and bump updated_at."""
    async with app.app_context():
        svc = IAMService(app.db)
        tid, org1 = _tenant_org(app.db)
        org2 = _extra_org(app.db, tid)
        p = svc.create_provider(
            tid, "n-" + uuid4().hex[:6], "aws_iam", {}, organization_id=org1
        )
        cleanup.append(p["id"])
        new_name = "renamed-" + uuid4().hex[:6]
        out = svc.update_provider(
            tid, p["id"], name=new_name, enabled=False, organization_id=org2
        )
        assert (out["name"], out["enabled"], out["organization_id"]) == (
            new_name,
            False,
            org2,
        )
        assert out["updated_at"] >= p["updated_at"]


@pytest.mark.asyncio
async def test_update_provider_nothing_to_update_is_noop(app, cleanup):
    """With no fields the row is returned unchanged and updated_at is not touched."""
    async with app.app_context():
        svc = IAMService(app.db)
        tid, org = _tenant_org(app.db)
        p = svc.create_provider(
            tid, "n-" + uuid4().hex[:6], "aws_iam", {}, organization_id=org
        )
        cleanup.append(p["id"])
        out = svc.update_provider(tid, p["id"])
        assert out["updated_at"] == p["updated_at"]
        assert out["name"] == p["name"]


@pytest.mark.asyncio
async def test_update_provider_keeps_stored_secret_when_blank_or_redacted(app, cleanup):
    """A blank/redacted secret in the new config keeps the stored value; a new value replaces it."""
    async with app.app_context():
        svc = IAMService(app.db)
        tid, org = _tenant_org(app.db)
        secret = _fake("sec")
        p = svc.create_provider(
            tid,
            "n-" + uuid4().hex[:6],
            "aws_iam",
            {"region": "us-east-1", "secret_access_key": secret, "token": _fake("t")},
            description="orig",
            organization_id=org,
        )
        cleanup.append(p["id"])

        def stored():
            return (
                app.db(app.db.iam_providers.id == p["id"]).select().first().config_json
            )

        svc.update_provider(
            tid,
            p["id"],
            config={"region": "eu-west-1", "secret_access_key": REDACTED, "token": ""},
        )
        cfg = stored()
        assert cfg["region"] == "eu-west-1"
        assert cfg["secret_access_key"] == secret
        assert cfg["token"].startswith("fake-t")
        assert cfg["_description"] == "orig"

        new_secret = _fake("new")
        svc.update_provider(tid, p["id"], config={"secret_access_key": new_secret})
        assert stored()["secret_access_key"] == new_secret


@pytest.mark.asyncio
async def test_update_provider_description_only_and_explicit_description_wins(
    app, cleanup
):
    """description alone rewrites _description on the stored config; explicit config key is overridden."""
    async with app.app_context():
        svc = IAMService(app.db)
        tid, org = _tenant_org(app.db)
        p = svc.create_provider(
            tid,
            "n-" + uuid4().hex[:6],
            "aws_iam",
            {"region": "r1"},
            organization_id=org,
        )
        cleanup.append(p["id"])
        out = svc.update_provider(tid, p["id"], description="only desc")
        assert out["config_json"] == {"region": "r1", "_description": "only desc"}
        out = svc.update_provider(
            tid,
            p["id"],
            config={"region": "r2", "_description": "cfg"},
            description="wins",
        )
        assert out["config_json"]["_description"] == "wins"
        assert out["config_json"]["region"] == "r2"


@pytest.mark.asyncio
async def test_update_provider_config_with_own_description_not_overwritten(
    app, cleanup
):
    """A _description inside the new config is kept instead of the stored one."""
    async with app.app_context():
        svc = IAMService(app.db)
        tid, org = _tenant_org(app.db)
        p = svc.create_provider(
            tid,
            "n-" + uuid4().hex[:6],
            "aws_iam",
            {},
            description="old",
            organization_id=org,
        )
        cleanup.append(p["id"])
        out = svc.update_provider(tid, p["id"], config={"_description": "new-in-cfg"})
        assert out["config_json"]["_description"] == "new-in-cfg"


@pytest.mark.asyncio
async def test_delete_provider_not_found(app):
    """delete_provider raises for an unknown id."""
    async with app.app_context():
        with pytest.raises(Exception, match="Provider not found: 987654321"):
            IAMService(app.db).delete_provider(1, 987654321)


@pytest.mark.asyncio
async def test_delete_provider_without_resources_succeeds(app, cleanup):
    """Deleting a provider that has no users/roles removes it and confirms."""
    async with app.app_context():
        svc = IAMService(app.db)
        tid, org = _tenant_org(app.db)
        p = svc.create_provider(
            tid, "d-" + uuid4().hex[:6], "aws_iam", {}, organization_id=org
        )
        cleanup.append(p["id"])
        assert svc.delete_provider(tid, p["id"]) == {
            "message": "Provider deleted successfully"
        }
        with pytest.raises(Exception, match="Provider not found"):
            svc.get_provider(tid, p["id"])


@pytest.mark.asyncio
async def test_get_provider_client_not_found(app):
    """The client factory raises for an unknown provider id."""
    async with app.app_context():
        with pytest.raises(Exception, match="IAM provider not found: 987654321"):
            IAMService(app.db)._get_provider_client(1, 987654321)


@pytest.mark.parametrize(
    "ptype,cls_name",
    [
        ("aws_iam", "AWSIAMClient"),
        ("gcp_iam", "GCPIAMClient"),
        ("kubernetes", "KubernetesRBACClient"),
    ],
)
@pytest.mark.asyncio
async def test_get_provider_client_builds_matching_client(
    app, cleanup, ptype, cls_name
):
    """The factory instantiates the right client class with merged config_json."""
    async with app.app_context():
        svc = IAMService(app.db)
        tid, org = _tenant_org(app.db)
        p = svc.create_provider(
            tid, "c-" + uuid4().hex[:6], ptype, {"region": "r9"}, organization_id=org
        )
        cleanup.append(p["id"])
        with patch.object(svc_mod, cls_name) as cls:
            result = svc._get_provider_client(tid, p["id"])
        assert result is cls.return_value
        cfg = cls.call_args.args[0]
        assert cfg["provider_type"] == ptype
        assert cfg["region"] == "r9"
        assert cfg["name"] == p["name"]


@pytest.mark.asyncio
async def test_get_provider_client_unsupported_type(app, cleanup):
    """A stored provider with an unsupported type is refused by the factory."""
    async with app.app_context():
        db = app.db
        tid, org = _tenant_org(db)
        now = datetime.now(UTC)
        pid = db.iam_providers.insert(
            name="u-" + uuid4().hex[:6],
            provider_type="Weird",
            organization_id=org,
            enabled=True,
            config_json={},
            village_id="00000001-" + uuid4().hex[:16],
            created_at=now,
            updated_at=now,
        )
        db.commit()
        cleanup.append(pid)
        with pytest.raises(Exception, match="Unsupported provider type: weird"):
            IAMService(db)._get_provider_client(tid, pid)


@pytest.mark.asyncio
async def test_provider_without_config_json_still_builds_client(app, cleanup):
    """A provider with empty config_json builds a client from the base columns only."""
    async with app.app_context():
        db = app.db
        tid, org = _tenant_org(db)
        now = datetime.now(UTC)
        pid = db.iam_providers.insert(
            name="e-" + uuid4().hex[:6],
            provider_type="aws_iam",
            organization_id=org,
            enabled=True,
            config_json=None,
            village_id="00000001-" + uuid4().hex[:16],
            created_at=now,
            updated_at=now,
        )
        db.commit()
        cleanup.append(pid)
        with patch.object(svc_mod, "AWSIAMClient") as cls:
            IAMService(db)._get_provider_client(tid, pid)
        assert cls.call_args.args[0]["provider_type"] == "aws_iam"


@pytest.mark.asyncio
async def test_test_provider_success_failure_and_unknown(app, cleanup):
    """test_provider reports the client's result and converts any failure into success=False."""
    async with app.app_context():
        svc = IAMService(app.db)
        with (
            patch.object(svc, "_get_scoped_provider"),
            patch.object(svc, "_get_provider_client") as getc,
        ):
            getc.return_value.test_connection.return_value = True
            ok = svc.test_provider(1, 7)
            assert (
                ok["success"] is True and ok["provider_id"] == 7 and "tested_at" in ok
            )
            getc.return_value.test_connection.return_value = False
            assert svc.test_provider(1, 7)["success"] is False
            getc.side_effect = Exception("no provider")
            bad = svc.test_provider(1, 7)
            assert bad["success"] is False
            assert bad["error"] == "no provider"


@pytest.mark.asyncio
async def test_sync_provider_unknown_provider_is_not_found(app):
    """An unknown/foreign provider id raises not-found instead of reporting a sync."""
    async with app.app_context():
        with pytest.raises(IAMProviderNotFoundError, match="Provider not found"):
            IAMService(app.db).sync_provider(1, 987654321)


def test_sync_provider_client_failure_is_reported_in_errors():
    """If the provider client fails, sync_provider returns zero counts plus the error."""
    svc = IAMService(MagicMock())
    with (
        patch.object(svc, "_get_scoped_provider"),
        patch.object(svc, "_get_provider_client", side_effect=Exception("boom")),
    ):
        assert svc.sync_provider(1, 5) == {
            "users_synced": 0,
            "roles_synced": 0,
            "policies_synced": 0,
            "errors": ["boom"],
        }


def test_sync_provider_logs_result_and_returns_it():
    """On success last_sync_at is stamped on the provider, committed and the result returned."""
    db = MagicMock()
    svc = IAMService(db)
    result = {
        "users_synced": 2,
        "roles_synced": 1,
        "policies_synced": 3,
        "errors": ["e"],
    }
    with (
        patch.object(svc, "_get_scoped_provider") as scoped,
        patch.object(svc, "_get_provider_client") as getc,
    ):
        scoped.return_value.id = 5
        getc.return_value.sync_from_provider.return_value = result
        assert svc.sync_provider(3, 5) == result
        getc.assert_called_once_with(3, 5)
    kwargs = db.return_value.update.call_args.kwargs
    assert kwargs["last_sync_at"] is not None
    assert kwargs["updated_at"] == kwargs["last_sync_at"]
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_sync_provider_with_real_db_returns_client_result(app, cleanup):
    """A successful provider sync should return the client's result even with the real database."""
    async with app.app_context():
        svc = IAMService(app.db)
        tid, org = _tenant_org(app.db)
        p = svc.create_provider(
            tid, "s-" + uuid4().hex[:6], "aws_iam", {}, organization_id=org
        )
        cleanup.append(p["id"])
        result = {
            "users_synced": 1,
            "roles_synced": 0,
            "policies_synced": 0,
            "errors": [],
        }
        with patch.object(svc, "_get_provider_client") as getc:
            getc.return_value.sync_from_provider.return_value = result
            assert svc.sync_provider(tid, p["id"]) == result
        stored = app.db(app.db.iam_providers.id == p["id"]).select().first()
        assert stored.last_sync_at is not None


# (service method, args, client method, expected client args)
DELEGATIONS = [
    ("list_users", (1,), {"limit": 3}, "list_users", (), {"limit": 3}),
    ("get_user", (1, "u"), {}, "get_user", ("u",), {}),
    ("create_user", (1,), {"username": "u"}, "create_user", (), {"username": "u"}),
    (
        "update_user",
        (1, "u"),
        {"display_name": "d"},
        "update_user",
        ("u",),
        {"display_name": "d"},
    ),
    ("delete_user", (1, "u"), {}, "delete_user", ("u",), {}),
    ("list_roles", (1,), {"limit": 3}, "list_roles", (), {"limit": 3}),
    ("get_role", (1, "r"), {}, "get_role", ("r",), {}),
    ("create_role", (1,), {"role_name": "r"}, "create_role", (), {"role_name": "r"}),
    (
        "update_role",
        (1, "r"),
        {"description": "d"},
        "update_role",
        ("r",),
        {"description": "d"},
    ),
    ("delete_role", (1, "r"), {}, "delete_role", ("r",), {}),
    (
        "list_policies",
        (1,),
        {"scope": "Local"},
        "list_policies",
        (),
        {"scope": "Local"},
    ),
    ("get_policy", (1, "p"), {}, "get_policy", ("p",), {}),
    (
        "create_policy",
        (1,),
        {"policy_name": "p"},
        "create_policy",
        (),
        {"policy_name": "p"},
    ),
    ("delete_policy", (1, "p"), {}, "delete_policy", ("p",), {}),
    (
        "attach_policy_to_user",
        (1, "u", "p"),
        {},
        "attach_policy_to_user",
        ("u", "p"),
        {},
    ),
    (
        "detach_policy_from_user",
        (1, "u", "p"),
        {},
        "detach_policy_from_user",
        ("u", "p"),
        {},
    ),
    (
        "attach_policy_to_role",
        (1, "r", "p"),
        {},
        "attach_policy_to_role",
        ("r", "p"),
        {},
    ),
    (
        "detach_policy_from_role",
        (1, "r", "p"),
        {},
        "detach_policy_from_role",
        ("r", "p"),
        {},
    ),
    ("list_user_policies", (1, "u"), {}, "list_user_policies", ("u",), {}),
    ("list_role_policies", (1, "r"), {}, "list_role_policies", ("r",), {}),
    ("create_access_key", (1, "u"), {}, "create_access_key", ("u",), {}),
    ("list_access_keys", (1, "u"), {}, "list_access_keys", ("u",), {}),
    ("delete_access_key", (1, "u", "k"), {}, "delete_access_key", ("u", "k"), {}),
    ("list_groups", (1,), {"limit": 2}, "list_groups", (), {"limit": 2}),
    (
        "create_group",
        (1,),
        {"group_name": "g"},
        "create_group",
        (),
        {"group_name": "g"},
    ),
    ("delete_group", (1, "g"), {}, "delete_group", ("g",), {}),
    ("add_user_to_group", (1, "u", "g"), {}, "add_user_to_group", ("u", "g"), {}),
    (
        "remove_user_from_group",
        (1, "u", "g"),
        {},
        "remove_user_from_group",
        ("u", "g"),
        {},
    ),
]


@pytest.mark.parametrize("method,args,kwargs,cmethod,cargs,ckwargs", DELEGATIONS)
def test_service_delegates_to_provider_client(
    method, args, kwargs, cmethod, cargs, ckwargs
):
    """Each service method forwards to the matching client method and returns its result."""
    svc = IAMService(MagicMock())
    with patch.object(svc, "_get_provider_client") as getc:
        sentinel = {"ok": method}
        getattr(getc.return_value, cmethod).return_value = sentinel
        assert getattr(svc, method)(42, *args, **kwargs) is sentinel
        getc.assert_called_once_with(42, 1)
        getattr(getc.return_value, cmethod).assert_called_once_with(*cargs, **ckwargs)


@pytest.mark.parametrize("method,args", [(m, a) for m, a, *_ in DELEGATIONS])
def test_service_delegation_propagates_missing_provider(method, args):
    """A missing provider surfaces as an exception from every delegating method."""
    svc = IAMService(MagicMock())
    with patch.object(
        svc, "_get_provider_client", side_effect=Exception("IAM provider not found: 1")
    ):
        with pytest.raises(Exception, match="IAM provider not found"):
            getattr(svc, method)(42, *args)


def test_sanitize_provider_without_config_leaves_fields():
    """A provider dict lacking config_json passes through untouched."""
    svc = IAMService(MagicMock())
    assert svc._sanitize_provider({"id": 1}) == {"id": 1}
    assert svc._sanitize_provider({"id": 1, "config_json": None}) == {
        "id": 1,
        "config_json": None,
    }


def test_sanitize_provider_redacts_only_real_secrets_and_parses_json_string():
    """Secrets are redacted, blank ones left, string config parsed; description is derived."""
    svc = IAMService(MagicMock())
    out = svc._sanitize_provider(
        {
            "config_json": '{"api_key": "abc", "token": "", "region": "r", "_description": "d"}'
        }
    )
    assert out["config_json"] == {
        "api_key": REDACTED,
        "token": "",
        "region": "r",
        "_description": "d",
    }
    assert out["description"] == "d"


def test_sanitize_provider_keeps_existing_description():
    """An existing description key is not overwritten by _description."""
    svc = IAMService(MagicMock())
    out = svc._sanitize_provider(
        {"description": "top", "config_json": {"_description": "inner"}}
    )
    assert out["description"] == "top"


def test_sanitize_provider_does_not_mutate_input():
    """Sanitising returns a new dict and leaves the original provider untouched."""
    svc = IAMService(MagicMock())
    src = {"config_json": {"token": "abc"}}
    svc._sanitize_provider(src)
    assert src["config_json"]["token"] == "abc"


@pytest.mark.asyncio
async def test_list_providers_without_filters_includes_all_orgs(app, cleanup):
    """Without an organization filter a tenant sees all of its orgs but no other tenant's."""
    async with app.app_context():
        svc = IAMService(app.db)
        tid_a, org_a = _tenant_org(app.db, "la")
        tid_b, org_b = _tenant_org(app.db, "lb")
        org_a2 = _extra_org(app.db, tid_a)
        a = svc.create_provider(
            tid_a, "la-" + uuid4().hex[:6], "aws_iam", {}, organization_id=org_a
        )
        a2 = svc.create_provider(
            tid_a, "la2-" + uuid4().hex[:6], "aws_iam", {}, organization_id=org_a2
        )
        b = svc.create_provider(
            tid_b, "lb-" + uuid4().hex[:6], "aws_iam", {}, organization_id=org_b
        )
        cleanup.extend([a["id"], a2["id"], b["id"]])
        ids = {p["id"] for p in svc.list_providers(tid_a)}
        assert ids == {a["id"], a2["id"]}
