"""Harden MFA material at rest: sealed TOTP secrets, hashed backup codes.

Revision ID: 056
Revises: 054
Create Date: 2026-11-24

What it does:

* adds ``portal_users.mfa_last_totp_counter`` / ``mfa_failed_attempts`` /
  ``mfa_locked_until`` (TOTP replay protection and second-factor lockout);
* adds ``portal_users.mfa_pending_secret`` / ``mfa_pending_backup_codes`` /
  ``mfa_pending_expires_at`` (enrolment stays pending until confirmed);
* creates ``portal_mfa_challenges`` (server-side, single-use login
  challenges binding the second-factor step to the password step);
* seals every non-envelope ``mfa_secret`` in ``portal_users`` and
  ``identities`` into the ``v1:<kid>:<nonce>:<ciphertext>`` AES-256-GCM
  envelope (see ``apps/api/utils/mfa_crypto.py``);
* replaces stored backup codes in ``portal_users.mfa_backup_codes`` with
  salted hashes.

Operational requirement: the process running this migration MUST have the
same ``MFA_ENCRYPTION_KEY`` / ``SECRET_KEY`` (and ``MFA_ENCRYPTION_OLD_KEYS``)
as the API. With rows to convert and no key in the environment the
migration aborts; it never falls back to a built-in default.

Idempotent: rows already sealed under the current key and backup codes that
are already hashed are skipped, so a re-run (or a run after a key rotation,
which re-seals rows under the new key) is safe. Work is done in id-ordered
batches. Steps whose column is absent in a given database are skipped.

Downgrade: TOTP secrets are decrypted back to their previous representation
on purpose -- the previous application revision reads the column as a raw
base32 secret, so leaving envelopes in place would break sign-in for every
MFA-enrolled user. Hashed backup codes cannot be reversed; they are cleared
and users regenerate them. The added columns and the challenge table are
dropped (in-flight enrolments and login challenges are simply restarted).
"""

from __future__ import annotations

import json
import os
from typing import Any

import sqlalchemy as sa

from alembic import op
from apps.api.utils.mfa_crypto import (
    MfaKeyring,
    build_keyring,
    decrypt_totp_secret,
    encrypt_totp_secret,
    hash_backup_code,
    is_encrypted,
    is_hashed_backup_code,
    needs_reseal,
    open_totp_secret,
)

revision = "056"
down_revision = "054"
branch_labels = None
depends_on = None

_BATCH_SIZE = 500
_SECRET_TABLES = ("portal_users", "identities")
_CHALLENGE_TABLE = "portal_mfa_challenges"
_NEW_COLUMN_NAMES = (
    "mfa_last_totp_counter",
    "mfa_failed_attempts",
    "mfa_locked_until",
    "mfa_pending_secret",
    "mfa_pending_backup_codes",
    "mfa_pending_expires_at",
)


def _new_columns() -> list[sa.Column[Any]]:
    """Return fresh Column objects for the MFA state columns.

    Built per call because a Column instance can only be attached to one table.
    """
    return [
        sa.Column("mfa_last_totp_counter", sa.BigInteger(), nullable=True),
        sa.Column(
            "mfa_failed_attempts", sa.Integer(), nullable=True, server_default="0"
        ),
        sa.Column("mfa_locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mfa_pending_secret", sa.String(255), nullable=True),
        sa.Column("mfa_pending_backup_codes", sa.JSON(), nullable=True),
        sa.Column("mfa_pending_expires_at", sa.DateTime(timezone=True), nullable=True),
    ]


