"""Comprehensive tests for CSV bulk import, manual vulnerabilities, issue dates,
data inventory labels/metadata, and on-call human identity auto-population."""

import io
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import current_app
from quart.datastructures import FileStorage

ALL_TEST_SCOPES = [
    "sbom:read",
    "sbom:write",
    "infrastructure:read",
    "infrastructure:write",
    "issues:read",
    "issues:write",
    "services_oncall:read",
    "services_oncall:write",
]


@pytest.fixture
def api_headers(generate_token):
    token = generate_token(tenant_id=1, scopes=ALL_TEST_SCOPES)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _mock_user():
    user = MagicMock(id=1, is_superuser=False, username="tester")
    with patch("apps.api.auth.decorators.get_current_user", return_value=user):
        yield user


def _make_org(db, tenant_id: int = 1, name: str | None = None) -> int:
    now = datetime.now(UTC)
    org_id = db.organizations.insert(
        tenant_id=tenant_id,
        name=name or f"Org-{uuid4().hex[:6]}",
        slug=f"org-{uuid4().hex[:8]}",
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return org_id


class TestCsvImportAndTemplates:
    """Verify exact CSV templates, 1,000-row cap, scope enforcement, and validation."""

    @pytest.mark.asyncio
    async def test_list_and_download_templates(self, async_client, api_headers):
        res = await async_client.get("/api/v1/import/templates", headers=api_headers)
        assert res.status_code == 200
        body = await res.get_json()
        assert body["max_rows"] == 1000
        resources = {t["resource"] for t in body["templates"]}
        assert {
            "vulnerabilities",
            "entities",
            "data_stores",
            "services",
            "software",
            "ai_resources",
        }.issubset(resources)

        dl = await async_client.get(
            "/api/v1/import/templates/vulnerabilities/download", headers=api_headers
        )
        assert dl.status_code == 200
        text = (await dl.get_data()).decode("utf-8")
        lines = [line.strip() for line in text.strip().splitlines()]
        assert lines[0] == "cve_id,title,severity,cvss_score,description,published_at"
        assert len(lines) == 2

        missing = await async_client.get(
            "/api/v1/import/templates/does_not_exist/download", headers=api_headers
        )
        assert missing.status_code == 404

    @pytest.mark.asyncio
    async def test_import_enforces_write_scope(self, async_client, generate_token):
        readonly_token = generate_token(
            tenant_id=1, scopes=["sbom:read", "infrastructure:read"]
        )
        res = await async_client.post(
            "/api/v1/import/vulnerabilities",
            headers={"Authorization": f"Bearer {readonly_token}"},
            json={
                "csv_text": (
                    "cve_id,title,severity,cvss_score,description,published_at\n"
                    f"CVE-2026-{uuid4().hex[:4]},Test,high,7.5,desc,2026-10-01\n"
                )
            },
        )
        assert res.status_code == 403

    @pytest.mark.asyncio
    async def test_import_rejects_header_mismatch_and_over_1000_rows(
        self, async_client, api_headers
    ):
        # Reordered headers must be rejected
        bad_hdr = await async_client.post(
            "/api/v1/import/vulnerabilities",
            headers=api_headers,
            json={
                "csv_text": (
                    "title,cve_id,severity,cvss_score,description,published_at\n"
                    "Test,CVE-2026-1001,high,7.5,desc,2026-10-01\n"
                )
            },
        )
        assert bad_hdr.status_code == 400
        err_body = await bad_hdr.get_json()
        assert err_body["expected_headers"][0] == "cve_id"
        assert err_body["actual_headers"][0] == "title"

        # 1001 data rows must be rejected
        header = "cve_id,title,severity,cvss_score,description,published_at\n"
        rows = "".join(
            f"CVE-2026-{10000 + i},Title {i},low,3.1,desc,2026-10-01\n"
            for i in range(1001)
        )
        too_many = await async_client.post(
            "/api/v1/import/vulnerabilities",
            headers=api_headers,
            json={"csv_text": header + rows},
        )
        assert too_many.status_code == 400

    @pytest.mark.asyncio
    async def test_import_rejects_formula_injection_and_is_atomic(
        self, async_client, api_headers, app
    ):
        cve_ok = f"CVE-2026-{uuid4().hex[:6].upper()}"
        cve_bad = f"CVE-2026-{uuid4().hex[:6].upper()}"
        csv_text = (
            "cve_id,title,severity,cvss_score,description,published_at\n"
            f"{cve_ok},Valid Row,high,8.2,Valid desc,2026-10-01\n"
            f"{cve_bad},=CMD|' /C calc'!A0,high,11.5,Bad row,not-a-date\n"
        )
        res = await async_client.post(
            "/api/v1/import/vulnerabilities",
            headers=api_headers,
            json={"csv_text": csv_text},
        )
        assert res.status_code == 400
        body = await res.get_json()
        assert len(body["details"]) >= 3

        # Atomic: valid row 1 must NOT have been inserted
        async with app.app_context():
            row = (
                current_app.db(current_app.db.vulnerabilities.cve_id == cve_ok)
                .select()
                .first()
            )
            assert row is None

    @pytest.mark.asyncio
    async def test_import_vulnerabilities_valid_multipart(
        self, async_client, api_headers
    ):
        cve1 = f"CVE-2026-{uuid4().hex[:6].upper()}"
        cve2 = f"CVE-2026-{uuid4().hex[:6].upper()}"
        csv_bytes = (
            "cve_id,title,severity,cvss_score,description,published_at\n"
            f"{cve1},First vuln,critical,9.8,Critical RCE,2026-10-01\n"
            f"{cve2},Second vuln,medium,5.3,Medium issue,2026-10-02\n"
        ).encode("utf-8")

        res = await async_client.post(
            "/api/v1/import/vulnerabilities",
            headers=api_headers,
            files={
                "file": FileStorage(
                    stream=io.BytesIO(csv_bytes),
                    filename="vulns.csv",
                    content_type="text/csv",
                )
            },
        )
        assert res.status_code == 201
        body = await res.get_json()
        assert body["imported"] == 2
        assert body["resource"] == "vulnerabilities"

    @pytest.mark.asyncio
    async def test_import_org_scoped_resources_and_rejects_bad_org(
        self, async_client, api_headers, app
    ):
        async with app.app_context():
            org_id = _make_org(current_app.db, tenant_id=1)

        # Bad org id fails validation
        bad_org = await async_client.post(
            "/api/v1/import/data_stores",
            headers=api_headers,
            json={
                "csv_text": (
                    "name,organization_id,data_classification,storage_type,location_region,description\n"
                    "Bad Org DB,999999,internal,database,us-east-1,desc\n"
                )
            },
        )
        assert bad_org.status_code == 400

        # Valid data_stores, entities, services, software, ai_resources imports
        cases = [
            (
                "data_stores",
                "name,organization_id,data_classification,storage_type,location_region,description\n"
                f"CSV DB {uuid4().hex[:4]},{org_id},restricted,database,us-east-1,Billing DB\n",
            ),
            (
                "entities",
                "name,type,organization_id,sub_type,status\n"
                f"csv-host-{uuid4().hex[:4]},host,{org_id},linux,active\n",
            ),
            (
                "services",
                "name,organization_id,port,status,is_public\n"
                f"csv-svc-{uuid4().hex[:4]},{org_id},8443,active,false\n",
            ),
            (
                "software",
                "name,organization_id,software_type,version,description\n"
                f"csv-pkg-{uuid4().hex[:4]},{org_id},container,1.2.3,Container image\n",
            ),
            (
                "ai_resources",
                "name,organization_id,provider,model_name,resource_type,endpoint_url,status,description\n"
                f"csv-ai-{uuid4().hex[:4]},{org_id},anthropic,claude-sonnet,llm,https://api.anthropic.com,active,Assistant\n",
            ),
        ]
        for resource, csv_text in cases:
            res = await async_client.post(
                f"/api/v1/import/{resource}",
                headers=api_headers,
                json={"csv_text": csv_text},
            )
            assert res.status_code == 201, f"{resource}: {await res.get_json()}"
            assert (await res.get_json())["imported"] == 1


class TestManualVulnerabilityAndOtherApiChanges:
    """Verify manual vulnerabilities, issue start/end dates, data inventory, and on-call."""

    @pytest.mark.asyncio
    async def test_manual_create_vulnerability_and_duplicate_conflict(
        self, async_client, api_headers
    ):
        cve_id = f"CVE-2026-{uuid4().hex[:6].upper()}"
        res = await async_client.post(
            "/api/v1/vulnerabilities",
            headers=api_headers,
            json={
                "cve_id": cve_id,
                "title": "Manual CVE",
                "severity": "high",
                "cvss_score": 8.1,
                "published_at": "2026-10-15",
            },
        )
        assert res.status_code == 201
        created = await res.get_json()
        assert created["cve_id"] == cve_id

        dup = await async_client.post(
            "/api/v1/vulnerabilities",
            headers=api_headers,
            json={"cve_id": cve_id, "title": "Dup", "severity": "low"},
        )
        assert dup.status_code == 409

        bad_cvss = await async_client.post(
            "/api/v1/vulnerabilities",
            headers=api_headers,
            json={
                "cve_id": f"CVE-2026-{uuid4().hex[:6].upper()}",
                "title": "Bad",
                "severity": "high",
                "cvss_score": 12.0,
            },
        )
        assert bad_cvss.status_code == 400

    @pytest.mark.asyncio
    async def test_issue_start_end_dates_timezone_safe_comparison(
        self, async_client, api_headers, app
    ):
        async with app.app_context():
            org_id = _make_org(current_app.db, tenant_id=1)

        create_res = await async_client.post(
            "/api/v1/issues",
            headers=api_headers,
            json={
                "title": "Scheduled issue",
                "organization_id": org_id,
                "start_date": "2026-11-20",
                "end_date": "2026-11-25",
            },
        )
        assert create_res.status_code == 201
        issue = await create_res.get_json()
        issue_id = issue["id"]

        # Update end_date with naive date string against stored UTC start_date
        patch_ok = await async_client.patch(
            f"/api/v1/issues/{issue_id}",
            headers=api_headers,
            json={"end_date": "2026-11-28"},
        )
        assert patch_ok.status_code == 200

        # End date before start date must return 400
        patch_bad = await async_client.patch(
            f"/api/v1/issues/{issue_id}",
            headers=api_headers,
            json={"end_date": "2026-11-10"},
        )
        assert patch_bad.status_code == 400

    @pytest.mark.asyncio
    async def test_data_inventory_preseeds_labels_and_hydrates_get(
        self, async_client, api_headers, app
    ):
        async with app.app_context():
            org_id = _make_org(current_app.db, tenant_id=1)

        # Listing triggers pre-seeding of PHI, PII, FedRAMP, NYDFS, SOX, PCI
        list_res = await async_client.get("/api/v1/data-stores", headers=api_headers)
        assert list_res.status_code == 200

        labels_res = await async_client.get(
            "/api/v1/labels?per_page=100", headers=api_headers
        )
        assert labels_res.status_code == 200
        labels_by_name = {
            lbl["name"]: lbl["id"] for lbl in (await labels_res.get_json())["items"]
        }
        for required in ("PHI", "PII", "FedRAMP", "NYDFS", "SOX", "PCI"):
            assert required in labels_by_name

        create_res = await async_client.post(
            "/api/v1/data-stores",
            headers=api_headers,
            json={
                "name": f"Labeled Store {uuid4().hex[:4]}",
                "organization_id": org_id,
                "data_classification": "restricted",
                "storage_type": "database",
                "label_ids": [labels_by_name["PHI"], labels_by_name["FedRAMP"]],
                "metadata": {"owner": "secops"},
            },
        )
        assert create_res.status_code == 201
        store = await create_res.get_json()
        assert {l["name"] for l in store["labels"]} == {"PHI", "FedRAMP"}
        assert store["metadata"] == {"owner": "secops"}

        # GET by ID also hydrates labels
        get_res = await async_client.get(
            f"/api/v1/data-stores/{store['id']}", headers=api_headers
        )
        assert get_res.status_code == 200
        fetched = await get_res.get_json()
        assert {l["name"] for l in fetched["labels"]} == {"PHI", "FedRAMP"}

        # Filter by label name
        filt_res = await async_client.get(
            "/api/v1/data-stores?label=FedRAMP", headers=api_headers
        )
        assert filt_res.status_code == 200
        ids = {item["id"] for item in (await filt_res.get_json())["items"]}
        assert store["id"] in ids

    @pytest.mark.asyncio
    async def test_on_call_rotation_auto_adds_org_human_identities(
        self, async_client, api_headers, app
    ):
        async with app.app_context():
            db = current_app.db
            org_id = _make_org(db, tenant_id=1)
            now = datetime.now(UTC)
            human_id = db.identities.insert(
                tenant_id=1,
                organization_id=org_id,
                username=f"alice-{uuid4().hex[:6]}",
                email=f"alice-{uuid4().hex[:6]}@example.com",
                identity_type="human",
                auth_provider="local",
                portal_role="viewer",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                created_at=now,
                updated_at=now,
            )
            bot_id = db.identities.insert(
                tenant_id=1,
                organization_id=org_id,
                username=f"bot-{uuid4().hex[:6]}",
                email=f"bot-{uuid4().hex[:6]}@example.com",
                identity_type="service_account",
                auth_provider="local",
                portal_role="viewer",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        res = await async_client.post(
            "/api/v1/on-call/rotations",
            headers=api_headers,
            json={
                "name": f"Org Rotation {uuid4().hex[:4]}",
                "scope_type": "organization",
                "organization_id": org_id,
                "schedule_type": "weekly",
                "rotation_length_days": 7,
                "rotation_start_date": "2026-11-20",
            },
        )
        assert res.status_code == 201
        rotation_id = (await res.get_json())["id"]

        parts_res = await async_client.get(
            f"/api/v1/on-call/rotations/{rotation_id}/participants",
            headers=api_headers,
        )
        assert parts_res.status_code == 200
        part_ids = {
            p["identity_id"] for p in (await parts_res.get_json()).get("items", [])
        }
        assert human_id in part_ids
        assert bot_id not in part_ids
