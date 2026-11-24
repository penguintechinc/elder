"""Real-Postgres tests for the DSAR personal-data inventory (erasure + export).

compliance audit H2: erasure used to anonymise the identities row only --
audit IP/UA, API keys, memberships, preferences and authorship links all
survived -- and export covered identity/roles/groups only. These tests seed
one row per inventory entry for a subject (and a decoy subject in another
tenant), run the real PrivacyService against real Postgres, and prove per
entry that nothing personal is left, the decoy is untouched, the audit trail
survives pseudonymised, and the export carries every category.

The inventory-drift tests need no database. The rest run in a private,
throwaway database created next to DATABASE_URL (dropped afterwards) so the
seeded rows can never pollute the shared unit-test database.
"""

from __future__ import annotations

import importlib
import json
import os
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
import sqlalchemy as sa

from apps.api.models.base import Base
from apps.api.modules import CORE_MODELS, MODULES
from apps.api.services.privacy.inventory import (
    IDENTITY_INVENTORY,
    IDENTITY_PII_COLUMNS,
    LEGAL_BASIS,
    NOT_ERASED_SYNCHRONOUSLY,
    PORTAL_USER_INVENTORY,
    PORTAL_USER_PII_COLUMNS,
    Action,
    ConsentPurpose,
    ConsentState,
    InventoryEntry,
    LawfulBasis,
    ReceiptStatus,
    SubjectType,
)
from apps.api.services.privacy.service import (
    ANONYMIZED_EMAIL_DOMAIN,
    LastAdminError,
    LegalHoldError,
    PrivacyService,
)

# Distinctive, greppable personal data for the subject under erasure...
PII = {
    "username": "zz-subject-qx7",
    "email": "zz.subject@pii.example",
    "full_name": "Zedekiah Subjectson-Qx7",
    "ip": "203.0.113.77",
    "user_agent": "PIIAgent-qx7/9.9",
    "secret": "s3cr3t-hash-qx7",
    "external": "ext-qx7-id",
}
# ...and for a decoy subject in ANOTHER tenant that must never be touched or
# exported. Its e-mail deliberately equals the subject's, to prove free-text
# matching stays inside the subject's tenant.
DECOY = {
    "username": "yy-decoy-kp4",
    "email": PII["email"],
    "full_name": "Yolanda Decoyworth-Kp4",
    "ip": "198.51.100.44",
    "user_agent": "DecoyAgent-kp4/1.0",
}
DECOY_ONLY_MARKERS = [DECOY["username"], DECOY["full_name"], DECOY["ip"], "kp4"]


def _load_models() -> None:
    """Import every registry model module so Base.metadata is complete."""
    modules = set(CORE_MODELS)
    for manifest in MODULES:
        modules.update(manifest.models_import)
    for path in sorted(modules):
        importlib.import_module(path)


_load_models()
TABLES = Base.metadata.tables


def _entry_id(entry: InventoryEntry) -> str:
    """Readable pytest id for an inventory entry."""
    return f"{entry.table}.{entry.column}:{entry.action.name.lower()}"


ALL_ENTRIES = [(SubjectType.IDENTITY, e) for e in IDENTITY_INVENTORY] + [
    (SubjectType.PORTAL_USER, e) for e in PORTAL_USER_INVENTORY
]
ALL_IDS = [f"{st.name.lower()}:{_entry_id(e)}" for st, e in ALL_ENTRIES]


class TestInventoryMatchesSchema:
    """The inventory must describe the real schema -- no drift, no gaps."""

    @pytest.mark.parametrize(
        ("target", "inventory"),
        [("identities", IDENTITY_INVENTORY), ("portal_users", PORTAL_USER_INVENTORY)],
    )
    def test_every_foreign_key_to_a_subject_table_is_inventoried(
        self, target, inventory
    ):
        """A new FK to identities/portal_users without an entry fails here."""
        declared = {(e.table, e.column) for e in inventory}
        actual = {
            (table.name, column.name)
            for table in TABLES.values()
            for column in table.columns
            for fk in column.foreign_keys
            if fk.column.table.name == target
        }
        assert len(actual) >= len(inventory) - 3  # the check really ran
        assert actual - declared == set()

    @pytest.mark.parametrize(("subject_type", "entry"), ALL_ENTRIES, ids=ALL_IDS)
    def test_entry_references_real_columns(self, subject_type, entry):
        assert entry.table in TABLES
        columns = TABLES[entry.table].columns
        for name in (entry.column, *entry.scrub, *entry.export):
            assert name in columns, f"{entry.table}.{name} does not exist"
        assert entry.basis in LEGAL_BASIS

    @pytest.mark.parametrize(("subject_type", "entry"), ALL_ENTRIES, ids=ALL_IDS)
    def test_action_is_feasible_for_the_column(self, subject_type, entry):
        """UNLINK needs a nullable link; RETAIN must be justified, not default."""
        columns = TABLES[entry.table].columns
        if entry.action in (Action.UNLINK, Action.SCRUB_MATCH):
            assert columns[entry.column].nullable
            for name in entry.scrub:
                assert columns[name].nullable
        if entry.action is Action.RETAIN:
            assert entry.basis != "erasure"
        if entry.action is Action.SCRUB_MATCH:
            assert "tenant_id" in columns  # free-text match must be tenant-scoped

    def test_no_duplicate_entries(self):
        for inventory in (IDENTITY_INVENTORY, PORTAL_USER_INVENTORY):
            keys = [(e.table, e.column) for e in inventory]
            assert len(keys) == len(set(keys))

    def test_children_are_processed_before_their_parents(self):
        """A DELETE target referenced by another inventoried table must come later."""
        order = {e.table: i for i, e in enumerate(IDENTITY_INVENTORY)}
        deleted = {e.table for e in IDENTITY_INVENTORY if e.action is Action.DELETE}
        checked = 0
        for table in TABLES.values():
            for column in table.columns:
                for fk in column.foreign_keys:
                    parent = fk.column.table.name
                    if parent in deleted and table.name != parent:
                        checked += 1
                        assert table.name in order, (
                            f"{table.name} references deleted table {parent} "
                            "but is not inventoried"
                        )
                        assert order[table.name] < order[parent]
        assert checked >= 1

    def test_pii_column_lists_exist(self):
        for table, names in (
            ("identities", IDENTITY_PII_COLUMNS),
            ("portal_users", PORTAL_USER_PII_COLUMNS),
        ):
            for name in names:
                assert name in TABLES[table].columns

    def test_export_never_lists_credential_columns(self):
        forbidden = {"key_hash", "password_hash", "mfa_secret", "mfa_backup_codes"}
        for _, entry in ALL_ENTRIES:
            assert forbidden.isdisjoint(entry.export)


