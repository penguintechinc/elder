"""GCP IAM client for identity and access management operations."""

# flake8: noqa: E501

import json
from typing import Any, Dict, List, Optional

try:
    from google.api_core import exceptions as google_exceptions
    from google.cloud import iam_admin_v1, resourcemanager_v3
    from google.oauth2 import service_account
except ImportError:
    iam_admin_v1 = None
    resourcemanager_v3 = None
    service_account = None
    google_exceptions = None

from apps.api.services.iam.base import BaseIAMProvider


class GCPIAMClient(BaseIAMProvider):
    """Google Cloud IAM implementation of IAM provider."""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize GCP IAM client.

        Args:
            config: Configuration dictionary with:
                - project_id: GCP project ID
                - credentials_json: Service account credentials JSON (optional)
                - organization_id: GCP organization ID (optional)
        """
        super().__init__(config)

        if iam_admin_v1 is None:
            raise ImportError(
                "google-cloud-iam is required for GCP IAM. "
                "Install with: pip install google-cloud-iam google-cloud-resource-manager"
            )

        self.project_id = config.get("project_id")
        self.organization_id = config.get("organization_id")

        # Initialize credentials if provided
        credentials = None
        if config.get("credentials_json"):
            if isinstance(config["credentials_json"], str):
                creds_dict = json.loads(config["credentials_json"])
            else:
                creds_dict = config["credentials_json"]

            credentials = service_account.Credentials.from_service_account_info(
                creds_dict
            )

        # Initialize IAM client
        self.iam_client = iam_admin_v1.IAMClient(credentials=credentials)

        # Initialize Resource Manager client for project-level IAM
        if resourcemanager_v3:
            self.resource_manager = resourcemanager_v3.ProjectsClient(
                credentials=credentials
            )
        else:
            self.resource_manager = None

    # User Management (Service Accounts in GCP)

    def list_users(
        self, limit: int | None = None, next_token: str | None = None
    ) -> dict[str, Any]:
        """List all service accounts in the project."""
        try:
            request = iam_admin_v1.ListServiceAccountsRequest(
                name=f"projects/{self.project_id}",
                page_size=limit or 100,
                page_token=next_token or "",
            )

            page_result = self.iam_client.list_service_accounts(request=request)

            users = []
            for sa in page_result.service_accounts:
                users.append(self._normalize_user(self._sa_to_dict(sa)))

            return {
                "users": users,
                "next_token": page_result.next_page_token or None,
                "truncated": bool(page_result.next_page_token),
            }

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM list users error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM list users error: {str(e)}")

    def get_user(self, user_identifier: str) -> dict[str, Any]:
        """Get service account details."""
        try:
            # Ensure full resource name
            if not user_identifier.startswith("projects/"):
                user_identifier = (
                    f"projects/{self.project_id}/serviceAccounts/{user_identifier}"
                )

            request = iam_admin_v1.GetServiceAccountRequest(name=user_identifier)
            sa = self.iam_client.get_service_account(request=request)

            return self._normalize_user(self._sa_to_dict(sa))

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM get user error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM get user error: {str(e)}")

    def create_user(
        self,
        username: str,
        display_name: str | None = None,
        tags: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Create a new service account."""
        try:
            # Ensure account_id doesn't have @ or project info
            if "@" in username:
                account_id = username.split("@")[0]
            else:
                account_id = username

            sa = iam_admin_v1.ServiceAccount(
                display_name=display_name or account_id,
                description=kwargs.get("description", ""),
            )

            request = iam_admin_v1.CreateServiceAccountRequest(
                name=f"projects/{self.project_id}",
                account_id=account_id,
                service_account=sa,
            )

            created_sa = self.iam_client.create_service_account(request=request)

            return self._normalize_user(self._sa_to_dict(created_sa))

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM create user error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM create user error: {str(e)}")

    def delete_user(self, user_identifier: str) -> dict[str, Any]:
        """Delete a service account."""
        try:
            # Ensure full resource name
            if not user_identifier.startswith("projects/"):
                user_identifier = (
                    f"projects/{self.project_id}/serviceAccounts/{user_identifier}"
                )

            request = iam_admin_v1.DeleteServiceAccountRequest(name=user_identifier)
            self.iam_client.delete_service_account(request=request)

            return {
                "message": f"Service account {user_identifier} deleted successfully"
            }

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM delete user error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM delete user error: {str(e)}")

    def update_user(
        self,
        user_identifier: str,
        display_name: str | None = None,
        tags: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Update service account metadata."""
        try:
            # Ensure full resource name
            if not user_identifier.startswith("projects/"):
                user_identifier = (
                    f"projects/{self.project_id}/serviceAccounts/{user_identifier}"
                )

            # Get current service account
            request = iam_admin_v1.GetServiceAccountRequest(name=user_identifier)
            sa = self.iam_client.get_service_account(request=request)

            # Update fields
            if display_name:
                sa.display_name = display_name
            if kwargs.get("description") is not None:
                sa.description = kwargs["description"]

            # Update service account
            update_request = iam_admin_v1.PatchServiceAccountRequest(service_account=sa)
            updated_sa = self.iam_client.patch_service_account(request=update_request)

            return self._normalize_user(self._sa_to_dict(updated_sa))

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM update user error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM update user error: {str(e)}")

    # Role Management (GCP uses predefined and custom roles)

    def list_roles(
        self, limit: int | None = None, next_token: str | None = None
    ) -> dict[str, Any]:
        """List all custom roles in the project."""
        try:
            request = iam_admin_v1.ListRolesRequest(
                parent=f"projects/{self.project_id}",
                page_size=limit or 100,
                page_token=next_token or "",
                view=iam_admin_v1.RoleView.FULL,
            )

            page_result = self.iam_client.list_roles(request=request)

            roles = []
            for role in page_result.roles:
                roles.append(self._normalize_role(self._role_to_dict(role)))

            return {
                "roles": roles,
                "next_token": page_result.next_page_token or None,
                "truncated": bool(page_result.next_page_token),
            }

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM list roles error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM list roles error: {str(e)}")

    def get_role(self, role_identifier: str) -> dict[str, Any]:
        """Get role details."""
        try:
            # Ensure full resource name
            if not role_identifier.startswith(
                "projects/"
            ) and not role_identifier.startswith("organizations/"):
                role_identifier = f"projects/{self.project_id}/roles/{role_identifier}"

            request = iam_admin_v1.GetRoleRequest(name=role_identifier)
            role = self.iam_client.get_role(request=request)

            return self._normalize_role(self._role_to_dict(role))

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM get role error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM get role error: {str(e)}")

    def create_role(
        self,
        role_name: str,
        description: str | None = None,
        trust_policy: dict[str, Any] | None = None,
        tags: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Create a new custom role."""
        try:
            # trust_policy in GCP context means permissions
            permissions = kwargs.get("permissions", [])

            role = iam_admin_v1.Role(
                title=role_name,
                description=description or f"Custom role: {role_name}",
                included_permissions=permissions,
                stage=iam_admin_v1.Role.RoleLaunchStage.GA,
            )

            # Extract role_id from role_name (no special characters allowed)
            role_id = role_name.replace(" ", "_").replace("-", "_").lower()

            request = iam_admin_v1.CreateRoleRequest(
                parent=f"projects/{self.project_id}",
                role_id=role_id,
                role=role,
            )

            created_role = self.iam_client.create_role(request=request)

            return self._normalize_role(self._role_to_dict(created_role))

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM create role error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM create role error: {str(e)}")

    def delete_role(self, role_identifier: str) -> dict[str, Any]:
        """Delete a custom role."""
        try:
            # Ensure full resource name
            if not role_identifier.startswith("projects/"):
                role_identifier = f"projects/{self.project_id}/roles/{role_identifier}"

            request = iam_admin_v1.DeleteRoleRequest(name=role_identifier)
            self.iam_client.delete_role(request=request)

            return {"message": f"Role {role_identifier} deleted successfully"}

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM delete role error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM delete role error: {str(e)}")

    def update_role(
        self,
        role_identifier: str,
        description: str | None = None,
        tags: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Update custom role metadata."""
        try:
            # Ensure full resource name
            if not role_identifier.startswith("projects/"):
                role_identifier = f"projects/{self.project_id}/roles/{role_identifier}"

            # Get current role
            get_request = iam_admin_v1.GetRoleRequest(name=role_identifier)
            role = self.iam_client.get_role(request=get_request)

            # Update fields
            if description:
                role.description = description
            if kwargs.get("permissions"):
                role.included_permissions = kwargs["permissions"]

            # Update role
            update_request = iam_admin_v1.UpdateRoleRequest(
                name=role_identifier, role=role
            )
            updated_role = self.iam_client.update_role(request=update_request)

            return self._normalize_role(self._role_to_dict(updated_role))

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM update role error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM update role error: {str(e)}")

    # Policy Management (GCP uses IAM Policy Bindings, not separate policy objects)

    def list_policies(
        self,
        scope: str | None = None,
        limit: int | None = None,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        """
        List IAM policy bindings for the project.

        Note: GCP doesn't have separate policy objects like AWS.
        This returns the project's IAM policy bindings.
        """
        try:
            if not self.resource_manager:
                return {
                    "policies": [],
                    "next_token": None,
                    "message": "Resource Manager client not available",
                }

            resource_name = f"projects/{self.project_id}"

            request = resourcemanager_v3.GetIamPolicyRequest(resource=resource_name)
            policy = self.resource_manager.get_iam_policy(request=request)

            # Convert bindings to policy-like format
            policies = []
            for binding in policy.bindings:
                policies.append(
                    {
                        "id": f"{resource_name}/bindings/{binding.role}",
                        "name": binding.role,
                        "role": binding.role,
                        "members": list(binding.members),
                        "condition": (
                            binding.condition.expression if binding.condition else None
                        ),
                    }
                )

            return {"policies": policies, "next_token": None, "truncated": False}

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM list policies error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM list policies error: {str(e)}")

    def get_policy(self, policy_identifier: str) -> dict[str, Any]:
        """
        Get IAM policy binding details.

        Args:
            policy_identifier: Role name (e.g., "roles/viewer")
        """
        try:
            if not self.resource_manager:
                raise Exception("Resource Manager client not available")

            resource_name = f"projects/{self.project_id}"
            request = resourcemanager_v3.GetIamPolicyRequest(resource=resource_name)
            policy = self.resource_manager.get_iam_policy(request=request)

            # Find binding for this role
            for binding in policy.bindings:
                if binding.role == policy_identifier:
                    return {
                        "id": f"{resource_name}/bindings/{binding.role}",
                        "name": binding.role,
                        "role": binding.role,
                        "members": list(binding.members),
                        "condition": (
                            binding.condition.expression if binding.condition else None
                        ),
                    }

            raise Exception(f"Policy binding for role {policy_identifier} not found")

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM get policy error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM get policy error: {str(e)}")

    def create_policy(
        self,
        policy_name: str,
        policy_document: dict[str, Any],
        description: str | None = None,
        tags: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Create IAM policy binding (attach role to members).

        Args:
            policy_name: Role name (e.g., "roles/viewer")
            policy_document: {"members": ["user:email@example.com"]}
        """
        try:
            if not self.resource_manager:
                raise Exception("Resource Manager client not available")

            resource_name = f"projects/{self.project_id}"

            # Get current policy
            get_request = resourcemanager_v3.GetIamPolicyRequest(resource=resource_name)
            policy = self.resource_manager.get_iam_policy(request=get_request)

            # Add new binding
            members = policy_document.get("members", [])
            condition = policy_document.get("condition")

            new_binding = resourcemanager_v3.Binding(
                role=policy_name, members=members, condition=condition
            )

            policy.bindings.append(new_binding)

            # Set updated policy
            set_request = resourcemanager_v3.SetIamPolicyRequest(
                resource=resource_name, policy=policy
            )
            updated_policy = self.resource_manager.set_iam_policy(request=set_request)

            return {
                "id": f"{resource_name}/bindings/{policy_name}",
                "name": policy_name,
                "role": policy_name,
                "members": members,
                "message": "Policy binding created successfully",
            }

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM create policy error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM create policy error: {str(e)}")

    def delete_policy(self, policy_identifier: str) -> dict[str, Any]:
        """Delete IAM policy binding (remove all members from role)."""
        try:
            if not self.resource_manager:
                raise Exception("Resource Manager client not available")

            resource_name = f"projects/{self.project_id}"

            # Get current policy
            get_request = resourcemanager_v3.GetIamPolicyRequest(resource=resource_name)
            policy = self.resource_manager.get_iam_policy(request=get_request)

            # Remove binding
            policy.bindings = [
                b for b in policy.bindings if b.role != policy_identifier
            ]

            # Set updated policy
            set_request = resourcemanager_v3.SetIamPolicyRequest(
                resource=resource_name, policy=policy
            )
            self.resource_manager.set_iam_policy(request=set_request)

            return {
                "message": f"Policy binding {policy_identifier} deleted successfully"
            }

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM delete policy error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM delete policy error: {str(e)}")

    # Policy Attachments (done via IAM policy bindings in GCP)

    def attach_policy_to_user(
        self, user_identifier: str, policy_identifier: str
    ) -> dict[str, Any]:
        """Attach role to service account (add to IAM policy binding)."""
        try:
            if not self.resource_manager:
                raise Exception("Resource Manager client not available")

            # Ensure service account format
            if not user_identifier.startswith("serviceAccount:"):
                if "@" not in user_identifier:
                    user_identifier = (
                        f"{user_identifier}@{self.project_id}.iam.gserviceaccount.com"
                    )
                user_identifier = f"serviceAccount:{user_identifier}"

            resource_name = f"projects/{self.project_id}"

            # Get current policy
            get_request = resourcemanager_v3.GetIamPolicyRequest(resource=resource_name)
            policy = self.resource_manager.get_iam_policy(request=get_request)

            # Find or create binding
            binding_found = False
            for binding in policy.bindings:
                if binding.role == policy_identifier:
                    if user_identifier not in binding.members:
                        binding.members.append(user_identifier)
                    binding_found = True
                    break

            if not binding_found:
                new_binding = resourcemanager_v3.Binding(
                    role=policy_identifier, members=[user_identifier]
                )
                policy.bindings.append(new_binding)

            # Set updated policy
            set_request = resourcemanager_v3.SetIamPolicyRequest(
                resource=resource_name, policy=policy
            )
            self.resource_manager.set_iam_policy(request=set_request)

            return {
                "message": f"Role {policy_identifier} attached to service account {user_identifier}"
            }

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM attach policy to user error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM attach policy to user error: {str(e)}")

    def detach_policy_from_user(
        self, user_identifier: str, policy_identifier: str
    ) -> dict[str, Any]:
        """Detach role from service account (remove from IAM policy binding)."""
        try:
            if not self.resource_manager:
                raise Exception("Resource Manager client not available")

            # Ensure service account format
            if not user_identifier.startswith("serviceAccount:"):
                if "@" not in user_identifier:
                    user_identifier = (
                        f"{user_identifier}@{self.project_id}.iam.gserviceaccount.com"
                    )
                user_identifier = f"serviceAccount:{user_identifier}"

            resource_name = f"projects/{self.project_id}"

            # Get current policy
            get_request = resourcemanager_v3.GetIamPolicyRequest(resource=resource_name)
            policy = self.resource_manager.get_iam_policy(request=get_request)

            # Remove member from binding
            for binding in policy.bindings:
                if binding.role == policy_identifier:
                    if user_identifier in binding.members:
                        binding.members.remove(user_identifier)
                    break

            # Set updated policy
            set_request = resourcemanager_v3.SetIamPolicyRequest(
                resource=resource_name, policy=policy
            )
            self.resource_manager.set_iam_policy(request=set_request)

            return {
                "message": f"Role {policy_identifier} detached from service account {user_identifier}"
            }

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM detach policy from user error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM detach policy from user error: {str(e)}")

    def attach_policy_to_role(
        self, role_identifier: str, policy_identifier: str
    ) -> dict[str, Any]:
        """Not applicable in GCP - roles don't have policies attached."""
        raise NotImplementedError("GCP doesn't support attaching policies to roles")

    def detach_policy_from_role(
        self, role_identifier: str, policy_identifier: str
    ) -> dict[str, Any]:
        """Not applicable in GCP - roles don't have policies attached."""
        raise NotImplementedError("GCP doesn't support detaching policies from roles")

    def list_user_policies(self, user_identifier: str) -> list[dict[str, Any]]:
        """List all roles attached to a service account."""
        try:
            if not self.resource_manager:
                return []

            # Ensure service account format
            if not user_identifier.startswith("serviceAccount:"):
                if "@" not in user_identifier:
                    user_identifier = (
                        f"{user_identifier}@{self.project_id}.iam.gserviceaccount.com"
                    )
                user_identifier = f"serviceAccount:{user_identifier}"

            resource_name = f"projects/{self.project_id}"

            # Get current policy
            get_request = resourcemanager_v3.GetIamPolicyRequest(resource=resource_name)
            policy = self.resource_manager.get_iam_policy(request=get_request)

            # Find all bindings that include this service account
            policies = []
            for binding in policy.bindings:
                if user_identifier in binding.members:
                    policies.append(
                        {
                            "name": binding.role,
                            "role": binding.role,
                            "arn": binding.role,
                        }
                    )

            return policies

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM list user policies error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM list user policies error: {str(e)}")

    def list_role_policies(self, role_identifier: str) -> list[dict[str, Any]]:
        """Not applicable in GCP - roles don't have policies."""
        return []

    # Access Keys (Service Account Keys in GCP)

    def create_access_key(self, user_identifier: str) -> dict[str, Any]:
        """Create service account key."""
        try:
            # Ensure full resource name
            if not user_identifier.startswith("projects/"):
                user_identifier = (
                    f"projects/{self.project_id}/serviceAccounts/{user_identifier}"
                )

            request = iam_admin_v1.CreateServiceAccountKeyRequest(
                name=user_identifier,
                private_key_type=iam_admin_v1.ServiceAccountPrivateKeyType.TYPE_GOOGLE_CREDENTIALS_FILE,
            )

            key = self.iam_client.create_service_account_key(request=request)

            # Decode the private key data
            key_data = json.loads(key.private_key_data.decode("utf-8"))

            return {
                "key_id": key.name.split("/")[-1],
                "key_data": key_data,
                "created_at": key.valid_after_time.isoformat(),
                "type": "google_credentials_file",
                "user": user_identifier,
            }

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM create access key error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM create access key error: {str(e)}")

    def list_access_keys(self, user_identifier: str) -> list[dict[str, Any]]:
        """List service account keys."""
        try:
            # Ensure full resource name
            if not user_identifier.startswith("projects/"):
                user_identifier = (
                    f"projects/{self.project_id}/serviceAccounts/{user_identifier}"
                )

            request = iam_admin_v1.ListServiceAccountKeysRequest(name=user_identifier)
            keys_list = self.iam_client.list_service_account_keys(request=request)

            keys = []
            for key in keys_list.keys:
                keys.append(
                    {
                        "key_id": key.name.split("/")[-1],
                        "type": key.key_type.name,
                        "algorithm": key.key_algorithm.name,
                        "valid_after": (
                            key.valid_after_time.isoformat()
                            if key.valid_after_time
                            else None
                        ),
                        "valid_before": (
                            key.valid_before_time.isoformat()
                            if key.valid_before_time
                            else None
                        ),
                    }
                )

            return keys

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM list access keys error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM list access keys error: {str(e)}")

    def delete_access_key(
        self, user_identifier: str, access_key_id: str
    ) -> dict[str, Any]:
        """Delete service account key."""
        try:
            # Ensure full resource name
            if not user_identifier.startswith("projects/"):
                user_identifier = (
                    f"projects/{self.project_id}/serviceAccounts/{user_identifier}"
                )

            key_name = f"{user_identifier}/keys/{access_key_id}"

            request = iam_admin_v1.DeleteServiceAccountKeyRequest(name=key_name)
            self.iam_client.delete_service_account_key(request=request)

            return {
                "message": f"Service account key {access_key_id} deleted for {user_identifier}"
            }

        except google_exceptions.GoogleAPIError as e:
            raise Exception(f"GCP IAM delete access key error: {str(e)}")
        except Exception as e:
            raise Exception(f"GCP IAM delete access key error: {str(e)}")

    # Utility Methods

    def test_connection(self) -> bool:
        """Test GCP IAM connectivity."""
        try:
            # Simple API call to test connectivity
            request = iam_admin_v1.ListServiceAccountsRequest(
                name=f"projects/{self.project_id}", page_size=1
            )
            self.iam_client.list_service_accounts(request=request)
            return True
        except Exception:
            return False

    def sync_from_provider(self) -> dict[str, Any]:
        """Sync IAM resources from GCP to Elder database."""
        errors = []
        users_synced = 0
        roles_synced = 0
        policies_synced = 0

        try:
            # Sync service accounts
            try:
                users_response = self.list_users(limit=1000)
                users_synced = len(users_response.get("users", []))
            except Exception as e:
                errors.append(f"Error syncing service accounts: {str(e)}")

            # Sync custom roles
            try:
                roles_response = self.list_roles(limit=1000)
                roles_synced = len(roles_response.get("roles", []))
            except Exception as e:
                errors.append(f"Error syncing roles: {str(e)}")

            # Sync IAM policy bindings
            try:
                policies_response = self.list_policies(limit=1000)
                policies_synced = len(policies_response.get("policies", []))
            except Exception as e:
                errors.append(f"Error syncing policy bindings: {str(e)}")

        except Exception as e:
            errors.append(f"General sync error: {str(e)}")

        return {
            "users_synced": users_synced,
            "roles_synced": roles_synced,
            "policies_synced": policies_synced,
            "errors": errors,
        }

    # Helper methods

    def _sa_to_dict(self, sa) -> dict[str, Any]:
        """Convert service account protobuf to dict."""
        return {
            "id": sa.unique_id,
            "username": sa.email,
            "email": sa.email,
            "display_name": sa.display_name,
            "name": sa.name,
            "created_at": None,  # Not provided in response
            "arn": sa.name,
            "project_id": sa.project_id,
            "disabled": sa.disabled,
        }

    def _role_to_dict(self, role) -> dict[str, Any]:
        """Convert role protobuf to dict."""
        return {
            "id": role.name,
            "name": role.name.split("/")[-1] if "/" in role.name else role.name,
            "title": role.title,
            "description": role.description,
            "permissions": list(role.included_permissions),
            "stage": role.stage.name if role.stage else None,
            "deleted": role.deleted,
        }
