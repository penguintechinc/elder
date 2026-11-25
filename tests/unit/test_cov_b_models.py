"""Unit tests for Elder model, pydantic and marshmallow schema modules.

Covers validators (valid and invalid inputs), ORM helper methods on transient
instances (no database), and small pure helpers.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

NOW = datetime(2026, 1, 2, tzinfo=UTC)


# ---------------------------------------------------------------- marshmallow


def test_organization_schemas_validate():
    """Create/update schemas load valid data and reject bad type/name."""
    from marshmallow import ValidationError as MmError

    from apps.api.schemas.organization import (
        OrganizationCreateSchema,
        OrganizationUpdateSchema,
    )

    ok = OrganizationCreateSchema().load({"name": "Acme", "tenant_id": 3})
    assert ok["organization_type"] == "organization"
    with pytest.raises(MmError):
        OrganizationCreateSchema().load({"name": "", "tenant_id": 1})
    with pytest.raises(MmError):
        OrganizationCreateSchema().load({"name": "x"})
    with pytest.raises(MmError):
        OrganizationCreateSchema().load(
            {"name": "x", "tenant_id": 1, "organization_type": "bogus"}
        )
    assert OrganizationUpdateSchema().load({"organization_type": "team"}) == {
        "organization_type": "team"
    }
    with pytest.raises(MmError):
        OrganizationUpdateSchema().load({"organization_type": "nope"})


# ------------------------------------------------------------ pydantic common


def test_pagination_params():
    """Pagination params default, strip sort_by and reject bad values."""
    from apps.api.models.pydantic.common import PaginationParams

    p = PaginationParams()
    assert (p.page, p.per_page, p.sort_order, p.sort_by) == (1, 20, "asc", None)
    assert PaginationParams(sort_by="  name ").sort_by == "name"
    assert PaginationParams(sort_by=None).sort_by is None
    with pytest.raises(ValidationError):
        PaginationParams(sort_by="   ")
    with pytest.raises(ValidationError):
        PaginationParams(per_page=101)
    with pytest.raises(ValidationError):
        PaginationParams(sort_order="up")
    with pytest.raises(ValidationError):
        PaginationParams(bogus=1)


def test_paginated_response_pages_must_match():
    """The pages count must agree with total and per_page."""
    from apps.api.models.pydantic.common import PaginatedResponse

    ok = PaginatedResponse[int](items=[1], total=41, page=1, per_page=20, pages=3)
    assert ok.pages == 3
    empty = PaginatedResponse[int](items=[], total=0, page=1, per_page=20, pages=0)
    assert empty.pages == 0
    with pytest.raises(ValidationError):
        PaginatedResponse[int](items=[], total=41, page=1, per_page=20, pages=9)


def test_bulk_operation_result():
    """Errors must match the failed count."""
    from apps.api.models.pydantic.common import BulkOperationResult

    assert BulkOperationResult(succeeded=3, failed=0).errors is None
    assert BulkOperationResult(succeeded=1, failed=1, errors=[{"i": 1}]).failed == 1
    with pytest.raises(ValidationError):
        BulkOperationResult(succeeded=1, failed=2, errors=[{"i": 1}])
    with pytest.raises(ValidationError):
        BulkOperationResult(succeeded=1, failed=1, errors=None)


def test_error_response_strips_and_rejects_blank():
    """ErrorResponse trims whitespace and rejects blank values."""
    from apps.api.models.pydantic.common import ErrorResponse

    e = ErrorResponse(error=" BAD ", message=" nope ")
    assert (e.error, e.message) == ("BAD", "nope")
    with pytest.raises(ValidationError):
        ErrorResponse(error=" ", message="m")
    with pytest.raises(ValidationError):
        ErrorResponse(error="e", message=" ")


# ----------------------------------------------------- pydantic dependency etc.


def test_dependency_models():
    """Dependency DTO and request models validate ids and reject unknown keys."""
    from apps.api.models.pydantic.dependency import (
        CreateDependencyRequest,
        DependencyDTO,
        UpdateDependencyRequest,
    )

    dto = DependencyDTO(
        id=1,
        tenant_id=1,
        source_type="entity",
        source_id=1,
        target_type="entity",
        target_id=2,
        dependency_type="depends_on",
        created_at=NOW,
        updated_at=NOW,
    )
    assert dto.metadata is None
    req = CreateDependencyRequest(
        source_type="a", source_id=1, target_type="b", target_id=2, dependency_type="c"
    )
    assert req.metadata is None
    with pytest.raises(ValidationError):
        CreateDependencyRequest(
            source_type="a",
            source_id=0,
            target_type="b",
            target_id=2,
            dependency_type="c",
        )
    assert UpdateDependencyRequest().model_dump(exclude_none=True) == {}
    with pytest.raises(ValidationError):
        UpdateDependencyRequest(target_id=0)


def test_group_models():
    """Group request models validate ids, owners and bulk approvals."""
    from apps.api.models.pydantic.group import (
        BulkApproveRequestsRequest,
        UpdateGroupRequest,
    )

    assert UpdateGroupRequest(owner_identity_id=4).owner_identity_id == 4
    assert BulkApproveRequestsRequest(request_ids=[1, 2]).request_ids == [1, 2]
    with pytest.raises(ValidationError):
        BulkApproveRequestsRequest(request_ids=[1, -2])


def test_organization_request_name_validation():
    """Organization create/update requests reject whitespace-only names."""
    from apps.api.models.pydantic.organization import (
        CreateOrganizationRequest,
        UpdateOrganizationRequest,
    )

    assert UpdateOrganizationRequest().name is None
    assert UpdateOrganizationRequest(name="ok").name == "ok"
    with pytest.raises(ValidationError):
        UpdateOrganizationRequest(name="  ")
    with pytest.raises(ValidationError):
        CreateOrganizationRequest(name="  ", organization_type="team")


def test_identity_request_password_rules():
    """Local auth demands a password; other providers do not."""
    from apps.api.models.pydantic.identity import CreateIdentityRequest

    base = {"username": "u", "identity_type": "human"}
    with pytest.raises(ValidationError):
        CreateIdentityRequest(auth_provider="local", **base)
    ok = CreateIdentityRequest(auth_provider="local", password="Pw-Value-123", **base)
    assert ok.password is not None
    assert CreateIdentityRequest(auth_provider="saml", **base).password is None


def test_idp_request_requires_https():
    """IdP URLs must be https; None passes through."""
    from apps.api.models.pydantic.idp import (
        CreateIdPConfigRequest,
        UpdateIdPConfigRequest,
    )

    assert CreateIdPConfigRequest(name="idp", sso_url=None).sso_url is None
    ok = CreateIdPConfigRequest(name="idp", sso_url="https://idp.example.test/sso")
    assert ok.sso_url
    with pytest.raises(ValidationError):
        CreateIdPConfigRequest(name="idp", sso_url="http://idp.example.test/sso")
    upd = UpdateIdPConfigRequest(is_active=False, oidc_client_secret="s3cret-value")
    changes = upd.to_changes()
    assert changes["is_active"] is False
    assert changes["oidc_client_secret"] == "s3cret-value"


def test_sbom_models_validators():
    """SBOM models accept known enum values and reject unknown ones."""
    from apps.api.models.pydantic import sbom

    ok = sbom.CreateSBOMScanRequest(
        parent_type="service", parent_id=1, scan_type="manifest"
    )
    assert ok.scan_type == "manifest"
    with pytest.raises(ValidationError):
        sbom.CreateSBOMScanRequest(parent_type="service", parent_id=1, scan_type="zzz")

    vuln = dict(id=1, tenant_id=1, cve_id="CVE-1", severity="HIGH", created_at=NOW)
    assert sbom.VulnerabilityDTO(**vuln).severity == "high"
    with pytest.raises(ValidationError):
        sbom.VulnerabilityDTO(**{**vuln, "severity": "weird"})

    cv = dict(
        id=1,
        tenant_id=1,
        component_id=1,
        vulnerability_id=1,
        status="open",
        created_at=NOW,
    )
    assert sbom.ComponentVulnerabilityDTO(**cv).status == "open"
    with pytest.raises(ValidationError):
        sbom.ComponentVulnerabilityDTO(**{**cv, "status": "bad"})

    lp = dict(id=1, tenant_id=1, name="p", action="warn", created_at=NOW)
    assert sbom.LicensePolicyDTO(**lp).action == "warn"
    with pytest.raises(ValidationError):
        sbom.LicensePolicyDTO(**{**lp, "action": "bad"})

    assert (
        sbom.CreateLicensePolicyRequest(
            name="p", organization_id=1, action="block"
        ).action
        == "block"
    )
    with pytest.raises(ValidationError):
        sbom.CreateLicensePolicyRequest(name="p", organization_id=1, action="bad")

    assert sbom.UpdateLicensePolicyRequest().action is None
    assert sbom.UpdateLicensePolicyRequest(action="audit").action == "audit"
    with pytest.raises(ValidationError):
        sbom.UpdateLicensePolicyRequest(action="bad")

    up = dict(parent_id=1, file_content="{}", filename="a.json")
    assert sbom.UploadSBOMRequest(parent_type="software", **up).parent_id == 1
    with pytest.raises(ValidationError):
        sbom.UploadSBOMRequest(parent_type="entity", **up)


def test_vulnerability_models_validators():
    """Vulnerability request models validate CVE ids, scores and status."""
    from apps.api.models.pydantic import vulnerability as v

    create = {"cve_id": " CVE-2026-1 ", "severity": "high"}
    req = v.CreateVulnerabilityRequest(**create)
    assert req.cve_id == "CVE-2026-1"
    with pytest.raises(ValidationError):
        v.CreateVulnerabilityRequest(cve_id="  ", severity="high")
    with pytest.raises(ValidationError):
        v.CreateVulnerabilityRequest(cve_id="CVE-1", severity="high", cvss_score=11)
    assert v.UpdateComponentVulnerabilityRequest().status is None
    assert v.UpdateComponentVulnerabilityRequest(status="open").status == "open"
    with pytest.raises(ValidationError):
        v.UpdateComponentVulnerabilityRequest(status="bogus")


def test_vulnerability_validator_functions_directly():
    """Call the status validator's body for non-model-bound branches."""
    from apps.api.models.pydantic import vulnerability as v

    cls = v.UpdateComponentVulnerabilityRequest
    dec = cls.__pydantic_decorators__.field_validators["status_valid"].bind_to_cls(cls)
    assert dec.func(None) is None
    assert dec.func("open") == "open"
    with pytest.raises(ValueError):
        dec.func("nope")


