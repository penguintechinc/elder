"""Coverage tests for ``KeysService`` (apps/api/services/keys/service.py).

Reads, updates, deletes and state changes run against the harness' real
Postgres (rows seeded with the real ``key_providers`` / ``crypto_keys`` /
``key_access_log`` columns). Provider SDK clients are patched at the
``_get_provider_client`` boundary. Writes that the current service cannot
perform against the real schema (``provider_type``/``description``/``key_hash``
columns) are documented with strict xfail tests stating the correct behaviour.
"""

# flake8: noqa: E501

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from apps.api.services.keys import KeysService
from apps.api.utils.async_utils import run_in_threadpool

SVC = "apps.api.services.keys.service"


def _vid():
    """Return a unique 25-char lowercase hex village id for seeded rows."""
    return uuid.uuid4().hex[:25]


@pytest_asyncio.fixture
async def world(app):
    """Seed one tenant/org with two providers and several keys (real DB)."""
    db = app.db
    sfx = uuid.uuid4().hex[:8]

    def _setup():
        now = datetime.now(UTC)
        tid = db.tenants.insert(name=f"ks-{sfx}", slug=f"ks-{sfx}", is_active=True)
        org = db.organizations.insert(
            name=f"ks-org-{sfx}", tenant_id=tid, created_at=now, updated_at=now
        )
        secret = "-".join(["fake", "secret", uuid.uuid4().hex[:6]])
        p1 = db.key_providers.insert(
            name=f"a-prov-{sfx}",
            provider="aws_kms",
            config_json={"region": "us-east-1", "secret_access_key": secret},
            organization_id=org,
            enabled=True,
            village_id=_vid(),
            created_at=now,
            updated_at=now,
        )
        p2 = db.key_providers.insert(
            name=f"b-prov-{sfx}",
            provider="infisical",
            config_json={"host": "http://x"},
            organization_id=org,
            enabled=False,
            village_id=_vid(),
            created_at=now,
            updated_at=now,
        )

        def _key(name, provider, key_type, state, age):
            return db.crypto_keys.insert(
                name=f"{name}-{sfx}",
                key_provider_id=provider,
                provider_key_id=f"pk-{name}-{sfx}",
                provider_key_arn=f"arn:{name}",
                key_hash="h",
                key_type=key_type,
                key_state=state,
                organization_id=org,
                village_id=_vid(),
                created_at=now - timedelta(minutes=age),
                updated_at=now,
            )

        k_sym = _key("sym", p1, "symmetric", "Enabled", 3)
        k_asym = _key("asym", p1, "asymmetric", "Enabled", 2)
        k_off = _key("off", p2, "symmetric", "Disabled", 1)
        db.commit()
        return {
            "tenant": tid,
            "org": org,
            "p1": p1,
            "p2": p2,
            "k_sym": k_sym,
            "k_asym": k_asym,
            "k_off": k_off,
            "secret": secret,
        }

    w = await run_in_threadpool(_setup)
    w["sfx"] = sfx
    w["svc"] = KeysService(db)
    return w


async def _run(fn, *a, **kw):
    """Run a sync service call in the threadpool (thread-local DAL)."""
    return await run_in_threadpool(lambda: fn(*a, **kw))


@pytest.fixture
def provider_client():
    """Patch _get_provider_client and _log_access; yield (client, log mock)."""
    client = MagicMock(name="provider_client")
    with (
        patch.object(KeysService, "_get_provider_client", return_value=client),
        patch.object(KeysService, "_log_access") as log,
    ):
        yield client, log


class _Table(dict):
    """Dict standing in for a PyDAL table: missing ids give None."""

    def __missing__(self, key):
        """Mimic PyDAL: unknown ids return None."""
        return None


def _stub_provider(provider_type, config_json=None, **extra):
    """Build a provider row lacking a DB (has ``provider_type`` like the service expects)."""
    data = {
        "id": 1,
        "provider_type": provider_type,
        "config_json": config_json,
        **extra,
    }
    row = SimpleNamespace(**data)
    row.as_dict = lambda: {"id": 1, "name": "n", "config_json": config_json}
    return row


