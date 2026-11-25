"""Shared constants and idempotent-seeding helpers for the Elder demo data.

Defines the single demo login (read by every seeder and documented in
``docs/demo-data.md`` / ``scripts/demo_login.example.env``), a get-or-create
helper that keeps re-runs idempotent, and the universal ``village_id`` rule
(every created object that has the column gets a unique one).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Demo login — defined ONCE here. Only reserved/non-routable demo domains.
# ---------------------------------------------------------------------------
DEFAULT_DEMO_ADMIN_EMAIL = "demo-admin@example.com"
# Local-dev-only default so screenshot tooling works with zero setup. It is
# refused when the process looks like production (see ``demo_admin_password``).
DEFAULT_DEMO_ADMIN_PASSWORD = "demo-local-only-1"  # noqa: S105 - documented dev default

DEMO_ADMIN_EMAIL = os.environ.get("DEMO_ADMIN_EMAIL", DEFAULT_DEMO_ADMIN_EMAIL)
DEMO_TENANT_SLUG = os.environ.get("DEMO_TENANT_SLUG", "demo-cloud-discovery")
DEMO_TENANT_NAME = os.environ.get("DEMO_TENANT_NAME", "Acme Corp")
DEMO_ORG_NAME = os.environ.get("DEMO_ORG_NAME", "Acme Corp")

# Only these domains may appear in seeded e-mail addresses (RFC 2606 / 6761).
RESERVED_EMAIL_SUFFIXES = (
    "@example.com",
    "@example.org",
    ".example.com",
    ".local",
    ".invalid",
)

_PROD_ENVS = {"production", "prod"}


def demo_admin_password() -> str:
    """Return the demo admin password (env override, dev default otherwise).

    Refuses the committed dev default when the process environment indicates
    production, so the well-known password can never protect a real deployment.
    """
    env_value = os.environ.get("DEMO_ADMIN_PASSWORD")
    if env_value:
        return env_value
    for var in ("ELDER_ENV", "QUART_ENV", "FLASK_ENV", "APP_ENV"):
        if os.environ.get(var, "").lower() in _PROD_ENVS:
            raise RuntimeError(
                "DEMO_ADMIN_PASSWORD must be set explicitly when "
                f"{var} indicates production; the dev default is local-only."
            )
    return DEFAULT_DEMO_ADMIN_PASSWORD


DEMO_ADMIN_PASSWORD = demo_admin_password()


def is_reserved_email(address: str) -> bool:
    """Return True when ``address`` ends with a reserved demo domain suffix."""
    return address.lower().endswith(RESERVED_EMAIL_SUFFIXES)


# ---------------------------------------------------------------------------
# Context + statistics
# ---------------------------------------------------------------------------
@dataclass
class SeedContext:
    """Everything a feature seeder needs: DB, Redis, tenant scope and stats."""

    db: Any
    redis_client: Any
    tenant_id: int
    org_id: int = 0
    admin_id: int = 0
    stats: dict[str, list[int]] = field(default_factory=dict)
    ids: dict[str, int] = field(default_factory=dict)

    def record(self, feature: str, created: bool) -> None:
        """Count one row as created or already-existing for ``feature``."""
        bucket = self.stats.setdefault(feature, [0, 0])
        bucket[0 if created else 1] += 1

    def created_total(self) -> int:
        """Return how many rows this run created across all features."""
        return sum(v[0] for v in self.stats.values())


def now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def get_or_create(
    ctx: SeedContext,
    table: str,
    lookup: dict[str, Any],
    values: dict[str, Any] | None = None,
    *,
    feature: str | None = None,
) -> int:
    """Return the id of the row matching ``lookup``, inserting it if missing.

    ``lookup`` is the natural key (it is also inserted); ``values`` are extra
    columns used only on insert. The tenant_id column is added automatically
    when the table has one and the caller did not supply it. ``village_id`` is
    minted by the insert hook installed with :func:`install_village_autofill`.
    """
    db = ctx.db
    tbl = getattr(db, table)
    query = None
    for column, value in lookup.items():
        cond = getattr(tbl, column) == value
        query = cond if query is None else (query & cond)
    existing = db(query).select().first()
    if existing is not None:
        ctx.record(feature or table, False)
        return int(existing.id)

    payload = {**lookup, **(values or {})}
    cols = _columns(tbl)
    if "tenant_id" in cols and "tenant_id" not in payload:
        payload["tenant_id"] = ctx.tenant_id
    for ts in ("created_at", "updated_at"):  # not every table has both
        if ts in payload and ts not in cols:
            del payload[ts]
    row_id = tbl.insert(**payload)
    db.commit()
    ctx.record(feature or table, True)
    return int(row_id)


def _columns(tbl: Any) -> set[str]:
    """Return the column names of a penguin-dal table proxy."""
    sa_table = getattr(tbl, "table", tbl)
    return {c.name for c in sa_table.columns}


# ---------------------------------------------------------------------------
# Universal village_id handling
# ---------------------------------------------------------------------------
@contextmanager
def village_autofill(redis_client: Any, default_tenant_id: int) -> Iterator[None]:
    """Patch ``TableProxy.insert`` so every seeded row is well-formed.

    Rows inserted without an explicit village_id (including those written by
    the legacy seeders and the access-review service) get one minted via the
    same generator the API uses, so the universal "every object has a unique
    village_id" rule holds for the whole demo data set. The patch also drops
    created_at/updated_at keys for tables that lack them (the access-review
    service passes updated_at to ``access_review_items``, which has no such
    column). The class is patched because penguin-dal builds a fresh proxy on
    every ``db.<table>`` access; the original is restored on exit.
    """
    from penguin_dal.table_proxy import TableProxy

    from shared.utils.village_id import generate_village_id

    original = TableProxy.insert

    def insert(self: Any, **kwargs: Any) -> Any:
        """Insert a row, minting village_id and trimming absent timestamps."""
        columns = _columns(self)
        for ts in ("created_at", "updated_at"):
            if ts in kwargs and ts not in columns:
                del kwargs[ts]
        if "village_id" in columns and not kwargs.get("village_id"):
            tid = kwargs.get("tenant_id") if "tenant_id" in columns else None
            kwargs["village_id"] = generate_village_id(
                int(tid or default_tenant_id), redis_client
            )
        return original(self, **kwargs)

    TableProxy.insert = insert  # type: ignore[method-assign]
    try:
        yield
    finally:
        TableProxy.insert = original  # type: ignore[method-assign]


def backfill_village_ids(db: Any, redis_client: Any, default_tenant_id: int) -> int:
    """Mint village_ids for pre-existing rows that lack one; return the count.

    Safety net for rows written by earlier versions of the seeders. Uses raw
    SQL through the DAL engine because it must touch every table generically.
    """
    from sqlalchemy import text

    from shared.utils.village_id import generate_village_id

    fixed = 0
    per_table: dict[str, int] = {}
    with db.engine.begin() as conn:
        for name, proxy in db.tables.items():
            cols = _columns(proxy)
            if "village_id" not in cols or "id" not in cols:
                continue
            rows = conn.execute(
                # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text -- seed tooling; table names come from the ORM metadata, values are bound
                text(f'SELECT id FROM "{name}" WHERE village_id IS NULL')  # noqa: S608
            ).fetchall()
            for (row_id,) in rows:
                tid = default_tenant_id
                conn.execute(
                    # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text -- seed tooling; table names come from the ORM metadata, values are bound
                    text(f'UPDATE "{name}" SET village_id = :v WHERE id = :i'),  # noqa: S608
                    {"v": generate_village_id(tid, redis_client), "i": row_id},
                )
                fixed += 1
                per_table[name] = per_table.get(name, 0) + 1
    if per_table:
        log(
            "  village_id backfill by table: "
            + ", ".join(f"{k}={v}" for k, v in per_table.items())
        )
    return fixed


def log(msg: str) -> None:
    """Print a progress line to stdout (kept tiny; the seeders are scripts)."""
    print(msg, file=sys.stdout)
