"""Admin CRUD + public submit tests for the IntakeForm model (hd_intake_forms).

Covers create + tenant-scoped list, global slug-uniqueness enforcement
(mirroring the HdTicketForm admin route test pattern), and the
unauthenticated public GET/submit routes that turn a form submission into a
native support Issue + customer_contact identity (Task 4).
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import current_app


class TestIntakeFormsAdmin:
    """Admin CRUD for /api/v1/intake-forms."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_and_list_form(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Admin can create a form; GET lists it scoped to the caller's tenant."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["helpdesk:admin"])
        slug = f"support-{uuid4().hex[:8]}"
        resp = await async_client.post(
            "/api/v1/intake-forms",
            json={
                "name": "Support Request",
                "slug": slug,
                "fields": [
                    {
                        "id": "email",
                        "label": "Email",
                        "type": "email",
                        "required": True,
                    },
                    {
                        "id": "subject",
                        "label": "Subject",
                        "type": "text",
                        "required": True,
                    },
                ],
                "is_public": True,
                "captcha_required": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (200, 201), (await resp.get_data()).decode()[:300]

        listing = await async_client.get(
            "/api/v1/intake-forms", headers={"Authorization": f"Bearer {token}"}
        )
        slugs = [f["slug"] for f in json.loads(await listing.get_data())["items"]]
        assert slug in slugs

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_duplicate_slug_conflict(
        self, mock_get_user, async_client, generate_token, app
    ):
        """A second form with the same globally-unique slug is rejected (409/400)."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["helpdesk:admin"])
        payload = {
            "name": "Dup Form",
            "slug": f"dup-{uuid4().hex[:8]}",
            "fields": [
                {
                    "id": "email",
                    "label": "Email",
                    "type": "email",
                    "required": True,
                }
            ],
        }
        first = await async_client.post(
            "/api/v1/intake-forms",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first.status_code in (200, 201), (await first.get_data()).decode()[:300]

        second = await async_client.post(
            "/api/v1/intake-forms",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert second.status_code in (400, 409), (
            await second.get_data()
        ).decode()[:300]


class TestIntakeFormsPublicSubmit:
    """Public (unauthenticated) GET/submit routes mounted at /api/v1/intake."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_public_submit_creates_issue_and_contact(
        self, mock_get_user, async_client, generate_token, app
    ):
        """End-to-end: a public submit creates a customer_contact identity +
        a native support Issue, and returns only a public reference — no
        internal ids or tenant in the response body."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["helpdesk:admin"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org_id = db.organizations.insert(
                name="Public Submit Org",
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        slug = f"help-{uuid4().hex[:8]}"
        create_resp = await async_client.post(
            "/api/v1/intake-forms",
            json={
                "name": "Help",
                "slug": slug,
                "fields": [
                    {
                        "id": "email",
                        "label": "Email",
                        "type": "email",
                        "required": True,
                    },
                    {
                        "id": "subject",
                        "label": "Subject",
                        "type": "text",
                        "required": True,
                    },
                ],
                "organization_id": org_id,
                "is_public": True,
                "captcha_required": False,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create_resp.status_code in (200, 201), (
            await create_resp.get_data()
        ).decode()[:300]

        email = f"cust-{uuid4().hex[:8]}@example.com"
        resp = await async_client.post(
            f"/api/v1/intake/{slug}/submit",
            json={"fields": {"email": email, "subject": "Cannot log in"}},
        )
        assert resp.status_code in (200, 201), (await resp.get_data()).decode()[:300]

        body = json.loads(await resp.get_data())
        assert body["status"] == "created"
        assert body["reference"]
        assert "id" not in body
        assert "tenant_id" not in body
        assert "organization_id" not in body

        async with app.app_context():
            db = current_app.db
            # Contact identities are keyed on a namespaced
            # `contact:{tenant_id}:{email}` username, never the bare email
            # (see security-review fix in intake_submit.py).
            assert (
                db(
                    (db.identities.email == email)
                    & (db.identities.identity_type == "customer_contact")
                ).count()
                == 1
            )
            assert (
                db(
                    (db.issues.issue_type == "SUPPORT")
                    & (db.issues.title == "Cannot log in")
                ).count()
                >= 1
            )

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_public_submit_captcha_required_rejects_without_altcha(
        self, mock_get_user, async_client, generate_token, app
    ):
        """A form with captcha_required=True rejects a submit carrying no
        (or an invalid) altcha solution with 400, before touching the DB."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["helpdesk:admin"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org_id = db.organizations.insert(
                name="Captcha Org",
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        slug = f"captcha-{uuid4().hex[:8]}"
        create_resp = await async_client.post(
            "/api/v1/intake-forms",
            json={
                "name": "Captcha Form",
                "slug": slug,
                "fields": [
                    {
                        "id": "email",
                        "label": "Email",
                        "type": "email",
                        "required": True,
                    }
                ],
                "organization_id": org_id,
                "is_public": True,
                "captcha_required": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create_resp.status_code in (200, 201), (
            await create_resp.get_data()
        ).decode()[:300]

        # No "altcha" key at all.
        no_altcha = await async_client.post(
            f"/api/v1/intake/{slug}/submit",
            json={"fields": {"email": "cust@example.com"}},
        )
        assert no_altcha.status_code == 400, (
            await no_altcha.get_data()
        ).decode()[:300]

        # Well-formed but bogus altcha solution.
        bad_altcha = await async_client.post(
            f"/api/v1/intake/{slug}/submit",
            json={
                "fields": {"email": "cust@example.com"},
                "altcha": {
                    "algorithm": "SHA-256",
                    "challenge": "deadbeef",
                    "salt": "salt",
                    "number": 1,
                    "signature": "not-a-real-signature",
                },
            },
        )
        assert bad_altcha.status_code == 400, (
            await bad_altcha.get_data()
        ).decode()[:300]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_public_get_private_form_returns_404(
        self, mock_get_user, async_client, generate_token
    ):
        """A form with is_public=False 404s on the public GET route — same
        response as a slug that doesn't exist at all."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["helpdesk:admin"])

        slug = f"private-{uuid4().hex[:8]}"
        create_resp = await async_client.post(
            "/api/v1/intake-forms",
            json={
                "name": "Private Form",
                "slug": slug,
                "fields": [
                    {
                        "id": "email",
                        "label": "Email",
                        "type": "email",
                        "required": True,
                    }
                ],
                "is_public": False,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create_resp.status_code in (200, 201), (
            await create_resp.get_data()
        ).decode()[:300]

        resp = await async_client.get(f"/api/v1/intake/{slug}")
        assert resp.status_code == 404, (await resp.get_data()).decode()[:300]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_public_submit_falls_back_to_tenant_root_org(
        self, mock_get_user, async_client, generate_token, app
    ):
        """A form created without organization_id still succeeds: resource_id
        is resolved from the tenant's own organizations rather than failing
        the issue's NOT NULL resource_id constraint."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["helpdesk:admin"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            db.organizations.insert(
                name="Root Org For Tenant 1",
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        slug = f"noorg-{uuid4().hex[:8]}"
        create_resp = await async_client.post(
            "/api/v1/intake-forms",
            json={
                "name": "No Org Form",
                "slug": slug,
                "fields": [
                    {
                        "id": "email",
                        "label": "Email",
                        "type": "email",
                        "required": True,
                    }
                ],
                "is_public": True,
                "captcha_required": False,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create_resp.status_code in (200, 201), (
            await create_resp.get_data()
        ).decode()[:300]

        resp = await async_client.post(
            f"/api/v1/intake/{slug}/submit",
            json={"fields": {"email": f"noorg-{uuid4().hex[:8]}@example.com"}},
        )
        assert resp.status_code in (200, 201), (await resp.get_data()).decode()[:300]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_public_submit_does_not_attach_to_existing_staff_identity(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Regression (security review): an email matching an EXISTING
        non-contact (staff) identity's username must NOT have the issue's
        reporter set to that staff identity. `upsert_customer_contact` keys
        on a namespaced `contact:{tenant_id}:{email}` username, never the
        bare email, so a real staff/admin account can never be impersonated
        by an anonymous public submitter."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["helpdesk:admin"])

        staff_email = f"staff-{uuid4().hex[:8]}@example.com"

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org_id = db.organizations.insert(
                name="Staff Collision Org",
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            staff_id = db.identities.insert(
                identity_type="human",
                username=staff_email,
                email=staff_email,
                tenant_id=1,
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

        slug = f"staffcollide-{uuid4().hex[:8]}"
        create_resp = await async_client.post(
            "/api/v1/intake-forms",
            json={
                "name": "Staff Collide Form",
                "slug": slug,
                "fields": [
                    {
                        "id": "email",
                        "label": "Email",
                        "type": "email",
                        "required": True,
                    }
                ],
                "organization_id": org_id,
                "is_public": True,
                "captcha_required": False,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create_resp.status_code in (200, 201), (
            await create_resp.get_data()
        ).decode()[:300]

        resp = await async_client.post(
            f"/api/v1/intake/{slug}/submit",
            json={"fields": {"email": staff_email}},
        )
        assert resp.status_code in (200, 201), (await resp.get_data()).decode()[:300]
        reference = json.loads(await resp.get_data())["reference"]

        async with app.app_context():
            db = current_app.db
            # Still exactly one identity with the bare-email username — no
            # duplicate/collision on the staff account's own username.
            assert db(db.identities.username == staff_email).count() == 1

            contact_row = (
                db(
                    (db.identities.email == staff_email)
                    & (db.identities.identity_type == "customer_contact")
                )
                .select()
                .first()
            )
            assert contact_row is not None
            assert contact_row.id != staff_id
            assert contact_row.username == f"contact:1:{staff_email}"

            issue_row = db(db.issues.village_id == reference).select().first()
            assert issue_row is not None
            assert issue_row.reporter_id == contact_row.id
            assert issue_row.reporter_id != staff_id

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_public_submit_same_email_different_tenants_no_collision(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Regression (security review): the same email submitted to two
        different tenants' public forms must succeed both times, creating
        two SEPARATE customer_contact identities — no cross-tenant username
        collision (globally-unique `identities.username`) and no 500."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org1_id = db.organizations.insert(
                name="Tenant1 Cross Org",
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            tenant2_id = db.tenants.insert(
                name="Cross Tenant Two",
                slug=f"cross-tenant-{uuid4().hex[:8]}",
                is_active=True,
            )
            db.commit()
            org2_id = db.organizations.insert(
                name="Tenant2 Cross Org",
                tenant_id=tenant2_id,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        token1 = generate_token(tenant_id=1, scopes=["helpdesk:admin"])
        token2 = generate_token(tenant_id=tenant2_id, scopes=["helpdesk:admin"])

        slug1 = f"cross1-{uuid4().hex[:8]}"
        slug2 = f"cross2-{uuid4().hex[:8]}"
        fields = [
            {"id": "email", "label": "Email", "type": "email", "required": True}
        ]

        r1 = await async_client.post(
            "/api/v1/intake-forms",
            json={
                "name": "T1 Cross Form",
                "slug": slug1,
                "fields": fields,
                "organization_id": org1_id,
                "is_public": True,
                "captcha_required": False,
            },
            headers={"Authorization": f"Bearer {token1}"},
        )
        assert r1.status_code in (200, 201), (await r1.get_data()).decode()[:300]

        r2 = await async_client.post(
            "/api/v1/intake-forms",
            json={
                "name": "T2 Cross Form",
                "slug": slug2,
                "fields": fields,
                "organization_id": org2_id,
                "is_public": True,
                "captcha_required": False,
            },
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert r2.status_code in (200, 201), (await r2.get_data()).decode()[:300]

        shared_email = f"shared-{uuid4().hex[:8]}@example.com"

        submit1 = await async_client.post(
            f"/api/v1/intake/{slug1}/submit",
            json={"fields": {"email": shared_email}},
        )
        assert submit1.status_code in (200, 201), (
            await submit1.get_data()
        ).decode()[:300]

        submit2 = await async_client.post(
            f"/api/v1/intake/{slug2}/submit",
            json={"fields": {"email": shared_email}},
        )
        assert submit2.status_code in (200, 201), (
            await submit2.get_data()
        ).decode()[:300]

        async with app.app_context():
            db = current_app.db
            contacts = db(
                (db.identities.email == shared_email)
                & (db.identities.identity_type == "customer_contact")
            ).select()
            assert len(contacts) == 2
            assert {c.tenant_id for c in contacts} == {1, tenant2_id}
            assert {c.username for c in contacts} == {
                f"contact:1:{shared_email}",
                f"contact:{tenant2_id}:{shared_email}",
            }

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_public_submit_non_object_body_returns_400(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Regression (security review): a syntactically valid JSON body
        that isn't an object (bare int/list/string) must 400, not 500 —
        previously `.get()` on a non-dict raised an unhandled AttributeError."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["helpdesk:admin"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org_id = db.organizations.insert(
                name="Nonobject Body Org",
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        slug = f"nonobj-{uuid4().hex[:8]}"
        create_resp = await async_client.post(
            "/api/v1/intake-forms",
            json={
                "name": "Nonobject Body Form",
                "slug": slug,
                "fields": [
                    {
                        "id": "email",
                        "label": "Email",
                        "type": "email",
                        "required": True,
                    }
                ],
                "organization_id": org_id,
                "is_public": True,
                "captcha_required": False,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create_resp.status_code in (200, 201), (
            await create_resp.get_data()
        ).decode()[:300]

        for bad_body in (42, [1, 2], "just a string"):
            resp = await async_client.post(
                f"/api/v1/intake/{slug}/submit", json=bad_body
            )
            assert resp.status_code == 400, (
                f"body={bad_body!r} -> {resp.status_code} "
                f"{(await resp.get_data()).decode()[:200]}"
            )
