"""
Regression test: BuiltinSecretsClient.get_secret() must never return real
kv_pairs values when is_masked=True.

security-audit (DTO pass): the KV branch set is_masked=True but returned the
parsed secret_json verbatim — a credential leak, since any caller trusting
is_masked to gate display got the real values anyway. Fixed by routing the
result through SecretValue.mask() before returning.
"""

from unittest.mock import MagicMock

import pytest


class TestBuiltinSecretsKvMasking:
    """get_secret() on a key-value built-in secret must redact every value."""

    @pytest.mark.asyncio
    async def test_kv_pairs_are_masked_not_real_values(self, app):
        from apps.api.services.secrets.builtin_client import BuiltinSecretsClient

        async with app.app_context():
            client = BuiltinSecretsClient({"organization_id": 1})

            row = MagicMock()
            row.name = "db-creds"
            row.secret_json = '{"username": "admin", "password": "s3cr3t-real-value"}'
            row.secret_type = "kv"
            row.description = None
            row.tags = None
            row.expires_at = None
            row.created_at = None
            row.updated_at = None

            client._get_secret_row = MagicMock(return_value=row)

            secret = client.get_secret("db-creds")

            assert secret.is_masked is True
            assert secret.is_kv is True
            assert secret.kv_pairs == {
                "username": "***MASKED***",
                "password": "***MASKED***",
            }
            leaked = [v for v in secret.kv_pairs.values() if v not in ("***MASKED***",)]
            assert not leaked, f"real secret values leaked: {leaked}"

    @pytest.mark.asyncio
    async def test_kv_pairs_keys_preserved_for_display(self, app):
        """Masking must keep the key names — only values are redacted."""
        from apps.api.services.secrets.builtin_client import BuiltinSecretsClient

        async with app.app_context():
            client = BuiltinSecretsClient({"organization_id": 1})

            row = MagicMock()
            row.name = "api-keys"
            row.secret_json = '{"client_id": "abc", "client_secret": "xyz"}'
            row.secret_type = "kv"
            row.description = None
            row.tags = None
            row.expires_at = None
            row.created_at = None
            row.updated_at = None

            client._get_secret_row = MagicMock(return_value=row)

            secret = client.get_secret("api-keys")

            assert set(secret.kv_pairs.keys()) == {"client_id", "client_secret"}