_DATABASE_URL = os.getenv("DATABASE_URL", "")
needs_postgres = pytest.mark.skipif(
    not _DATABASE_URL.startswith("postgres"),
    reason="DATABASE_URL is not a Postgres URL (mock-only unit run)",
)


class _Seeder:
    """Insert minimal valid rows for any table straight from model metadata.

    Required foreign keys are satisfied by creating (and reusing) one parent
    row per table, so a single inventory entry can be seeded without
    hand-written fixtures for ~70 tables.
    """

    def __init__(self, engine: sa.Engine, memo: dict[str, int]) -> None:
        """Bind to `engine`; `memo` pre-seeds the parent row id per table."""
        self.engine = engine
        self.memo = dict(memo)
        self.counter = 1000

    def _value(self, column: sa.Column) -> Any:
        """A unique, type-appropriate filler value for a required column."""
        self.counter += 1
        kind = column.type
        if isinstance(kind, sa.Boolean):
            return False
        if isinstance(kind, sa.Enum):
            return kind.enums[0]
        if isinstance(kind, (sa.Integer, sa.Numeric, sa.Float)):
            return self.counter
        if isinstance(kind, sa.DateTime):
            return datetime.now(UTC)
        if isinstance(kind, sa.Date):
            return date.today()
        if isinstance(kind, sa.JSON):
            return {}
        if isinstance(kind, sa.LargeBinary):
            return b"x"
        length = getattr(kind, "length", None) or 40
        return f"f{self.counter}-{uuid.uuid4().hex}"[:length]

    def insert(self, table_name: str, **overrides: Any) -> int:
        """Insert one row into `table_name` and return its primary key."""
        table = TABLES[table_name]
        values: dict[str, Any] = dict(overrides)
        for column in table.columns:
            if column.name in values:
                continue
            if column.primary_key and not column.foreign_keys:
                continue  # autoincrement surrogate key
            fks = list(column.foreign_keys)
            if column.nullable:
                continue
            if fks:
                parent = fks[0].column.table.name
                if parent not in self.memo:
                    self.memo[parent] = self.insert(parent)
                values[column.name] = self.memo[parent]
            elif column.server_default is None:
                values[column.name] = self._value(column)
        by_name = {column.name: column for column in table.columns}
        with self.engine.begin() as conn:
            result = conn.execute(
                table.insert().values({by_name[k]: v for k, v in values.items()})
            )
            return int(result.inserted_primary_key[0])


def _admin_url(url: str, database: str) -> str:
    """`url` re-pointed at `database` on the same server."""
    return sa.engine.make_url(url).set(database=database).render_as_string(False)