class TestGetProviderClient:
    """_get_provider_client dispatch (stub table; SDK clients patched)."""

    @pytest.mark.parametrize(
        ("ptype", "cls"),
        [
            ("aws_kms", "AWSKMSClient"),
            ("GCP_KMS", "GCPKMSClient"),
            ("infisical", "InfisicalClient"),
        ],
    )
    def test_dispatch_by_type(self, ptype, cls):
        """The provider type picks the right client class and merges config_json."""
        db = SimpleNamespace(
            key_providers=_Table({1: _stub_provider(ptype, {"region": "r1"})})
        )
        with patch(f"{SVC}.{cls}") as client_cls:
            out = KeysService(db)._get_provider_client(1)
        assert out is client_cls.return_value
        cfg = client_cls.call_args.args[0]
        assert cfg["provider_type"] == ptype
        assert cfg["region"] == "r1"

    def test_no_config_json(self):
        """A provider without config_json still builds a client."""
        db = SimpleNamespace(key_providers=_Table({1: _stub_provider("infisical")}))
        with patch(f"{SVC}.InfisicalClient") as client_cls:
            KeysService(db)._get_provider_client(1)
        assert "region" not in client_cls.call_args.args[0]

    def test_unsupported_type(self):
        """An unknown type raises."""
        db = SimpleNamespace(key_providers=_Table({1: _stub_provider("vault")}))
        with pytest.raises(Exception, match="Unsupported provider type: vault"):
            KeysService(db)._get_provider_client(1)

    def test_missing_provider(self):
        """A missing provider row raises 'not found'."""
        db = SimpleNamespace(key_providers=_Table())
        with pytest.raises(Exception, match="Key provider not found: 9"):
            KeysService(db)._get_provider_client(9)

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="bug: service.py:45 reads provider.provider_type but key_providers column is `provider`",
    )
    async def test_real_row_dispatches_by_provider_column(self, world):
        """A real DB provider row (column ``provider``) resolves to its client."""
        with patch(f"{SVC}.AWSKMSClient") as client_cls:
            out = await _run(world["svc"]._get_provider_client, world["p1"])
        assert out is client_cls.return_value


class TestProviderReads:
    """list_providers / get_provider on the real DB."""

    @pytest.mark.asyncio
    async def test_list_sorted_and_redacted(self, world):
        """Providers are name-sorted and secret config fields are redacted."""
        rows = await _run(world["svc"].list_providers)
        mine = [r for r in rows if r["id"] in (world["p1"], world["p2"])]
        assert [r["id"] for r in mine] == [world["p1"], world["p2"]]
        assert mine[0]["config_json"]["secret_access_key"] == "***REDACTED***"
        assert mine[0]["config_json"]["region"] == "us-east-1"
        assert world["secret"] not in str(mine)

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="bug: service.py:78 `enabled is True` evaluates to a bool so `query &= bool` raises AttributeError (enabled_only broken)",
    )
    async def test_list_enabled_only_excludes_disabled(self, world):
        """enabled_only must return only enabled providers."""
        rows = await _run(world["svc"].list_providers, enabled_only=True)
        ids = {r["id"] for r in rows}
        assert world["p1"] in ids
        assert world["p2"] not in ids

    @pytest.mark.asyncio
    async def test_get_provider_and_missing(self, world):
        """get_provider returns a redacted dict; unknown ids raise."""
        got = await _run(world["svc"].get_provider, world["p1"])
        assert got["id"] == world["p1"]
        assert got["config_json"]["secret_access_key"] == "***REDACTED***"
        with pytest.raises(Exception, match="Provider not found: 99999999"):
            await _run(world["svc"].get_provider, 99999999)


