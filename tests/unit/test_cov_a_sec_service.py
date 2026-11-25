"""Coverage tests for SecretsService (real Postgres, provider client faked at the boundary)."""

# flake8: noqa: E501

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import pytest_asyncio

from apps.api.services.secrets.base import (
    SecretMetadata,
    SecretNotFoundException,
    SecretProviderClient,
    SecretProviderException,
    SecretValue,
)
from apps.api.services.secrets.service import SecretsService
from apps.api.utils.async_utils import run_in_threadpool


class FakeClient(SecretProviderClient):
    """Provider client double driven by its config dict (the external boundary)."""

    def _validate_config(self):
        """Fail construction when config requests it."""
        if self.config.get("init_fail"):
            raise RuntimeError("bad config")

    def test_connection(self):
        """Report the configured connection result."""
        return self.config.get("connect", True)

    def get_secret(self, path, version=None):
        """Return a value, a KV value, or raise per config."""
        mode = self.config.get("mode", "ok")
        if mode == "missing":
            raise SecretNotFoundException(path)
        if mode == "boom":
            raise SecretProviderException("provider down")
        if mode == "kv":
            return SecretValue(
                name=path, is_masked=False, is_kv=True, kv_pairs={"a": "1", "b": "2"}
            )
        return SecretValue(name=path, value="s3cret-val", is_masked=False, version="v1")

    def list_secrets(self, prefix=None):
        """Return the configured list of provider paths."""
        return [
            SecretMetadata(name=p, path=p, is_kv=False)
            for p in self.config.get("paths", [])
        ]

    def create_secret(self, path, value, metadata=None):
        """Unused by the service."""
        raise NotImplementedError

    def update_secret(self, path, value):
        """Unused by the service."""
        raise NotImplementedError

    def delete_secret(self, path, force=False):
        """Unused by the service."""
        raise NotImplementedError

    def get_secret_versions(self, path):
        """Unused by the service."""
        raise NotImplementedError


@pytest.fixture(autouse=True)
def fake_provider():
    """Route every provider type to FakeClient for the duration of the test."""
    mapping = {k: FakeClient for k in SecretsService.PROVIDER_CLIENTS}
    with patch.dict(SecretsService.PROVIDER_CLIENTS, mapping):
        yield


@pytest_asyncio.fixture
async def world(app):
    """Two tenants/orgs and one identity, all uniquely named."""
    db = app.db
    sfx = uuid.uuid4().hex[:8]

    def _setup():
        now = datetime.now(UTC)
        out = {"sfx": sfx}
        for key in ("a", "b"):
            tid = db.tenants.insert(
                name=f"svc-{key}-{sfx}", slug=f"svc-{key}-{sfx}", is_active=True
            )
            out[f"tenant_{key}"] = tid
            out[f"org_{key}"] = db.organizations.insert(
                name=f"sorg-{key}-{sfx}", tenant_id=tid, created_at=now, updated_at=now
            )
        out["identity"] = db.identities.insert(
            identity_type="human",
            username=f"svc-user-{sfx}",
            email=f"svc-{sfx}@example.com",
            tenant_id=out["tenant_a"],
            auth_provider="local",
            is_active=True,
            is_superuser=False,
            mfa_enabled=False,
            must_change_password=False,
            portal_role="observer",
            created_at=now,
            updated_at=now,
        )
        db.commit()
        return out

    return await run_in_threadpool(_setup)


async def _svc(app, fn):
    """Run ``fn(service)`` on a worker thread with a SecretsService bound to the DB."""

    def _run():
        return fn(SecretsService(app.db))

    return await run_in_threadpool(_run)


async def _provider(app, world, **cfg):
    """Create a provider through the service and return its dict."""
    return await _svc(
        app,
        lambda s: s.create_provider(
            f"prov-{uuid.uuid4().hex[:6]}",
            "aws_secrets_manager",
            cfg,
            world["org_a"],
        ),
    )


async def _secret(app, world, prov, path=None, **kw):
    """Register a secret under ``prov`` through the service."""
    path = path or f"/p/{uuid.uuid4().hex[:8]}"
    return await _svc(
        app,
        lambda s: s.create_secret(
            name=f"sec-{uuid.uuid4().hex[:6]}",
            provider_id=prov["id"],
            provider_path=path,
            secret_type="password",
            organization_id=world["org_a"],
            identity_id=kw.get("identity_id"),
            metadata=kw.get("metadata"),
        ),
    )