@pytest.fixture(scope="module")
def private_db():
    """A throwaway Postgres database with the full schema, dropped afterwards."""
    from penguin_dal import DAL

    name = f"elder_privacy_{uuid.uuid4().hex[:12]}"
    admin = sa.create_engine(
        _admin_url(_DATABASE_URL, "postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = _admin_url(_DATABASE_URL, name)
    engine = sa.create_engine(url)
    dal = None
    try:
        Base.metadata.create_all(engine)
        dal = DAL(url, pool_size=2, migrate=False)
        yield engine, dal
    finally:
        if dal is not None:
            dal.close()
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _seed_identity(seeder: _Seeder, tenant_id: int, data: dict[str, str]) -> int:
    """Insert an identities row carrying `data` as its personal data."""
    return seeder.insert(
        "identities",
        tenant_id=tenant_id,
        username=data["username"],
        email=data["email"],
        full_name=data["full_name"],
        password_hash=data.get("secret", "decoy-hash-kp4"),
        mfa_secret=data.get("secret", "decoy-hash-kp4"),
        mfa_enabled=True,
        external_id=data.get("external", "ext-kp4-id"),
        auth_provider_id=data.get("external", "ext-kp4-id"),
        metadata={"phone": "+1-555-0100-qx7" if data is PII else "+1-555-0199-kp4"},
        last_login_at="2026-01-01T00:00:00+00:00",
        is_active=True,
    )


def _seed_entry(
    seeder: _Seeder,
    entry: InventoryEntry,
    subject_id: int,
    data: dict[str, str],
) -> int:
    """Insert one row linked to `subject_id` through `entry`, PII in scrub columns."""
    overrides: dict[str, Any] = {}
    if entry.action is Action.SCRUB_MATCH:
        overrides[entry.column] = data["email"]
    else:
        overrides[entry.column] = subject_id
    for name in entry.scrub:
        overrides[name] = data["ip"] if "ip" in name else data["user_agent"]
    if "tenant_id" in TABLES[entry.table].columns:
        overrides["tenant_id"] = seeder.memo["tenants"]
    return seeder.insert(entry.table, **overrides)


@pytest.fixture(scope="module")
def world(private_db):
    """Seed subject + decoy across the whole inventory, export, then erase.

    Returns everything the per-entry assertions need. Export is captured
    BEFORE erasure (afterwards there is nothing personal left to export).
    """
    engine, dal = private_db
    base = _Seeder(engine, {})
    tenant_a = base.insert("tenants")
    tenant_b = base.insert("tenants")
    tenant_hold = base.insert("tenants", legal_hold=True)

    seed_a = _Seeder(engine, {"tenants": tenant_a})
    filler_a = seed_a.insert("identities", tenant_id=tenant_a, is_active=True)
    seed_a.memo["identities"] = filler_a
    subject = _seed_identity(seed_a, tenant_a, PII)

    seed_b = _Seeder(engine, {"tenants": tenant_b})
    filler_b = seed_b.insert("identities", tenant_id=tenant_b, is_active=True)
    seed_b.memo["identities"] = filler_b
    decoy = _seed_identity(seed_b, tenant_b, DECOY)

    seed_hold = _Seeder(engine, {"tenants": tenant_hold})
    held = seed_hold.insert(
        "identities",
        tenant_id=tenant_hold,
        email="held.user@pii.example",
        is_active=True,
    )

    rows: dict[tuple[str, str], int] = {}
    decoy_rows: dict[tuple[str, str], int] = {}
    for entry in IDENTITY_INVENTORY:
        key = (entry.table, entry.column)
        rows[key] = _seed_entry(seed_a, entry, subject, PII)
        decoy_rows[key] = _seed_entry(seed_b, entry, decoy, DECOY)

    # Audit trail: an event by the subject and an admin event ABOUT them.
    details = {
        "email": PII["email"],
        "note": f"login by {PII['username']}",
        "nested": [{"who": PII["full_name"], "kept": "resource-state"}],
    }
    own_audit = seed_a.insert(
        "audit_logs",
        identity_id=subject,
        action_name="login",
        ip_address=PII["ip"],
        user_agent=PII["user_agent"],
        details=details,
    )
    about_audit = seed_a.insert(
        "audit_logs",
        identity_id=filler_a,
        action_name="update",
        resource_type="identity",
        resource_id=subject,
        ip_address="192.0.2.10",
        details={"changed": {"email": PII["email"]}, "field_count": 1},
    )
    decoy_audit = seed_b.insert(
        "audit_logs",
        identity_id=decoy,
        action_name="login",
        ip_address=DECOY["ip"],
        user_agent=DECOY["user_agent"],
        details={"note": f"login by {DECOY['username']}"},
    )

    # Portal user sharing a numeric id with an existing identity.
    portal = seed_a.insert(
        "portal_users",
        tenant_id=tenant_a,
        email="portal.person.qx7@pii.example",
        full_name="Portia Portal-Qx7",
        password_hash=PII["secret"],
        mfa_secret=PII["secret"],
        mfa_backup_codes=["code-qx7"],
        is_active=True,
        email_verified=True,
    )
    seed_a.memo["portal_users"] = seed_a.insert(
        "portal_users", tenant_id=tenant_a, is_active=True, email_verified=True
    )
    # A second portal user's assignment that must survive the erasure.
    seed_a.insert("portal_user_org_assignments")
    portal_rows = {
        (e.table, e.column): seed_a.insert(e.table, **{e.column: portal})
        for e in PORTAL_USER_INVENTORY
    }

    PrivacyService.record_consent(
        dal, subject, tenant_a, ConsentPurpose.ANALYTICS, True
    )
    PrivacyService.record_consent(
        dal, subject, tenant_a, ConsentPurpose.ANALYTICS, False
    )
    PrivacyService.set_do_not_sell(dal, subject, tenant_a, True)
    PrivacyService.record_consent(dal, decoy, tenant_b, ConsentPurpose.MARKETING, True)

    export = PrivacyService.export_identity(dal, subject, tenant_a)
    portal_export = PrivacyService.export_identity(
        dal, portal, tenant_a, SubjectType.PORTAL_USER
    )

    result = PrivacyService.erase_identity(
        dal, subject, tenant_a, False, requested_by_id=subject
    )
    portal_result = PrivacyService.erase_identity(
        dal, portal, tenant_a, False, SubjectType.PORTAL_USER
    )
    return {
        "engine": engine,
        "dal": dal,
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "tenant_hold": tenant_hold,
        "subject": subject,
        "decoy": decoy,
        "held": held,
        "filler_a": filler_a,
        "portal": portal,
        "rows": rows,
        "decoy_rows": decoy_rows,
        "portal_rows": portal_rows,
        "own_audit": own_audit,
        "about_audit": about_audit,
        "decoy_audit": decoy_audit,
        "export": export,
        "portal_export": portal_export,
        "result": result,
        "portal_result": portal_result,
    }


def _fetch(engine: sa.Engine, table: str, row_id: int) -> dict[str, Any] | None:
    """Fetch one row by primary key as a plain dict (None if gone)."""
    tbl = TABLES[table]
    if "id" not in tbl.columns:  # composite-PK table: use _linked_count
        return None
    with engine.connect() as conn:
        row = conn.execute(sa.select(tbl).where(tbl.c.id == row_id)).mappings().first()
    return dict(row) if row else None


def _linked_count(engine: sa.Engine, table: str, column: str, value: Any) -> int:
    """Number of rows of `table` whose `column` equals `value`."""
    tbl = TABLES[table]
    with engine.connect() as conn:
        return int(
            conn.execute(
                sa.select(sa.func.count())
                .select_from(tbl)
                .where(tbl.c[column] == value)
            ).scalar()
        )


def _count_text_hits(engine: sa.Engine, table: str, needle: str) -> int:
    """Rows of `table` whose ENTIRE row, cast to text, contains `needle`."""
    with engine.connect() as conn:
        return int(
            conn.execute(
                sa.text(
                    f'SELECT count(*) FROM "{table}" AS t WHERE t::text ILIKE :needle'
                ),
                {"needle": f"%{needle}%"},
            ).scalar()
        )


SUBJECT_MARKERS = [
    PII["username"],
    PII["full_name"],
    PII["ip"],
    PII["user_agent"],
    PII["secret"],
    PII["external"],
    "qx7",
]
SCANNED_TABLES = sorted(
    {e.table for _, e in ALL_ENTRIES}
    | {"identities", "portal_users", "audit_logs", "privacy_erasure_receipts"}
)


@needs_postgres
class TestErasureAcrossInventory:
    """GDPR Art. 17: nothing personal survives, per inventoried table."""

    @pytest.mark.parametrize("entry", IDENTITY_INVENTORY, ids=_entry_id)
    def test_entry_is_erased_for_the_subject(self, world, entry):
        key = (entry.table, entry.column)
        row = _fetch(world["engine"], entry.table, world["rows"][key])
        if entry.action is Action.DELETE:
            assert row is None
            assert (
                _linked_count(
                    world["engine"], entry.table, entry.column, world["subject"]
                )
                == 0
            )
        elif entry.action in (Action.UNLINK, Action.SCRUB_MATCH):
            assert row is not None, "the record itself must be retained"
            assert row[entry.column] is None
            for name in entry.scrub:
                assert row[name] is None
        else:  # RETAIN: still attached, but only to the anonymised tombstone
            assert row is not None
            assert row[entry.column] == world["subject"]

    @pytest.mark.parametrize("entry", IDENTITY_INVENTORY, ids=_entry_id)
    def test_other_tenants_subject_is_untouched(self, world, entry):
        key = (entry.table, entry.column)
        row = _fetch(world["engine"], entry.table, world["decoy_rows"][key])
        if "id" not in TABLES[entry.table].columns:
            assert (
                _linked_count(
                    world["engine"], entry.table, entry.column, world["decoy"]
                )
                == 1
            )
            return
        assert row is not None
        if entry.action is Action.SCRUB_MATCH:
            # Same e-mail string, different tenant: must NOT be scrubbed.
            assert row[entry.column] == DECOY["email"]
        else:
            assert row[entry.column] == world["decoy"]
        for name in entry.scrub:
            assert row[name] in (DECOY["ip"], DECOY["user_agent"])

    @pytest.mark.parametrize("table", SCANNED_TABLES)
    def test_no_subject_pii_left_anywhere_in_table(self, world, table):
        """Whole-row text scan: no column of any row still carries a marker."""
        with world["engine"].connect() as conn:
            total = conn.execute(
                sa.select(sa.func.count()).select_from(TABLES[table])
            ).scalar()
        assert total >= 1, f"{table} was never seeded -- the scan proves nothing"
        for marker in SUBJECT_MARKERS:
            assert (
                _count_text_hits(world["engine"], table, marker) == 0
            ), f"{table} still contains subject marker {marker!r}"

    def test_subject_email_survives_only_on_the_other_tenants_rows(self, world):
        """The shared e-mail string may remain solely where the decoy owns it."""
        with world["engine"].connect() as conn:
            rows = conn.execute(
                sa.select(TABLES["identities"].c.id).where(
                    TABLES["identities"].c.email == PII["email"]
                )
            ).all()
        assert [r[0] for r in rows] == [world["decoy"]]

    def test_identity_row_is_an_anonymised_tombstone_not_a_soft_delete(self, world):
        row = _fetch(world["engine"], "identities", world["subject"])
        assert row is not None  # the row survives so retained FKs stay valid
        assert row["username"].startswith(f"erased-{world['subject']}-")
        assert row["email"].endswith(f"@{ANONYMIZED_EMAIL_DOMAIN}")
        for name in (
            "full_name",
            "password_hash",
            "mfa_secret",
            "auth_provider_id",
            "external_id",
            "metadata",
            "last_login_at",
        ):
            assert row[name] is None, f"identities.{name} was not erased"
        assert row["mfa_enabled"] is False
        assert row["anonymized_at"] is not None

    def test_sessions_and_api_keys_are_revoked(self, world):
        """Inactive subject => auth rejects every token; API keys are gone."""
        row = _fetch(world["engine"], "identities", world["subject"])
        assert row["is_active"] is False
        assert row["password_hash"] is None
        keys = TABLES["api_keys"]
        with world["engine"].connect() as conn:
            left = conn.execute(
                sa.select(sa.func.count())
                .select_from(keys)
                .where(keys.c.identity_id == world["subject"])
            ).scalar()
        assert left == 0
        revocation = world["result"]["receipt"]["revocation"]
        assert revocation["subject_deactivated"] is True
        assert revocation["api_keys_deleted"] == 1

    def test_audit_events_retained_but_pseudonymised(self, world):
        own = _fetch(world["engine"], "audit_logs", world["own_audit"])
        assert own is not None, "the security event must be retained"
        assert own["action_name"] == "login"
        assert own["identity_id"] is None
        assert own["ip_address"] is None
        assert own["user_agent"] is None
        assert own["details"]["email"] == "[erased]"
        assert own["details"]["note"] == "[erased]"
        assert own["details"]["nested"][0]["who"] == "[erased]"
        assert own["details"]["nested"][0]["kept"] == "resource-state"

        about = _fetch(world["engine"], "audit_logs", world["about_audit"])
        # Another actor's event about the subject: the actor link stays,
        # the subject's identifiers inside `details` do not.
        assert about["identity_id"] == world["filler_a"]
        assert about["ip_address"] == "192.0.2.10"
        assert about["details"]["changed"]["email"] == "[erased]"
        assert about["details"]["field_count"] == 1

        decoy = _fetch(world["engine"], "audit_logs", world["decoy_audit"])
        assert decoy["identity_id"] == world["decoy"]
        assert decoy["ip_address"] == DECOY["ip"]
        assert decoy["details"] == {"note": f"login by {DECOY['username']}"}

    def test_decoy_identity_is_fully_intact(self, world):
        row = _fetch(world["engine"], "identities", world["decoy"])
        assert row["username"] == DECOY["username"]
        assert row["email"] == DECOY["email"]
        assert row["full_name"] == DECOY["full_name"]
        assert row["is_active"] is True
        assert row["anonymized_at"] is None

    def test_receipt_is_stored_complete_and_pii_free(self, world):
        receipt = world["result"]["receipt"]
        blob = json.dumps(receipt)
        for marker in [*SUBJECT_MARKERS, PII["email"]]:
            assert marker not in blob
        lines = {(ln["table"], ln["column"]): ln for ln in receipt["tables"]}
        assert lines[("identities", "id")]["action_name"] == "anonymize"
        for entry in IDENTITY_INVENTORY:
            line = lines[(entry.table, entry.column)]
            assert line["action"] == int(entry.action)
            assert line["legal_basis"] == LEGAL_BASIS[entry.basis]
            # audit_logs: the seeded row plus the subject's own login event.
            expected = 2 if entry.table == "audit_logs" else 1
            assert line["rows"] == expected, f"{entry.table}.{entry.column}"
            assert "skipped" not in line
        assert lines[("audit_logs", "details")]["rows"] == 2
        stores = {item["store"] for item in receipt["not_erased_synchronously"]}
        assert stores == {item["store"] for item in NOT_ERASED_SYNCHRONOUSLY}
        assert "database_backups" in stores

        table = TABLES["privacy_erasure_receipts"]
        with world["engine"].connect() as conn:
            stored = (
                conn.execute(
                    sa.select(table).where(
                        table.c.subject_id == world["subject"],
                        table.c.subject_type == int(SubjectType.IDENTITY),
                    )
                )
                .mappings()
                .all()
            )
        assert len(stored) == 1
        assert stored[0]["status"] == int(ReceiptStatus.COMPLETED)
        assert stored[0]["tenant_id"] == world["tenant_a"]
        assert stored[0]["receipt"]["receipt_id"] == receipt["receipt_id"]
        assert stored[0]["public_id"] == receipt["receipt_id"]

    def test_second_erasure_is_idempotent(self, world):
        before = _fetch(world["engine"], "identities", world["subject"])
        again = PrivacyService.erase_identity(
            world["dal"], world["subject"], world["tenant_a"], False
        )
        after = _fetch(world["engine"], "identities", world["subject"])
        assert again["anonymized"] is True
        assert again["already_erased"] is True
        assert (
            again["receipt"]["receipt_id"] == world["result"]["receipt"]["receipt_id"]
        )
        assert after == before  # pseudonym not regenerated, nothing rewritten
        table = TABLES["privacy_erasure_receipts"]
        with world["engine"].connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(table)
                .where(table.c.subject_id == world["subject"])
                .where(table.c.subject_type == int(SubjectType.IDENTITY))
            ).scalar()
        assert count == 1

    def test_cross_tenant_erasure_and_export_are_rejected(self, world):
        """Tenant A may neither erase nor export tenant B's subject."""
        with pytest.raises(LookupError):
            PrivacyService.erase_identity(
                world["dal"], world["decoy"], world["tenant_a"], False
            )
        with pytest.raises(LookupError):
            PrivacyService.export_identity(
                world["dal"], world["decoy"], world["tenant_a"]
            )
        with pytest.raises(LookupError):
            PrivacyService.set_do_not_sell(
                world["dal"], world["decoy"], world["tenant_a"], True
            )
        row = _fetch(world["engine"], "identities", world["decoy"])
        assert row["anonymized_at"] is None
        assert row["do_not_sell_share"] is False

    def test_legal_hold_blocks_erasure_even_if_the_caller_says_no_hold(self, world):
        """The service re-reads the hold; a stale `False` from the route can't bypass it."""
        with pytest.raises(LegalHoldError, match="legal hold"):
            PrivacyService.erase_identity(
                world["dal"], world["held"], world["tenant_hold"], False
            )
        row = _fetch(world["engine"], "identities", world["held"])
        assert row["email"] == "held.user@pii.example"
        assert row["is_active"] is True
        assert row["anonymized_at"] is None
        table = TABLES["privacy_erasure_receipts"]
        with world["engine"].connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(table)
                .where(table.c.subject_id == world["held"])
            ).scalar()
        assert count == 0


