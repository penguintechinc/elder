"""Canonical list of tables covered by row-level security (RLS).

Single source of truth for the runtime posture check
(``shared.database.rls.check_rls_posture``). Migration ``043_rls_tenant_isolation``
keeps its own frozen copy (migrations must not change when this file does);
``tests/unit/test_rls_table_registry.py`` fails if the two ever diverge.
"""

POLICY_NAME = "elder_tenant_isolation"

# Every table with a direct ``tenant_id`` column that migration 043 protects.
RLS_TABLES: tuple[str, ...] = (
    "access_review_assignments",
    "access_review_items",
    "access_reviews",
    "certificates",
    "component_vulnerabilities",
    "data_stores",
    "dependencies",
    "dg_collaboration_sessions",
    "dg_collection_items",
    "dg_collection_shares",
    "dg_collections",
    "dg_comment_replies",
    "dg_comments",
    "dg_connectors",
    "dg_diagram_versions",
    "dg_diagrams",
    "dg_library_shapes",
    "dg_node_metadata",
    "dg_shape_libraries",
    "dg_shape_metadata",
    "dg_shapes",
    "dg_share_analytics",
    "dg_shares",
    "dg_storage_providers",
    "dg_templates",
    "doc_collections",
    "doc_documents",
    "dsar_requests",
    "group_access_approvals",
    "group_access_requests",
    "hd_canned_responses",
    "hd_sla_policies",
    "hd_teams",
    "hd_tickets",
    "iceflows",
    "iceflows_approvals",
    "iceflows_credentials",
    "iceflows_executions",
    "iceflows_promotions",
    "iceflows_stage_approvers",
    "iceflows_stage_calls",
    "iceflows_stage_reviews",
    "iceflows_stage_tests",
    "iceflows_stages",
    "iceflows_webhooks",
    "ipam_addresses",
    "ipam_prefixes",
    "ipam_vlans",
    "issue_comments",
    "issues",
    "license_policies",
    "milestones",
    "on_call_rotations",
    "organizations",
    "pg_pages",
    "projects",
    "references",
    "sbom_components",
    "sbom_scan_schedules",
    "sbom_scans",
    "services",
    "software",
    "stream_approval_gates",
    "stream_custom_modules",
    "stream_edges",
    "stream_editor_locks",
    "stream_execution_approvals",
    "stream_executions",
    "stream_form_submissions",
    "stream_forms",
    "stream_node_executions",
    "stream_node_metadata",
    "stream_nodes",
    "stream_playbooks",
    "stream_schedules",
    "stream_shares",
    "stream_templates",
    "stream_versions",
    "stream_webhooks",
    "tenant_modules",
    "vulnerabilities",
    "webhooks",
)

# tenant_id tables intentionally not covered yet (tranche 2): their pre-auth
# lookups (login, SSO, SCIM) need ``system_scope(AUTH_BOOTSTRAP)`` wrappers first.
DEFERRED_TABLES: tuple[str, ...] = (
    "identities",
    "idp_configurations",
    "portal_users",
    "scim_configurations",
)

# Tables an alembic-built database legitimately does not have: no migration ever
# created them, only the models' create_all() does (so migration 043 cannot
# protect them). The startup posture check still flags them once they exist.
ABSENT_OK: tuple[str, ...] = ("references",)

# Alembic-built databases carry tenant_id here, the model does not: groups are
# globally named and the create route sets no tenant (documented schema gap), so
# a policy would break group creation. Needs a data-model decision first.
MODEL_LACKS_TENANT_ID: tuple[str, ...] = ("identity_groups",)
