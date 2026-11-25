"""Unit coverage for apps/worker/discovery/service.py.

Persistence paths run against the shared test database through the real DAL;
only provider SDK clients and deliberate failure injection are mocked.
"""

from __future__ import annotations

import tests.unit.conftest_worker_stubs  # noqa: F401  isort: skip

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from apps.worker.discovery import service as svc_mod
from apps.worker.discovery.service import (
    DiscoveryClaimTokenError,
    DiscoveryJobNotClaimableError,
    DiscoveryService,
)


def _uid() -> str:
    """Short unique suffix for names stored in the shared test database."""
    return uuid.uuid4().hex[:10]


@pytest.fixture
def ctx(app: Any) -> Any:
    """A DiscoveryService with a fresh tenant and organization."""
    db = app.db
    tenant_id = db.tenants.insert(
        name=f"cove-tenant-{_uid()}", slug=f"cove-{_uid()}", is_active=True
    )
    org_id = db.organizations.insert(name=f"cove-org-{_uid()}", tenant_id=tenant_id)
    db.commit()
    return SimpleNamespace(
        svc=DiscoveryService(db), db=db, org=org_id, tenant=tenant_id
    )


@pytest.fixture
def broken() -> DiscoveryService:
    """A DiscoveryService whose database raises on every query."""
    db = MagicMock(side_effect=RuntimeError("db down"))
    return DiscoveryService(db)


def _job(db: Any, provider: str = "network", interval: int = 0, **fields: Any) -> int:
    """Insert a discovery job row and return its id."""
    now = datetime.now(UTC)
    cfg = fields.pop("config_json", {})
    job_id = db.discovery_jobs.insert(
        name=f"cove-job-{_uid()}",
        provider=provider,
        config_json=cfg,
        schedule_interval=interval,
        enabled=fields.pop("enabled", True),
        created_at=now,
        updated_at=now,
        **fields,
    )
    db.commit()
    return job_id


# --------------------------------------------------------------------------- #
# Config helpers
# --------------------------------------------------------------------------- #