@needs_postgres
class TestPortalUserSubject:
    """portal_users is a second identity table; its subjects have the same rights."""

    def test_portal_user_is_anonymised(self, world):
        row = _fetch(world["engine"], "portal_users", world["portal"])
        assert row["email"].endswith(f"@{ANONYMIZED_EMAIL_DOMAIN}")
        for name in ("full_name", "password_hash", "mfa_secret", "mfa_backup_codes"):
            assert row[name] is None
        assert row["is_active"] is False
        assert world["portal_result"]["anonymized"] is True

    @pytest.mark.parametrize("entry", PORTAL_USER_INVENTORY, ids=_entry_id)
    def test_portal_entry_is_erased(self, world, entry):
        row = _fetch(
            world["engine"],
            entry.table,
            world["portal_rows"][(entry.table, entry.column)],
        )
        if entry.action is Action.DELETE:
            assert row is None
        elif entry.action is Action.UNLINK:
            assert row is not None
            assert row[entry.column] is None
        else:
            assert row[entry.column] == world["portal"]

    def test_identity_sharing_the_portal_users_id_is_not_touched(self, world):
        """Overlapping id spaces: erasing portal user N must not erase identity N."""
        twin = _fetch(world["engine"], "identities", world["portal"])
        assert twin is not None, "fixture must provide an identity with the same id"
        assert twin["anonymized_at"] is None
        assert twin["is_active"] is True

    def test_portal_export_and_idempotency(self, world):
        assert world["portal_export"]["identity"]["email"] == (
            "portal.person.qx7@pii.example"
        )
        assert world["portal_export"]["subject"]["type"] == int(SubjectType.PORTAL_USER)
        assert "password_hash" not in world["portal_export"]["identity"]
        again = PrivacyService.erase_identity(
            world["dal"],
            world["portal"],
            world["tenant_a"],
            False,
            SubjectType.PORTAL_USER,
        )
        assert again["already_erased"] is True