class TestProviderWrites:
    """update/delete/test provider and create-provider validation."""

    @pytest.mark.asyncio
    async def test_update_fields(self, world):
        """name, config and enabled are persisted; untouched fields stay."""
        out = await _run(
            world["svc"].update_provider,
            world["p2"],
            name=f"renamed-{world['sfx']}",
            config={"host": "http://y", "token": "t"},
            enabled=True,
        )
        assert out["name"] == f"renamed-{world['sfx']}"
        assert out["enabled"] is True
        assert out["config_json"]["host"] == "http://y"
        assert out["config_json"]["token"] == "***REDACTED***"

    @pytest.mark.asyncio
    async def test_update_without_changes_is_noop(self, world):
        """With no fields given the provider is returned unchanged."""
        before = await _run(world["svc"].get_provider, world["p1"])
        after = await _run(world["svc"].update_provider, world["p1"])
        assert before == after

    @pytest.mark.asyncio
    async def test_update_missing_provider(self, world):
        """Updating an unknown provider raises."""
        with pytest.raises(Exception, match="Provider not found"):
            await _run(world["svc"].update_provider, 99999999, name="x")

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="bug: service.py:180 writes `description`, but key_providers has no such column",
    )
    async def test_update_description_persists(self, world):
        """Updating the description must persist (column missing in schema)."""
        out = await _run(
            world["svc"].update_provider, world["p1"], description="new desc"
        )
        assert out["description"] == "new desc"

    @pytest.mark.asyncio
    async def test_delete_provider_blocked_by_keys(self, world):
        """A provider with registered keys cannot be deleted and is kept."""
        with pytest.raises(
            Exception, match="Cannot delete provider with 2 registered keys"
        ):
            await _run(world["svc"].delete_provider, world["p1"])
        assert (await _run(world["svc"].get_provider, world["p1"]))["id"] == world["p1"]

    @pytest.mark.asyncio
    async def test_delete_provider_without_keys(self, world, app):
        """A provider with no keys is deleted from the DB."""
        db = app.db

        def _mk():
            now = datetime.now(UTC)
            pid = db.key_providers.insert(
                name=f"del-{world['sfx']}",
                provider="infisical",
                config_json={},
                organization_id=world["org"],
                enabled=True,
                village_id=_vid(),
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return pid

        pid = await run_in_threadpool(_mk)
        out = await _run(world["svc"].delete_provider, pid)
        assert out == {"message": "Provider deleted successfully"}
        with pytest.raises(Exception, match="Provider not found"):
            await _run(world["svc"].get_provider, pid)

    @pytest.mark.asyncio
    async def test_delete_missing_provider(self, world):
        """Deleting an unknown provider raises."""
        with pytest.raises(Exception, match="Provider not found"):
            await _run(world["svc"].delete_provider, 99999999)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("connected", [True, False])
    async def test_test_provider_reports_connection(self, world, connected):
        """test_provider returns the client's connectivity result."""
        client = MagicMock()
        client.test_connection.return_value = connected
        with patch.object(KeysService, "_get_provider_client", return_value=client):
            out = await _run(world["svc"].test_provider, world["p1"])
        assert out["success"] is connected
        assert out["provider_id"] == world["p1"]
        assert "error" not in out

    @pytest.mark.asyncio
    async def test_test_provider_captures_errors(self, world):
        """Client construction/connection errors are returned, not raised."""
        with patch.object(
            KeysService, "_get_provider_client", side_effect=Exception("no client")
        ):
            out = await _run(world["svc"].test_provider, world["p1"])
        assert out["success"] is False
        assert out["error"] == "no client"

    def test_create_provider_rejects_bad_type(self):
        """An unsupported provider type is rejected before touching the DB."""
        db = MagicMock()
        with pytest.raises(Exception, match="Invalid provider type: vault"):
            KeysService(db).create_provider("n", "vault", {})
        db.key_providers.insert.assert_not_called()

    def test_create_provider_inserts_normalised_row(self):
        """The type is lower-cased and the row inserted then read back."""
        db = MagicMock()
        db.key_providers.insert.return_value = 5
        service = KeysService(db)
        with (
            patch(f"{SVC}.KeysService.get_provider", return_value={"id": 5}),
            patch("apps.api.utils.village_id.mint_village_id", return_value="v" * 25),
        ):
            out = service.create_provider("n", "AWS_KMS", {"region": "r"}, "d")
        kwargs = db.key_providers.insert.call_args.kwargs
        assert kwargs["provider_type"] == "aws_kms"
        assert kwargs["config_json"] == {"region": "r"}
        assert kwargs["enabled"] is True
        db.commit.assert_called_once()
        assert out == {"id": 5}

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="bug: service.py:134 inserts provider_type/description and no organization_id; key_providers has `provider`, no description, org NOT NULL",
    )
    async def test_create_provider_persists_on_real_db(self, world):
        """create_provider must create a readable provider row on the real schema."""
        out = await _run(
            world["svc"].create_provider,
            f"new-{world['sfx']}",
            "infisical",
            {"host": "h"},
        )
        assert out["name"] == f"new-{world['sfx']}"


