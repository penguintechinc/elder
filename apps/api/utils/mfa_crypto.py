"""At-rest protection for MFA material (TOTP secrets and backup codes).

TOTP secrets must be recoverable to verify codes, so they are sealed with
AES-256-GCM under a key derived (HKDF-SHA256, dedicated context label) from
the application's secret configuration -- never the raw signing key itself.
Backup codes only ever need to be compared, so they are stored hashed.

Stored TOTP envelope format::

    v1:<kid>:<nonce-b64url>:<ciphertext+tag-b64url>

``kid`` is a non-secret fingerprint of the derived key, which is what makes
rotation possible: several master secrets can be configured at once and the
right one is selected per row.

Key configuration (config mapping or environment):

* ``MFA_ENCRYPTION_KEY`` -- dedicated master secret (preferred).
* ``SECRET_KEY`` -- fallback master secret when the dedicated one is unset.
* ``MFA_ENCRYPTION_OLD_KEYS`` -- comma-separated previous master secrets,
  accepted for decryption only.

Rotation procedure:

1. Set ``MFA_ENCRYPTION_KEY`` to the new secret and append the previous
   master secret (the old ``MFA_ENCRYPTION_KEY``, or ``SECRET_KEY`` if that
   was in use) to ``MFA_ENCRYPTION_OLD_KEYS``; roll the API.
2. Rows are re-sealed under the new key on each user's next successful MFA
   verification. To force it, re-run the envelope pass of Alembic revision
   053 (it re-seals any row whose ``kid`` is not the current one).
3. Once no row carries the old ``kid`` (``mfa_secret NOT LIKE 'v1:<new>:%'``
   returns nothing), drop the old secret from ``MFA_ENCRYPTION_OLD_KEYS``.

Rotating ``SECRET_KEY`` alone, while it is the active MFA master secret,
makes existing envelopes undecryptable: move the old value into
``MFA_ENCRYPTION_OLD_KEYS`` first. Failures are always loud -- a missing or
wrong key raises, it never degrades to "MFA not required".

This module is import-light on purpose (no Quart, no DAL) so the Alembic
migration and the service layer share one implementation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from werkzeug.security import check_password_hash, generate_password_hash

ENVELOPE_VERSION = "v1"
ENVELOPE_PREFIX = f"{ENVELOPE_VERSION}:"

_KEY_INFO = b"elder/portal-auth/mfa-totp-secret/aes-256-gcm/v1"
_KID_INFO = b"elder/portal-auth/mfa-totp-secret/key-id/v1"
_NONCE_BYTES = 12
_KID_HEX_CHARS = 16
_BACKUP_CODE_RE = re.compile(r"^[0-9a-f]{8}$")
_HASH_PREFIXES = ("scrypt:", "pbkdf2:")


class MfaCryptoError(RuntimeError):
    """Base class for MFA at-rest protection failures.

    Raised instead of returning a sentinel so a broken key setup can never
    be mistaken for "this user has no MFA".
    """


class MfaKeyUnavailableError(MfaCryptoError):
    """No usable master secret is configured, or no key matches a row's kid.

    Signals an operator/configuration problem rather than a bad user input.
    """


class MfaDecryptionError(MfaCryptoError):
    """A stored envelope is malformed or failed authentication.

    Covers tampering, a row copied between users (AAD mismatch) and a
    wrong key that happens to share a kid.
    """


@dataclass(frozen=True, slots=True)
class MfaKeyring:
    """Derived AES keys for sealing (current) and opening (current + old).

    Key bytes are excluded from ``repr`` so a keyring can never leak key
    material into a log line or traceback.
    """

    current_kid: str
    keys: Mapping[str, bytes] = field(repr=False)

    def current_key(self) -> bytes:
        """Return the AES-256 key used for new envelopes.

        Kept as a method so the bytes never appear as a dataclass field value
        in debugging output.
        """
        return self.keys[self.current_kid]


def _hkdf(master_secret: str, info: bytes, length: int) -> bytes:
    """Derive ``length`` bytes from a master secret under a context label.

    A distinct ``info`` per purpose keeps the MFA key cryptographically
    separate from every other use of the same master secret (JWT signing).
    """
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info).derive(
        master_secret.encode("utf-8")
    )


def derive_subkey(master_secret: str, info: bytes, length: int = 32) -> bytes:
    """Derive an independent key from a master secret for a named purpose.

    Lets a narrowly-scoped credential (e.g. the MFA enrolment token) be
    signed with a key no other verifier in the application holds.
    """
    if not master_secret:
        raise MfaKeyUnavailableError("No master secret configured")
    return _hkdf(master_secret, info, length)


def hash_challenge_id(challenge_id: str) -> str:
    """Return the SHA-256 hex digest stored for an opaque MFA challenge id.

    The id itself is high-entropy random, so an unsalted digest is enough to
    keep a database read from yielding usable challenges.
    """
    return hashlib.sha256(challenge_id.encode("utf-8")).hexdigest()


def _derive(master_secret: str) -> tuple[str, bytes]:
    """Derive the ``(kid, aes_key)`` pair for one master secret.

    The kid comes from a separate HKDF output, so publishing it in every
    envelope reveals nothing about the encryption key.
    """
    key = _hkdf(master_secret, _KEY_INFO, 32)
    kid = hashlib.sha256(_hkdf(master_secret, _KID_INFO, 32)).hexdigest()
    return kid[:_KID_HEX_CHARS], key


def build_keyring(config: Mapping[str, Any]) -> MfaKeyring:
    """Build the MFA keyring from a config mapping or ``os.environ``.

    Raises:
        MfaKeyUnavailableError: neither ``MFA_ENCRYPTION_KEY`` nor
            ``SECRET_KEY`` holds a non-empty value.
    """
    primary = config.get("MFA_ENCRYPTION_KEY") or config.get("SECRET_KEY")
    if not primary or not str(primary).strip():
        raise MfaKeyUnavailableError(
            "No MFA encryption key configured: set MFA_ENCRYPTION_KEY (or "
            "SECRET_KEY) to the same value the API uses."
        )
    current_kid, current_key = _derive(str(primary))
    keys: dict[str, bytes] = {current_kid: current_key}
    raw_old = config.get("MFA_ENCRYPTION_OLD_KEYS") or ""
    old_values = (
        raw_old.split(",") if isinstance(raw_old, str) else [str(v) for v in raw_old]
    )
    for old in old_values:
        if old.strip():
            kid, key = _derive(old.strip())
            keys.setdefault(kid, key)
    return MfaKeyring(current_kid=current_kid, keys=keys)


def _b64e(raw: bytes) -> str:
    """Encode bytes as unpadded URL-safe base64 (no ``:`` in the alphabet).

    The envelope uses ``:`` as its field separator, so the encoding must
    never produce one.
    """
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    """Decode unpadded URL-safe base64, raising ``ValueError`` on bad input.

    Strict validation keeps a corrupted envelope from decoding to garbage.
    """
    return base64.b64decode(
        text + "=" * (-len(text) % 4), altchars=b"-_", validate=True
    )


def is_encrypted(stored: Any) -> bool:
    """Return True when a stored column value is a versioned envelope.

    A legacy plaintext TOTP secret is base32 and can never contain ``:``,
    so the prefix check is unambiguous.
    """
    return isinstance(stored, str) and stored.startswith(ENVELOPE_PREFIX)


def envelope_kid(stored: str) -> str | None:
    """Return the key id of an envelope, or None for non-envelope values.

    Used to decide whether a row still needs re-sealing after a rotation.
    """
    if not is_encrypted(stored):
        return None
    parts = stored.split(":")
    return parts[1] if len(parts) == 4 else None


def encrypt_totp_secret(plaintext: str, keyring: MfaKeyring, *, aad: str) -> str:
    """Seal a TOTP secret into a versioned AES-256-GCM envelope.

    ``aad`` binds the envelope to its row (``"<table>:<id>"``) so ciphertext
    copied onto another user's row fails authentication.
    """
    if not plaintext:
        raise MfaCryptoError("Refusing to encrypt an empty TOTP secret")
    nonce = secrets.token_bytes(_NONCE_BYTES)
    sealed = AESGCM(keyring.current_key()).encrypt(
        nonce, plaintext.encode("utf-8"), aad.encode("utf-8")
    )
    return f"{ENVELOPE_PREFIX}{keyring.current_kid}:{_b64e(nonce)}:{_b64e(sealed)}"


def decrypt_totp_secret(stored: str, keyring: MfaKeyring, *, aad: str) -> str:
    """Open a versioned envelope and return the TOTP secret.

    Raises:
        MfaKeyUnavailableError: the envelope's kid is not in the keyring.
        MfaDecryptionError: the envelope is malformed or fails authentication.
    """
    parts = stored.split(":")
    if len(parts) != 4 or parts[0] != ENVELOPE_VERSION:
        raise MfaDecryptionError("Malformed MFA secret envelope")
    _, kid, nonce_b64, sealed_b64 = parts
    key = keyring.keys.get(kid)
    if key is None:
        raise MfaKeyUnavailableError(
            f"No MFA encryption key available for key id {kid!r}; add the "
            "previous master secret to MFA_ENCRYPTION_OLD_KEYS."
        )
    try:
        plaintext = AESGCM(key).decrypt(
            _b64d(nonce_b64), _b64d(sealed_b64), aad.encode("utf-8")
        )
    except (InvalidTag, ValueError) as exc:
        raise MfaDecryptionError("MFA secret envelope failed authentication") from exc
    return plaintext.decode("utf-8")


def open_totp_secret(stored: str, keyring: MfaKeyring, *, aad: str) -> str:
    """Return the usable TOTP secret for a stored value of either generation.

    Envelopes are decrypted; a pre-encryption plaintext value is returned
    as-is so rows written before Alembic revision 053 keep working until
    they are re-sealed.
    """
    if is_encrypted(stored):
        return decrypt_totp_secret(stored, keyring, aad=aad)
    return stored


def needs_reseal(stored: str, keyring: MfaKeyring) -> bool:
    """Return True when a stored value is plaintext or sealed under an old key.

    Drives both the lazy re-seal on successful verification and the
    migration's idempotency check.
    """
    return envelope_kid(stored) != keyring.current_kid


def normalize_backup_code(code: Any) -> str | None:
    """Return a canonical backup code, or None if it is not backup-shaped.

    Gating on shape keeps ordinary 6-digit TOTP guesses from triggering the
    deliberately expensive hash comparison.
    """
    candidate = str(code).strip().lower()
    return candidate if _BACKUP_CODE_RE.fullmatch(candidate) else None


def is_hashed_backup_code(stored: Any) -> bool:
    """Return True when a stored backup code entry is already a password hash.

    Lets the migration and the verifier tell hashed entries from legacy
    plaintext ones.
    """
    return isinstance(stored, str) and stored.startswith(_HASH_PREFIXES)


def hash_backup_code(code: str) -> str:
    """Hash one backup code with the same KDF the portal uses for passwords.

    Backup codes are single-use bearer credentials, so only a salted hash
    is ever written to the database.
    """
    return generate_password_hash(code.strip().lower())


def match_backup_code(stored_codes: list[Any], candidate: str) -> int | None:
    """Return the index of the stored entry matching ``candidate``, if any.

    Hashed entries are checked with the KDF verifier; legacy plaintext
    entries (pre-revision-053 rows) with a constant-time comparison.
    """
    matched: int | None = None
    for idx, stored in enumerate(stored_codes):
        if is_hashed_backup_code(stored):
            hit = check_password_hash(stored, candidate)
        else:
            hit = hmac.compare_digest(
                str(stored).strip().lower().encode("utf-8"), candidate.encode("utf-8")
            )
        if hit and matched is None:
            matched = idx
    return matched