class TestProviderClientResolution:
    """SecretsService.get_provider_client branches."""

    @pytest.mark.asyncio
    async def test_unknown_provider_id(self, app):
        """A non-existent provider id raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            await _svc(app, lambda s: s.get_provider_client(2_000_000_000))

    @pytest.mark.asyncio
    async def test_disabled_provider(self, app, world):
        """A disabled provider cannot be turned into a client."""
        prov = await _provider(app, world)
        await _svc(app, lambda s: s.update_provider(prov["id"], enabled=False))
        with pytest.raises(ValueError, match="disabled"):
            await _svc(app, lambda s: s.get_provider_client(prov["id"]))

    @pytest.mark.asyncio
    async def test_unknown_provider_type(self, app, world):
        """A stored provider with an unmapped type raises ValueError."""
        prov = await _provider(app, world)

        def _corrupt():
            app.db(app.db.secret_providers.id == prov["id"]).update(provider="nope")
            app.db.commit()

        await run_in_threadpool(_corrupt)
        with pytest.raises(ValueError, match="Unknown provider type"):
            await _svc(app, lambda s: s.get_provider_client(prov["id"]))

    @pytest.mark.asyncio
    async def test_client_init_failure_wrapped(self, app, world):
        """A client constructor error is re-raised as SecretProviderException."""
        prov = await _provider(app, world)

        def _break():
            app.db(app.db.secret_providers.id == prov["id"]).update(
                config_json={"init_fail": True}
            )
            app.db.commit()

        await run_in_threadpool(_break)
        with pytest.raises(SecretProviderException, match="Failed to initialize"):
            await _svc(app, lambda s: s.get_provider_client(prov["id"]))

    @pytest.mark.asyncio
    async def test_success_returns_client(self, app, world):
        """A valid enabled provider yields an initialised client."""
        prov = await _provider(app, world)
        client = await _svc(app, lambda s: s.get_provider_client(prov["id"]))
        assert isinstance(client, FakeClient)


class TestProviderCrud:
    """Provider create/list/get/update/delete against the real DB."""

    @pytest.mark.asyncio
    async def test_create_and_get(self, app, world):
        """create_provider persists an enabled provider retrievable by id."""
        prov = await _provider(app, world)
        got = await _svc(app, lambda s: s.get_provider(prov["id"]))
        assert got["enabled"] is True and got["organization_id"] == world["org_a"]
        assert got["provider"] == "aws_secrets_manager"
        assert got["village_id"]

    @pytest.mark.asyncio
    async def test_create_rejects_unknown_type(self, app, world):
        """An unknown provider_type is rejected before any client is built."""
        with pytest.raises(ValueError, match="Unknown provider type"):
            await _svc(
                app, lambda s: s.create_provider("n", "vault9", {}, world["org_a"])
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "cfg,msg",
        [
            ({"connect": False}, "connection test failed"),
            ({"init_fail": True}, "validation failed"),
        ],
    )
    async def test_create_rejects_bad_provider(self, app, world, cfg, msg):
        """A failing connection test or client init yields ValueError, nothing stored."""
        name = f"bad-{uuid.uuid4().hex[:6]}"
        with pytest.raises(ValueError, match=msg):
            await _svc(
                app,
                lambda s: s.create_provider(
                    name, "aws_secrets_manager", cfg, world["org_a"]
                ),
            )

        def _count():
            return app.db(app.db.secret_providers.name == name).count()

        assert await run_in_threadpool(_count) == 0

    @pytest.mark.asyncio
    async def test_get_missing(self, app):
        """get_provider on an unknown id raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            await _svc(app, lambda s: s.get_provider(2_000_000_000))

    @pytest.mark.asyncio
    async def test_list_filters(self, app, world):
        """list_providers filters by organization and provider type."""
        prov = await _provider(app, world)
        rows = await _svc(
            app,
            lambda s: s.list_providers(
                organization_id=world["org_a"], provider_type="aws_secrets_manager"
            ),
        )
        assert prov["id"] in [r["id"] for r in rows]
        none = await _svc(
            app,
            lambda s: s.list_providers(
                organization_id=world["org_a"], provider_type="infisical"
            ),
        )
        assert prov["id"] not in [r["id"] for r in none]
        other = await _svc(
            app, lambda s: s.list_providers(organization_id=world["org_b"])
        )
        assert prov["id"] not in [r["id"] for r in other]

    @pytest.mark.asyncio
    async def test_list_without_filters_includes_provider(self, app, world):
        """An unfiltered list returns every provider including a new one."""
        prov = await _provider(app, world)
        rows = await _svc(app, lambda s: s.list_providers())
        assert prov["id"] in [r["id"] for r in rows]

    @pytest.mark.asyncio
    async def test_update_name_config_enabled(self, app, world):
        """update_provider persists name, a validated config and the enabled flag."""
        prov = await _provider(app, world)
        upd = await _svc(
            app,
            lambda s: s.update_provider(
                prov["id"],
                name=f"ren-{world['sfx']}",
                config_json={"x": 1},
                enabled=False,
            ),
        )
        assert upd["name"] == f"ren-{world['sfx']}" and upd["enabled"] is False

    @pytest.mark.asyncio
    async def test_update_noop(self, app, world):
        """update_provider with no fields leaves the provider untouched."""
        prov = await _provider(app, world)
        upd = await _svc(app, lambda s: s.update_provider(prov["id"]))
        assert upd["name"] == prov["name"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cfg", [{"connect": False}, {"init_fail": True}])
    async def test_update_rejects_bad_config(self, app, world, cfg):
        """A config failing validation is rejected and not stored."""
        prov = await _provider(app, world)
        with pytest.raises(ValueError, match="config validation failed"):
            await _svc(app, lambda s: s.update_provider(prov["id"], config_json=cfg))

    @pytest.mark.asyncio
    async def test_update_missing(self, app):
        """update_provider on an unknown id raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            await _svc(app, lambda s: s.update_provider(2_000_000_000, name="x"))

    @pytest.mark.asyncio
    async def test_delete_missing(self, app):
        """delete_provider on an unknown id raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            await _svc(app, lambda s: s.delete_provider(2_000_000_000))

    @pytest.mark.asyncio
    async def test_delete_in_use_rejected(self, app, world):
        """A provider still referenced by a secret cannot be deleted."""
        prov = await _provider(app, world)
        await _secret(app, world, prov)
        with pytest.raises(ValueError, match="Cannot delete provider"):
            await _svc(app, lambda s: s.delete_provider(prov["id"]))

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="bug: service.py:579 `del db.secret_providers[id]` unsupported by penguin-dal TableProxy",
    )
    async def test_delete_unused_provider(self, app, world):
        """An unused provider is deleted and no longer retrievable."""
        prov = await _provider(app, world)
        assert await _svc(app, lambda s: s.delete_provider(prov["id"])) is True
        with pytest.raises(ValueError):
            await _svc(app, lambda s: s.get_provider(prov["id"]))

    @pytest.mark.asyncio
    async def test_test_connection(self, app, world):
        """test_provider_connection reports True/False and swallows lookup errors."""
        good = await _provider(app, world)
        assert await _svc(app, lambda s: s.test_provider_connection(good["id"])) is True

        def _flip():
            app.db(app.db.secret_providers.id == good["id"]).update(
                config_json={"connect": False}
            )
            app.db.commit()

        await run_in_threadpool(_flip)
        assert (
            await _svc(app, lambda s: s.test_provider_connection(good["id"])) is False
        )
        assert (
            await _svc(app, lambda s: s.test_provider_connection(2_000_000_000))
            is False
        )


