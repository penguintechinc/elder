"""Village ID generator for Elder application.

Generates unique hierarchical 64-bit hexadecimal identifiers for all trackable resources.
Format: TTTT-OOOO-IIIIIIII (tenant-org-item)
- Tenant: 16-bit (4 hex chars)
- Organization: 16-bit (4 hex chars)
- Item: 32-bit (8 hex chars)
"""

# flake8: noqa: E501


import secrets


def generate_segment(bits: int = 16) -> str:
    """Generate a random hex segment.

    Args:
        bits: Number of bits (16 for 4 chars, 32 for 8 chars)

    Returns:
        str: Lowercase hex string
    """
    return secrets.token_hex(bits // 8)


def generate_tenant_village_id() -> str:
    """Generate a Village ID for a tenant.

    Format: TTTT-0000-00000000

    Returns:
        str: 18-character Village ID with dashes
    """
    tenant_segment = generate_segment(16)
    return f"{tenant_segment}-0000-00000000"


def generate_org_village_id(tenant_segment: str) -> str:
    """Generate a Village ID for an organization.

    Format: TTTT-OOOO-00000000

    Args:
        tenant_segment: The 4-char tenant segment from parent tenant

    Returns:
        str: 18-character Village ID with dashes
    """
    org_segment = generate_segment(16)
    return f"{tenant_segment}-{org_segment}-00000000"


def generate_item_village_id(tenant_segment: str, org_segment: str) -> str:
    """Generate a Village ID for an item (resource, entity, element).

    Format: TTTT-OOOO-IIIIIIII

    Args:
        tenant_segment: The 4-char tenant segment
        org_segment: The 4-char organization segment

    Returns:
        str: 18-character Village ID with dashes
    """
    item_segment = generate_segment(32)
    return f"{tenant_segment}-{org_segment}-{item_segment}"


def parse_village_id(village_id: str) -> dict:
    """Parse a Village ID into its components.

    Args:
        village_id: The full Village ID (e.g., "a1b2-c3d4-e5f67890")

    Returns:
        dict: {'tenant': str, 'org': str, 'item': str}
    """
    parts = village_id.split("-")
    if len(parts) != 3:
        raise ValueError(f"Invalid Village ID format: {village_id}")

    return {"tenant": parts[0], "org": parts[1], "item": parts[2]}


# Legacy function for backward compatibility during transition
def generate_village_id() -> str:
    """Generate a legacy flat Village ID.

    DEPRECATED: Use generate_tenant_village_id, generate_org_village_id,
    or generate_item_village_id instead.

    Returns:
        str: 18-character Village ID (uses 0000 for tenant/org)
    """
    return f"0000-0000-{generate_segment(32)}"
