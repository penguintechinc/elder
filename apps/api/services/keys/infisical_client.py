"""Infisical client for key management operations."""

# flake8: noqa: E501


import base64
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    import requests
except ImportError:
    requests = None

from apps.api.services.keys.base import BaseKeyProvider


class InfisicalClient(BaseKeyProvider):
    """Infisical implementation of key management provider."""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Infisical client.

        Args:
            config: Configuration dictionary with:
                - host: Infisical host (e.g., 'https://app.infisical.com')
                - token: Service token or API key
                - workspace_id: Workspace/project ID
        """
        super().__init__(config)

        if requests is None:
            raise ImportError(
                "requests is required for Infisical. Install with: pip install requests"
            )

        self.host = config.get("host", "https://app.infisical.com").rstrip("/")
        self.token = config.get("token")
        self.workspace_id = config.get("workspace_id")

        if not self.token:
            raise ValueError("token is required for Infisical")

        if not self.workspace_id:
            raise ValueError("workspace_id is required for Infisical")

        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        # Infisical API version
        self.api_version = "v3"
        self.base_url = f"{self.host}/api/{self.api_version}"

    def create_key(
        self,
        key_name: str,
        key_type: str = "symmetric",
        key_spec: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new encryption key in Infisical.

        Infisical doesn't have native KMS, so we generate and store a key.

        Args:
            key_name: Name for the key
            key_type: Type of key (symmetric, asymmetric, hmac)
            key_spec: Key specification
            description: Key description
            tags: Key tags

        Returns:
            Dictionary with key details
        """
        try:
            import secrets

            # Generate key material based on type
            if key_type == "symmetric":
                # Generate 256-bit symmetric key
                key_material = secrets.token_bytes(32)
                key_data = {
                    "type": "symmetric",
                    "algorithm": key_spec or "AES-256-GCM",
                    "material": base64.b64encode(key_material).decode("utf-8"),
                }
            elif key_type == "asymmetric":
                # For asymmetric, we'd use cryptography library to generate key pair
                # For now, store a placeholder
                key_data = {
                    "type": "asymmetric",
                    "algorithm": key_spec or "RSA-2048",
                    "material": "asymmetric-key-placeholder",
                }
            elif key_type == "hmac":
                # Generate HMAC key
                key_material = secrets.token_bytes(32)
                key_data = {
                    "type": "hmac",
                    "algorithm": key_spec or "HMAC-SHA256",
                    "material": base64.b64encode(key_material).decode("utf-8"),
                }
            else:
                raise ValueError(f"Unsupported key type: {key_type}")

            # Add metadata
            key_data["description"] = description or f"Elder managed key: {key_name}"
            key_data["tags"] = tags or {}
            key_data["created_at"] = datetime.now(timezone.utc).isoformat()
            key_data["enabled"] = True

            # Store as secret in Infisical
            endpoint = f"{self.base_url}/secrets/{key_name}"
            payload = {
                "workspaceId": self.workspace_id,
                "environment": "production",  # Could be configurable
                "type": "shared",
                "secretKey": key_name,
                "secretValue": json.dumps(key_data),
                "secretComment": description or "",
            }

            response = requests.post(endpoint, headers=self.headers, json=payload)

            if response.status_code not in [200, 201]:
                raise Exception(f"Infisical API error: {response.text}")

            result = response.json()

            return {
                "key_id": key_name,
                "key_arn": f"infisical://{self.workspace_id}/{key_name}",
                "key_type": key_type,
                "state": "Enabled",
                "created_at": key_data["created_at"],
                "description": description,
            }

        except requests.RequestException as e:
            raise Exception(f"Infisical create key error: {str(e)}")
        except Exception as e:
            raise Exception(f"Infisical create key error: {str(e)}")

    def get_key(self, key_id: str) -> Dict[str, Any]:
        """
        Get key metadata from Infisical.

        Args:
            key_id: Key name/identifier

        Returns:
            Dictionary with key metadata
        """
        try:
            endpoint = f"{self.base_url}/secrets/{key_id}"
            params = {"workspaceId": self.workspace_id, "environment": "production"}

            response = requests.get(endpoint, headers=self.headers, params=params)

            if response.status_code == 404:
                raise Exception(f"Key not found: {key_id}")

            if response.status_code != 200:
                raise Exception(f"Infisical API error: {response.text}")

            result = response.json()
            secret_value = result.get("secret", {}).get("secretValue", "{}")

            # Parse stored key data
            key_data = (
                json.loads(secret_value)
                if isinstance(secret_value, str)
                else secret_value
            )

            return {
                "key_id": key_id,
                "key_arn": f"infisical://{self.workspace_id}/{key_id}",
                "state": "Enabled" if key_data.get("enabled", True) else "Disabled",
                "enabled": key_data.get("enabled", True),
                "created_at": key_data.get("created_at", ""),
                "description": key_data.get("description", ""),
                "type": key_data.get("type", "symmetric"),
                "algorithm": key_data.get("algorithm", ""),
            }

        except requests.RequestException as e:
            raise Exception(f"Infisical get key error: {str(e)}")
        except Exception as e:
            raise Exception(f"Infisical get key error: {str(e)}")

    def list_keys(
        self, limit: Optional[int] = None, next_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        List all encryption keys.

        Args:
            limit: Maximum number of keys to return
            next_token: Pagination token

        Returns:
            Dictionary with keys list
        """
        try:
            endpoint = f"{self.base_url}/secrets"
            params = {"workspaceId": self.workspace_id, "environment": "production"}

            response = requests.get(endpoint, headers=self.headers, params=params)

            if response.status_code != 200:
                raise Exception(f"Infisical API error: {response.text}")

            result = response.json()
            secrets = result.get("secrets", [])

            keys = []
            for secret in secrets:
                try:
                    # Parse key data
                    secret_value = secret.get("secretValue", "{}")
                    key_data = (
                        json.loads(secret_value)
                        if isinstance(secret_value, str)
                        else secret_value
                    )

                    # Only include keys (filter out regular secrets)
                    if isinstance(key_data, dict) and key_data.get("type") in [
                        "symmetric",
                        "asymmetric",
                        "hmac",
                    ]:
                        keys.append(
                            {
                                "key_id": secret.get("secretKey"),
                                "key_arn": f"infisical://{self.workspace_id}/{secret.get('secretKey')}",
                                "state": (
                                    "Enabled"
                                    if key_data.get("enabled", True)
                                    else "Disabled"
                                ),
                                "created_at": key_data.get("created_at", ""),
                                "type": key_data.get("type", ""),
                            }
                        )
                except Exception:
                    # Skip malformed secrets
                    continue

            # Apply limit if specified
            if limit:
                keys = keys[:limit]

            return {"keys": keys, "next_token": None}

        except requests.RequestException as e:
            raise Exception(f"Infisical list keys error: {str(e)}")
        except Exception as e:
            raise Exception(f"Infisical list keys error: {str(e)}")

    def enable_key(self, key_id: str) -> Dict[str, Any]:
        """Enable a disabled key."""
        try:
            # Get current key data
            key_data = self.get_key(key_id)

            # Update enabled status
            endpoint = f"{self.base_url}/secrets/{key_id}"

            # Fetch full secret data
            params = {"workspaceId": self.workspace_id, "environment": "production"}
            get_response = requests.get(endpoint, headers=self.headers, params=params)
            if get_response.status_code != 200:
                raise Exception(f"Infisical API error: {get_response.text}")

            secret_data = get_response.json().get("secret", {})
            key_value = json.loads(secret_data.get("secretValue", "{}"))
            key_value["enabled"] = True

            # Update secret
            payload = {
                "workspaceId": self.workspace_id,
                "environment": "production",
                "type": "shared",
                "secretValue": json.dumps(key_value),
            }

            update_response = requests.patch(
                endpoint, headers=self.headers, json=payload
            )
            if update_response.status_code != 200:
                raise Exception(f"Infisical API error: {update_response.text}")

            return self.get_key(key_id)

        except Exception as e:
            raise Exception(f"Infisical enable key error: {str(e)}")

    def disable_key(self, key_id: str) -> Dict[str, Any]:
        """Disable a key."""
        try:
            # Get current key data
            endpoint = f"{self.base_url}/secrets/{key_id}"
            params = {"workspaceId": self.workspace_id, "environment": "production"}

            get_response = requests.get(endpoint, headers=self.headers, params=params)
            if get_response.status_code != 200:
                raise Exception(f"Infisical API error: {get_response.text}")

            secret_data = get_response.json().get("secret", {})
            key_value = json.loads(secret_data.get("secretValue", "{}"))
            key_value["enabled"] = False

            # Update secret
            payload = {
                "workspaceId": self.workspace_id,
                "environment": "production",
                "type": "shared",
                "secretValue": json.dumps(key_value),
            }

            update_response = requests.patch(
                endpoint, headers=self.headers, json=payload
            )
            if update_response.status_code != 200:
                raise Exception(f"Infisical API error: {update_response.text}")

            return self.get_key(key_id)

        except Exception as e:
            raise Exception(f"Infisical disable key error: {str(e)}")

    def schedule_key_deletion(
        self, key_id: str, pending_days: int = 30
    ) -> Dict[str, Any]:
        """
        Schedule key deletion.

        Infisical doesn't support pending deletion, so we mark for deletion immediately.
        """
        try:
            endpoint = f"{self.base_url}/secrets/{key_id}"
            params = {"workspaceId": self.workspace_id, "environment": "production"}

            response = requests.delete(endpoint, headers=self.headers, params=params)

            if response.status_code not in [200, 204]:
                raise Exception(f"Infisical API error: {response.text}")

            return {
                "key_id": key_id,
                "state": "Deleted",
                "message": "Key deleted immediately (Infisical doesn't support pending deletion)",
            }

        except Exception as e:
            raise Exception(f"Infisical schedule deletion error: {str(e)}")

    def cancel_key_deletion(self, key_id: str) -> Dict[str, Any]:
        """Cancel scheduled key deletion."""
        raise NotImplementedError("Infisical doesn't support canceling deletion")

    def encrypt(
        self, key_id: str, plaintext: str, context: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Encrypt data using a key.

        Args:
            key_id: Key identifier
            plaintext: Data to encrypt
            context: Optional encryption context

        Returns:
            Dictionary with ciphertext
        """
        try:
            # Get key material
            key_info = self.get_key(key_id)

            # Fetch key material
            endpoint = f"{self.base_url}/secrets/{key_id}"
            params = {"workspaceId": self.workspace_id, "environment": "production"}
            response = requests.get(endpoint, headers=self.headers, params=params)

            if response.status_code != 200:
                raise Exception(f"Infisical API error: {response.text}")

            secret_data = response.json().get("secret", {})
            key_data = json.loads(secret_data.get("secretValue", "{}"))

            if not key_data.get("enabled", True):
                raise Exception("Key is disabled")

            key_material = base64.b64decode(key_data.get("material", ""))

            # Perform encryption using AES-GCM
            import os

            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            aesgcm = AESGCM(key_material)
            nonce = os.urandom(12)  # 96-bit nonce for GCM

            # Include context in AAD if provided
            aad = json.dumps(context).encode("utf-8") if context else None

            ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad)

            # Combine nonce + ciphertext
            combined = nonce + ciphertext

            return {
                "ciphertext": base64.b64encode(combined).decode("utf-8"),
                "key_id": key_id,
            }

        except Exception as e:
            raise Exception(f"Infisical encrypt error: {str(e)}")

    def decrypt(
        self, ciphertext: str, context: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Decrypt data.

        This is a simplified implementation that requires the key_id to be known.
        In production, you'd embed key_id in the ciphertext.
        """
        raise NotImplementedError(
            "Infisical decrypt requires key_id - use provider-specific API"
        )

    def generate_data_key(
        self,
        key_id: str,
        key_spec: str = "AES_256",
        context: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Generate a data encryption key.

        Args:
            key_id: Master key identifier
            key_spec: Data key specification (AES_256 or AES_128)
            context: Optional encryption context

        Returns:
            Dictionary with plaintext and encrypted data key
        """
        try:
            import secrets

            # Generate data key
            key_length = 32 if key_spec == "AES_256" else 16
            plaintext_key = secrets.token_bytes(key_length)

            # Encrypt the data key with master key
            plaintext_b64 = base64.b64encode(plaintext_key).decode("utf-8")
            encrypted = self.encrypt(key_id, plaintext_b64, context)

            return {
                "plaintext_key": base64.b64encode(plaintext_key).decode("utf-8"),
                "ciphertext_key": encrypted["ciphertext"],
                "key_id": key_id,
            }

        except Exception as e:
            raise Exception(f"Infisical generate data key error: {str(e)}")

    def sign(
        self,
        key_id: str,
        message: str,
        signing_algorithm: str = "RSASSA_PSS_SHA_256",
    ) -> Dict[str, Any]:
        """Sign a message using an asymmetric key."""
        raise NotImplementedError("Infisical sign not yet implemented")

    def verify(
        self, key_id: str, message: str, signature: str, signing_algorithm: str
    ) -> Dict[str, Any]:
        """Verify a message signature."""
        raise NotImplementedError("Infisical verify not yet implemented")

    def rotate_key(self, key_id: str) -> Dict[str, Any]:
        """
        Rotate a key by creating a new version.

        Args:
            key_id: Key identifier

        Returns:
            Dictionary with rotation details
        """
        try:
            import secrets

            # Get current key
            current_key = self.get_key(key_id)

            # Generate new key material
            new_material = secrets.token_bytes(32)

            # Update key with new material
            endpoint = f"{self.base_url}/secrets/{key_id}"
            params = {"workspaceId": self.workspace_id, "environment": "production"}

            # Fetch current data
            get_response = requests.get(endpoint, headers=self.headers, params=params)
            if get_response.status_code != 200:
                raise Exception(f"Infisical API error: {get_response.text}")

            key_data = json.loads(
                get_response.json().get("secret", {}).get("secretValue", "{}")
            )

            # Update material
            key_data["material"] = base64.b64encode(new_material).decode("utf-8")
            key_data["rotated_at"] = datetime.now(timezone.utc).isoformat()

            payload = {
                "workspaceId": self.workspace_id,
                "environment": "production",
                "type": "shared",
                "secretValue": json.dumps(key_data),
            }

            update_response = requests.patch(
                endpoint, headers=self.headers, json=payload
            )
            if update_response.status_code != 200:
                raise Exception(f"Infisical API error: {update_response.text}")

            return {
                "key_id": key_id,
                "rotated_at": key_data["rotated_at"],
                "message": "Key material rotated successfully",
            }

        except Exception as e:
            raise Exception(f"Infisical rotate key error: {str(e)}")

    def test_connection(self) -> bool:
        """
        Test Infisical connectivity.

        Returns:
            True if connection successful
        """
        try:
            endpoint = f"{self.base_url}/secrets"
            params = {"workspaceId": self.workspace_id, "environment": "production"}
            response = requests.get(
                endpoint, headers=self.headers, params=params, timeout=5
            )
            return response.status_code == 200
        except Exception:
            return False
