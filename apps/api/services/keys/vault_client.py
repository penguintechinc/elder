"""Hashicorp Vault Transit secrets engine client for key management."""

# flake8: noqa: E501

import base64
import logging
from datetime import datetime
from typing import Any, Dict, Optional

import requests

from .base import BaseKeyProvider

logger = logging.getLogger(__name__)


class VaultTransitClient(BaseKeyProvider):
    """Hashicorp Vault Transit Secrets Engine client for key management."""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize Vault Transit client.

        Expected config:
        {
            "url": "https://vault.example.com:8200",  # Required
            "token": "s.XXXXXXXXXXXX",  # Required
            "namespace": "admin/namespace",  # Optional (Vault Enterprise)
            "mount_point": "transit",  # Optional, default: "transit"
            "verify_tls": true,  # Optional, default: true
            "ca_cert": "/path/to/ca.pem",  # Optional
            "client_cert": "/path/to/client.pem",  # Optional (for mTLS)
            "client_key": "/path/to/client-key.pem"  # Optional (for mTLS)
        }
        """
        super().__init__(config)
        self._init_client()

    def _init_client(self) -> None:
        """Initialize Vault client session."""
        from apps.api.common.providers.vault import create_vault_session

        self.base_url = self.config["url"].rstrip("/")
        self.mount_point = self.config.get("mount_point", "transit")

        self.session = create_vault_session(self.config)
        logger.info(f"Initialized Vault Transit client for {self.base_url}")

    def _make_request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        """Make a request to Vault API."""
        url = f"{self.base_url}{path}"

        try:
            response = self.session.request(method, url, **kwargs)

            if response.status_code == 404:
                raise Exception(f"Resource not found at path: {path}")
            elif response.status_code == 403:
                raise Exception(f"Access denied to path: {path}")
            elif response.status_code >= 400:
                error_msg = (
                    response.json().get("errors", [response.text])[0]
                    if response.text
                    else "Unknown error"
                )
                raise Exception(f"Vault API error: {error_msg}")

            return response.json() if response.text else {}

        except requests.exceptions.RequestException as e:
            logger.error(f"Vault request failed: {str(e)}")
            raise Exception(f"Vault request failed: {str(e)}")

    def test_connection(self) -> bool:
        """Test connection to Vault Transit."""
        try:
            # Try to list keys to test connectivity
            self.list_keys(limit=1)
            logger.info("Vault Transit connection test successful")
            return True
        except Exception as e:
            logger.error(f"Vault Transit connection test failed: {str(e)}")
            return False

    def create_key(
        self,
        key_name: str,
        key_type: str = "symmetric",
        key_spec: str | None = None,
        description: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Create a new encryption key in Vault Transit.

        Transit key types:
        - symmetric: aes128-gcm96, aes256-gcm96, chacha20-poly1305
        - asymmetric: rsa-2048, rsa-3072, rsa-4096, ecdsa-p256, ecdsa-p384, ecdsa-p521, ed25519
        """
        try:
            # Map generic key types to Vault Transit types
            type_mapping = {
                "symmetric": key_spec or "aes256-gcm96",
                "asymmetric": key_spec or "rsa-2048",
                "hmac": "aes256-gcm96",  # Transit doesn't have dedicated HMAC type
            }

            vault_type = type_mapping.get(key_type, "aes256-gcm96")

            payload = {
                "type": vault_type,
                "derived": False,
                "exportable": False,
                "allow_plaintext_backup": False,
            }

            api_path = f"/v1/{self.mount_point}/keys/{key_name}"
            self._make_request("POST", api_path, json=payload)

            # Get key details
            key_info = self.get_key(key_name)

            logger.info(f"Created Transit key '{key_name}' (type: {vault_type})")

            return {
                "key_id": key_name,
                "key_arn": f"vault://{self.mount_point}/keys/{key_name}",
                "key_type": key_type,
                "state": key_info.get("state", "enabled"),
                "created_at": datetime.now().isoformat(),
            }

        except Exception as e:
            logger.error(f"Failed to create key '{key_name}': {str(e)}")
            raise Exception(f"Failed to create key: {str(e)}")

    def get_key(self, key_id: str) -> dict[str, Any]:
        """Get Transit key metadata."""
        try:
            api_path = f"/v1/{self.mount_point}/keys/{key_id}"
            response = self._make_request("GET", api_path)

            data = response.get("data", {})

            return {
                "key_id": key_id,
                "key_arn": f"vault://{self.mount_point}/keys/{key_id}",
                "key_type": data.get("type", "unknown"),
                "state": (
                    "enabled"
                    if not data.get("deletion_allowed", False)
                    else "pending_deletion"
                ),
                "created_at": None,  # Vault doesn't provide creation time
                "description": "",
                "enabled": True,
                "latest_version": data.get("latest_version"),
                "min_decryption_version": data.get("min_decryption_version"),
                "min_encryption_version": data.get("min_encryption_version"),
                "supports_encryption": data.get("supports_encryption", False),
                "supports_decryption": data.get("supports_decryption", False),
                "supports_derivation": data.get("supports_derivation", False),
                "supports_signing": data.get("supports_signing", False),
            }

        except Exception as e:
            logger.error(f"Failed to get key '{key_id}': {str(e)}")
            raise Exception(f"Failed to get key: {str(e)}")

    def list_keys(
        self, limit: int | None = None, next_token: str | None = None
    ) -> dict[str, Any]:
        """
        List all Transit keys.

        Note: Vault Transit doesn't support pagination, so limit and next_token are ignored.
        """
        try:
            api_path = f"/v1/{self.mount_point}/keys"
            response = self._make_request("LIST", api_path)

            key_names = response.get("data", {}).get("keys", [])

            keys = []
            for name in key_names[:limit] if limit else key_names:
                try:
                    key_info = self.get_key(name)
                    keys.append(key_info)
                except Exception as e:
                    logger.warning(f"Failed to get info for key '{name}': {str(e)}")
                    continue

            logger.info(f"Listed {len(keys)} Transit keys")

            return {
                "keys": keys,
                "next_token": None,  # Vault Transit doesn't support pagination
            }

        except Exception as e:
            logger.error(f"Failed to list keys: {str(e)}")
            raise Exception(f"Failed to list keys: {str(e)}")

    def enable_key(self, key_id: str) -> dict[str, Any]:
        """
        Enable a Transit key.

        Note: Vault Transit doesn't have explicit enable/disable, this updates config.
        """
        try:
            # In Vault Transit, we can re-allow encryption/decryption to "enable"
            api_path = f"/v1/{self.mount_point}/keys/{key_id}/config"
            payload = {
                "deletion_allowed": False,
                "exportable": False,
                "allow_plaintext_backup": False,
            }

            self._make_request("POST", api_path, json=payload)

            logger.info(f"Enabled Transit key '{key_id}'")

            return {"key_id": key_id, "state": "enabled"}

        except Exception as e:
            logger.error(f"Failed to enable key '{key_id}': {str(e)}")
            raise Exception(f"Failed to enable key: {str(e)}")

    def disable_key(self, key_id: str) -> dict[str, Any]:
        """
        Disable a Transit key (soft delete).

        Note: Vault Transit doesn't support disabling keys directly.
        This sets min_encryption_version very high to effectively disable new encryption.
        """
        try:
            # Get current key info
            key_info = self.get_key(key_id)
            latest_version = key_info.get("latest_version", 1)

            # Set min_encryption_version to a very high number to prevent new encryption
            api_path = f"/v1/{self.mount_point}/keys/{key_id}/config"
            payload = {"min_encryption_version": latest_version + 1000}

            self._make_request("POST", api_path, json=payload)

            logger.info(
                f"Disabled Transit key '{key_id}' (set min_encryption_version high)"
            )

            return {"key_id": key_id, "state": "disabled"}

        except Exception as e:
            logger.error(f"Failed to disable key '{key_id}': {str(e)}")
            raise Exception(f"Failed to disable key: {str(e)}")

    def schedule_key_deletion(
        self, key_id: str, pending_days: int = 30
    ) -> dict[str, Any]:
        """
        Schedule Transit key deletion.

        Note: Vault Transit requires deletion_allowed=true before deletion.
        Transit doesn't support pending periods, so pending_days is ignored.
        """
        try:
            # First, allow deletion
            config_path = f"/v1/{self.mount_point}/keys/{key_id}/config"
            self._make_request("POST", config_path, json={"deletion_allowed": True})

            logger.info(
                f"Scheduled deletion for Transit key '{key_id}' (deletion_allowed=true)"
            )

            return {
                "key_id": key_id,
                "state": "pending_deletion",
                "deletion_date": None,  # Transit doesn't have pending periods
            }

        except Exception as e:
            logger.error(f"Failed to schedule deletion for key '{key_id}': {str(e)}")
            raise Exception(f"Failed to schedule key deletion: {str(e)}")

    def cancel_key_deletion(self, key_id: str) -> dict[str, Any]:
        """Cancel scheduled Transit key deletion."""
        try:
            # Disallow deletion
            config_path = f"/v1/{self.mount_point}/keys/{key_id}/config"
            self._make_request("POST", config_path, json={"deletion_allowed": False})

            logger.info(f"Cancelled deletion for Transit key '{key_id}'")

            return {"key_id": key_id, "state": "enabled"}

        except Exception as e:
            logger.error(f"Failed to cancel deletion for key '{key_id}': {str(e)}")
            raise Exception(f"Failed to cancel key deletion: {str(e)}")

    def encrypt(
        self, key_id: str, plaintext: str, context: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Encrypt data using a Transit key."""
        try:
            # Vault Transit requires base64-encoded plaintext
            plaintext_b64 = base64.b64encode(plaintext.encode()).decode()

            payload = {"plaintext": plaintext_b64}

            if context:
                # Transit uses "context" as base64-encoded string
                context_str = str(context)
                payload["context"] = base64.b64encode(context_str.encode()).decode()

            api_path = f"/v1/{self.mount_point}/encrypt/{key_id}"
            response = self._make_request("POST", api_path, json=payload)

            ciphertext = response.get("data", {}).get("ciphertext")

            return {
                "ciphertext": ciphertext,
                "key_id": key_id,
            }

        except Exception as e:
            logger.error(f"Failed to encrypt with key '{key_id}': {str(e)}")
            raise Exception(f"Failed to encrypt: {str(e)}")

    def decrypt(
        self, ciphertext: str, context: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """
        Decrypt data using Vault Transit.

        Note: Transit ciphertext includes the key version prefix (vault:v1:...)
        so we can extract the key_id from it.
        """
        try:
            # Extract key_id from ciphertext prefix if possible
            # Format: vault:v1:base64ciphertext
            key_id = ciphertext.split(":")[0] if ":" in ciphertext else None

            if not key_id or key_id == "vault":
                # Ciphertext doesn't contain key info, need to try all keys
                # This is not ideal, but Transit requires knowing which key to use
                raise Exception(
                    "Cannot determine key_id from ciphertext, Transit requires explicit key_id"
                )

            payload = {"ciphertext": ciphertext}

            if context:
                context_str = str(context)
                payload["context"] = base64.b64encode(context_str.encode()).decode()

            # We'll need to modify the signature to accept key_id or infer it somehow
            # For now, raise an error indicating this limitation
            raise Exception(
                "Decrypt requires key_id to be passed separately for Vault Transit"
            )

        except Exception as e:
            logger.error(f"Failed to decrypt: {str(e)}")
            raise Exception(f"Failed to decrypt: {str(e)}")

    def decrypt_with_key(
        self, key_id: str, ciphertext: str, context: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Decrypt data using a specific Transit key."""
        try:
            payload = {"ciphertext": ciphertext}

            if context:
                context_str = str(context)
                payload["context"] = base64.b64encode(context_str.encode()).decode()

            api_path = f"/v1/{self.mount_point}/decrypt/{key_id}"
            response = self._make_request("POST", api_path, json=payload)

            plaintext_b64 = response.get("data", {}).get("plaintext")
            plaintext = base64.b64decode(plaintext_b64).decode()

            return {
                "plaintext": plaintext,
                "key_id": key_id,
            }

        except Exception as e:
            logger.error(f"Failed to decrypt with key '{key_id}': {str(e)}")
            raise Exception(f"Failed to decrypt: {str(e)}")

    def generate_data_key(
        self,
        key_id: str,
        key_spec: str = "AES_256",
        context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Generate a data encryption key using Transit.

        Transit supports datakey generation with the key being wrapped.
        """
        try:
            # Map key_spec to bits
            bits_mapping = {
                "AES_256": 256,
                "AES_128": 128,
            }
            bits = bits_mapping.get(key_spec, 256)

            payload = {"bits": bits}

            if context:
                context_str = str(context)
                payload["context"] = base64.b64encode(context_str.encode()).decode()

            api_path = f"/v1/{self.mount_point}/datakey/plaintext/{key_id}"
            response = self._make_request("POST", api_path, json=payload)

            data = response.get("data", {})

            return {
                "plaintext_key": data.get("plaintext"),
                "ciphertext_key": data.get("ciphertext"),
                "key_id": key_id,
            }

        except Exception as e:
            logger.error(f"Failed to generate data key with '{key_id}': {str(e)}")
            raise Exception(f"Failed to generate data key: {str(e)}")

    def sign(
        self,
        key_id: str,
        message: str,
        signing_algorithm: str = "RSASSA_PSS_SHA_256",
    ) -> dict[str, Any]:
        """Sign a message using Transit."""
        try:
            # Map signing algorithm
            algo_mapping = {
                "RSASSA_PSS_SHA_256": "pss",
                "RSASSA_PKCS1_SHA_256": "pkcs1v15",
                "ECDSA_SHA_256": "ecdsa",
            }

            vault_algo = algo_mapping.get(signing_algorithm, "pss")

            # Transit requires base64-encoded input
            input_b64 = base64.b64encode(message.encode()).decode()

            payload = {"input": input_b64, "signature_algorithm": vault_algo}

            api_path = f"/v1/{self.mount_point}/sign/{key_id}"
            response = self._make_request("POST", api_path, json=payload)

            signature = response.get("data", {}).get("signature")

            return {
                "signature": signature,
                "key_id": key_id,
                "algorithm": signing_algorithm,
            }

        except Exception as e:
            logger.error(f"Failed to sign with key '{key_id}': {str(e)}")
            raise Exception(f"Failed to sign: {str(e)}")

    def verify(
        self, key_id: str, message: str, signature: str, signing_algorithm: str
    ) -> dict[str, Any]:
        """Verify a message signature using Transit."""
        try:
            algo_mapping = {
                "RSASSA_PSS_SHA_256": "pss",
                "RSASSA_PKCS1_SHA_256": "pkcs1v15",
                "ECDSA_SHA_256": "ecdsa",
            }

            vault_algo = algo_mapping.get(signing_algorithm, "pss")

            input_b64 = base64.b64encode(message.encode()).decode()

            payload = {
                "input": input_b64,
                "signature": signature,
                "signature_algorithm": vault_algo,
            }

            api_path = f"/v1/{self.mount_point}/verify/{key_id}"
            response = self._make_request("POST", api_path, json=payload)

            valid = response.get("data", {}).get("valid", False)

            return {
                "valid": valid,
                "key_id": key_id,
            }

        except Exception as e:
            logger.error(f"Failed to verify signature with key '{key_id}': {str(e)}")
            raise Exception(f"Failed to verify: {str(e)}")

    def rotate_key(self, key_id: str) -> dict[str, Any]:
        """Rotate a Transit key (create new version)."""
        try:
            api_path = f"/v1/{self.mount_point}/keys/{key_id}/rotate"
            self._make_request("POST", api_path)

            # Get updated key info
            key_info = self.get_key(key_id)

            logger.info(
                f"Rotated Transit key '{key_id}' to version {key_info.get('latest_version')}"
            )

            return {
                "key_id": key_id,
                "latest_version": key_info.get("latest_version"),
                "rotation_date": datetime.now().isoformat(),
            }

        except Exception as e:
            logger.error(f"Failed to rotate key '{key_id}': {str(e)}")
            raise Exception(f"Failed to rotate key: {str(e)}")