def _create_challenge_table() -> None:
    """Create the server-side MFA login challenge table.

    Mirrors ``apps.api.models.tenant.PortalMfaChallenge`` at this revision.
    """
    op.create_table(
        _CHALLENGE_TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("challenge_hash", sa.String(64), nullable=False),
        sa.Column(
            "portal_user_id",
            sa.Integer(),
            sa.ForeignKey("portal_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("fingerprint_hash", sa.String(64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_portal_mfa_challenges_challenge_hash",
        _CHALLENGE_TABLE,
        ["challenge_hash"],
        unique=True,
    )


class _LazyKeyring:
    """Resolve the MFA keyring from the environment on first real use.

    A database with no MFA rows migrates without any key configured; the
    key is only demanded (loudly) once a row actually needs converting.
    """

    def __init__(self) -> None:
        """Start with no keyring resolved."""
        self._keyring: MfaKeyring | None = None

    def get(self) -> MfaKeyring:
        """Return the keyring, building it from ``os.environ`` once.

        Raises ``MfaKeyUnavailableError`` when no master secret is set.
        """
        if self._keyring is None:
            self._keyring = build_keyring(os.environ)
        return self._keyring


def _secret_table(name: str) -> sa.TableClause:
    """Return a lightweight table handle for the MFA secret column.

    Declared locally so the migration never depends on the current ORM
    models, which keep evolving after this revision.
    """
    return sa.table(
        name, sa.column("id", sa.Integer), sa.column("mfa_secret", sa.String)
    )


def _batches(conn: Any, table: sa.TableClause, *columns: Any) -> Any:
    """Yield id-ordered batches of rows that have a non-empty MFA secret.

    Keyset pagination on the primary key keeps memory flat on large tables.
    """
    last_id = 0
    while True:
        rows = conn.execute(
            sa.select(table.c.id, *columns)
            .where(table.c.id > last_id)
            .where(table.c.mfa_secret.isnot(None))
            .where(table.c.mfa_secret != "")
            .order_by(table.c.id)
            .limit(_BATCH_SIZE)
        ).fetchall()
        if not rows:
            return
        yield rows
        last_id = rows[-1][0]


def _seal_secrets(conn: Any, name: str, keyring: _LazyKeyring) -> None:
    """Seal every plaintext or old-key MFA secret in one table.

    Rows already carrying a current-key envelope are left untouched, which
    is what makes the migration re-runnable.
    """
    table = _secret_table(name)
    for rows in _batches(conn, table, table.c.mfa_secret):
        for row_id, stored in rows:
            ring = keyring.get()
            if not needs_reseal(stored, ring):
                continue
            aad = f"{name}:{row_id}"
            plaintext = open_totp_secret(stored, ring, aad=aad)
            conn.execute(
                sa.update(table)
                .where(table.c.id == row_id)
                .values(mfa_secret=encrypt_totp_secret(plaintext, ring, aad=aad))
            )


def _unseal_secrets(conn: Any, name: str, keyring: _LazyKeyring) -> None:
    """Decrypt MFA secret envelopes in one table back to plaintext.

    Downgrade-only: restores the representation the previous revision reads.
    """
    table = _secret_table(name)
    for rows in _batches(conn, table, table.c.mfa_secret):
        for row_id, stored in rows:
            if not is_encrypted(stored):
                continue
            plaintext = decrypt_totp_secret(
                stored, keyring.get(), aad=f"{name}:{row_id}"
            )
            conn.execute(
                sa.update(table)
                .where(table.c.id == row_id)
                .values(mfa_secret=plaintext)
            )


def _backup_table() -> sa.TableClause:
    """Return a lightweight ``portal_users`` handle for the backup-code column.

    The column is typed JSON so values round-trip as Python lists.
    """
    return sa.table(
        "portal_users",
        sa.column("id", sa.Integer),
        sa.column("mfa_backup_codes", sa.JSON),
    )


def _backup_rows(conn: Any, table: sa.TableClause) -> Any:
    """Yield id-ordered batches of ``(id, mfa_backup_codes)`` rows.

    Every row is visited; NULL / empty values are filtered by the caller
    because JSON columns have no portable ``IS NOT NULL``-style emptiness test.
    """
    last_id = 0
    while True:
        rows = conn.execute(
            sa.select(table.c.id, table.c.mfa_backup_codes)
            .where(table.c.id > last_id)
            .order_by(table.c.id)
            .limit(_BATCH_SIZE)
        ).fetchall()
        if not rows:
            return
        yield rows
        last_id = rows[-1][0]


def _as_code_list(raw: Any) -> list[Any]:
    """Normalize a stored backup-code value to a list.

    Handles a decoded list, a JSON-encoded string, and NULL.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return list(raw) if isinstance(raw, list) else []


def _hash_backup_codes(conn: Any) -> None:
    """Replace stored plaintext backup codes with salted hashes.

    Entries that are already hashes are kept as-is (idempotent).
    """
    table = _backup_table()
    for rows in _backup_rows(conn, table):
        for row_id, raw in rows:
            codes = _as_code_list(raw)
            if not codes or all(is_hashed_backup_code(c) for c in codes):
                continue
            hashed = [
                c if is_hashed_backup_code(c) else hash_backup_code(str(c))
                for c in codes
            ]
            conn.execute(
                sa.update(table)
                .where(table.c.id == row_id)
                .values(mfa_backup_codes=hashed)
            )


def _clear_hashed_backup_codes(conn: Any) -> None:
    """Empty backup-code lists that contain hashes (downgrade only).

    The previous revision compares codes as plaintext, so hashes would be
    unusable there; an empty list makes that state explicit.
    """
    table = _backup_table()
    for rows in _backup_rows(conn, table):
        for row_id, raw in rows:
            if any(is_hashed_backup_code(c) for c in _as_code_list(raw)):
                conn.execute(
                    sa.update(table)
                    .where(table.c.id == row_id)
                    .values(mfa_backup_codes=[])
                )


def _column_names(inspector: Any, table: str) -> set[str]:
    """Return the column names a table actually has in this database.

    Databases built by ``create_all`` and by the Alembic chain do not carry
    an identical ``portal_users`` shape, so every step checks before it acts.
    """
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    """Add the MFA state columns, seal TOTP secrets, hash backup codes (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())
    keyring = _LazyKeyring()

    if "portal_users" in tables:
        existing = _column_names(inspector, "portal_users")
        for column in _new_columns():
            if column.name not in existing:
                op.add_column("portal_users", column)
        if "mfa_backup_codes" in existing:
            _hash_backup_codes(conn)
        if _CHALLENGE_TABLE not in tables:
            _create_challenge_table()

    for name in _SECRET_TABLES:
        if name in tables and "mfa_secret" in _column_names(inspector, name):
            _seal_secrets(conn, name, keyring)


def downgrade() -> None:
    """Decrypt TOTP secrets, clear hashed backup codes, drop the added columns."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())
    keyring = _LazyKeyring()

    for name in _SECRET_TABLES:
        if name in tables and "mfa_secret" in _column_names(inspector, name):
            _unseal_secrets(conn, name, keyring)

    if _CHALLENGE_TABLE in tables:
        op.drop_table(_CHALLENGE_TABLE)

    if "portal_users" in tables:
        existing = _column_names(inspector, "portal_users")
        if "mfa_backup_codes" in existing:
            _clear_hashed_backup_codes(conn)
        for column_name in reversed(_NEW_COLUMN_NAMES):
            if column_name in existing:
                op.drop_column("portal_users", column_name)