# ------------------------------------------------------------- ORM / helpers


def test_orm_reprs_and_helpers():
    """ORM instances expose reprs and membership helpers without a database."""
    from apps.api.models.identity import (
        AuthProvider,
        Identity,
        IdentityGroup,
        IdentityGroupMembership,
        IdentityType,
    )
    from apps.api.models.rbac import (
        Permission,
        Role,
        RolePermission,
        RoleScope,
        UserRole,
    )

    ident = Identity(
        id=5,
        username="alice",
        full_name=None,
        identity_type=IdentityType.HUMAN,
        auth_provider=AuthProvider.LOCAL,
    )
    assert ident.display_name == "alice"
    ident.full_name = "Alice A"
    assert ident.display_name == "Alice A"
    assert "alice" in repr(ident)

    group = IdentityGroup(id=9, name="ops")
    assert "ops" in repr(group)
    membership = group.add_member(ident)
    assert isinstance(membership, IdentityGroupMembership)
    membership.identity_id = ident.id
    membership.group_id = group.id
    assert "identity_id=5" in repr(membership)
    assert ident.get_groups() == [group]
    assert ident.is_member_of(group) is True
    assert ident.is_member_of(IdentityGroup(id=77, name="other")) is False
    assert group.get_members() == [ident]
    assert group.remove_member(ident) is True
    assert group.remove_member(ident) is False

    role = Role(id=1, name="editor")
    perm = Permission(id=2, name="entities:write")
    rp = RolePermission(role_id=1, permission_id=2)
    rp.permission = perm
    role.permissions.append(rp)
    assert role.has_permission("entities:write") is True
    assert role.has_permission("nope") is False
    assert role.get_permission_names() == ["entities:write"]
    assert "editor" in repr(role) and "entities:write" in repr(perm)
    assert "role_id=1" in repr(rp)

    glob = UserRole(identity_id=1, role_id=1, scope=RoleScope.GLOBAL)
    org = UserRole(
        identity_id=1, role_id=1, scope=RoleScope.ORGANIZATION, organization_id=4
    )
    assert glob.is_global() and glob.applies_to_organization(99)
    assert org.is_org_scoped() and org.applies_to_organization(4)
    assert not org.applies_to_organization(5)
    assert "org_id=4" in repr(org)


