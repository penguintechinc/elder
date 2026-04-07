"""GCP Cloud KMS client for key management operations."""

# flake8: noqa: E501


import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

try:
    from google.api_core import exceptions as google_exceptions
    from google.cloud import kms
    from google.oauth2 import service_account
except ImportError:
    kms = None
    google_exceptions = None
    service_account = None

from apps.api.services.keys.base import BaseKeyProvider


class GCPKMSClient(BaseKeyProvider):
    """Google Cloud KMS implementation of key management provider."""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize GCP Cloud KMS client.

        Args:
            config: Configuration dictionary with:
                - project_id: GCP project ID
                - location_id: KMS location (e.g., 'us-east1', 'global')
                - keyring_id: Key ring ID (will be created if doesn't exist)
                - credentials_json: Optional service account JSON
        """
        super().__init__(config)

        if kms is None:
            raise ImportError(
                "google-cloud-kms is required for GCP KMS. "
                "Install with: pip install google-cloud-kms"
            )

        self.project_id = config.get("project_id")
        self.location_id = config.get("location_id", "us-east1")
        self.keyring_id = config.get("keyring_id", "elder-keyring")

        if not self.project_id:
            raise ValueError("project_id is required for GCP KMS")

        # Initialize client with credentials if provided
        if config.get("credentials_json"):
            credentials = service_account.Credentials.from_service_account_info(
                json.loads(config["credentials_json"])
            )
            self.client = kms.KeyManagementServiceClient(credentials=credentials)
        else:
            # Use default credentials (from GOOGLE_APPLICATION_CREDENTIALS env var)
            self.client = kms.KeyManagementServiceClient()

        # Ensure key ring exists
        self._ensure_keyring_exists()

    def _ensure_keyring_exists(self):
        """Create key ring if it doesn't exist."""
        try:
            location_name = self.client.location_path(self.project_id, self.location_id)
            keyring_name = self.client.key_ring_path(
                self.project_id, self.location_id, self.keyring_id
            )

            try:
                self.client.get_key_ring(name=keyring_name)
            except google_exceptions.NotFound:
                # Create key ring
                self.client.create_key_ring(
                    request={"parent": location_name, "key_ring_id": self.keyring_id}
                )
        except Exception:
            # Ignore errors - keyring might exist or we lack permissions
            pass

    def create_key(
        self,
        key_name: str,
        key_type: str = "symmetric",
        key_spec: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new Cloud KMS key.

        Args:
            key_name: Name for the crypto key
            key_type: Type of key (symmetric, asymmetric, hmac)
            key_spec: Key specification
            description: Key description
            tags: Key labels

        Returns:
            Dictionary with key details
        """
        try:
            parent = self.client.key_ring_path(
                self.project_id, self.location_id, self.keyring_id
            )

            # Map key types to GCP purposes
            purpose_map = {
                "symmetric": kms.CryptoKey.CryptoKeyPurpose.ENCRYPT_DECRYPT,
                "asymmetric": kms.CryptoKey.CryptoKeyPurpose.ASYMMETRIC_SIGN,
                "hmac": kms.CryptoKey.CryptoKeyPurpose.MAC,
            }
            purpose = purpose_map.get(
                key_type, kms.CryptoKey.CryptoKeyPurpose.ENCRYPT_DECRYPT
            )

            # Build crypto key
            crypto_key = {
                "purpose": purpose,
                "version_template": {},
            }

            # Set algorithm based on key type
            if key_type == "symmetric":
                crypto_key["version_template"][
                    "algorithm"
                ] = (
                    kms.CryptoKeyVersion.CryptoKeyVersionAlgorithm.GOOGLE_SYMMETRIC_ENCRYPTION
                )
            elif key_type == "asymmetric":
                crypto_key["version_template"][
                    "algorithm"
                ] = (
                    kms.CryptoKeyVersion.CryptoKeyVersionAlgorithm.RSA_SIGN_PSS_2048_SHA256
                )
            elif key_type == "hmac":
                crypto_key["version_template"][
                    "algorithm"
                ] = kms.CryptoKeyVersion.CryptoKeyVersionAlgorithm.HMAC_SHA256

            # Add labels if provided
            if tags:
                crypto_key["labels"] = tags

            # Create the key
            request = {
                "parent": parent,
                "crypto_key_id": key_name,
                "crypto_key": crypto_key,
            }

            created_key = self.client.create_crypto_key(request=request)

            return {
                "key_id": key_name,
                "key_arn": created_key.name,
                "key_type": key_type,
                "state": self._translate_state(created_key.primary.state),
                "created_at": created_key.create_time.isoformat(),
                "description": description,
                "labels": tags or {},
            }

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP KMS create key error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP KMS create key error: {str(e)}")

    def get_key(self, key_id: str) -> Dict[str, Any]:
        """
        Get key metadata.

        Args:
            key_id: Crypto key ID or full resource name

        Returns:
            Dictionary with key metadata
        """
        try:
            # Build full key name if needed
            if not key_id.startswith("projects/"):
                key_name = self.client.crypto_key_path(
                    self.project_id, self.location_id, self.keyring_id, key_id
                )
            else:
                key_name = key_id

            crypto_key = self.client.get_crypto_key(name=key_name)

            return {
                "key_id": crypto_key.name.split("/")[-1],
                "key_arn": crypto_key.name,
                "state": self._translate_state(crypto_key.primary.state),
                "enabled": crypto_key.primary.state
                == kms.CryptoKeyVersion.CryptoKeyVersionState.ENABLED,
                "created_at": crypto_key.create_time.isoformat(),
                "purpose": crypto_key.purpose.name,
                "algorithm": crypto_key.version_template.algorithm.name,
                "labels": dict(crypto_key.labels),
                "rotation_period": (
                    crypto_key.rotation_period.seconds
                    if crypto_key.rotation_period
                    else None
                ),
            }

        except google_exceptions.NotFound:
            raise Exception(f"GCP KMS key not found: {key_id}")
        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP KMS get key error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP KMS get key error: {str(e)}")

    def list_keys(
        self, limit: Optional[int] = None, next_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        List all Cloud KMS keys.

        Args:
            limit: Maximum number of keys to return
            next_token: Pagination token

        Returns:
            Dictionary with keys list and pagination token
        """
        try:
            parent = self.client.key_ring_path(
                self.project_id, self.location_id, self.keyring_id
            )

            request = {"parent": parent}
            if limit:
                request["page_size"] = limit
            if next_token:
                request["page_token"] = next_token

            response = self.client.list_crypto_keys(request=request)

            keys = []
            for crypto_key in response:
                keys.append(
                    {
                        "key_id": crypto_key.name.split("/")[-1],
                        "key_arn": crypto_key.name,
                        "state": self._translate_state(crypto_key.primary.state),
                        "created_at": crypto_key.create_time.isoformat(),
                        "purpose": crypto_key.purpose.name,
                    }
                )

            return {
                "keys": keys,
                "next_token": (
                    response.next_page_token if response.next_page_token else None
                ),
            }

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP KMS list keys error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP KMS list keys error: {str(e)}")

    def enable_key(self, key_id: str) -> Dict[str, Any]:
        """Enable a disabled key version."""
        # GCP doesn't have a direct enable/disable - we restore from destroyed state
        raise NotImplementedError("GCP KMS doesn't support direct key enable")

    def disable_key(self, key_id: str) -> Dict[str, Any]:
        """Disable a key version."""
        try:
            # Get primary version
            key_data = self.get_key(key_id)
            version_name = f"{key_data['key_arn']}/cryptoKeyVersions/1"

            # Disable the version
            update_mask = {"paths": ["state"]}
            crypto_key_version = {
                "name": version_name,
                "state": kms.CryptoKeyVersion.CryptoKeyVersionState.DISABLED,
            }

            self.client.update_crypto_key_version(
                request={
                    "crypto_key_version": crypto_key_version,
                    "update_mask": update_mask,
                }
            )

            return self.get_key(key_id)

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP KMS disable key error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP KMS disable key error: {str(e)}")

    def schedule_key_deletion(
        self, key_id: str, pending_days: int = 30
    ) -> Dict[str, Any]:
        """
        Schedule key deletion.

        GCP KMS uses destroy instead of deletion, and doesn't support pending periods.
        """
        try:
            key_data = self.get_key(key_id)
            version_name = f"{key_data['key_arn']}/cryptoKeyVersions/1"

            # Schedule destruction immediately
            self.client.destroy_crypto_key_version(name=version_name)

            return {
                "key_id": key_id,
                "state": "DESTROY_SCHEDULED",
                "message": "GCP KMS destroys keys immediately (no pending period)",
            }

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP KMS schedule deletion error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP KMS schedule deletion error: {str(e)}")

    def cancel_key_deletion(self, key_id: str) -> Dict[str, Any]:
        """Cancel scheduled key deletion."""
        # GCP doesn't support canceling destruction
        raise NotImplementedError("GCP KMS doesn't support canceling key destruction")

    def encrypt(
        self, key_id: str, plaintext: str, context: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Encrypt data using Cloud KMS key.

        Args:
            key_id: Crypto key ID or full resource name
            plaintext: Data to encrypt
            context: Optional additional authenticated data

        Returns:
            Dictionary with ciphertext
        """
        try:
            # Build full key name
            if not key_id.startswith("projects/"):
                key_name = self.client.crypto_key_path(
                    self.project_id, self.location_id, self.keyring_id, key_id
                )
            else:
                key_name = key_id

            plaintext_bytes = plaintext.encode("utf-8")

            request = {"name": key_name, "plaintext": plaintext_bytes}

            # Add additional authenticated data if provided
            if context:
                # GCP expects a single AAD field, so we JSON encode the context
                request["additional_authenticated_data"] = json.dumps(context).encode(
                    "utf-8"
                )

            response = self.client.encrypt(request=request)

            return {
                "ciphertext": base64.b64encode(response.ciphertext).decode("utf-8"),
                "key_id": key_id,
            }

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP KMS encrypt error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP KMS encrypt error: {str(e)}")

    def decrypt(
        self, ciphertext: str, context: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Decrypt data.

        Args:
            ciphertext: Base64-encoded ciphertext
            context: Optional additional authenticated data (must match encryption)

        Returns:
            Dictionary with plaintext
        """
        try:
            # GCP stores the key ID in the ciphertext, so we don't need to specify it
            # We need to use the key ring parent for decrypt
            parent = self.client.key_ring_path(
                self.project_id, self.location_id, self.keyring_id
            )

            ciphertext_bytes = base64.b64decode(ciphertext)

            request = {"name": parent, "ciphertext": ciphertext_bytes}

            # Add AAD if provided
            if context:
                request["additional_authenticated_data"] = json.dumps(context).encode(
                    "utf-8"
                )

            response = self.client.decrypt(request=request)

            return {
                "plaintext": response.plaintext.decode("utf-8"),
                "key_id": "embedded",  # GCP embeds key info in ciphertext
            }

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP KMS decrypt error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP KMS decrypt error: {str(e)}")

    def generate_data_key(
        self,
        key_id: str,
        key_spec: str = "AES_256",
        context: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Generate a data encryption key.

        GCP KMS doesn't have a native generate_data_key function,
        so we generate a random key and encrypt it.
        """
        try:
            import secrets

            # Generate random key
            key_length = 32 if key_spec == "AES_256" else 16  # AES_128
            plaintext_key = secrets.token_bytes(key_length)

            # Encrypt the key with KMS
            encrypted = self.encrypt(
                key_id, base64.b64encode(plaintext_key).decode("utf-8"), context
            )

            return {
                "plaintext_key": base64.b64encode(plaintext_key).decode("utf-8"),
                "ciphertext_key": encrypted["ciphertext"],
                "key_id": key_id,
            }

        except Exception as e:
            raise Exception(f"GCP KMS generate data key error: {str(e)}")

    def sign(
        self,
        key_id: str,
        message: str,
        signing_algorithm: str = "RSA_SIGN_PSS_2048_SHA256",
    ) -> Dict[str, Any]:
        """
        Sign a message using an asymmetric key.

        Args:
            key_id: Asymmetric crypto key ID
            message: Message to sign
            signing_algorithm: GCP signing algorithm

        Returns:
            Dictionary with signature
        """
        try:
            # Build key version name (use primary version)
            if not key_id.startswith("projects/"):
                key_name = self.client.crypto_key_path(
                    self.project_id, self.location_id, self.keyring_id, key_id
                )
                version_name = f"{key_name}/cryptoKeyVersions/1"
            else:
                version_name = f"{key_id}/cryptoKeyVersions/1"

            # Compute digest (GCP requires pre-computed digest)
            import hashlib

            digest = hashlib.sha256(message.encode("utf-8")).digest()

            request = {
                "name": version_name,
                "digest": {"sha256": digest},
            }

            response = self.client.asymmetric_sign(request=request)

            return {
                "signature": base64.b64encode(response.signature).decode("utf-8"),
                "key_id": key_id,
                "algorithm": signing_algorithm,
            }

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP KMS sign error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP KMS sign error: {str(e)}")

    def verify(
        self, key_id: str, message: str, signature: str, signing_algorithm: str
    ) -> Dict[str, Any]:
        """
        Verify a message signature.

        GCP KMS doesn't support direct verification, so we export the public key
        and verify locally.
        """
        raise NotImplementedError("GCP KMS verify not yet implemented")

    def rotate_key(self, key_id: str) -> Dict[str, Any]:
        """
        Enable automatic key rotation or rotate key immediately.

        Args:
            key_id: Crypto key ID

        Returns:
            Dictionary with rotation details
        """
        try:
            # Build full key name
            if not key_id.startswith("projects/"):
                key_name = self.client.crypto_key_path(
                    self.project_id, self.location_id, self.keyring_id, key_id
                )
            else:
                key_name = key_id

            # Update to enable automatic rotation (every 90 days)
            from google.protobuf import duration_pb2

            rotation_period = duration_pb2.Duration()
            rotation_period.seconds = 90 * 24 * 60 * 60  # 90 days

            crypto_key = {
                "name": key_name,
                "rotation_period": rotation_period,
                "next_rotation_time": datetime.now(timezone.utc) + timedelta(days=90),
            }

            update_mask = {"paths": ["rotation_period", "next_rotation_time"]}

            updated_key = self.client.update_crypto_key(
                request={"crypto_key": crypto_key, "update_mask": update_mask}
            )

            return {
                "key_id": key_id,
                "rotation_enabled": True,
                "rotation_period_days": 90,
                "next_rotation": updated_key.next_rotation_time.isoformat(),
            }

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP KMS rotate key error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP KMS rotate key error: {str(e)}")

    def test_connection(self) -> bool:
        """
        Test GCP Cloud KMS connectivity.

        Returns:
            True if connection successful
        """
        try:
            # Try to list key rings
            location_name = self.client.location_path(self.project_id, self.location_id)
            list(
                self.client.list_key_rings(
                    request={"parent": location_name, "page_size": 1}
                )
            )
            return True
        except Exception:
            return False

    def _translate_state(self, gcp_state) -> str:
        """Translate GCP key state to normalized state."""
        state_map = {
            kms.CryptoKeyVersion.CryptoKeyVersionState.ENABLED: "Enabled",
            kms.CryptoKeyVersion.CryptoKeyVersionState.DISABLED: "Disabled",
            kms.CryptoKeyVersion.CryptoKeyVersionState.DESTROYED: "Destroyed",
            kms.CryptoKeyVersion.CryptoKeyVersionState.DESTROY_SCHEDULED: "PendingDeletion",
        }
        return state_map.get(gcp_state, "Unknown")