class TestKeyReads:
    """get_key / list_keys on the real DB."""

    @pytest.mark.asyncio
    async def test_get_key_includes_provider_name(self, world):
        """get_key returns the row plus its provider's name."""
        out = await _run(world["svc"].get_key, world["k_sym"])
        assert out["id"] == world["k_sym"]
        assert out["provider_name"] == f"a-prov-{world['sfx']}"

    @pytest.mark.asyncio
    async def test_get_key_missing(self, world):
        """Unknown key ids raise 'Key not found'."""
        with pytest.raises(Exception, match="Key not found: 99999999"):
            await _run(world["svc"].get_key, 99999999)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({}, ["k_sym", "k_asym", "k_off"]),
            ({"provider_id": "p1"}, ["k_sym", "k_asym"]),
            ({"key_type": "asymmetric"}, ["k_asym"]),
            ({"enabled_only": True}, ["k_sym", "k_asym"]),
            ({"provider_id": "p2", "key_type": "symmetric"}, ["k_off"]),
            ({"provider_id": "p2", "enabled_only": True}, []),
        ],
    )
    async def test_list_filters(self, world, kwargs, expected):
        """Provider, type and enabled filters narrow results, ordered by creation."""
        kwargs = {
            k: (world[v] if isinstance(v, str) and v in world else v)
            for k, v in kwargs.items()
        }
        rows = await _run(world["svc"].list_keys, **kwargs)
        mine = {world[n]: n for n in ("k_sym", "k_asym", "k_off")}
        got = [mine[r["id"]] for r in rows if r["id"] in mine]
        assert got == expected
        assert all("provider_name" in r for r in rows)


class TestKeyStateChanges:
    """enable/disable/delete a key: provider call plus DB state update."""

    @pytest.mark.asyncio
    async def test_enable_updates_state(self, world, provider_client):
        """enable_key calls the provider and persists the returned state."""
        client, _ = provider_client
        client.enable_key.return_value = {"state": "Enabled"}
        out = await _run(world["svc"].enable_key, world["k_off"])
        client.enable_key.assert_called_once_with(f"pk-off-{world['sfx']}")
        assert out["key_state"] == "Enabled"

    @pytest.mark.asyncio
    async def test_disable_updates_state(self, world, provider_client):
        """disable_key persists a Disabled state (default when provider omits it)."""
        client, _ = provider_client
        client.disable_key.return_value = {}
        out = await _run(world["svc"].disable_key, world["k_sym"])
        assert out["key_state"] == "Disabled"

    @pytest.mark.asyncio
    async def test_delete_schedules_and_logs(self, world, provider_client):
        """delete_key schedules deletion, stores PendingDeletion and audits it."""
        client, log = provider_client
        client.schedule_key_deletion.return_value = {"state": "PendingDeletion"}
        out = await _run(world["svc"].delete_key, world["k_asym"], 14)
        client.schedule_key_deletion.assert_called_once_with(
            f"pk-asym-{world['sfx']}", 14
        )
        log.assert_called_once_with(
            world["k_asym"], "schedule_deletion", pending_days=14
        )
        assert out == {"state": "PendingDeletion"}
        assert (await _run(world["svc"].get_key, world["k_asym"]))[
            "key_state"
        ] == "PendingDeletion"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("op", ["enable_key", "disable_key", "delete_key"])
    async def test_missing_key(self, world, provider_client, op):
        """State changes on an unknown key raise before any provider call."""
        client, _ = provider_client
        with pytest.raises(Exception, match="Key not found"):
            await _run(getattr(world["svc"], op), 99999999)
        assert not client.method_calls

    @pytest.mark.asyncio
    async def test_provider_failure_propagates_and_keeps_state(
        self, world, provider_client
    ):
        """A provider error aborts the change and leaves DB state untouched."""
        client, _ = provider_client
        client.disable_key.side_effect = Exception("kms down")
        with pytest.raises(Exception, match="kms down"):
            await _run(world["svc"].disable_key, world["k_sym"])
        assert (await _run(world["svc"].get_key, world["k_sym"]))[
            "key_state"
        ] == "Enabled"