@needs_postgres
class TestExportBundle:
    """GDPR Art. 15: every inventory category, only this subject's rows."""

    def test_profile_fields(self, world):
        identity = world["export"]["identity"]
        assert identity["username"] == PII["username"]
        assert identity["email"] == PII["email"]
        assert identity["full_name"] == PII["full_name"]
        assert identity["external_id"] == PII["external"]
        assert identity["profile_metadata"] == {"phone": "+1-555-0100-qx7"}
        assert world["export"]["subject"] == {
            "type": int(SubjectType.IDENTITY),
            "id": world["subject"],
            "tenant_id": world["tenant_a"],
        }

    @pytest.mark.parametrize(
        "entry",
        [e for e in IDENTITY_INVENTORY if e.action is not Action.SCRUB_MATCH],
        ids=_entry_id,
    )
    def test_export_contains_each_inventory_category(self, world, entry):
        key = f"{entry.table}.{entry.column}"
        rows = world["export"]["categories"][entry.category][key]
        seeded = world["rows"][(entry.table, entry.column)]
        if "id" in entry.export:
            assert seeded in [r["id"] for r in rows]
        for row in rows:
            assert set(row) == set(entry.export)  # exact DTO, no extra column
        if entry.table != "audit_logs":
            assert len(rows) == 1

    def test_export_has_no_secrets_and_nothing_from_other_subjects(self, world):
        blob = json.dumps(world["export"])
        assert PII["secret"] not in blob  # password hash / MFA secret / key hash
        for marker in DECOY_ONLY_MARKERS:
            assert marker not in blob
        decoy_ids = {
            (table, row_id) for (table, _), row_id in world["decoy_rows"].items()
        }
        for tables in world["export"]["categories"].values():
            for key, rows in tables.items():
                table = key.split(".")[0]
                for row in rows:
                    if "id" in row:
                        assert (table, row["id"]) not in decoy_ids
        assert world["export"]["truncated"] == []

    def test_export_includes_consent_ledger_with_lawful_basis(self, world):
        consent = world["export"]["consent"]
        assert consent["do_not_sell_share"] is True
        history = [
            (h["purpose"], h["state"], h["lawful_basis"]) for h in consent["history"]
        ]
        assert history == [
            (
                int(ConsentPurpose.ANALYTICS),
                int(ConsentState.GRANTED),
                int(LawfulBasis.CONSENT),
            ),
            (
                int(ConsentPurpose.ANALYTICS),
                int(ConsentState.WITHDRAWN),
                int(LawfulBasis.CONSENT),
            ),
            (
                int(ConsentPurpose.SALE_OR_SHARING),
                int(ConsentState.WITHDRAWN),
                int(LawfulBasis.LEGAL_OBLIGATION),
            ),
        ]
        assert all(h["recorded_at"] for h in consent["history"])
        current = {p["purpose"]: p["state"] for p in consent["purposes"]}
        assert current == {
            int(ConsentPurpose.SALE_OR_SHARING): int(ConsentState.WITHDRAWN),
            int(ConsentPurpose.ANALYTICS): int(ConsentState.WITHDRAWN),
        }

    def test_consent_ledger_is_scoped_to_subject_and_tenant(self, world):
        decoy_view = PrivacyService.get_consent(
            world["dal"], world["decoy"], world["tenant_b"]
        )
        assert [h["purpose"] for h in decoy_view["history"]] == [
            int(ConsentPurpose.MARKETING)
        ]
        assert decoy_view["do_not_sell_share"] is False

    def test_withdraw_all_consent_records_every_purpose(self, world):
        result = PrivacyService.withdraw_all_consent(
            world["dal"], world["decoy"], world["tenant_b"]
        )
        assert result["do_not_sell_share"] is True
        assert len(result["withdrawn"]) == len(ConsentPurpose)
        view = PrivacyService.get_consent(
            world["dal"], world["decoy"], world["tenant_b"]
        )
        assert view["do_not_sell_share"] is True
        assert {p["state"] for p in view["purposes"]} == {int(ConsentState.WITHDRAWN)}
        row = _fetch(world["engine"], "identities", world["decoy"])
        assert row["do_not_sell_share"] is True
        assert row["consent_withdrawn_at"] is not None
        # Opting back in restores the decoy for the other tests' invariants.
        PrivacyService.set_do_not_sell(
            world["dal"], world["decoy"], world["tenant_b"], False
        )
        row = _fetch(world["engine"], "identities", world["decoy"])
        assert row["do_not_sell_share"] is False
        assert row["consent_withdrawn_at"] is None


