"""Kubernetes RBAC client for identity and access management operations."""

# flake8: noqa: E501

from typing import Any, Dict, List, Optional

try:
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException
except ImportError:
    client = None
    config = None
    ApiException = Exception

from apps.api.services.iam.base import BaseIAMProvider


class KubernetesRBACClient(BaseIAMProvider):
    """Kubernetes RBAC implementation of IAM provider."""

    def __init__(self, config_dict: dict[str, Any]):
        """
        Initialize Kubernetes RBAC client.

        Args:
            config_dict: Configuration dictionary with:
                - kubeconfig_path: Path to kubeconfig file (optional)
                - context: Kubernetes context name (optional)
                - namespace: Default namespace (default: "default")
                - in_cluster: Use in-cluster config (default: False)
        """
        super().__init__(config_dict)

        if client is None:
            raise ImportError(
                "kubernetes is required for K8s RBAC. "
                "Install with: pip install kubernetes"
            )

        self.namespace = config_dict.get("namespace", "default")

        # Load kubernetes configuration
        if config_dict.get("in_cluster"):
            config.load_incluster_config()
        else:
            kubeconfig_path = config_dict.get("kubeconfig_path")
            context = config_dict.get("context")
            config.load_kube_config(config_file=kubeconfig_path, context=context)

        # Initialize API clients
        self.core_v1 = client.CoreV1Api()
        self.rbac_v1 = client.RbacAuthorizationV1Api()

    # User Management (Service Accounts in Kubernetes)

    def list_users(
        self, limit: int | None = None, next_token: str | None = None
    ) -> dict[str, Any]:
        """List all service accounts in the namespace."""
        try:
            # Kubernetes doesn't have pagination tokens in the same way
            response = self.core_v1.list_namespaced_service_account(
                namespace=self.namespace, limit=limit
            )

            users = []
            for sa in response.items:
                users.append(self._normalize_user(self._sa_to_dict(sa)))

            return {
                "users": users,
                "next_token": (
                    response.metadata._continue
                    if hasattr(response.metadata, "_continue")
                    else None
                ),
                "truncated": bool(
                    response.metadata._continue
                    if hasattr(response.metadata, "_continue")
                    else False
                ),
            }

        except ApiException as e:
            raise Exception(f"K8s RBAC list users error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC list users error: {str(e)}")

    def get_user(self, user_identifier: str) -> dict[str, Any]:
        """Get service account details."""
        try:
            sa = self.core_v1.read_namespaced_service_account(
                name=user_identifier, namespace=self.namespace
            )

            return self._normalize_user(self._sa_to_dict(sa))

        except ApiException as e:
            raise Exception(f"K8s RBAC get user error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC get user error: {str(e)}")

    def create_user(
        self,
        username: str,
        display_name: str | None = None,
        tags: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Create a new service account."""
        try:
            sa = client.V1ServiceAccount(
                metadata=client.V1ObjectMeta(
                    name=username,
                    namespace=self.namespace,
                    labels=tags or {},
                    annotations={"description": display_name or username},
                )
            )

            created_sa = self.core_v1.create_namespaced_service_account(
                namespace=self.namespace, body=sa
            )

            return self._normalize_user(self._sa_to_dict(created_sa))

        except ApiException as e:
            raise Exception(f"K8s RBAC create user error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC create user error: {str(e)}")

    def delete_user(self, user_identifier: str) -> dict[str, Any]:
        """Delete a service account."""
        try:
            self.core_v1.delete_namespaced_service_account(
                name=user_identifier, namespace=self.namespace
            )

            return {
                "message": f"Service account {user_identifier} deleted successfully"
            }

        except ApiException as e:
            raise Exception(f"K8s RBAC delete user error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC delete user error: {str(e)}")

    def update_user(
        self,
        user_identifier: str,
        display_name: str | None = None,
        tags: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Update service account metadata."""
        try:
            # Get current service account
            sa = self.core_v1.read_namespaced_service_account(
                name=user_identifier, namespace=self.namespace
            )

            # Update metadata
            if tags:
                sa.metadata.labels = tags
            if display_name:
                if not sa.metadata.annotations:
                    sa.metadata.annotations = {}
                sa.metadata.annotations["description"] = display_name

            # Update service account
            updated_sa = self.core_v1.patch_namespaced_service_account(
                name=user_identifier, namespace=self.namespace, body=sa
            )

            return self._normalize_user(self._sa_to_dict(updated_sa))

        except ApiException as e:
            raise Exception(f"K8s RBAC update user error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC update user error: {str(e)}")

    # Role Management

    def list_roles(
        self, limit: int | None = None, next_token: str | None = None
    ) -> dict[str, Any]:
        """List all roles in the namespace."""
        try:
            response = self.rbac_v1.list_namespaced_role(
                namespace=self.namespace, limit=limit
            )

            roles = []
            for role in response.items:
                roles.append(self._normalize_role(self._role_to_dict(role)))

            return {
                "roles": roles,
                "next_token": (
                    response.metadata._continue
                    if hasattr(response.metadata, "_continue")
                    else None
                ),
                "truncated": bool(
                    response.metadata._continue
                    if hasattr(response.metadata, "_continue")
                    else False
                ),
            }

        except ApiException as e:
            raise Exception(f"K8s RBAC list roles error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC list roles error: {str(e)}")

    def get_role(self, role_identifier: str) -> dict[str, Any]:
        """Get role details."""
        try:
            role = self.rbac_v1.read_namespaced_role(
                name=role_identifier, namespace=self.namespace
            )

            return self._normalize_role(self._role_to_dict(role))

        except ApiException as e:
            raise Exception(f"K8s RBAC get role error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC get role error: {str(e)}")

    def create_role(
        self,
        role_name: str,
        description: str | None = None,
        trust_policy: dict[str, Any] | None = None,
        tags: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Create a new role."""
        try:
            # trust_policy in K8s context means rules
            rules = kwargs.get("rules", [])

            # Convert rules to PolicyRule objects if needed
            policy_rules = []
            for rule in rules:
                policy_rules.append(
                    client.V1PolicyRule(
                        api_groups=rule.get("api_groups", [""]),
                        resources=rule.get("resources", []),
                        verbs=rule.get("verbs", []),
                        resource_names=rule.get("resource_names"),
                    )
                )

            role = client.V1Role(
                metadata=client.V1ObjectMeta(
                    name=role_name,
                    namespace=self.namespace,
                    labels=tags or {},
                    annotations={"description": description or role_name},
                ),
                rules=policy_rules,
            )

            created_role = self.rbac_v1.create_namespaced_role(
                namespace=self.namespace, body=role
            )

            return self._normalize_role(self._role_to_dict(created_role))

        except ApiException as e:
            raise Exception(f"K8s RBAC create role error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC create role error: {str(e)}")

    def delete_role(self, role_identifier: str) -> dict[str, Any]:
        """Delete a role."""
        try:
            self.rbac_v1.delete_namespaced_role(
                name=role_identifier, namespace=self.namespace
            )

            return {"message": f"Role {role_identifier} deleted successfully"}

        except ApiException as e:
            raise Exception(f"K8s RBAC delete role error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC delete role error: {str(e)}")

    def update_role(
        self,
        role_identifier: str,
        description: str | None = None,
        tags: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Update role metadata."""
        try:
            # Get current role
            role = self.rbac_v1.read_namespaced_role(
                name=role_identifier, namespace=self.namespace
            )

            # Update metadata
            if tags:
                role.metadata.labels = tags
            if description:
                if not role.metadata.annotations:
                    role.metadata.annotations = {}
                role.metadata.annotations["description"] = description

            # Update rules if provided
            if kwargs.get("rules"):
                rules = kwargs["rules"]
                policy_rules = []
                for rule in rules:
                    policy_rules.append(
                        client.V1PolicyRule(
                            api_groups=rule.get("api_groups", [""]),
                            resources=rule.get("resources", []),
                            verbs=rule.get("verbs", []),
                            resource_names=rule.get("resource_names"),
                        )
                    )
                role.rules = policy_rules

            # Update role
            updated_role = self.rbac_v1.patch_namespaced_role(
                name=role_identifier, namespace=self.namespace, body=role
            )

            return self._normalize_role(self._role_to_dict(updated_role))

        except ApiException as e:
            raise Exception(f"K8s RBAC update role error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC update role error: {str(e)}")

    # Policy Management (RoleBindings in Kubernetes)

    def list_policies(
        self,
        scope: str | None = None,
        limit: int | None = None,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        """List all role bindings in the namespace."""
        try:
            response = self.rbac_v1.list_namespaced_role_binding(
                namespace=self.namespace, limit=limit
            )

            policies = []
            for rb in response.items:
                policies.append(self._rb_to_policy_dict(rb))

            return {
                "policies": policies,
                "next_token": (
                    response.metadata._continue
                    if hasattr(response.metadata, "_continue")
                    else None
                ),
                "truncated": bool(
                    response.metadata._continue
                    if hasattr(response.metadata, "_continue")
                    else False
                ),
            }

        except ApiException as e:
            raise Exception(f"K8s RBAC list policies error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC list policies error: {str(e)}")

    def get_policy(self, policy_identifier: str) -> dict[str, Any]:
        """Get role binding details."""
        try:
            rb = self.rbac_v1.read_namespaced_role_binding(
                name=policy_identifier, namespace=self.namespace
            )

            return self._rb_to_policy_dict(rb)

        except ApiException as e:
            raise Exception(f"K8s RBAC get policy error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC get policy error: {str(e)}")

    def create_policy(
        self,
        policy_name: str,
        policy_document: dict[str, Any],
        description: str | None = None,
        tags: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Create a role binding."""
        try:
            # policy_document should contain role_ref and subjects
            role_ref = policy_document.get("role_ref", {})
            subjects = policy_document.get("subjects", [])

            # Create role reference
            role_ref_obj = client.V1RoleRef(
                api_group=role_ref.get("api_group", "rbac.authorization.k8s.io"),
                kind=role_ref.get("kind", "Role"),
                name=role_ref.get("name"),
            )

            # Create subjects
            subject_objs = []
            for subj in subjects:
                subject_objs.append(
                    client.V1Subject(
                        kind=subj.get("kind", "ServiceAccount"),
                        name=subj.get("name"),
                        namespace=subj.get("namespace", self.namespace),
                    )
                )

            rb = client.V1RoleBinding(
                metadata=client.V1ObjectMeta(
                    name=policy_name,
                    namespace=self.namespace,
                    labels=tags or {},
                    annotations={"description": description or policy_name},
                ),
                role_ref=role_ref_obj,
                subjects=subject_objs,
            )

            created_rb = self.rbac_v1.create_namespaced_role_binding(
                namespace=self.namespace, body=rb
            )

            return self._rb_to_policy_dict(created_rb)

        except ApiException as e:
            raise Exception(f"K8s RBAC create policy error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC create policy error: {str(e)}")

    def delete_policy(self, policy_identifier: str) -> dict[str, Any]:
        """Delete a role binding."""
        try:
            self.rbac_v1.delete_namespaced_role_binding(
                name=policy_identifier, namespace=self.namespace
            )

            return {"message": f"Role binding {policy_identifier} deleted successfully"}

        except ApiException as e:
            raise Exception(f"K8s RBAC delete policy error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC delete policy error: {str(e)}")

    # Policy Attachments (via RoleBindings)

    def attach_policy_to_user(
        self, user_identifier: str, policy_identifier: str
    ) -> dict[str, Any]:
        """Attach role to service account (create/update role binding)."""
        try:
            # Get or create role binding
            try:
                rb = self.rbac_v1.read_namespaced_role_binding(
                    name=f"{user_identifier}-{policy_identifier}",
                    namespace=self.namespace,
                )
                # Binding exists, just return success
                return {
                    "message": f"Role {policy_identifier} already attached to service account {user_identifier}"
                }
            except ApiException:
                # Create new binding
                role_ref = client.V1RoleRef(
                    api_group="rbac.authorization.k8s.io",
                    kind="Role",
                    name=policy_identifier,
                )

                subject = client.V1Subject(
                    kind="ServiceAccount",
                    name=user_identifier,
                    namespace=self.namespace,
                )

                rb = client.V1RoleBinding(
                    metadata=client.V1ObjectMeta(
                        name=f"{user_identifier}-{policy_identifier}",
                        namespace=self.namespace,
                    ),
                    role_ref=role_ref,
                    subjects=[subject],
                )

                self.rbac_v1.create_namespaced_role_binding(
                    namespace=self.namespace, body=rb
                )

                return {
                    "message": f"Role {policy_identifier} attached to service account {user_identifier}"
                }

        except ApiException as e:
            raise Exception(f"K8s RBAC attach policy to user error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC attach policy to user error: {str(e)}")

    def detach_policy_from_user(
        self, user_identifier: str, policy_identifier: str
    ) -> dict[str, Any]:
        """Detach role from service account (delete role binding)."""
        try:
            self.rbac_v1.delete_namespaced_role_binding(
                name=f"{user_identifier}-{policy_identifier}",
                namespace=self.namespace,
            )

            return {
                "message": f"Role {policy_identifier} detached from service account {user_identifier}"
            }

        except ApiException as e:
            raise Exception(f"K8s RBAC detach policy from user error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC detach policy from user error: {str(e)}")

    def attach_policy_to_role(
        self, role_identifier: str, policy_identifier: str
    ) -> dict[str, Any]:
        """Not applicable in K8s - roles don't have policies attached."""
        raise NotImplementedError("K8s doesn't support attaching policies to roles")

    def detach_policy_from_role(
        self, role_identifier: str, policy_identifier: str
    ) -> dict[str, Any]:
        """Not applicable in K8s - roles don't have policies attached."""
        raise NotImplementedError("K8s doesn't support detaching policies from roles")

    def list_user_policies(self, user_identifier: str) -> list[dict[str, Any]]:
        """List all roles attached to a service account."""
        try:
            response = self.rbac_v1.list_namespaced_role_binding(
                namespace=self.namespace
            )

            policies = []
            for rb in response.items:
                # Check if this service account is in the subjects
                for subject in rb.subjects or []:
                    if (
                        subject.kind == "ServiceAccount"
                        and subject.name == user_identifier
                    ):
                        policies.append(
                            {
                                "name": rb.role_ref.name,
                                "role": rb.role_ref.name,
                                "binding_name": rb.metadata.name,
                            }
                        )
                        break

            return policies

        except ApiException as e:
            raise Exception(f"K8s RBAC list user policies error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC list user policies error: {str(e)}")

    def list_role_policies(self, role_identifier: str) -> list[dict[str, Any]]:
        """Not applicable in K8s - roles don't have policies."""
        return []

    # Access Keys (Service Account Tokens in Kubernetes)

    def create_access_key(self, user_identifier: str) -> dict[str, Any]:
        """Create service account token."""
        try:
            # Create token request
            token_request = client.AuthenticationV1TokenRequest(
                spec=client.AuthenticationV1TokenRequestSpec(
                    audiences=["https://kubernetes.default.svc"]
                )
            )

            # Create token
            response = self.core_v1.create_namespaced_service_account_token(
                name=user_identifier, namespace=self.namespace, body=token_request
            )

            return {
                "token": response.status.token,
                "expiration": (
                    response.status.expiration_timestamp.isoformat()
                    if response.status.expiration_timestamp
                    else None
                ),
                "user": user_identifier,
            }

        except ApiException as e:
            raise Exception(f"K8s RBAC create access key error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC create access key error: {str(e)}")

    def list_access_keys(self, user_identifier: str) -> list[dict[str, Any]]:
        """List service account secrets/tokens."""
        try:
            sa = self.core_v1.read_namespaced_service_account(
                name=user_identifier, namespace=self.namespace
            )

            keys = []
            for secret_ref in sa.secrets or []:
                keys.append(
                    {
                        "secret_name": secret_ref.name,
                        "namespace": secret_ref.namespace or self.namespace,
                    }
                )

            return keys

        except ApiException as e:
            raise Exception(f"K8s RBAC list access keys error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC list access keys error: {str(e)}")

    def delete_access_key(
        self, user_identifier: str, access_key_id: str
    ) -> dict[str, Any]:
        """Delete service account token secret."""
        try:
            self.core_v1.delete_namespaced_secret(
                name=access_key_id, namespace=self.namespace
            )

            return {"message": f"Token secret {access_key_id} deleted successfully"}

        except ApiException as e:
            raise Exception(f"K8s RBAC delete access key error: {e.reason}")
        except Exception as e:
            raise Exception(f"K8s RBAC delete access key error: {str(e)}")

    # Utility Methods

    def test_connection(self) -> bool:
        """Test Kubernetes connectivity."""
        try:
            # Simple API call to test connectivity
            self.core_v1.list_namespace(limit=1)
            return True
        except Exception:
            return False

    def sync_from_provider(self) -> dict[str, Any]:
        """Sync RBAC resources from Kubernetes to Elder database."""
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

            # Sync roles
            try:
                roles_response = self.list_roles(limit=1000)
                roles_synced = len(roles_response.get("roles", []))
            except Exception as e:
                errors.append(f"Error syncing roles: {str(e)}")

            # Sync role bindings
            try:
                policies_response = self.list_policies(limit=1000)
                policies_synced = len(policies_response.get("policies", []))
            except Exception as e:
                errors.append(f"Error syncing role bindings: {str(e)}")

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
        """Convert service account to dict."""
        return {
            "id": sa.metadata.uid,
            "username": sa.metadata.name,
            "name": sa.metadata.name,
            "namespace": sa.metadata.namespace,
            "created_at": (
                sa.metadata.creation_timestamp.isoformat()
                if sa.metadata.creation_timestamp
                else None
            ),
            "labels": sa.metadata.labels or {},
            "annotations": sa.metadata.annotations or {},
        }

    def _role_to_dict(self, role) -> dict[str, Any]:
        """Convert role to dict."""
        rules = []
        for rule in role.rules or []:
            rules.append(
                {
                    "api_groups": rule.api_groups or [],
                    "resources": rule.resources or [],
                    "verbs": rule.verbs or [],
                    "resource_names": rule.resource_names or [],
                }
            )

        return {
            "id": role.metadata.uid,
            "name": role.metadata.name,
            "namespace": role.metadata.namespace,
            "created_at": (
                role.metadata.creation_timestamp.isoformat()
                if role.metadata.creation_timestamp
                else None
            ),
            "rules": rules,
            "labels": role.metadata.labels or {},
            "annotations": role.metadata.annotations or {},
        }

    def _rb_to_policy_dict(self, rb) -> dict[str, Any]:
        """Convert role binding to policy dict."""
        subjects = []
        for subj in rb.subjects or []:
            subjects.append(
                {
                    "kind": subj.kind,
                    "name": subj.name,
                    "namespace": subj.namespace,
                }
            )

        return {
            "id": rb.metadata.uid,
            "name": rb.metadata.name,
            "namespace": rb.metadata.namespace,
            "role_ref": {
                "kind": rb.role_ref.kind,
                "name": rb.role_ref.name,
                "api_group": rb.role_ref.api_group,
            },
            "subjects": subjects,
            "created_at": (
                rb.metadata.creation_timestamp.isoformat()
                if rb.metadata.creation_timestamp
                else None
            ),
        }