class TestCryptoOperations:
    """encrypt/decrypt/generate_data_key/sign/verify/rotate guards and delegation."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("op", "args", "client_op", "client_args", "log_op", "log_kwargs"),
        [
            (
                "encrypt",
                ("hello", {"c": "1"}),
                "encrypt",
                ("hello", {"c": "1"}),
                "encrypt",
                {"plaintext_length": 5},
            ),
            ("decrypt", ("ct", None), "decrypt", ("ct", None), "decrypt", {}),
            (
                "generate_data_key",
                ("AES_128", None),
                "generate_data_key",
                ("AES_128", None),
                "generate_data_key",
                {"key_spec": "AES_128"},
            ),
            ("rotate_key", (), "rotate_key", (), "rotate", {}),
        ],
    )
    async def test_delegation(
        self,
        world,
        provider_client,
        op,
        args,
        client_op,
        client_args,
        log_op,
        log_kwargs,
    ):
        """Each operation delegates with the provider key id and writes an audit entry."""
        client, log = provider_client
        getattr(client, client_op).return_value = {"ok": True}
        out = await _run(getattr(world["svc"], op), world["k_sym"], *args)
        assert out == {"ok": True}
        expected = client_args
        if client_op != "decrypt" and client_op != "rotate_key":
            expected = (f"pk-sym-{world['sfx']}", *client_args)
        elif client_op == "rotate_key":
            expected = (f"pk-sym-{world['sfx']}",)
        getattr(client, client_op).assert_called_once_with(*expected)
        log.assert_called_once_with(world["k_sym"], log_op, **log_kwargs)

    @pytest.mark.asyncio
    async def test_sign_and_verify_asymmetric(self, world, provider_client):
        """Sign/verify delegate for asymmetric keys with the algorithm audited."""
        client, log = provider_client
        client.sign.return_value = {"signature": "s"}
        client.verify.return_value = {"valid": True}
        assert await _run(world["svc"].sign, world["k_asym"], "m", "ALG") == {
            "signature": "s"
        }
        client.sign.assert_called_once_with(f"pk-asym-{world['sfx']}", "m", "ALG")
        assert await _run(world["svc"].verify, world["k_asym"], "m", "s", "ALG") == {
            "valid": True
        }
        client.verify.assert_called_once_with(
            f"pk-asym-{world['sfx']}", "m", "s", "ALG"
        )
        assert [c.args[1] for c in log.call_args_list] == ["sign", "verify"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("op", "args"),
        [
            ("encrypt", ("p",)),
            ("decrypt", ("c",)),
            ("generate_data_key", ()),
            ("sign", ("m",)),
            ("verify", ("m", "s", "A")),
            ("rotate_key", ()),
        ],
    )
    async def test_missing_key(self, world, provider_client, op, args):
        """Every crypto operation rejects unknown key ids without a provider call."""
        client, log = provider_client
        with pytest.raises(Exception, match="Key not found"):
            await _run(getattr(world["svc"], op), 99999999, *args)
        assert not client.method_calls
        log.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("op", "args"),
        [
            ("encrypt", ("p",)),
            ("decrypt", ("c",)),
            ("generate_data_key", ()),
        ],
    )
    async def test_disabled_key_refused(self, world, provider_client, op, args):
        """Disabled keys cannot be used for encrypt/decrypt/data-key generation."""
        client, log = provider_client
        with pytest.raises(Exception, match=r"Key is not enabled \(state: Disabled\)"):
            await _run(getattr(world["svc"], op), world["k_off"], *args)
        assert not client.method_calls
        log.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("op", "args", "label"),
        [("sign", ("m",), "signing"), ("verify", ("m", "s", "A"), "verification")],
    )
    async def test_symmetric_key_cannot_sign_or_verify(
        self, world, provider_client, op, args, label
    ):
        """Only asymmetric keys can sign or verify."""
        client, _ = provider_client
        with pytest.raises(Exception, match=f"must be asymmetric for {label}"):
            await _run(getattr(world["svc"], op), world["k_sym"], *args)
        assert not client.method_calls

    @pytest.mark.asyncio
    async def test_sign_disabled_asymmetric_key_refused(
        self, world, provider_client, app
    ):
        """A disabled asymmetric key cannot sign."""
        db = app.db
        client, _ = provider_client

        def _disable():
            db(db.crypto_keys.id == world["k_asym"]).update(key_state="Disabled")
            db.commit()

        await run_in_threadpool(_disable)
        with pytest.raises(Exception, match="Key is not enabled"):
            await _run(world["svc"].sign, world["k_asym"], "m")
        assert not client.method_calls

    @pytest.mark.asyncio
    async def test_provider_error_skips_audit(self, world, provider_client):
        """A provider failure propagates and nothing is audit-logged."""
        client, log = provider_client
        client.encrypt.side_effect = Exception("throttled")
        with pytest.raises(Exception, match="throttled"):
            await _run(world["svc"].encrypt, world["k_sym"], "p")
        log.assert_not_called()


class TestCreateKey:
    """create_key registers the provider-created key (stub DB for the insert)."""

    def _db(self, org_id=None):
        """Build a MagicMock db whose provider row has the given org id."""
        db = MagicMock()
        db.key_providers.__getitem__.return_value = SimpleNamespace(
            organization_id=org_id
        )
        db.crypto_keys.insert.return_value = 77
        return db

    @pytest.mark.parametrize("org_id", [None, 12])
    def test_registers_key(self, org_id):
        """Provider key details are stored and the DB key re-read."""
        db = self._db(org_id)
        service = KeysService(db)
        client = MagicMock()
        client.create_key.return_value = {
            "key_id": "pk1",
            "key_arn": "arn:1",
            "state": "Enabled",
            "algorithm": "AES",
        }
        with (
            patch.object(KeysService, "_get_provider_client", return_value=client),
            patch(
                "apps.api.utils.village_id.resolve_tenant_id_for_org", return_value=3
            ) as res,
            patch(
                "apps.api.utils.village_id.mint_village_id", return_value="v" * 25
            ) as mint,
            patch.object(KeysService, "get_key", return_value={"id": 77}) as gk,
        ):
            out = service.create_key(
                4, "kname", "symmetric", "SPEC", "desc", {"t": "v"}
            )
        client.create_key.assert_called_once_with(
            "kname", "symmetric", "SPEC", "desc", {"t": "v"}
        )
        res.assert_called_once_with(db, org_id)
        mint.assert_called_once_with(3)
        kwargs = db.crypto_keys.insert.call_args.kwargs
        assert kwargs["provider_key_id"] == "pk1"
        assert kwargs["key_state"] == "Enabled"
        assert kwargs["metadata_json"] == {
            "key_spec": "SPEC",
            "algorithm": "AES",
            "description": "desc",
            "tags": {"t": "v"},
        }
        db.commit.assert_called_once()
        gk.assert_called_once_with(77)
        assert out == {"id": 77}

    def test_provider_without_row(self):
        """A vanished provider row yields no org id but still registers the key."""
        db = MagicMock()
        db.key_providers.__getitem__.return_value = None
        db.crypto_keys.insert.return_value = 1
        client = MagicMock()
        client.create_key.return_value = {"key_id": "pk"}
        with (
            patch.object(KeysService, "_get_provider_client", return_value=client),
            patch(
                "apps.api.utils.village_id.resolve_tenant_id_for_org", return_value=1
            ) as res,
            patch("apps.api.utils.village_id.mint_village_id", return_value="v" * 25),
            patch.object(KeysService, "get_key", return_value={}),
        ):
            KeysService(db).create_key(1, "k")
        res.assert_called_once_with(db, None)
        assert db.crypto_keys.insert.call_args.kwargs["key_state"] == "Enabled"

    def test_provider_failure_inserts_nothing(self):
        """If the provider fails to create the key nothing is registered."""
        db = MagicMock()
        client = MagicMock()
        client.create_key.side_effect = Exception("quota")
        with (
            patch.object(KeysService, "_get_provider_client", return_value=client),
            pytest.raises(Exception, match="quota"),
        ):
            KeysService(db).create_key(1, "k")
        db.crypto_keys.insert.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="bug: service.py:288 omits crypto_keys.key_hash/organization_id (NOT NULL), so the insert fails on the real schema",
    )
    async def test_create_key_persists_on_real_db(self, world):
        """create_key must register a key row on the real schema."""
        client = MagicMock()
        client.create_key.return_value = {"key_id": f"pk-new-{world['sfx']}"}
        with patch.object(KeysService, "_get_provider_client", return_value=client):
            out = await _run(
                world["svc"].create_key, world["p1"], f"new-{world['sfx']}"
            )
        assert out["provider_key_id"] == f"pk-new-{world['sfx']}"


class TestAccessLog:
    """_log_access / get_access_log."""

    @pytest.mark.asyncio
    async def test_get_access_log_orders_and_paginates(self, world, app):
        """Logs for a key come back newest first with limit/offset applied."""
        db = app.db

        def _seed():
            now = datetime.now(UTC)
            for i in range(3):
                db.key_access_log.insert(
                    key_id=world["k_sym"],
                    operation=f"op{i}",
                    accessed_at=now - timedelta(minutes=10 - i),
                    metadata_json={"i": i},
                )
            db.key_access_log.insert(
                key_id=world["k_asym"], operation="other", accessed_at=now
            )
            db.commit()

        await run_in_threadpool(_seed)
        all_logs = await _run(world["svc"].get_access_log, world["k_sym"])
        assert [r["operation"] for r in all_logs] == ["op2", "op1", "op0"]
        first = await _run(world["svc"].get_access_log, world["k_sym"], limit=2)
        assert [r["operation"] for r in first] == ["op2", "op1"]
        assert await _run(world["svc"].get_access_log, 99999999) == []

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="bug: service.py:594 limitby=(offset, offset+limit) returns limit+offset rows under penguin-dal (limit=1, offset=1 gives 2)",
    )
    async def test_get_access_log_offset_window(self, world, app):
        """limit=1/offset=1 must return exactly the second-newest entry."""
        db = app.db

        def _seed():
            now = datetime.now(UTC)
            for i in range(3):
                db.key_access_log.insert(
                    key_id=world["k_off"],
                    operation=f"op{i}",
                    accessed_at=now - timedelta(minutes=10 - i),
                )
            db.commit()

        await run_in_threadpool(_seed)
        page = await _run(
            world["svc"].get_access_log, world["k_off"], limit=1, offset=1
        )
        assert [r["operation"] for r in page] == ["op1"]

    def test_log_access_records_metadata(self):
        """_log_access inserts operation and kwargs as metadata and commits."""
        db = MagicMock()
        KeysService(db)._log_access(5, "encrypt", plaintext_length=3)
        kwargs = db.key_access_log.insert.call_args.kwargs
        assert kwargs["key_id"] == 5
        assert kwargs["operation"] == "encrypt"
        assert kwargs["metadata_json"] == {"plaintext_length": 3}
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="bug: service.py:568 inserts created_at/updated_at which key_access_log does not have",
    )
    async def test_log_access_persists_on_real_db(self, world):
        """_log_access must write an audit row on the real schema."""
        await _run(world["svc"]._log_access, world["k_sym"], "encrypt", n=1)
        logs = await _run(world["svc"].get_access_log, world["k_sym"])
        assert any(r["operation"] == "encrypt" for r in logs)


class TestSanitize:
    """_sanitize_provider redaction rules."""

    @pytest.mark.parametrize(
        "field",
        ["access_key_id", "secret_access_key", "credentials_json", "token", "api_key"],
    )
    def test_sensitive_fields_redacted(self, field):
        """Each sensitive config field is masked without mutating the input."""
        original = {"config_json": {field: "v", "region": "r"}}
        out = KeysService(None)._sanitize_provider(original)
        assert out["config_json"][field] == "***REDACTED***"
        assert out["config_json"]["region"] == "r"
        assert original["config_json"][field] == "v"

    @pytest.mark.parametrize(
        "provider", [{}, {"config_json": None}, {"config_json": {}}]
    )
    def test_missing_or_empty_config(self, provider):
        """Providers without config are passed through untouched."""
        assert KeysService(None)._sanitize_provider(provider) == provider
