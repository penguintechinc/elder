"""Every module belongs to exactly one WIRED group, per the locked mapping.

Elder's five module groups are the WIRED pillars: Workstreams, Issues,
Relationships, Entities, Documents. Grouping is presentation/deployment only —
no group is tier-locked.
"""

from apps.api.modules import MODULES

VALID_GROUPS = {"workstreams", "issues", "relationships", "entities", "documents"}
EXPECTED = {
    # W — Workstreams: things that run on a trigger
    "streams": "workstreams",
    "flows": "workstreams",
    "webhooks_alerting": "workstreams",
    # I — Issues: things that need doing
    "issues": "issues",
    "helpdesk": "issues",
    # R — Relationships: edges, discovered and attested
    "discovery": "relationships",
    "access_reviews": "relationships",
    # E — Entities: things you inventory
    "infrastructure": "entities",
    "ipam": "entities",
    "sbom": "entities",
    "services_oncall": "entities",
    "secrets": "entities",
    # D — Documents: things you write down
    "documents": "documents",
    "pages": "documents",
    "diagrams": "documents",
}


def test_every_module_has_a_valid_group():
    for m in MODULES:
        assert m.group in VALID_GROUPS, f"{m.name} has invalid group {m.group!r}"


def test_group_mapping_matches_spec():
    got = {m.name: m.group for m in MODULES}
    assert got == EXPECTED


def test_every_wired_group_is_populated():
    """WIRED is five pillars — an empty group would break the nav and the story."""
    used = {m.group for m in MODULES}
    assert used == VALID_GROUPS