def _new_tenant(engine: sa.Engine) -> tuple[_Seeder, int]:
    """A fresh tenant plus a seeder whose parent rows live in it."""
    tenant = _Seeder(engine, {}).insert("tenants")
    seeder = _Seeder(engine, {"tenants": tenant})
    seeder.memo["identities"] = seeder.insert(
        "identities", tenant_id=tenant, is_active=True
    )
    return seeder, tenant


@needs_postgres
class TestLastTenantAdminGuard:
    """M2: a sole administrator cannot erase themselves and orphan the tenant."""

    def _admin(self, seeder: _Seeder, tenant: int, **overrides: Any) -> int:
        values = {"tenant_id": tenant, "is_active": True, "portal_role": "admin"}
        values.update(overrides)
        return seeder.insert("identities", **values)

    def test_sole_admin_is_refused_and_untouched(self, private_db):
        engine, dal = private_db
        seeder, tenant = _new_tenant(engine)
        admin = self._admin(seeder, tenant, email="sole.admin@pii.example")
        with pytest.raises(LastAdminError, match="transfer the admin role"):
            PrivacyService.erase_identity(dal, admin, tenant, False)
        row = _fetch(engine, "identities", admin)
        assert row["email"] == "sole.admin@pii.example"
        assert row["is_active"] is True
        assert row["anonymized_at"] is None
        assert (
            _linked_count(engine, "privacy_erasure_receipts", "subject_id", admin) == 0
        )

    def test_two_admins_allows_erasure_then_protects_the_remaining_one(
        self, private_db
    ):
        engine, dal = private_db
        seeder, tenant = _new_tenant(engine)
        first = self._admin(seeder, tenant)
        second = self._admin(seeder, tenant)
        result = PrivacyService.erase_identity(dal, first, tenant, False)
        assert result["anonymized"] is True
        assert _fetch(engine, "identities", first)["anonymized_at"] is not None
        with pytest.raises(LastAdminError):
            PrivacyService.erase_identity(dal, second, tenant, False)

    @pytest.mark.parametrize(
        "other",
        [
            {"portal_role": "admin", "is_active": False},  # deactivated admin
            {"portal_role": "editor"},  # active non-admin
        ],
    )
    def test_inactive_or_non_admin_peers_do_not_count(self, private_db, other):
        engine, dal = private_db
        seeder, tenant = _new_tenant(engine)
        admin = self._admin(seeder, tenant)
        self._admin(seeder, tenant, **other)
        with pytest.raises(LastAdminError):
            PrivacyService.erase_identity(dal, admin, tenant, False)

    def test_admin_in_another_tenant_does_not_count(self, private_db):
        engine, dal = private_db
        seeder, tenant = _new_tenant(engine)
        other_seeder, other_tenant = _new_tenant(engine)
        admin = self._admin(seeder, tenant)
        self._admin(other_seeder, other_tenant)
        with pytest.raises(LastAdminError):
            PrivacyService.erase_identity(dal, admin, tenant, False)

    def test_superuser_and_portal_admin_count_as_administrators(self, private_db):
        engine, dal = private_db
        seeder, tenant = _new_tenant(engine)
        admin = self._admin(seeder, tenant)
        seeder.insert(
            "portal_users",
            tenant_id=tenant,
            is_active=True,
            email_verified=True,
            global_role="admin",
        )
        assert PrivacyService.erase_identity(dal, admin, tenant, False)["anonymized"]

        seeder2, tenant2 = _new_tenant(engine)
        admin2 = self._admin(seeder2, tenant2)
        self._admin(seeder2, tenant2, portal_role="observer", is_superuser=True)
        assert PrivacyService.erase_identity(dal, admin2, tenant2, False)["anonymized"]

    def test_sole_portal_admin_is_refused_and_non_admin_is_not(self, private_db):
        engine, dal = private_db
        seeder, tenant = _new_tenant(engine)
        portal_admin = seeder.insert(
            "portal_users",
            tenant_id=tenant,
            is_active=True,
            email_verified=True,
            tenant_role="admin",
        )
        with pytest.raises(LastAdminError):
            PrivacyService.erase_identity(
                dal, portal_admin, tenant, False, SubjectType.PORTAL_USER
            )
        member = seeder.insert(
            "portal_users", tenant_id=tenant, is_active=True, email_verified=True
        )
        result = PrivacyService.erase_identity(
            dal, member, tenant, False, SubjectType.PORTAL_USER
        )
        assert result["anonymized"] is True

    def test_bulk_erase_reports_last_admin_per_item(self, private_db):
        engine, dal = private_db
        seeder, tenant = _new_tenant(engine)
        admin = self._admin(seeder, tenant)
        user = seeder.insert("identities", tenant_id=tenant, is_active=True)
        results = PrivacyService.bulk_erase(dal, [user, admin], tenant, False)[
            "results"
        ]
        assert results[0]["anonymized"] is True
        assert results[1]["anonymized"] is False
        assert "only active administrator" in results[1]["error"]