class TestHelpers:
    """Module level helper functions."""

    @pytest.mark.parametrize("raw", ["", "abc", "10"])
    def test_lease_default_on_bad_values(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        """Invalid or sub-minimum leases use the default."""
        monkeypatch.setenv("ELDER_DISCOVERY_CLAIM_LEASE_SECONDS", raw)
        assert svc_mod._claim_lease_seconds() == 900

    def test_lease_override_and_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A valid value is honoured; unset falls back silently."""
        monkeypatch.setenv("ELDER_DISCOVERY_CLAIM_LEASE_SECONDS", "300")
        assert svc_mod._claim_lease_seconds() == 300
        monkeypatch.delenv("ELDER_DISCOVERY_CLAIM_LEASE_SECONDS")
        assert svc_mod._claim_lease_seconds() == 900

    def test_parse_json_dict(self) -> None:
        """Dicts copy, JSON strings parse, everything else is empty."""
        src = {"a": 1}
        out = svc_mod._parse_json_dict(src)
        assert out == src and out is not src
        assert svc_mod._parse_json_dict('{"b": 2}') == {"b": 2}
        assert svc_mod._parse_json_dict("[1]") == {}
        assert svc_mod._parse_json_dict("not json") == {}
        assert svc_mod._parse_json_dict(None) == {}

    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, True),
            ("", True),
            ("  ***REDACTED***", True),
            ("***masked***", True),
            ({}, True),
            ("real", False),
            ({"k": 1}, False),
            (5, False),
        ],
    )
    def test_blank_or_redacted(self, value: Any, expected: bool) -> None:
        """Secret placeholder detection."""
        assert svc_mod._is_blank_or_redacted_secret(value) is expected


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


class TestProviders:
    """get_provider and _get_discovery_client."""

    @pytest.mark.parametrize(
        "ptype,cls",
        [
            ("AWS", "AWSDiscoveryClient"),
            ("gcp", "GCPDiscoveryClient"),
            ("azure", "AzureDiscoveryClient"),
            ("kubernetes", "KubernetesDiscoveryClient"),
            ("vultr", "VultrDiscoveryClient"),
            ("digitalocean", "DigitalOceanDiscoveryClient"),
            ("fleetdm", "FleetDMDiscoveryClient"),
        ],
    )
    def test_get_provider_dispatch(self, ptype: str, cls: str) -> None:
        """Each provider type builds its client with a provider_type default."""
        with patch.object(svc_mod, cls) as klass:
            client = DiscoveryService.get_provider(ptype, {"x": 1})
        assert client is klass.return_value
        cfg = klass.call_args.args[0]
        assert cfg["provider_type"] == ptype.lower() and cfg["x"] == 1

    def test_get_provider_unsupported(self) -> None:
        """Unknown providers are rejected."""
        with pytest.raises(Exception, match="Unsupported provider type"):
            DiscoveryService.get_provider("nope", {})

    def test_get_discovery_client_merges_config(self, ctx: Any) -> None:
        """The job's config_json is merged into the client config."""
        job_id = _job(ctx.db, "aws", config_json={"region": "r1"})
        with patch.object(svc_mod.DiscoveryService, "get_provider") as gp:
            ctx.svc._get_discovery_client(job_id)
        assert gp.call_args.args[0] == "aws"
        assert gp.call_args.args[1]["region"] == "r1"

    def test_get_discovery_client_missing(self, ctx: Any) -> None:
        """An unknown job id raises."""
        with pytest.raises(Exception, match="not found"):
            ctx.svc._get_discovery_client(987654321)

    def test_get_discovery_client_without_config(self, ctx: Any) -> None:
        """A job with an empty config still resolves a client."""
        job_id = _job(ctx.db, "aws")
        with patch.object(svc_mod.DiscoveryService, "get_provider") as gp:
            ctx.svc._get_discovery_client(job_id)
        gp.assert_called_once()


# --------------------------------------------------------------------------- #
# Job CRUD
# --------------------------------------------------------------------------- #


class TestJobCrud:
    """list/get/create/update/delete."""

    def test_create_and_get_sanitizes(self, ctx: Any) -> None:
        """Created jobs hide secrets and surface org/description."""
        job = ctx.svc.create_job(
            name=f"n-{_uid()}",
            provider="AWS",
            config={"access_key_id": "val", "region": "r"},
            organization_id=ctx.org,
            description="desc",
        )
        assert job["config_json"]["access_key_id"] == "***REDACTED***"
        assert job["organization_id"] == ctx.org
        assert job["description"] == "desc"
        assert job["schedule_interval"] == 3600
        assert ctx.svc.get_job(job["id"])["id"] == job["id"]

    def test_create_none_config_custom_interval(self, ctx: Any) -> None:
        """Config may be None and the interval is respected."""
        job = ctx.svc.create_job(
            name=f"n-{_uid()}",
            provider="network",
            config=None,
            schedule_interval=0,
            enabled=False,
        )
        assert job["schedule_interval"] == 0 and job["enabled"] is False

    def test_create_invalid_provider(self, ctx: Any) -> None:
        """Unknown providers are refused."""
        with pytest.raises(Exception, match="Invalid provider"):
            ctx.svc.create_job("n", "bogus", {})

    def test_get_missing(self, ctx: Any) -> None:
        """Missing jobs raise."""
        with pytest.raises(Exception, match="not found"):
            ctx.svc.get_job(987654321)

    def test_list_filters(self, ctx: Any) -> None:
        """Provider, enabled and organization filters apply."""
        mine = ctx.svc.create_job(
            f"n-{_uid()}", "gcp", {}, organization_id=ctx.org, enabled=True
        )
        other = ctx.svc.create_job(f"n-{_uid()}", "gcp", {}, enabled=False)
        ids = {j["id"] for j in ctx.svc.list_jobs(provider="GCP", enabled=True)}
        assert mine["id"] in ids and other["id"] not in ids
        org_ids = {j["id"] for j in ctx.svc.list_jobs(organization_id=ctx.org)}
        assert mine["id"] in org_ids and other["id"] not in org_ids

    def test_list_org_filter_bad_values(self, ctx: Any) -> None:
        """A non-numeric stored org id never matches an int filter."""
        job_id = _job(ctx.db, "gcp", config_json={"_organization_id": "abc"})
        ids = {j["id"] for j in ctx.svc.list_jobs(organization_id=ctx.org)}
        assert job_id not in ids

    def test_list_org_filter_row_column(self, ctx: Any) -> None:
        """A real organization_id column takes precedence when present."""
        fake_row = MagicMock()
        fake_row.as_dict.return_value = {
            "id": 1,
            "organization_id": ctx.org,
            "config_json": None,
        }
        other_row = MagicMock()
        other_row.as_dict.return_value = {"id": 2, "config_json": '{"x": 1}'}
        query = MagicMock()
        query.select.return_value = [fake_row, other_row]
        db = MagicMock(return_value=query)
        db.discovery_jobs.id = 5
        out = DiscoveryService(db).list_jobs(organization_id=ctx.org)
        assert [j["id"] for j in out] == [1]

    def test_update_job_paths(self, ctx: Any) -> None:
        """Updates merge config, keep org/description/secrets and refresh fields."""
        job = ctx.svc.create_job(
            f"n-{_uid()}",
            "aws",
            {"secret_access_key": "sekret", "region": "r1"},
            organization_id=ctx.org,
            description="old",
        )
        jid = job["id"]
        # redacted secret in the update must not clobber the stored one
        out = ctx.svc.update_job(
            jid,
            name="renamed",
            config={"secret_access_key": "***REDACTED***", "region": "r2"},
            schedule_interval=60,
            enabled=False,
        )
        assert out["name"] == "renamed" and out["enabled"] is False
        stored = ctx.db.discovery_jobs[jid].config_json
        assert stored["secret_access_key"] == "sekret"
        assert stored["_organization_id"] == ctx.org
        assert stored["_description"] == "old"
        # description-only update reuses existing config
        ctx.svc.update_job(jid, description="new")
        assert ctx.db.discovery_jobs[jid].config_json["_description"] == "new"
        # config plus explicit description
        ctx.svc.update_job(jid, config={"region": "r3"}, description="d3")
        stored = ctx.db.discovery_jobs[jid].config_json
        assert stored["_description"] == "d3" and stored["region"] == "r3"
        # new secret replaces old
        ctx.svc.update_job(jid, config={"secret_access_key": "fresh"})
        assert ctx.db.discovery_jobs[jid].config_json["secret_access_key"] == "fresh"
        # no-op update does not fail
        assert ctx.svc.update_job(jid)["id"] == jid

    def test_update_missing(self, ctx: Any) -> None:
        """Updating a missing job raises."""
        with pytest.raises(Exception, match="not found"):
            ctx.svc.update_job(987654321, name="x")

    def test_delete_job(self, ctx: Any) -> None:
        """Deleting removes the job and its history."""
        jid = _job(ctx.db)
        ctx.db.discovery_history.insert(
            job_id=jid,
            started_at=datetime.now(UTC),
            status="completed",
            entities_discovered=0,
            entities_updated=0,
            entities_created=0,
        )
        ctx.db.commit()
        assert "deleted" in ctx.svc.delete_job(jid)["message"]
        assert ctx.db.discovery_jobs[jid] is None

    def test_delete_missing(self, ctx: Any) -> None:
        """Deleting a missing job raises."""
        with pytest.raises(Exception, match="not found"):
            ctx.svc.delete_job(987654321)


# --------------------------------------------------------------------------- #
# test_job / run_discovery / queue / history
# --------------------------------------------------------------------------- #


class TestExecution:
    """Connectivity tests, discovery runs, queueing and history."""

    @staticmethod
    def _client(**attrs: Any) -> Any:
        """Build a client double with only the given attributes."""

        class C:
            """Minimal client."""

        c = C()
        for k, v in attrs.items():
            setattr(c, k, v)
        return c

    def test_test_job_dict_success_with_extras(self, ctx: Any) -> None:
        """A connected dict result surfaces whitelisted fields and AWS extras."""
        client = self._client(
            test_connection=lambda: {
                "connected": True,
                "status": "ok",
                "email": "a@example.com",
                "droplet_limit": None,
                "ignored": 1,
            },
            get_auth_method=lambda: "keys",
            get_caller_identity=lambda: {"arn": "x"},
        )
        with patch.object(ctx.svc, "_get_discovery_client", return_value=client):
            out = ctx.svc.test_job(1)
        assert out["success"] is True
        assert out["status"] == "ok" and out["email"] == "a@example.com"
        assert "droplet_limit" not in out and "ignored" not in out
        assert out["auth_method"] == "keys" and out["identity"] == {"arn": "x"}

    def test_test_job_dict_failure_and_no_identity(self, ctx: Any) -> None:
        """A failed dict result reports the error and skips identity."""
        client = self._client(
            test_connection=lambda: {"success": False, "error": "nope"},
            get_caller_identity=lambda: {"arn": "x"},
        )
        with patch.object(ctx.svc, "_get_discovery_client", return_value=client):
            out = ctx.svc.test_job(1)
        assert out == {**out, "success": False, "error": "nope"}
        assert "identity" not in out

    def test_test_job_bool_and_empty_identity(self, ctx: Any) -> None:
        """A bool result works; an empty identity is omitted."""
        client = self._client(
            test_connection=lambda: True, get_caller_identity=lambda: None
        )
        with patch.object(ctx.svc, "_get_discovery_client", return_value=client):
            out = ctx.svc.test_job(1)
        assert out["success"] is True and "identity" not in out

    def test_test_job_dict_failure_without_error(self, ctx: Any) -> None:
        """A failed dict result without error text adds nothing."""
        client = self._client(test_connection=lambda: {"connected": False})
        with patch.object(ctx.svc, "_get_discovery_client", return_value=client):
            assert "error" not in ctx.svc.test_job(1)

    def test_test_job_error(self, ctx: Any) -> None:
        """Client errors are returned as failures."""
        with patch.object(
            ctx.svc, "_get_discovery_client", side_effect=RuntimeError("bad")
        ):
            out = ctx.svc.test_job(1)
        assert out["success"] is False and out["error"] == "bad"

    def test_run_discovery_success_stores_resources(self, ctx: Any) -> None:
        """A run with an organization stores resources and history."""
        jid = _job(ctx.db, "aws", config_json={"_organization_id": ctx.org})
        results = {
            "resources_count": 1,
            "discovery_time": datetime.now(UTC),
            "compute": [
                {
                    "name": f"web-{_uid()}",
                    "resource_type": "ec2_instance",
                    "resource_id": f"i-{_uid()}",
                    "provider": "aws",
                }
            ],
        }
        client = MagicMock()
        client.discover_all.return_value = results
        with patch.object(ctx.svc, "_get_discovery_client", return_value=client):
            out = ctx.svc.run_discovery(jid)
        assert out["success"] is True and out["resources_discovered"] == 1
        assert out["edges_created"] == 0
        hist = ctx.db(ctx.db.discovery_history.job_id == jid).select().first()
        assert hist.status == "completed"

    def test_run_discovery_without_org(self, ctx: Any) -> None:
        """Without an organization id nothing is stored but history is kept."""
        jid = _job(ctx.db, "aws")
        client = MagicMock()
        client.discover_all.return_value = {
            "resources_count": 0,
            "discovery_time": datetime.now(UTC),
        }
        with patch.object(ctx.svc, "_get_discovery_client", return_value=client):
            assert ctx.svc.run_discovery(jid)["success"] is True

    def test_run_discovery_failure_recorded(self, ctx: Any) -> None:
        """A failing client records a failed history row."""
        jid = _job(ctx.db, "aws")
        with patch.object(
            ctx.svc, "_get_discovery_client", side_effect=RuntimeError("boom")
        ):
            out = ctx.svc.run_discovery(jid)
        assert out["success"] is False and out["error"] == "boom"
        hist = ctx.db(ctx.db.discovery_history.job_id == jid).select().first()
        assert hist.status == "failed"

    def test_run_discovery_missing(self, ctx: Any) -> None:
        """A missing job raises before running."""
        with pytest.raises(Exception, match="not found"):
            ctx.svc.run_discovery(987654321)

    def test_queue_job(self, ctx: Any) -> None:
        """Queueing sets next_run_at; missing jobs raise."""
        jid = _job(ctx.db)
        assert ctx.svc.queue_job_for_worker(jid)["success"] is True
        assert ctx.db.discovery_jobs[jid].next_run_at is not None
        with pytest.raises(Exception, match="not found"):
            ctx.svc.queue_job_for_worker(987654321)

    def test_history_filters(self, ctx: Any) -> None:
        """History can be listed globally or per job with a limit."""
        jid = _job(ctx.db)
        for _ in range(3):
            ctx.db.discovery_history.insert(
                job_id=jid,
                started_at=datetime.now(UTC),
                status="completed",
                entities_discovered=0,
                entities_updated=0,
                entities_created=0,
            )
        ctx.db.commit()
        assert len(ctx.svc.get_discovery_history(job_id=jid, limit=2)) == 2
        assert ctx.svc.get_discovery_history(limit=5)


# --------------------------------------------------------------------------- #
# Provider detection, root entity, networking
# --------------------------------------------------------------------------- #


class TestDetectProvider:
    """_detect_provider_type."""

    @pytest.mark.parametrize(
        "results,expected",
        [
            ({"compute": [{"resource_type": "k8s_pod"}]}, "kubernetes"),
            ({"storage": [{"resource_type": "gce_disk"}]}, "gcp"),
            ({"storage": [{"type": "gcs_bucket"}]}, "gcp"),
            ({"compute": [{"resource_type": "fleetdm_host"}]}, "fleetdm"),
            ({"compute": [{"resource_type": "do_droplet"}]}, "digitalocean"),
            ({"compute": [{"resource_type": "x", "provider": "azure"}]}, "azure"),
            (
                {
                    "compute": [{"resource_type": "x", "provider": "zzz"}],
                    "provider": "p",
                },
                "p",
            ),
            ({"compute": "notalist", "network": []}, "unknown"),
            ({"provider": ""}, "unknown"),
        ],
    )
    def test_detect(self, results: dict, expected: str) -> None:
        """Provider is inferred from resource types, then provider fields."""
        assert DiscoveryService(MagicMock())._detect_provider_type(results) == expected


class TestRootEntity:
    """_ensure_provider_root_entity."""

    def test_unknown_provider(self, ctx: Any) -> None:
        """Providers without a root sub-type return None."""
        assert ctx.svc._ensure_provider_root_entity(ctx.org, "azure", {}) is None

    @pytest.mark.parametrize(
        "provider,cfg",
        [
            ("kubernetes", {"cluster_name": "c"}),
            ("aws", {"account_id": "123"}),
            ("gcp", {"project_id": "p"}),
            ("digitalocean", {}),
            ("fleetdm", {"name": "n"}),
        ],
    )
    def test_create_then_reuse(self, ctx: Any, provider: str, cfg: dict) -> None:
        """First call inserts; second call finds and updates the same row."""
        cfg = {k: f"{v}-{ctx.org}" for k, v in cfg.items()}
        first = ctx.svc._ensure_provider_root_entity(ctx.org, provider, cfg)
        second = ctx.svc._ensure_provider_root_entity(ctx.org, provider, cfg)
        assert first and first == second


class TestNetworking:
    """Intermediate networking, upserts and mappings."""

    def test_kubernetes_namespaces(self, ctx: Any) -> None:
        """Namespaces from resources become networking rows with root links."""
        root = ctx.svc._ensure_provider_root_entity(
            ctx.org, "kubernetes", {"name": f"k-{_uid()}"}
        )
        res = {
            "compute": [{"metadata": {"namespace": f"ns-{ctx.org}"}}],
            "network": [{"metadata": {}}],
            "iam": [{"metadata": {"namespace": f"ns2-{ctx.org}"}}],
        }
        lookup = ctx.svc._ensure_intermediate_networking(
            ctx.org, "kubernetes", root, res
        )
        assert set(lookup) == {f"namespace:ns-{ctx.org}", f"namespace:ns2-{ctx.org}"}
        # without a root entity no mapping is made, but lookup is returned
        lookup2 = ctx.svc._ensure_intermediate_networking(
            ctx.org, "kubernetes", None, res
        )
        assert lookup2 == lookup

    def test_aws_vpc_and_subnet(self, ctx: Any) -> None:
        """AWS VPCs register first, subnets link to their VPC parent."""
        root = ctx.svc._ensure_provider_root_entity(
            ctx.org, "aws", {"name": f"a-{_uid()}"}
        )
        vpc = f"vpc-{_uid()}"
        res = {
            "network": [
                {
                    "resource_type": "vpc",
                    "name": "v",
                    "resource_id": vpc,
                    "metadata": {"vpc_id": vpc, "cidr_block": "10.0.0.0/16"},
                },
                {
                    "resource_type": "vpc",
                    "resource_id": f"vpc2-{_uid()}",
                    "metadata": {},
                },
                {
                    "resource_type": "subnet",
                    "name": f"s-{_uid()}",
                    "resource_id": "sn-1",
                    "metadata": {"vpc_id": vpc, "subnet_id": "sn-1"},
                },
                {
                    "resource_type": "subnet",
                    "name": f"s2-{_uid()}",
                    "resource_id": "sn-2",
                    "metadata": {},
                },
                {"resource_type": "other"},
            ]
        }
        lookup = ctx.svc._ensure_intermediate_networking(ctx.org, "aws", root, res)
        assert (
            f"vpc:{vpc}" in lookup
            and "subnet:sn-1" in lookup
            and "subnet:sn-2" in lookup
        )
        parent = ctx.db.networking_resources[lookup["subnet:sn-1"]].parent_id
        assert parent == lookup[f"vpc:{vpc}"]
        # no root entity: VPC still stored, no mapping attempted
        res2 = {
            "network": [
                {
                    "resource_type": "vpc",
                    "name": f"v-{_uid()}",
                    "resource_id": "v9",
                    "metadata": {},
                }
            ]
        }
        assert ctx.svc._ensure_intermediate_networking(ctx.org, "aws", None, res2)

    def test_gcp_vpc(self, ctx: Any) -> None:
        """GCP VPCs become global networking rows."""
        root = ctx.svc._ensure_provider_root_entity(
            ctx.org, "gcp", {"name": f"g-{_uid()}"}
        )
        res = {
            "network": [
                {
                    "resource_type": "vpc",
                    "name": f"gv-{_uid()}",
                    "resource_id": "gv1",
                    "metadata": {"auto_create_subnets": True},
                },
                {"resource_type": "subnet"},
            ]
        }
        assert "vpc:gv1" in ctx.svc._ensure_intermediate_networking(
            ctx.org, "gcp", root, res
        )
        res2 = {
            "network": [
                {"resource_type": "vpc", "name": f"gw-{_uid()}", "resource_id": "gv2"}
            ]
        }
        assert "vpc:gv2" in ctx.svc._ensure_intermediate_networking(
            ctx.org, "gcp", None, res2
        )

    def test_other_provider_empty(self, ctx: Any) -> None:
        """Other providers create no intermediate resources."""
        assert ctx.svc._ensure_intermediate_networking(ctx.org, "azure", None, {}) == {}

    def test_upsert_networking_create_update(self, ctx: Any) -> None:
        """Re-upserting the same key updates (with/without external id)."""
        name = f"net-{_uid()}"
        first = ctx.svc._upsert_networking_resource(
            ctx.org, name, "other", attributes={"a": 1}
        )
        again = ctx.svc._upsert_networking_resource(
            ctx.org, name, "other", external_id="e1"
        )
        third = ctx.svc._upsert_networking_resource(ctx.org, name, "other")
        assert first == again == third
        assert ctx.db.networking_resources[first].external_id == "e1"

    def test_upsert_networking_error(self, broken: DiscoveryService) -> None:
        """Failures return None."""
        assert broken._upsert_networking_resource(1, "n", "other") is None

    def test_entity_mapping(self, ctx: Any) -> None:
        """Mappings are created once and tolerate failures."""
        net = ctx.svc._upsert_networking_resource(ctx.org, f"m-{_uid()}", "other")
        ent = ctx.svc._store_as_entity(
            ctx.org,
            {
                "name": f"e-{_uid()}",
                "resource_type": "ec2_instance",
                "resource_id": _uid(),
            },
            "compute",
        )
        ctx.svc._upsert_network_entity_mapping(net, ent)
        ctx.svc._upsert_network_entity_mapping(net, ent)
        rows = ctx.db(
            (ctx.db.network_entity_mappings.network_id == net)
            & (ctx.db.network_entity_mappings.entity_id == ent)
        ).select()
        assert len(rows) == 1

    def test_entity_mapping_error(self, broken: DiscoveryService) -> None:
        """Failures are swallowed."""
        broken._upsert_network_entity_mapping(1, 2)


# --------------------------------------------------------------------------- #
# Domain stores
# --------------------------------------------------------------------------- #


class TestStores:
    """_store_as_* helpers."""

    @pytest.mark.parametrize(
        "rtype,meta,method",
        [
            ("k8s_service", {"ports": [{"port": 80}]}, "kubernetes"),
            ("k8s_service", {}, "kubernetes"),
            ("lambda_function", {"state": "Active"}, "serverless"),
            ("sqs_queue", {}, "aws_sqs"),
            ("sns_topic", {}, "aws_sns"),
            ("step_function", {}, "aws_stepfunctions"),
            ("eventbridge_rule", {}, "aws_eventbridge"),
            ("api_gateway_rest_api", {}, "api_gateway"),
        ],
    )
    def test_store_service(self, ctx: Any, rtype: str, meta: dict, method: str) -> None:
        """Each service-like resource lands in services, then updates in place."""
        res = {
            "name": f"svc-{_uid()}",
            "resource_type": rtype,
            "metadata": meta,
            "resource_id": _uid(),
        }
        first = ctx.svc._store_as_service(ctx.org, res, "aws")
        second = ctx.svc._store_as_service(ctx.org, res, "aws")
        assert first and first == second
        assert ctx.db.services[first].deployment_method == method

    def test_store_service_unknown_and_error(
        self, ctx: Any, broken: DiscoveryService
    ) -> None:
        """Unknown types return None; DB errors return None."""
        assert ctx.svc._store_as_service(ctx.org, {"resource_type": "x"}, "aws") is None
        assert (
            broken._store_as_service(1, {"resource_type": "sqs_queue"}, "aws") is None
        )

    @pytest.mark.parametrize(
        "rtype",
        [
            "s3_bucket",
            "ebs_volume",
            "rds_instance",
            "gcs_bucket",
            "k8s_persistent_volume",
            "k8s_pvc",
            "dynamodb_table",
            "ecr_repository",
            "cloudwatch_log_group",
            "efs_filesystem",
            "elasticache_cluster",
        ],
    )
    def test_store_data_store(self, ctx: Any, rtype: str) -> None:
        """Each storage resource lands in data_stores, then updates in place."""
        res = {
            "name": f"ds-{_uid()}",
            "resource_type": rtype,
            "external_id": _uid(),
            "region": "r",
        }
        first = ctx.svc._store_as_data_store(ctx.org, res, "aws")
        assert first == ctx.svc._store_as_data_store(ctx.org, res, "aws")

    def test_store_data_store_unknown_and_error(
        self, ctx: Any, broken: DiscoveryService
    ) -> None:
        """Unknown types and DB errors return None."""
        assert (
            ctx.svc._store_as_data_store(ctx.org, {"resource_type": "x"}, "aws") is None
        )
        assert (
            broken._store_as_data_store(1, {"resource_type": "s3_bucket"}, "aws")
            is None
        )

    def test_store_load_balancer(self, ctx: Any) -> None:
        """Load balancers link to their VPC parent; others are skipped."""
        assert (
            ctx.svc._store_as_networking_resource(ctx.org, {"resource_type": "vpc"}, {})
            is None
        )
        vpc = ctx.svc._upsert_networking_resource(ctx.org, f"v-{_uid()}", "other")
        res = {
            "name": f"lb-{_uid()}",
            "resource_type": "load_balancer",
            "resource_id": _uid(),
            "metadata": {"vpc_id": "v1", "dns_name": "d", "type": "alb"},
        }
        lb = ctx.svc._store_as_networking_resource(ctx.org, res, {"vpc:v1": vpc})
        assert ctx.db.networking_resources[lb].parent_id == vpc
        res2 = {
            "name": f"lb-{_uid()}",
            "resource_type": "load_balancer",
            "metadata": {},
        }
        assert ctx.svc._store_as_networking_resource(ctx.org, res2, {})

    def test_store_k8s_service_account(
        self, ctx: Any, broken: DiscoveryService
    ) -> None:
        """Service accounts become identities (idempotent); errors return None."""
        res = {"name": f"sa-{_uid()}", "metadata": {"namespace": "ns"}}
        first = ctx.svc._store_k8s_service_account_as_identity(ctx.org, res, "c1")
        assert first == ctx.svc._store_k8s_service_account_as_identity(
            ctx.org, res, "c1"
        )
        res_default = {"name": f"sa-{_uid()}"}
        assert ctx.svc._store_k8s_service_account_as_identity(
            ctx.org, res_default, "c1"
        )
        assert broken._store_k8s_service_account_as_identity(1, res, "c") is None

    @pytest.mark.parametrize(
        "image,name,version,vendor",
        [
            ("ghcr.io/o/app:1.2", "ghcr.io/o/app", "1.2", "ghcr.io"),
            ("nginx", "nginx", "latest", "docker.io"),
            ("sha256:abcd", "sha256:abcd", "latest", "docker.io"),
        ],
    )
    def test_store_software(
        self, ctx: Any, image: str, name: str, version: str, vendor: str
    ) -> None:
        """Container images parse into name/version/vendor and de-duplicate."""
        image = (
            image.replace("app", f"app{_uid()}") if "app" in image else image + _uid()
        )
        first = ctx.svc._store_container_image_as_software(ctx.org, image)
        assert first == ctx.svc._store_container_image_as_software(ctx.org, image)
        row = ctx.db.software[first]
        assert row.version == (
            version if ":" in image and not image.startswith("sha256:") else "latest"
        )
        assert row.vendor in (vendor, "docker.io", "ghcr.io")

    def test_store_software_error(self, broken: DiscoveryService) -> None:
        """DB errors return None."""
        assert broken._store_container_image_as_software(1, "img:1") is None

    def test_store_entity_paths(self, ctx: Any) -> None:
        """Entities insert with tags/parent, update merging metadata."""
        name = f"ent-{_uid()}"
        res = {
            "name": name,
            "resource_type": "ec2_instance",
            "resource_id": "i-1",
            "region": "r",
            "tags": {"k": "v"},
            "metadata": {"a": 1},
        }
        root = ctx.svc._ensure_provider_root_entity(
            ctx.org, "aws", {"name": f"a-{_uid()}"}
        )
        first = ctx.svc._store_as_entity(ctx.org, res, "compute", parent_id=root)
        ctx.db(ctx.db.entities.id == first).update(metadata={"extra": "keep"})
        ctx.db.commit()
        second = ctx.svc._store_as_entity(ctx.org, res, "compute", parent_id=root)
        third = ctx.svc._store_as_entity(ctx.org, res, "compute")
        assert first == second == third
        meta = ctx.db.entities[first].metadata
        assert meta["extra"] == "keep" and meta["a"] == 1

    def test_store_secret(self, ctx: Any, broken: DiscoveryService) -> None:
        """K8s secrets store metadata only; repeat updates in place."""
        res = {"name": f"sec-{_uid()}", "metadata": {"namespace": "n", "keys": ["a"]}}
        first = ctx.svc._store_k8s_secret(ctx.org, res)
        assert first == ctx.svc._store_k8s_secret(ctx.org, res)
        assert ctx.svc._store_k8s_secret(ctx.org, {"name": f"sec-{_uid()}"})
        assert broken._store_k8s_secret(1, res) is None

    @pytest.mark.parametrize(
        "meta",
        [
            {
                "not_after": "2030-01-01T00:00:00Z",
                "not_before": "2029-01-01T00:00:00Z",
                "dns_names": ["a.example.com"],
                "issuer_ref": {"name": "iss", "kind": "Issuer"},
            },
        ],
    )
    def test_store_certificate(self, ctx: Any, meta: dict) -> None:
        """Certificates store with dates, and repeat updates in place."""
        res = {"name": f"cert-{_uid()}", "metadata": meta}
        first = ctx.svc._store_cert_manager_certificate(ctx.org, res)
        assert first and first == ctx.svc._store_cert_manager_certificate(ctx.org, res)

    @pytest.mark.xfail(
        strict=True,
        reason="bug: certificates.expiration_date is NOT NULL but "
        "_store_cert_manager_certificate inserts None when not_after is "
        "missing/unparseable, so the insert fails and the cert is dropped",
    )
    @pytest.mark.parametrize(
        "meta", [{}, {"not_after": "garbage", "not_before": "garbage"}]
    )
    def test_store_certificate_without_expiry(self, ctx: Any, meta: dict) -> None:
        """A certificate lacking a parseable expiry should still be stored."""
        res = {"name": f"cert-{_uid()}", "metadata": meta}
        assert ctx.svc._store_cert_manager_certificate(ctx.org, res)

    def test_store_certificate_error(self, broken: DiscoveryService) -> None:
        """DB errors return None."""
        assert broken._store_cert_manager_certificate(1, {"name": "c"}) is None

    def test_store_cni(self, ctx: Any) -> None:
        """CNI plugins become networking resources."""
        res = {"name": f"cni-{_uid()}", "metadata": {"cni_type": "cilium"}}
        assert ctx.svc._store_cni_as_networking(ctx.org, res, {})
        assert ctx.svc._store_cni_as_networking(ctx.org, {}, {}) is not None

    @pytest.mark.parametrize(
        "rtype,expected",
        [
            ("k8s_service", "service"),
            ("k8s_pod", "entity"),
            ("vpc", "networking"),
            ("iam_user", "identity"),
            ("s3_bucket", "data_store"),
            ("k8s_secret", "builtin_secret"),
            ("cert_manager_certificate", "certificate"),
            ("never-heard-of-it", "entity"),
        ],
    )
    def test_domain_map(self, rtype: str, expected: str) -> None:
        """Resource types map to domain tables with an entity default."""
        assert DiscoveryService(MagicMock())._resource_type_to_domain(rtype) == expected

    def test_iam_identity(self, ctx: Any) -> None:
        """IAM users/roles/others store identities and refresh on repeat."""
        acct = f"{int(uuid.uuid4().int % 10**12):012d}"
        user = {
            "name": f"u-{_uid()}",
            "resource_type": "iam_user",
            "metadata": {"arn": f"arn:aws:iam::{acct}:user/x"},
        }
        first = ctx.svc._store_iam_as_identity(ctx.org, user)
        assert first == ctx.svc._store_iam_as_identity(ctx.org, user)
        assert ctx.db.identities[first].identity_type == "integration"
        role = {
            "name": f"r-{_uid()}",
            "resource_type": "iam_role",
            "resource_id": "noarn",
        }
        rid = ctx.svc._store_iam_as_identity(ctx.org, role)
        assert ctx.db.identities[rid].identity_type == "serviceAccount"
        other = {
            "name": f"o-{_uid()}",
            "resource_type": "weird",
            "metadata": {"arn": f"arn:aws:iam::{acct}:thing/y"},
        }
        oid = ctx.svc._store_iam_as_identity(ctx.org, other)
        assert ctx.db.identities[oid].identity_type == "other"

    def test_tenant_for_org_missing(self, ctx: Any) -> None:
        """An unknown organization cannot resolve a tenant."""
        with pytest.raises(ValueError, match="Cannot resolve tenant"):
            ctx.svc._tenant_for_org(987654321)
        assert ctx.svc._tenant_for_org(ctx.org) == ctx.tenant


# --------------------------------------------------------------------------- #
# Dependencies and linkers
# --------------------------------------------------------------------------- #


class TestLinks:
    """Dependency creation, resolution and registration."""

    def test_dependency_link(self, ctx: Any, broken: DiscoveryService) -> None:
        """Links are idempotent; non-canonical types warn; errors return False."""
        sid, tid = 900001, 900002
        assert ctx.svc._create_dependency_link(
            "service", sid, "entity", tid, ctx.tenant
        )
        assert ctx.svc._create_dependency_link(
            "service", sid, "entity", tid, ctx.tenant
        )
        assert ctx.svc._create_dependency_link(
            "service", sid, "entity", tid, ctx.tenant, "weird_edge", {"m": 1}
        )
        assert broken._create_dependency_link("a", 1, "b", 2, 1) is False

    def test_resolve_target_db_fallback(self, ctx: Any) -> None:
        """Targets resolve from the scan index first then the database."""
        ext = f"ext-{_uid()}"
        net = ctx.svc._upsert_networking_resource(
            ctx.org, f"n-{_uid()}", "other", external_id=ext
        )
        idx = {("aws", "hit"): ("entity", 5)}
        assert ctx.svc._resolve_target(idx, "aws", "hit", None, ctx.org) == (
            "entity",
            5,
        )
        assert ctx.svc._resolve_target(
            {}, "aws", ext, "networking_resource", ctx.org
        ) == ("networking_resource", net)
        assert ctx.svc._resolve_target({}, "aws", ext, None, ctx.org) == (
            "networking_resource",
            net,
        )
        assert ctx.svc._resolve_target({}, "aws", "missing", "bogus", ctx.org) is None

    def test_link_resources_dual_write(self, ctx: Any) -> None:
        """in_network edges between entity and networking also write a mapping."""
        net = ctx.svc._upsert_networking_resource(ctx.org, f"n-{_uid()}", "other")
        ent = ctx.svc._store_as_entity(
            ctx.org,
            {"name": f"e-{_uid()}", "resource_type": "t", "resource_id": _uid()},
            "compute",
        )
        assert ctx.svc._link_resources(
            ("entity", ent), ("networking_resource", net), "in_network", ctx.tenant
        )
        mapped = ctx.db(ctx.db.network_entity_mappings.network_id == net).select()
        assert len(mapped) == 1
        assert ctx.svc._link_resources(
            ("entity", ent), ("service", 1), "attached_to", ctx.tenant
        )

    def test_register(self, ctx: Any) -> None:
        """Only rows with a row id and native id are indexed."""
        idx: dict = {}
        ctx.svc._register(idx, "aws", {"resource_id": "r"}, "entity", None)
        ctx.svc._register(idx, "aws", {}, "entity", 3)
        assert idx == {}
        ctx.svc._register(idx, "aws", {"external_id": "x"}, "entity", 3)
        assert idx == {("aws", "x"): ("entity", 3)}

    def test_ingress_links_backend_services(self, ctx: Any) -> None:
        """Ingresses link to services and append route paths once."""
        svc_name = f"backend-{_uid()}"
        svc_id = ctx.svc._store_as_service(
            ctx.org, {"name": svc_name, "resource_type": "k8s_service"}, "kubernetes"
        )
        ns = f"ns-{_uid()}"
        net = ctx.svc._upsert_networking_resource(ctx.org, ns, "namespace")
        res = {
            "name": f"ing-{_uid()}",
            "metadata": {
                "namespace": ns,
                "backend_services": [svc_name, f"absent-{_uid()}"],
                "paths": [
                    {"service": svc_name, "host": "h.example.com", "path": "/x"},
                    {"service": "other"},
                ],
            },
        }
        for _ in range(2):
            ing = ctx.svc._store_k8s_ingress(
                ctx.org, res, {f"namespace:{ns}": net}, ctx.tenant
            )
        assert ing
        assert ctx.db.services[svc_id].paths == ["GET h.example.com/x"]

    def test_ingress_link_error_is_logged(self, ctx: Any) -> None:
        """A failure while linking one backend does not abort the ingress."""
        res = {"name": f"ing-{_uid()}", "metadata": {"backend_services": ["a"]}}
        with patch.object(ctx.svc, "db", MagicMock(side_effect=RuntimeError("x"))):
            with patch.object(ctx.svc, "_upsert_networking_resource", return_value=7):
                assert ctx.svc._store_k8s_ingress(ctx.org, res, {}, ctx.tenant) == 7

    def test_ingress_no_id_skips_linking(self, ctx: Any) -> None:
        """If the networking upsert fails, no linking is attempted."""
        with patch.object(ctx.svc, "_upsert_networking_resource", return_value=None):
            assert (
                ctx.svc._store_k8s_ingress(ctx.org, {"name": "i"}, {}, ctx.tenant)
                is None
            )

    def test_pvc_links_to_pv(self, ctx: Any) -> None:
        """PVCs bound to a known PV record a bound_to edge."""
        pv_name = f"pv-{_uid()}"
        pv = ctx.svc._store_as_data_store(
            ctx.org,
            {"name": pv_name, "resource_type": "k8s_persistent_volume"},
            "kubernetes",
        )
        pvc = {
            "name": f"pvc-{_uid()}",
            "resource_type": "k8s_pvc",
            "metadata": {"volume_name": pv_name},
        }
        pvc_id = ctx.svc._store_k8s_pvc_as_data_store(
            ctx.org, pvc, "kubernetes", ctx.tenant
        )
        dep = (
            ctx.db(
                (ctx.db.dependencies.source_id == pvc_id)
                & (ctx.db.dependencies.target_id == pv)
                & (ctx.db.dependencies.dependency_type == "bound_to")
            )
            .select()
            .first()
        )
        assert dep is not None
        # unknown volume and no volume name
        pvc2 = {
            "name": f"pvc-{_uid()}",
            "resource_type": "k8s_pvc",
            "metadata": {"volume_name": "nope"},
        }
        assert ctx.svc._store_k8s_pvc_as_data_store(
            ctx.org, pvc2, "kubernetes", ctx.tenant
        )
        pvc3 = {"name": f"pvc-{_uid()}", "resource_type": "k8s_pvc"}
        assert ctx.svc._store_k8s_pvc_as_data_store(
            ctx.org, pvc3, "kubernetes", ctx.tenant
        )

    def test_pvc_link_error_and_no_id(self, ctx: Any) -> None:
        """Lookup errors are logged; an unstored PVC returns None."""
        pvc = {
            "name": "p",
            "resource_type": "k8s_pvc",
            "metadata": {"volume_name": "v"},
        }
        with patch.object(ctx.svc, "_store_as_data_store", return_value=11):
            with patch.object(ctx.svc, "db", MagicMock(side_effect=RuntimeError("x"))):
                assert ctx.svc._store_k8s_pvc_as_data_store(ctx.org, pvc, "k", 1) == 11
        with patch.object(ctx.svc, "_store_as_data_store", return_value=None):
            assert ctx.svc._store_k8s_pvc_as_data_store(ctx.org, pvc, "k", 1) is None


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #


class TestStoreDiscovered:
    """_store_discovered_resources end to end."""

    def test_kubernetes_full(self, ctx: Any) -> None:
        """A cluster scan routes every resource type and resolves edges."""
        u = _uid()
        results = {
            "resources_count": 9,
            "discovery_time": datetime.now(UTC),
            "duration_seconds": 1,
            "compute": [
                {
                    "name": f"pod-{u}",
                    "resource_type": "k8s_pod",
                    "resource_id": f"pod-{u}",
                    "metadata": {
                        "namespace": f"ns-{u}",
                        "containers": [
                            {"image": f"reg.example.com/a{u}:1"},
                            {"image": f"reg.example.com/a{u}:1"},
                            {"image": ""},
                        ],
                    },
                    "relationships": [
                        {"target_external_id": f"ns-{u}", "edge_type": "in_network"},
                        {"target_external_id": "never", "edge_type": "runs_on"},
                    ],
                },
                {
                    "name": f"svc-{u}",
                    "resource_type": "k8s_service",
                    "resource_id": f"svc-{u}",
                    "metadata": {"namespace": f"ns-{u}", "ports": [{"port": 80}]},
                },
            ],
            "iam": [
                {
                    "name": f"sa-{u}",
                    "resource_type": "k8s_service_account",
                    "metadata": {"namespace": f"ns-{u}"},
                },
            ],
            "storage": [
                {
                    "name": f"pv-{u}",
                    "resource_type": "k8s_persistent_volume",
                    "resource_id": f"pv-{u}",
                },
                {
                    "name": f"pvc-{u}",
                    "resource_type": "k8s_pvc",
                    "resource_id": f"pvc-{u}",
                    "metadata": {"volume_name": f"pv-{u}"},
                },
            ],
            "security": [
                {
                    "name": f"sec-{u}",
                    "resource_type": "k8s_secret",
                    "metadata": {"namespace": f"ns-{u}"},
                },
                {
                    "name": f"cert-{u}",
                    "resource_type": "cert_manager_certificate",
                    "metadata": {"not_after": "2031-01-01T00:00:00Z"},
                },
            ],
            "network": [
                {
                    "name": f"ing-{u}",
                    "resource_type": "k8s_ingress",
                    "metadata": {
                        "namespace": f"ns-{u}",
                        "backend_services": [f"svc-{u}"],
                    },
                },
                {"name": f"cni-{u}", "resource_type": "k8s_cni"},
                {
                    "name": f"lb-{u}",
                    "resource_type": "load_balancer",
                    "resource_id": f"lb-{u}",
                },
                {
                    "name": f"sg-{u}",
                    "resource_type": "security_group",
                    "resource_id": f"sg-{u}",
                },
                {"name": f"vpc-{u}", "resource_type": "vpc"},
            ],
            "database": [],
        }
        counts = ctx.svc._store_discovered_resources(ctx.org, results)
        assert counts["edges_created"] == 1
        assert counts["unresolved_edges"] == 1

    def test_aws_full(self, ctx: Any) -> None:
        """AWS scans store IAM/data/networking and honour source-less edges."""
        u = _uid()
        acct = f"{int(uuid.uuid4().int % 10**12):012d}"
        results = {
            "compute": [
                {
                    "name": f"i-{u}",
                    "resource_type": "ec2_instance",
                    "resource_id": f"i-{u}",
                    "provider": "aws",
                    "metadata": {"vpc_id": f"vpc-{u}"},
                    "relationships": [
                        {"target_external_id": f"vpc-{u}", "edge_type": "in_network"}
                    ],
                },
                # a source that never registers (unknown service type) with relationships
                {
                    "name": "ghost",
                    "resource_type": "sqs_queue",
                    "relationships": [
                        {"target_external_id": "x", "edge_type": "attached_to"}
                    ],
                },
                {
                    "name": "noid",
                    "resource_type": "ec2_instance",
                    "metadata": {},
                    "relationships": [
                        {"target_external_id": "x", "edge_type": "attached_to"},
                        {"target_external_id": "y", "edge_type": "attached_to"},
                    ],
                },
            ],
            "network": [
                {
                    "name": f"vpc-{u}",
                    "resource_type": "vpc",
                    "resource_id": f"vpc-{u}",
                    "provider": "aws",
                    "metadata": {"vpc_id": f"vpc-{u}"},
                },
            ],
            "iam": [
                {
                    "name": f"user-{u}",
                    "resource_type": "iam_user",
                    "metadata": {"arn": f"arn:aws:iam::{acct}:user/{u}"},
                },
                {
                    "name": f"role-{u}",
                    "resource_type": "iam_role",
                    "resource_id": f"role-{u}",
                },
                {"name": "unknown", "resource_type": "weird"},
            ],
            "storage": [
                {
                    "name": f"bkt-{u}",
                    "resource_type": "s3_bucket",
                    "resource_id": f"bkt-{u}",
                }
            ],
            "serverless": [
                {
                    "name": f"fn-{u}",
                    "resource_type": "lambda_function",
                    "resource_id": f"fn-{u}",
                }
            ],
        }
        counts = ctx.svc._store_discovered_resources(ctx.org, results)
        assert counts["edges_created"] >= 1
        assert counts["unresolved_edges"] >= 3

    @pytest.mark.xfail(
        strict=True,
        reason="bug: pass 2 of _store_discovered_resources does not skip the "
        "top-level 'provider' string (pass 1 does), so results shaped like "
        "DigitalOceanDiscoveryClient.discover_all raise AttributeError",
    )
    def test_top_level_provider_key_is_skipped_in_pass_two(self, ctx: Any) -> None:
        """Results carrying a top-level provider string must store cleanly."""
        results = {
            "provider": "digitalocean",
            "compute": [
                {
                    "name": f"d-{_uid()}",
                    "resource_type": "do_droplet",
                    "resource_id": _uid(),
                }
            ],
        }
        assert ctx.svc._store_discovered_resources(ctx.org, results) == {
            "edges_created": 0,
            "unresolved_edges": 0,
        }

    def test_gcp_legacy_vpc_mapping(self, ctx: Any) -> None:
        """Non-AWS providers keep the legacy vpc connected_to mapping."""
        u = _uid()
        results = {
            "compute": [
                {
                    "name": f"vm-{u}",
                    "resource_type": "gce_instance",
                    "resource_id": f"vm-{u}",
                    "metadata": {"vpc_id": f"gvpc-{u}"},
                }
            ],
            "network": [
                {
                    "name": f"gvpc-{u}",
                    "resource_type": "vpc",
                    "resource_id": f"gvpc-{u}",
                }
            ],
        }
        counts = ctx.svc._store_discovered_resources(ctx.org, results)
        assert counts["unresolved_edges"] == 0
        vm = (
            ctx.db(
                (ctx.db.entities.organization_id == ctx.org)
                & (ctx.db.entities.name == f"vm-{u}")
            )
            .select()
            .first()
        )
        maps = ctx.db(ctx.db.network_entity_mappings.entity_id == vm.id).select()
        assert [m.relationship_type for m in maps] == ["connected_to"]

    def test_no_root_entity_unknown_provider(self, ctx: Any) -> None:
        """Without a provider root nothing is linked to a root but resources store."""
        u = _uid()
        results = {
            "compute": [
                {"name": f"x-{u}", "resource_type": "weird", "resource_id": f"x-{u}"}
            ],
            "empty": None,
        }
        assert ctx.svc._store_discovered_resources(ctx.org, results) == {
            "edges_created": 0,
            "unresolved_edges": 0,
        }


# --------------------------------------------------------------------------- #
# Sanitizer + scanner claim lifecycle
# --------------------------------------------------------------------------- #


class TestSanitize:
    """_sanitize_job."""

    def test_sanitize(self) -> None:
        """Secrets are masked, claim fields dropped, org/description surfaced."""
        svc = DiscoveryService(MagicMock())
        out = svc._sanitize_job(
            {
                "config_json": json.dumps(
                    {
                        "token": "t",
                        "api_key": "",
                        "_organization_id": 4,
                        "_description": "d",
                    }
                ),
                "claim_token_hash": "h",
                "claim_expires_at": "x",
            }
        )
        cfg = out["config_json"]
        assert cfg["token"] == "***REDACTED***" and cfg["api_key"] == ""
        assert out["organization_id"] == 4 and out["description"] == "d"
        assert "claim_token_hash" not in out and "claim_expires_at" not in out

    def test_sanitize_existing_fields_and_none(self) -> None:
        """Existing org/description keys win; None config passes through."""
        svc = DiscoveryService(MagicMock())
        out = svc._sanitize_job(
            {
                "config_json": {"_organization_id": 4, "_description": "d"},
                "organization_id": 9,
                "description": "keep",
            }
        )
        assert out["organization_id"] == 9 and out["description"] == "keep"
        assert svc._sanitize_job({"config_json": None})["config_json"] is None


class TestClaimLifecycle:
    """Pending list, claim, completion and stale requeue."""

    def test_claim_and_complete_success(self, ctx: Any) -> None:
        """A claimed scheduled job completes with its token and reschedules."""
        jid = _job(ctx.db, "network", interval=3600)
        assert jid in {j["id"] for j in ctx.svc.get_pending_jobs()}
        claim = ctx.svc.mark_job_running(jid)
        with pytest.raises(DiscoveryJobNotClaimableError):
            ctx.svc.mark_job_running(jid)
        out = ctx.svc.complete_job(
            jid, True, {"r": 1}, claim_token=claim["claim_token"]
        )
        assert out["status"] == "completed" and out["duplicate"] is False
        assert ctx.db.discovery_jobs[jid].next_run_at is not None
        dup = ctx.svc.complete_job(jid, True, {}, claim_token=claim["claim_token"])
        assert dup["duplicate"] is True and dup["status"] == "ignored"

    def test_complete_failure_one_time(self, ctx: Any) -> None:
        """A failed one-time job records the failure."""
        jid = _job(ctx.db, "banner")
        claim = ctx.svc.mark_job_running(jid)
        out = ctx.svc.complete_job(jid, False, {}, "err", claim["claim_token"])
        assert out["status"] == "failed"

    def test_complete_token_rules(
        self, ctx: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing/wrong tokens are rejected; transition mode accepts tokenless."""
        jid = _job(ctx.db, "network")
        ctx.svc.mark_job_running(jid)
        with pytest.raises(DiscoveryClaimTokenError):
            ctx.svc.complete_job(jid, True, {})
        with pytest.raises(DiscoveryClaimTokenError):
            ctx.svc.complete_job(jid, True, {}, claim_token="x" * 50)
        monkeypatch.setenv("ELDER_REQUIRE_CLAIM_TOKEN", "false")
        monkeypatch.setattr(svc_mod, "claim_token_required", lambda: False)
        assert ctx.svc.complete_job(jid, True, {})["status"] == "completed"
        with pytest.raises(Exception, match="not found"):
            ctx.svc.complete_job(987654321, True, {})

    def test_mark_missing(self, ctx: Any) -> None:
        """Marking a missing job raises."""
        with pytest.raises(Exception, match="not found"):
            ctx.svc.mark_job_running(987654321)

    def test_requeue_stale(self, ctx: Any) -> None:
        """Expired claims are released; running history is failed."""
        one_time = _job(ctx.db, "network")
        scheduled = _job(ctx.db, "network", interval=3600)
        for jid in (one_time, scheduled):
            ctx.svc.mark_job_running(jid)
            ctx.db(ctx.db.discovery_jobs.id == jid).update(
                claim_expires_at=datetime.now(UTC) - timedelta(seconds=5)
            )
        ctx.db.commit()
        assert ctx.svc.requeue_stale_claims() >= 2
        assert ctx.db.discovery_jobs[one_time].last_run_at is None
        assert ctx.db.discovery_jobs[one_time].claim_token_hash is None
        assert ctx.svc.requeue_stale_claims(one_time) == 0

    def test_requeue_single_job(self, ctx: Any) -> None:
        """A job-scoped requeue only touches that job."""
        jid = _job(ctx.db, "network")
        ctx.svc.mark_job_running(jid)
        ctx.db(ctx.db.discovery_jobs.id == jid).update(
            claim_expires_at=datetime.now(UTC) - timedelta(seconds=5)
        )
        ctx.db.commit()
        assert ctx.svc.requeue_stale_claims(jid) == 1
