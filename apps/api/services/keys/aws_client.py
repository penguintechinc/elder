"""AWS KMS client for key management operations."""

# flake8: noqa: E501

import base64
from typing import Any, Dict, Optional

try:
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    ClientError = Exception
    BotoCoreError = Exception

from apps.api.common.providers.aws import create_aws_session_and_client
from apps.api.services.keys.base import BaseKeyProvider


class AWSKMSClient(BaseKeyProvider):
    """AWS KMS implementation of key management provider."""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize AWS KMS client.

        Args:
            config: Configuration dictionary with:
                - region: AWS region (e.g., 'us-east-1')
                - access_key_id: Optional AWS access key ID
                - secret_access_key: Optional AWS secret access key
                - session_token: Optional AWS session token
                - endpoint_url: Optional custom KMS endpoint
        """
        super().__init__(config)
        self.client = create_aws_session_and_client(config, "kms")

    def create_key(
        self,
        key_name: str,
        key_type: str = "symmetric",
        key_spec: str | None = None,
        description: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Create a new KMS key.

        Args:
            key_name: Alias for the key (will be prefixed with 'alias/')
            key_type: Type of key (symmetric, asymmetric, hmac)
            key_spec: Key specification (SYMMETRIC_DEFAULT, RSA_2048, RSA_4096, etc.)
            description: Key description
            tags: Key tags

        Returns:
            Dictionary with key details
        """
        try:
            # Map key types to AWS key specs
            if not key_spec:
                key_spec_map = {
                    "symmetric": "SYMMETRIC_DEFAULT",
                    "asymmetric": "RSA_2048",
                    "hmac": "HMAC_256",
                }
                key_spec = key_spec_map.get(key_type, "SYMMETRIC_DEFAULT")

            # Create key
            create_params = {
                "Description": description or f"Elder managed key: {key_name}",
                "KeyUsage": (
                    "ENCRYPT_DECRYPT" if key_type != "hmac" else "GENERATE_VERIFY_MAC"
                ),
                "KeySpec": key_spec,
            }

            # Add tags if provided
            if tags:
                create_params["Tags"] = [
                    {"TagKey": k, "TagValue": v} for k, v in tags.items()
                ]

            response = self.client.create_key(**create_params)
            key_metadata = response["KeyMetadata"]

            # Create alias
            alias_name = (
                f"alias/{key_name}" if not key_name.startswith("alias/") else key_name
            )
            try:
                self.client.create_alias(
                    AliasName=alias_name, TargetKeyId=key_metadata["KeyId"]
                )
            except ClientError as e:
                # Alias might already exist
                if e.response["Error"]["Code"] != "AlreadyExistsException":
                    raise

            return {
                "key_id": key_metadata["KeyId"],
                "key_arn": key_metadata["Arn"],
                "key_type": key_type,
                "key_spec": key_spec,
                "state": key_metadata["KeyState"],
                "created_at": key_metadata["CreationDate"].isoformat(),
                "alias": alias_name,
                "description": description,
            }

        except ClientError as e:
            raise Exception(
                f"AWS KMS create key error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS KMS create key error: {str(e)}")

    def get_key(self, key_id: str) -> dict[str, Any]:
        """
        Get key metadata.

        Args:
            key_id: Key ID, ARN, or alias

        Returns:
            Dictionary with key metadata
        """
        try:
            response = self.client.describe_key(KeyId=key_id)
            key_metadata = response["KeyMetadata"]

            # Get aliases
            aliases_response = self.client.list_aliases(KeyId=key_metadata["KeyId"])
            aliases = [
                alias["AliasName"] for alias in aliases_response.get("Aliases", [])
            ]

            return {
                "key_id": key_metadata["KeyId"],
                "key_arn": key_metadata["Arn"],
                "state": key_metadata["KeyState"],
                "enabled": key_metadata["Enabled"],
                "created_at": key_metadata["CreationDate"].isoformat(),
                "description": key_metadata.get("Description", ""),
                "key_usage": key_metadata.get("KeyUsage"),
                "key_spec": key_metadata.get("KeySpec"),
                "aliases": aliases,
                "deletion_date": (
                    key_metadata.get("DeletionDate").isoformat()
                    if key_metadata.get("DeletionDate")
                    else None
                ),
            }

        except ClientError as e:
            raise Exception(f"AWS KMS get key error: {e.response['Error']['Message']}")
        except Exception as e:
            raise Exception(f"AWS KMS get key error: {str(e)}")

    def list_keys(
        self, limit: int | None = None, next_token: str | None = None
    ) -> dict[str, Any]:
        """
        List all KMS keys.

        Args:
            limit: Maximum number of keys to return
            next_token: Pagination token

        Returns:
            Dictionary with keys list and pagination token
        """
        try:
            params = {}
            if limit:
                params["Limit"] = min(limit, 1000)  # AWS max is 1000
            if next_token:
                params["Marker"] = next_token

            response = self.client.list_keys(**params)

            keys = []
            for key in response.get("Keys", []):
                try:
                    # Get detailed metadata for each key
                    key_details = self.get_key(key["KeyId"])
                    keys.append(key_details)
                except Exception:
                    # Skip keys we can't access
                    continue

            return {
                "keys": keys,
                "next_token": response.get("NextMarker"),
                "truncated": response.get("Truncated", False),
            }

        except ClientError as e:
            raise Exception(
                f"AWS KMS list keys error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS KMS list keys error: {str(e)}")

    def enable_key(self, key_id: str) -> dict[str, Any]:
        """Enable a disabled key."""
        try:
            self.client.enable_key(KeyId=key_id)
            return self.get_key(key_id)

        except ClientError as e:
            raise Exception(
                f"AWS KMS enable key error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS KMS enable key error: {str(e)}")

    def disable_key(self, key_id: str) -> dict[str, Any]:
        """Disable a key."""
        try:
            self.client.disable_key(KeyId=key_id)
            return self.get_key(key_id)

        except ClientError as e:
            raise Exception(
                f"AWS KMS disable key error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS KMS disable key error: {str(e)}")

    def schedule_key_deletion(
        self, key_id: str, pending_days: int = 30
    ) -> dict[str, Any]:
        """
        Schedule key deletion.

        Args:
            key_id: Key ID, ARN, or alias
            pending_days: Days before deletion (7-30)

        Returns:
            Dictionary with deletion details
        """
        try:
            # AWS KMS requires 7-30 days
            pending_days = max(7, min(pending_days, 30))

            response = self.client.schedule_key_deletion(
                KeyId=key_id, PendingWindowInDays=pending_days
            )

            return {
                "key_id": response["KeyId"],
                "deletion_date": response["DeletionDate"].isoformat(),
                "state": "PendingDeletion",
            }

        except ClientError as e:
            raise Exception(
                f"AWS KMS schedule key deletion error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS KMS schedule key deletion error: {str(e)}")

    def cancel_key_deletion(self, key_id: str) -> dict[str, Any]:
        """Cancel scheduled key deletion."""
        try:
            response = self.client.cancel_key_deletion(KeyId=key_id)

            return {
                "key_id": response["KeyId"],
                "state": "Disabled",  # Keys are disabled after canceling deletion
            }

        except ClientError as e:
            raise Exception(
                f"AWS KMS cancel key deletion error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS KMS cancel key deletion error: {str(e)}")

    def encrypt(
        self, key_id: str, plaintext: str, context: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """
        Encrypt data using KMS key.

        Args:
            key_id: Key ID, ARN, or alias
            plaintext: Data to encrypt
            context: Optional encryption context

        Returns:
            Dictionary with ciphertext
        """
        try:
            params = {"KeyId": key_id, "Plaintext": plaintext.encode("utf-8")}

            if context:
                params["EncryptionContext"] = context

            response = self.client.encrypt(**params)

            return {
                "ciphertext": base64.b64encode(response["CiphertextBlob"]).decode(
                    "utf-8"
                ),
                "key_id": response["KeyId"],
            }

        except ClientError as e:
            raise Exception(f"AWS KMS encrypt error: {e.response['Error']['Message']}")
        except Exception as e:
            raise Exception(f"AWS KMS encrypt error: {str(e)}")

    def decrypt(
        self, ciphertext: str, context: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """
        Decrypt data.

        Args:
            ciphertext: Base64-encoded ciphertext
            context: Optional encryption context (must match encryption)

        Returns:
            Dictionary with plaintext
        """
        try:
            ciphertext_blob = base64.b64decode(ciphertext)

            params = {"CiphertextBlob": ciphertext_blob}

            if context:
                params["EncryptionContext"] = context

            response = self.client.decrypt(**params)

            return {
                "plaintext": response["Plaintext"].decode("utf-8"),
                "key_id": response["KeyId"],
            }

        except ClientError as e:
            raise Exception(f"AWS KMS decrypt error: {e.response['Error']['Message']}")
        except Exception as e:
            raise Exception(f"AWS KMS decrypt error: {str(e)}")

    def generate_data_key(
        self,
        key_id: str,
        key_spec: str = "AES_256",
        context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Generate a data encryption key.

        Args:
            key_id: Master key ID, ARN, or alias
            key_spec: Data key spec (AES_256 or AES_128)
            context: Optional encryption context

        Returns:
            Dictionary with plaintext and encrypted data key
        """
        try:
            params = {"KeyId": key_id, "KeySpec": key_spec}

            if context:
                params["EncryptionContext"] = context

            response = self.client.generate_data_key(**params)

            return {
                "plaintext_key": base64.b64encode(response["Plaintext"]).decode(
                    "utf-8"
                ),
                "ciphertext_key": base64.b64encode(response["CiphertextBlob"]).decode(
                    "utf-8"
                ),
                "key_id": response["KeyId"],
            }

        except ClientError as e:
            raise Exception(
                f"AWS KMS generate data key error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS KMS generate data key error: {str(e)}")

    def sign(
        self,
        key_id: str,
        message: str,
        signing_algorithm: str = "RSASSA_PSS_SHA_256",
    ) -> dict[str, Any]:
        """
        Sign a message using an asymmetric KMS key.

        Args:
            key_id: Asymmetric key ID, ARN, or alias
            message: Message to sign
            signing_algorithm: Signing algorithm

        Returns:
            Dictionary with signature
        """
        try:
            response = self.client.sign(
                KeyId=key_id,
                Message=message.encode("utf-8"),
                SigningAlgorithm=signing_algorithm,
            )

            return {
                "signature": base64.b64encode(response["Signature"]).decode("utf-8"),
                "key_id": response["KeyId"],
                "algorithm": response["SigningAlgorithm"],
            }

        except ClientError as e:
            raise Exception(f"AWS KMS sign error: {e.response['Error']['Message']}")
        except Exception as e:
            raise Exception(f"AWS KMS sign error: {str(e)}")

    def verify(
        self, key_id: str, message: str, signature: str, signing_algorithm: str
    ) -> dict[str, Any]:
        """
        Verify a message signature.

        Args:
            key_id: Asymmetric key ID, ARN, or alias
            message: Original message
            signature: Base64-encoded signature
            signing_algorithm: Signing algorithm used

        Returns:
            Dictionary with verification result
        """
        try:
            signature_bytes = base64.b64decode(signature)

            response = self.client.verify(
                KeyId=key_id,
                Message=message.encode("utf-8"),
                Signature=signature_bytes,
                SigningAlgorithm=signing_algorithm,
            )

            return {
                "valid": response["SignatureValid"],
                "key_id": response["KeyId"],
            }

        except ClientError as e:
            raise Exception(f"AWS KMS verify error: {e.response['Error']['Message']}")
        except Exception as e:
            raise Exception(f"AWS KMS verify error: {str(e)}")

    def rotate_key(self, key_id: str) -> dict[str, Any]:
        """
        Enable automatic key rotation or rotate key immediately.

        Args:
            key_id: Key ID, ARN, or alias

        Returns:
            Dictionary with rotation details
        """
        try:
            # Enable automatic key rotation (AWS rotates annually)
            self.client.enable_key_rotation(KeyId=key_id)

            # Get rotation status
            rotation_status = self.client.get_key_rotation_status(KeyId=key_id)

            return {
                "key_id": key_id,
                "rotation_enabled": rotation_status["KeyRotationEnabled"],
                "message": "Automatic key rotation enabled (rotates annually)",
            }

        except ClientError as e:
            raise Exception(
                f"AWS KMS rotate key error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS KMS rotate key error: {str(e)}")

    def test_connection(self) -> bool:
        """
        Test AWS KMS connectivity.

        Returns:
            True if connection successful
        """
        try:
            # Simple API call to test connectivity
            self.client.list_keys(Limit=1)
            return True
        except Exception:
            return False