@needs_postgres
class TestAccessReviewEvidenceRetained:
    """L1: completed access reviews are audit evidence and survive erasure."""

    def test_review_item_and_its_membership_are_kept_other_memberships_deleted(
        self, private_db
    ):
        engine, dal = private_db
        seeder, tenant = _new_tenant(engine)
        subject = seeder.insert("identities", tenant_id=tenant, is_active=True)
        reviewed = seeder.insert(
            "identity_group_memberships",
            identity_id=subject,
            provider_member_id="ext-member-qq1",
        )
        unreviewed = seeder.insert(
            "identity_group_memberships",
            identity_id=subject,
            group_id=seeder.insert("identity_groups"),
        )
        item = seeder.insert(
            "access_review_items",
            identity_id=subject,
            membership_id=reviewed,
            tenant_id=tenant,
            decision="keep",
        )
        receipt = PrivacyService.erase_identity(dal, subject, tenant, False)["receipt"]

        kept_item = _fetch(engine, "access_review_items", item)
        assert kept_item is not None, "review evidence must not be deleted"
        assert kept_item["decision"] == "keep"
        assert kept_item["identity_id"] == subject  # resolves to the tombstone
        kept_membership = _fetch(engine, "identity_group_memberships", reviewed)
        assert kept_membership is not None
        assert kept_membership["provider_member_id"] is None  # external id scrubbed
        assert _fetch(engine, "identity_group_memberships", unreviewed) is None

        lines = {(ln["table"], ln["column"]): ln for ln in receipt["tables"]}
        membership_line = lines[("identity_group_memberships", "identity_id")]
        assert membership_line["rows"] == 1
        assert membership_line["retained_rows"] == 1
        assert "access review" in membership_line["retained_basis"]
        item_line = lines[("access_review_items", "identity_id")]
        assert item_line["action_name"] == "retain"
        assert item_line["legal_basis"] == LEGAL_BASIS["access_review_evidence"]
        assert _fetch(engine, "identities", subject)["anonymized_at"] is not None