class TestSecretCrud:
    """Secret registration, retrieval, update, sync, listing and audit log."""

    @pytest.mark.asyncio
    async def test_create_logs_access_and_returns_fields(self, app, world):
        """create_secret persists the row and writes a 'create' audit entry."""
        prov = await _provider(app, world)
        sec = await _secret(app, world, prov, identity_id=world["identity"])
        assert sec["organization_id"] == world["org_a"] and sec["village_id"]
        log = await _svc(app, lambda s: s.get_secret_access_log(sec["id"]))
        assert [e["action"] for e in log] == ["create"]
        assert log[0]["identity_id"] == world["identity"] and log[0]["masked"] is True

    @pytest.mark.asyncio
    async def test_create_without_identity_writes_no_log(self, app, world):
        """Without an identity no audit entry is written."""
        prov = await _provider(app, world)
        sec = await _secret(app, world, prov)
        assert await _svc(app, lambda s: s.get_secret_access_log(sec["id"])) == []

    @pytest.mark.asyncio
    async def test_create_unknown_provider(self, app, world):
        """Creating under a missing provider raises ValueError."""
        with pytest.raises(ValueError, match="Provider .* not found"):
            await _svc(
                app,
                lambda s: s.create_secret(
                    "n", 2_000_000_000, "/p", "password", world["org_a"]
                ),
            )

    @pytest.mark.asyncio
    async def test_create_path_missing_in_provider(self, app, world):
        """A path absent in the provider is a ValueError and nothing is stored."""
        prov = await _provider(app, world)

        def _miss():
            app.db(app.db.secret_providers.id == prov["id"]).update(
                config_json={"mode": "missing"}
            )
            app.db.commit()

        await run_in_threadpool(_miss)
        path = f"/gone/{world['sfx']}"
        with pytest.raises(ValueError, match="not found in provider"):
            await _svc(
                app,
                lambda s: s.create_secret(
                    "n", prov["id"], path, "password", world["org_a"]
                ),
            )

        def _count():
            return app.db(app.db.secrets.provider_path == path).count()

        assert await run_in_threadpool(_count) == 0

    @pytest.mark.asyncio
    async def test_get_masked_and_unmasked_logging(self, app, world):
        """get_secret masks by default; unmask returns the value; both are audited."""
        prov = await _provider(app, world)
        sec = await _secret(app, world, prov)
        masked = await _svc(
            app, lambda s: s.get_secret(sec["id"], identity_id=world["identity"])
        )
        assert masked["value"] == "***MASKED***" and masked["is_masked"] is True
        clear = await _svc(
            app,
            lambda s: s.get_secret(
                sec["id"], unmask=True, identity_id=world["identity"]
            ),
        )
        assert clear["value"] == "s3cret-val" and clear["version"] == "v1"
        log = await _svc(app, lambda s: s.get_secret_access_log(sec["id"], limit=10))
        assert {e["action"] for e in log} == {"view_masked", "view_unmasked"}

    @pytest.mark.asyncio
    async def test_get_kv_secret(self, app, world):
        """A KV secret returns kv_pairs (masked values) and no scalar value."""
        prov = await _provider(app, world, mode="kv")
        sec = await _secret(app, world, prov)
        got = await _svc(app, lambda s: s.get_secret(sec["id"]))
        assert got["value"] is None
        assert got["kv_pairs"] == {"a": "***MASKED***", "b": "***MASKED***"}

    @pytest.mark.asyncio
    async def test_get_missing_and_provider_error(self, app, world):
        """Unknown ids raise ValueError; provider failures propagate."""
        with pytest.raises(ValueError, match="not found"):
            await _svc(app, lambda s: s.get_secret(2_000_000_000))
        prov = await _provider(app, world)
        sec = await _secret(app, world, prov)

        def _boom():
            app.db(app.db.secret_providers.id == prov["id"]).update(
                config_json={"mode": "boom"}
            )
            app.db.commit()

        await run_in_threadpool(_boom)
        with pytest.raises(SecretProviderException, match="provider down"):
            await _svc(app, lambda s: s.get_secret(sec["id"]))

    @pytest.mark.asyncio
    async def test_list_filters(self, app, world):
        """list_secrets filters by organization, provider and type."""
        prov = await _provider(app, world)
        sec = await _secret(app, world, prov)
        by_org = await _svc(
            app, lambda s: s.list_secrets(organization_id=world["org_a"])
        )
        assert sec["id"] in [r["id"] for r in by_org]
        by_prov = await _svc(app, lambda s: s.list_secrets(provider_id=prov["id"]))
        assert [r["id"] for r in by_prov] == [sec["id"]]
        by_type = await _svc(
            app,
            lambda s: s.list_secrets(
                organization_id=world["org_a"], secret_type="certificate"
            ),
        )
        assert sec["id"] not in [r["id"] for r in by_type]
        other_org = await _svc(
            app, lambda s: s.list_secrets(organization_id=world["org_b"])
        )
        assert sec["id"] not in [r["id"] for r in other_org]

    @pytest.mark.asyncio
    async def test_update_metadata(self, app, world):
        """update_secret_metadata persists changes and writes an 'update' audit entry."""
        prov = await _provider(app, world)
        sec = await _secret(app, world, prov)
        upd = await _svc(
            app,
            lambda s: s.update_secret_metadata(
                sec["id"],
                name=f"new-{world['sfx']}",
                secret_type="api_key",
                metadata={"k": "v"},
                identity_id=world["identity"],
            ),
        )
        assert upd["name"] == f"new-{world['sfx']}" and upd["secret_type"] == "api_key"
        log = await _svc(app, lambda s: s.get_secret_access_log(sec["id"]))
        assert "update" in [e["action"] for e in log]

    @pytest.mark.asyncio
    async def test_update_without_identity_writes_no_audit(self, app, world):
        """Updating without an identity changes the row but writes no audit entry."""
        prov = await _provider(app, world)
        sec = await _secret(app, world, prov)
        upd = await _svc(
            app, lambda s: s.update_secret_metadata(sec["id"], name="solo")
        )
        assert upd["name"] == "solo"
        assert await _svc(app, lambda s: s.get_secret_access_log(sec["id"])) == []

    @pytest.mark.asyncio
    async def test_update_noop_and_missing(self, app, world):
        """No fields -> unchanged secret; unknown id -> ValueError."""
        prov = await _provider(app, world)
        sec = await _secret(app, world, prov)
        same = await _svc(app, lambda s: s.update_secret_metadata(sec["id"]))
        assert same["name"] == sec["name"]
        with pytest.raises(ValueError, match="not found"):
            await _svc(app, lambda s: s.update_secret_metadata(2_000_000_000, name="x"))

    @pytest.mark.asyncio
    async def test_delete_missing(self, app):
        """delete_secret on an unknown id raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            await _svc(app, lambda s: s.delete_secret(2_000_000_000))

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="bug: service.py:352 `del db.secrets[id]` unsupported by penguin-dal TableProxy",
    )
    async def test_delete_secret(self, app, world):
        """delete_secret removes the registration."""
        prov = await _provider(app, world)
        sec = await _secret(app, world, prov)
        assert await _svc(app, lambda s: s.delete_secret(sec["id"], world["identity"]))
        with pytest.raises(ValueError):
            await _svc(app, lambda s: s.get_secret(sec["id"]))

    @pytest.mark.asyncio
    async def test_sync_secret(self, app, world):
        """sync_secret refreshes is_kv from the provider and audits the view."""
        prov = await _provider(app, world, mode="kv")
        sec = await _secret(app, world, prov)
        assert sec["is_kv"] is False
        synced = await _svc(
            app, lambda s: s.sync_secret(sec["id"], identity_id=world["identity"])
        )
        assert synced["is_kv"] is True

    @pytest.mark.asyncio
    async def test_sync_missing_and_provider_error(self, app, world):
        """sync_secret raises for unknown ids and propagates provider errors."""
        with pytest.raises(ValueError, match="not found"):
            await _svc(app, lambda s: s.sync_secret(2_000_000_000))
        prov = await _provider(app, world)
        sec = await _secret(app, world, prov)

        def _boom():
            app.db(app.db.secret_providers.id == prov["id"]).update(
                config_json={"mode": "boom"}
            )
            app.db.commit()

        await run_in_threadpool(_boom)
        with pytest.raises(SecretProviderException):
            await _svc(app, lambda s: s.sync_secret(sec["id"]))

    @pytest.mark.asyncio
    async def test_access_log_limit_and_order(self, app, world):
        """The access log is newest-first and honours the limit."""
        prov = await _provider(app, world)
        sec = await _secret(app, world, prov)
        for _ in range(3):
            await _svc(
                app, lambda s: s.get_secret(sec["id"], identity_id=world["identity"])
            )
        log = await _svc(app, lambda s: s.get_secret_access_log(sec["id"], limit=2))
        assert len(log) == 2
        assert log[0]["accessed_at"] >= log[1]["accessed_at"]

    @pytest.mark.asyncio
    async def test_log_failure_does_not_break_operation(self, app, world):
        """An audit-log failure (bad identity FK) never fails the read."""
        prov = await _provider(app, world)
        sec = await _secret(app, world, prov)
        got = await _svc(
            app, lambda s: s.get_secret(sec["id"], identity_id=2_000_000_000)
        )
        assert got["id"] == sec["id"]


class TestSyncProvider:
    """SecretsService.sync_provider."""

    @pytest.mark.asyncio
    async def test_sync_counts_only_registered(self, app, world):
        """Only provider secrets already registered locally are counted as synced."""
        known = f"/known/{world['sfx']}"
        prov = await _provider(app, world, paths=[known, "/unregistered"])
        await _secret(app, world, prov, path=known)
        res = await _svc(app, lambda s: s.sync_provider(prov["id"]))
        assert res["secrets_synced"] == 1 and res["total_provider_secrets"] == 2
        got = await _svc(app, lambda s: s.get_provider(prov["id"]))
        assert got["last_sync_at"] is not None

    @pytest.mark.asyncio
    async def test_sync_missing_provider(self, app):
        """sync_provider on an unknown id raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            await _svc(app, lambda s: s.sync_provider(2_000_000_000))

    @pytest.mark.asyncio
    async def test_sync_disabled_provider_propagates(self, app, world):
        """A disabled provider makes sync_provider raise (client lookup fails)."""
        prov = await _provider(app, world)
        await _svc(app, lambda s: s.update_provider(prov["id"], enabled=False))
        with pytest.raises(ValueError, match="disabled"):
            await _svc(app, lambda s: s.sync_provider(prov["id"]))
