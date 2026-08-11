"""Phase 1: every module belongs to exactly one valid group, per the locked mapping."""

from apps.api.modules import MODULES

VALID_GROUPS = {"core", "crm", "workflow", "kb"}
EXPECTED = {
    "infrastructure": "core",
    "ipam": "core",
    "discovery": "core",
    "sbom": "core",
    "secrets": "core",
    "services_oncall": "core",
    "access_reviews": "core",
    "webhooks_alerting": "core",
    "helpdesk": "crm",
    "issues": "workflow",
    "streams": "workflow",
    "flows": "workflow",
    "documents": "kb",
    "pages": "kb",
    "diagrams": "kb",
}


def test_every_module_has_a_valid_group():
    for m in MODULES:
        assert m.group in VALID_GROUPS, f"{m.name} has invalid group {m.group!r}"


def test_group_mapping_matches_spec():
    got = {m.name: m.group for m in MODULES}
    assert got == EXPECTED