@needs_postgres
class TestReceiptIdempotency:
    """M3: a completed receipt short-circuits; an interrupted run is resumed."""

    def test_completed_receipt_short_circuits_even_under_a_later_legal_hold(
        self, private_db
    ):
        engine, dal = private_db
        seeder, tenant = _new_tenant(engine)
        subject = seeder.insert("identities", tenant_id=tenant, is_active=True)
        first = PrivacyService.erase_identity(dal, subject, tenant, False)
        with engine.begin() as conn:
            conn.execute(
                TABLES["tenants"]
                .update()
                .where(TABLES["tenants"].c.id == tenant)
                .values(legal_hold=True)
            )
        before = _fetch(engine, "identities", subject)
        again = PrivacyService.erase_identity(dal, subject, tenant, False)
        assert again["already_erased"] is True
        assert again["receipt"]["receipt_id"] == first["receipt"]["receipt_id"]
        assert _fetch(engine, "identities", subject) == before
        assert (
            _linked_count(engine, "privacy_erasure_receipts", "subject_id", subject)
            == 1
        )

    def test_interrupted_run_is_resumed_on_the_same_receipt(self, private_db):
        """Simulate a crash after 'deactivate' and before the tombstone step."""
        engine, dal = private_db
        seeder, tenant = _new_tenant(engine)
        subject = seeder.insert(
            "identities",
            tenant_id=tenant,
            is_active=False,
            email="half.erased@pii.example",
        )
        key = seeder.insert("api_keys", identity_id=subject)
        public_id = str(uuid.uuid4())
        seeder.insert(
            "privacy_erasure_receipts",
            tenant_id=tenant,
            public_id=public_id,
            subject_type=int(SubjectType.IDENTITY),
            subject_id=subject,
            status=int(ReceiptStatus.IN_PROGRESS),
        )
        result = PrivacyService.erase_identity(dal, subject, tenant, False)
        assert result["already_erased"] is False
        assert result["receipt"]["receipt_id"] == public_id
        assert _fetch(engine, "api_keys", key) is None
        row = _fetch(engine, "identities", subject)
        assert row["email"].endswith(f"@{ANONYMIZED_EMAIL_DOMAIN}")
        table = TABLES["privacy_erasure_receipts"]
        with engine.connect() as conn:
            stored = (
                conn.execute(sa.select(table).where(table.c.subject_id == subject))
                .mappings()
                .all()
            )
        assert len(stored) == 1
        assert stored[0]["status"] == int(ReceiptStatus.COMPLETED)


@needs_postgres
class TestReauthentication:
    """M1: password or TOTP verification against the real stored credential."""

    def _subject(self, private_db, **overrides: Any) -> tuple[Any, int, int]:
        engine, dal = private_db
        seeder, tenant = _new_tenant(engine)
        values = {"tenant_id": tenant, "is_active": True}
        values.update(overrides)
        return dal, tenant, seeder.insert("identities", **values)

    def test_password_factor(self, private_db):
        from werkzeug.security import generate_password_hash

        dal, tenant, subject = self._subject(
            private_db, password_hash=generate_password_hash("correct horse")
        )
        check = PrivacyService.verify_reauth
        kind = SubjectType.IDENTITY
        assert check(dal, subject, tenant, kind, "correct horse", None) is True
        assert check(dal, subject, tenant, kind, "wrong", None) is False
        assert check(dal, subject, tenant, kind, "", None) is False
        assert check(dal, subject, tenant, kind, None, None) is False
        assert PrivacyService.has_local_credential(dal, subject, tenant) is True

    def test_totp_factor_and_malformed_secret_fails_closed(self, private_db):
        import pyotp

        secret = pyotp.random_base32()
        dal, tenant, subject = self._subject(private_db, mfa_secret=secret)
        check = PrivacyService.verify_reauth
        kind = SubjectType.IDENTITY
        assert check(dal, subject, tenant, kind, None, pyotp.TOTP(secret).now()) is True
        assert check(dal, subject, tenant, kind, None, "000000x") is False
        dal2, tenant2, broken = self._subject(private_db, mfa_secret="not base32 !!")
        assert check(dal2, broken, tenant2, kind, None, "123456") is False

    def test_sso_subject_has_no_local_credential(self, private_db):
        dal, tenant, subject = self._subject(private_db)
        assert PrivacyService.has_local_credential(dal, subject, tenant) is False
        assert (
            PrivacyService.verify_reauth(
                dal, subject, tenant, SubjectType.IDENTITY, "anything", "123456"
            )
            is False
        )

    def test_portal_user_and_cross_tenant(self, private_db):
        from werkzeug.security import generate_password_hash

        engine, dal = private_db
        seeder, tenant = _new_tenant(engine)
        portal = seeder.insert(
            "portal_users",
            tenant_id=tenant,
            is_active=True,
            email_verified=True,
            password_hash=generate_password_hash("portal pw"),
        )
        kind = SubjectType.PORTAL_USER
        assert PrivacyService.verify_reauth(
            dal, portal, tenant, kind, "portal pw", None
        )
        assert not PrivacyService.verify_reauth(dal, portal, tenant, kind, "x", None)
        _, other_tenant = _new_tenant(engine)
        with pytest.raises(LookupError):
            PrivacyService.verify_reauth(
                dal, portal, other_tenant, kind, "portal pw", None
            )
        with pytest.raises(LookupError):
            PrivacyService.has_local_credential(dal, portal, other_tenant, kind)