def test_audit_and_tenant_module_repr():
    """AuditLog and TenantModule reprs include their key fields."""
    from apps.api.models.audit import AuditLog
    from apps.api.models.tenant_modules import TenantModule

    assert "TenantModule" in repr(
        TenantModule(tenant_id=1, module_name="m", enabled=True)
    )
    assert "AuditLog" in repr(
        AuditLog(id=1, action_name="login", resource_type="identity", resource_id=2)
    )


def test_to_dict_excludes_and_formats_datetimes():
    """to_dict ISO-formats datetimes and honours exclusions."""
    from apps.api.models.base import to_dict
    from apps.api.models.tenant_modules import TenantModule

    obj = TenantModule(tenant_id=1, module_name="m", enabled=True, created_at=NOW)
    out = to_dict(obj)
    assert out["created_at"] == NOW.isoformat()
    assert "module_name" not in to_dict(obj, exclude=["module_name"])


def test_entity_type_helpers():
    """Entity type helpers list, validate and template sub-types."""
    from apps.api.models import entity_types as et

    kinds = et.get_all_entity_types()
    assert kinds and all(et.is_valid_entity_type(k) for k in kinds)
    assert not et.is_valid_entity_type("nonsense")
    assert et.get_subtypes_for_type("nonsense") == []
    first = kinds[0]
    subs = et.get_subtypes_for_type(first)
    if subs:
        assert et.is_valid_subtype(first, subs[0])
    assert not et.is_valid_subtype(first, "nope")
    assert et.get_default_metadata_for_subtype("nonsense", "x") == {}
    for kind in kinds:
        for sub in et.get_subtypes_for_type(kind):
            assert isinstance(et.get_default_metadata_for_subtype(kind, sub), dict)


def test_dataclass_helpers():
    """from_pydal_row(s) filter unknown fields and handle None."""
    from dataclasses import dataclass

    from apps.api.models.dataclasses import (
        from_pydal_row,
        from_pydal_rows,
        to_dict,
    )

    @dataclass
    class Thing:
        """Tiny DTO for the helper tests."""

        a: int
        b: str = "x"

    class Row:
        """Row double exposing as_dict()."""

        def __init__(self, d):
            """Store the dict."""
            self.d = d

        def as_dict(self):
            """Return the stored dict."""
            return self.d

    assert from_pydal_row(None, Thing) is None
    assert from_pydal_row(Row({"a": 1, "zzz": 2}), Thing) == Thing(a=1)
    assert from_pydal_rows([Row({"a": 1}), Row({"a": 2, "b": "y"})], Thing)[1].b == "y"
    assert to_dict(Thing(a=3)) == {"a": 3, "b": "x"}
    assert from_pydal_row(Row({"a": 4}), dict) == {"a": 4}
    assert from_pydal_rows([Row({"q": 1})], dict) == [{"q": 1}]
