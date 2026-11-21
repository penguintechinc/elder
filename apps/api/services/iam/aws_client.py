"""AWS IAM client for identity and access management operations."""

# flake8: noqa: E501

import json
from typing import Any, Dict, List, Optional

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    boto3 = None
    ClientError = Exception
    BotoCoreError = Exception

from apps.api.services.iam.base import BaseIAMProvider


class AWSIAMClient(BaseIAMProvider):
    """AWS IAM implementation of IAM provider."""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize AWS IAM client.

        Args:
            config: Configuration dictionary with:
                - region: AWS region (e.g., 'us-east-1')
                - access_key_id: Optional AWS access key ID
                - secret_access_key: Optional AWS secret access key
                - session_token: Optional AWS session token
                - endpoint_url: Optional custom IAM endpoint
        """
        super().__init__(config)

        if boto3 is None:
            raise ImportError(
                "boto3 is required for AWS IAM. Install with: pip install boto3"
            )

        self.region = config.get("region", "us-east-1")

        # Build client configuration
        client_config = {"region_name": self.region}

        # Add credentials if provided (otherwise uses IAM role/environment)
        if config.get("access_key_id") and config.get("secret_access_key"):
            client_config["aws_access_key_id"] = config["access_key_id"]
            client_config["aws_secret_access_key"] = config["secret_access_key"]

            if config.get("session_token"):
                client_config["aws_session_token"] = config["session_token"]

        # Add custom endpoint if provided (for LocalStack, etc.)
        if config.get("endpoint_url"):
            client_config["endpoint_url"] = config["endpoint_url"]

        self.client = boto3.client("iam", **client_config)

    # User Management

    def list_users(
        self, limit: int | None = None, next_token: str | None = None
    ) -> dict[str, Any]:
        """List all IAM users."""
        try:
            params = {}
            if limit:
                params["MaxItems"] = min(limit, 1000)  # AWS max is 1000
            if next_token:
                params["Marker"] = next_token

            response = self.client.list_users(**params)

            users = [self._normalize_user(user) for user in response.get("Users", [])]

            return {
                "users": users,
                "next_token": response.get("Marker"),
                "truncated": response.get("IsTruncated", False),
            }

        except ClientError as e:
            raise Exception(
                f"AWS IAM list users error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM list users error: {str(e)}")

    def get_user(self, user_identifier: str) -> dict[str, Any]:
        """Get IAM user details."""
        try:
            response = self.client.get_user(UserName=user_identifier)
            user = response["User"]

            # Get user tags
            try:
                tags_response = self.client.list_user_tags(UserName=user_identifier)
                tags = {
                    tag["Key"]: tag["Value"] for tag in tags_response.get("Tags", [])
                }
            except Exception:
                tags = {}

            normalized_user = self._normalize_user(user)
            normalized_user["tags"] = tags

            return normalized_user

        except ClientError as e:
            raise Exception(f"AWS IAM get user error: {e.response['Error']['Message']}")
        except Exception as e:
            raise Exception(f"AWS IAM get user error: {str(e)}")

    def create_user(
        self,
        username: str,
        display_name: str | None = None,
        tags: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Create a new IAM user."""
        try:
            create_params = {"UserName": username}

            # Add path if provided
            if kwargs.get("path"):
                create_params["Path"] = kwargs["path"]

            response = self.client.create_user(**create_params)
            user = response["User"]

            # Add tags if provided
            if tags:
                tag_list = [{"Key": k, "Value": v} for k, v in tags.items()]
                self.client.tag_user(UserName=username, Tags=tag_list)

            normalized_user = self._normalize_user(user)
            normalized_user["tags"] = tags or {}

            return normalized_user

        except ClientError as e:
            raise Exception(
                f"AWS IAM create user error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM create user error: {str(e)}")

    def delete_user(self, user_identifier: str) -> dict[str, Any]:
        """Delete an IAM user."""
        try:
            # Must delete access keys, inline policies, and detach managed policies first
            # Delete access keys
            try:
                keys_response = self.client.list_access_keys(UserName=user_identifier)
                for key in keys_response.get("AccessKeyMetadata", []):
                    self.client.delete_access_key(
                        UserName=user_identifier, AccessKeyId=key["AccessKeyId"]
                    )
            except Exception:
                pass

            # Detach managed policies
            try:
                policies_response = self.client.list_attached_user_policies(
                    UserName=user_identifier
                )
                for policy in policies_response.get("AttachedPolicies", []):
                    self.client.detach_user_policy(
                        UserName=user_identifier, PolicyArn=policy["PolicyArn"]
                    )
            except Exception:
                pass

            # Delete inline policies
            try:
                inline_policies = self.client.list_user_policies(
                    UserName=user_identifier
                )
                for policy_name in inline_policies.get("PolicyNames", []):
                    self.client.delete_user_policy(
                        UserName=user_identifier, PolicyName=policy_name
                    )
            except Exception:
                pass

            # Remove from all groups
            try:
                groups_response = self.client.list_groups_for_user(
                    UserName=user_identifier
                )
                for group in groups_response.get("Groups", []):
                    self.client.remove_user_from_group(
                        UserName=user_identifier, GroupName=group["GroupName"]
                    )
            except Exception:
                pass

            # Finally delete the user
            self.client.delete_user(UserName=user_identifier)

            return {"message": f"User {user_identifier} deleted successfully"}

        except ClientError as e:
            raise Exception(
                f"AWS IAM delete user error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM delete user error: {str(e)}")

    def update_user(
        self,
        user_identifier: str,
        display_name: str | None = None,
        tags: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Update IAM user metadata."""
        try:
            # Update user path or name if provided
            if kwargs.get("new_user_name") or kwargs.get("new_path"):
                update_params = {"UserName": user_identifier}
                if kwargs.get("new_user_name"):
                    update_params["NewUserName"] = kwargs["new_user_name"]
                if kwargs.get("new_path"):
                    update_params["NewPath"] = kwargs["new_path"]
                self.client.update_user(**update_params)

            # Update tags
            if tags is not None:
                # Remove all existing tags
                try:
                    existing_tags = self.client.list_user_tags(UserName=user_identifier)
                    if existing_tags.get("Tags"):
                        tag_keys = [tag["Key"] for tag in existing_tags["Tags"]]
                        self.client.untag_user(
                            UserName=user_identifier, TagKeys=tag_keys
                        )
                except Exception:
                    pass

                # Add new tags
                if tags:
                    tag_list = [{"Key": k, "Value": v} for k, v in tags.items()]
                    self.client.tag_user(UserName=user_identifier, Tags=tag_list)

            # Return updated user
            return self.get_user(kwargs.get("new_user_name", user_identifier))

        except ClientError as e:
            raise Exception(
                f"AWS IAM update user error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM update user error: {str(e)}")

    # Role Management

    def list_roles(
        self, limit: int | None = None, next_token: str | None = None
    ) -> dict[str, Any]:
        """List all IAM roles."""
        try:
            params = {}
            if limit:
                params["MaxItems"] = min(limit, 1000)
            if next_token:
                params["Marker"] = next_token

            response = self.client.list_roles(**params)

            roles = [self._normalize_role(role) for role in response.get("Roles", [])]

            return {
                "roles": roles,
                "next_token": response.get("Marker"),
                "truncated": response.get("IsTruncated", False),
            }

        except ClientError as e:
            raise Exception(
                f"AWS IAM list roles error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM list roles error: {str(e)}")

    def get_role(self, role_identifier: str) -> dict[str, Any]:
        """Get IAM role details."""
        try:
            response = self.client.get_role(RoleName=role_identifier)
            role = response["Role"]

            # Get role tags
            try:
                tags_response = self.client.list_role_tags(RoleName=role_identifier)
                tags = {
                    tag["Key"]: tag["Value"] for tag in tags_response.get("Tags", [])
                }
            except Exception:
                tags = {}

            normalized_role = self._normalize_role(role)
            normalized_role["tags"] = tags
            normalized_role["assume_role_policy"] = role.get("AssumeRolePolicyDocument")

            return normalized_role

        except ClientError as e:
            raise Exception(f"AWS IAM get role error: {e.response['Error']['Message']}")
        except Exception as e:
            raise Exception(f"AWS IAM get role error: {str(e)}")

    def create_role(
        self,
        role_name: str,
        description: str | None = None,
        trust_policy: dict[str, Any] | None = None,
        tags: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Create a new IAM role."""
        try:
            # Default trust policy if not provided
            if not trust_policy:
                trust_policy = {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "ec2.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }

            create_params = {
                "RoleName": role_name,
                "AssumeRolePolicyDocument": json.dumps(trust_policy),
            }

            if description:
                create_params["Description"] = description

            if kwargs.get("path"):
                create_params["Path"] = kwargs["path"]

            if kwargs.get("max_session_duration"):
                create_params["MaxSessionDuration"] = kwargs["max_session_duration"]

            if tags:
                create_params["Tags"] = [
                    {"Key": k, "Value": v} for k, v in tags.items()
                ]

            response = self.client.create_role(**create_params)
            role = response["Role"]

            normalized_role = self._normalize_role(role)
            normalized_role["tags"] = tags or {}

            return normalized_role

        except ClientError as e:
            raise Exception(
                f"AWS IAM create role error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM create role error: {str(e)}")

    def delete_role(self, role_identifier: str) -> dict[str, Any]:
        """Delete an IAM role."""
        try:
            # Must detach managed policies and delete inline policies first
            # Detach managed policies
            try:
                policies_response = self.client.list_attached_role_policies(
                    RoleName=role_identifier
                )
                for policy in policies_response.get("AttachedPolicies", []):
                    self.client.detach_role_policy(
                        RoleName=role_identifier, PolicyArn=policy["PolicyArn"]
                    )
            except Exception:
                pass

            # Delete inline policies
            try:
                inline_policies = self.client.list_role_policies(
                    RoleName=role_identifier
                )
                for policy_name in inline_policies.get("PolicyNames", []):
                    self.client.delete_role_policy(
                        RoleName=role_identifier, PolicyName=policy_name
                    )
            except Exception:
                pass

            # Delete instance profiles
            try:
                profiles_response = self.client.list_instance_profiles_for_role(
                    RoleName=role_identifier
                )
                for profile in profiles_response.get("InstanceProfiles", []):
                    self.client.remove_role_from_instance_profile(
                        InstanceProfileName=profile["InstanceProfileName"],
                        RoleName=role_identifier,
                    )
            except Exception:
                pass

            # Finally delete the role
            self.client.delete_role(RoleName=role_identifier)

            return {"message": f"Role {role_identifier} deleted successfully"}

        except ClientError as e:
            raise Exception(
                f"AWS IAM delete role error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM delete role error: {str(e)}")

    def update_role(
        self,
        role_identifier: str,
        description: str | None = None,
        tags: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Update IAM role metadata."""
        try:
            # Update description if provided
            if description is not None:
                self.client.update_role_description(
                    RoleName=role_identifier, Description=description
                )

            # Update max session duration if provided
            if kwargs.get("max_session_duration"):
                self.client.update_role(
                    RoleName=role_identifier,
                    MaxSessionDuration=kwargs["max_session_duration"],
                )

            # Update tags
            if tags is not None:
                # Remove all existing tags
                try:
                    existing_tags = self.client.list_role_tags(RoleName=role_identifier)
                    if existing_tags.get("Tags"):
                        tag_keys = [tag["Key"] for tag in existing_tags["Tags"]]
                        self.client.untag_role(
                            RoleName=role_identifier, TagKeys=tag_keys
                        )
                except Exception:
                    pass

                # Add new tags
                if tags:
                    tag_list = [{"Key": k, "Value": v} for k, v in tags.items()]
                    self.client.tag_role(RoleName=role_identifier, Tags=tag_list)

            # Return updated role
            return self.get_role(role_identifier)

        except ClientError as e:
            raise Exception(
                f"AWS IAM update role error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM update role error: {str(e)}")

    # Policy Management

    def list_policies(
        self,
        scope: str | None = None,
        limit: int | None = None,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        """List IAM policies."""
        try:
            params = {}

            if scope:
                params["Scope"] = scope  # "All", "AWS", or "Local"

            if limit:
                params["MaxItems"] = min(limit, 1000)
            if next_token:
                params["Marker"] = next_token

            response = self.client.list_policies(**params)

            policies = [
                self._normalize_policy(policy)
                for policy in response.get("Policies", [])
            ]

            return {
                "policies": policies,
                "next_token": response.get("Marker"),
                "truncated": response.get("IsTruncated", False),
            }

        except ClientError as e:
            raise Exception(
                f"AWS IAM list policies error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM list policies error: {str(e)}")

    def get_policy(self, policy_identifier: str) -> dict[str, Any]:
        """Get IAM policy details."""
        try:
            response = self.client.get_policy(PolicyArn=policy_identifier)
            policy = response["Policy"]

            # Get policy version (default version document)
            try:
                version_response = self.client.get_policy_version(
                    PolicyArn=policy_identifier,
                    VersionId=policy["DefaultVersionId"],
                )
                policy_document = version_response["PolicyVersion"]["Document"]
            except Exception:
                policy_document = None

            normalized_policy = self._normalize_policy(policy)
            normalized_policy["policy_document"] = policy_document
            normalized_policy["version_id"] = policy.get("DefaultVersionId")

            return normalized_policy

        except ClientError as e:
            raise Exception(
                f"AWS IAM get policy error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM get policy error: {str(e)}")

    def create_policy(
        self,
        policy_name: str,
        policy_document: dict[str, Any],
        description: str | None = None,
        tags: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Create a new IAM policy."""
        try:
            create_params = {
                "PolicyName": policy_name,
                "PolicyDocument": json.dumps(policy_document),
            }

            if description:
                create_params["Description"] = description

            if kwargs.get("path"):
                create_params["Path"] = kwargs["path"]

            if tags:
                create_params["Tags"] = [
                    {"Key": k, "Value": v} for k, v in tags.items()
                ]

            response = self.client.create_policy(**create_params)
            policy = response["Policy"]

            normalized_policy = self._normalize_policy(policy)
            normalized_policy["tags"] = tags or {}

            return normalized_policy

        except ClientError as e:
            raise Exception(
                f"AWS IAM create policy error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM create policy error: {str(e)}")

    def delete_policy(self, policy_identifier: str) -> dict[str, Any]:
        """Delete an IAM policy."""
        try:
            # Must delete all non-default versions first
            try:
                versions_response = self.client.list_policy_versions(
                    PolicyArn=policy_identifier
                )
                for version in versions_response.get("Versions", []):
                    if not version["IsDefaultVersion"]:
                        self.client.delete_policy_version(
                            PolicyArn=policy_identifier, VersionId=version["VersionId"]
                        )
            except Exception:
                pass

            # Delete the policy
            self.client.delete_policy(PolicyArn=policy_identifier)

            return {"message": f"Policy {policy_identifier} deleted successfully"}

        except ClientError as e:
            raise Exception(
                f"AWS IAM delete policy error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM delete policy error: {str(e)}")

    # Policy Attachments

    def attach_policy_to_user(
        self, user_identifier: str, policy_identifier: str
    ) -> dict[str, Any]:
        """Attach a policy to a user."""
        try:
            self.client.attach_user_policy(
                UserName=user_identifier, PolicyArn=policy_identifier
            )

            return {
                "message": f"Policy {policy_identifier} attached to user {user_identifier}"
            }

        except ClientError as e:
            raise Exception(
                f"AWS IAM attach policy to user error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM attach policy to user error: {str(e)}")

    def detach_policy_from_user(
        self, user_identifier: str, policy_identifier: str
    ) -> dict[str, Any]:
        """Detach a policy from a user."""
        try:
            self.client.detach_user_policy(
                UserName=user_identifier, PolicyArn=policy_identifier
            )

            return {
                "message": f"Policy {policy_identifier} detached from user {user_identifier}"
            }

        except ClientError as e:
            raise Exception(
                f"AWS IAM detach policy from user error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM detach policy from user error: {str(e)}")

    def attach_policy_to_role(
        self, role_identifier: str, policy_identifier: str
    ) -> dict[str, Any]:
        """Attach a policy to a role."""
        try:
            self.client.attach_role_policy(
                RoleName=role_identifier, PolicyArn=policy_identifier
            )

            return {
                "message": f"Policy {policy_identifier} attached to role {role_identifier}"
            }

        except ClientError as e:
            raise Exception(
                f"AWS IAM attach policy to role error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM attach policy to role error: {str(e)}")

    def detach_policy_from_role(
        self, role_identifier: str, policy_identifier: str
    ) -> dict[str, Any]:
        """Detach a policy from a role."""
        try:
            self.client.detach_role_policy(
                RoleName=role_identifier, PolicyArn=policy_identifier
            )

            return {
                "message": f"Policy {policy_identifier} detached from role {role_identifier}"
            }

        except ClientError as e:
            raise Exception(
                f"AWS IAM detach policy from role error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM detach policy from role error: {str(e)}")

    def list_user_policies(self, user_identifier: str) -> list[dict[str, Any]]:
        """List all policies attached to a user."""
        try:
            response = self.client.list_attached_user_policies(UserName=user_identifier)

            policies = [
                self._normalize_policy(policy)
                for policy in response.get("AttachedPolicies", [])
            ]

            return policies

        except ClientError as e:
            raise Exception(
                f"AWS IAM list user policies error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM list user policies error: {str(e)}")

    def list_role_policies(self, role_identifier: str) -> list[dict[str, Any]]:
        """List all policies attached to a role."""
        try:
            response = self.client.list_attached_role_policies(RoleName=role_identifier)

            policies = [
                self._normalize_policy(policy)
                for policy in response.get("AttachedPolicies", [])
            ]

            return policies

        except ClientError as e:
            raise Exception(
                f"AWS IAM list role policies error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM list role policies error: {str(e)}")

    # Access Keys

    def create_access_key(self, user_identifier: str) -> dict[str, Any]:
        """Create access key for a user."""
        try:
            response = self.client.create_access_key(UserName=user_identifier)
            access_key = response["AccessKey"]

            return {
                "access_key_id": access_key["AccessKeyId"],
                "secret_access_key": access_key["SecretAccessKey"],
                "status": access_key["Status"],
                "created_at": access_key["CreateDate"].isoformat(),
                "user": user_identifier,
            }

        except ClientError as e:
            raise Exception(
                f"AWS IAM create access key error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM create access key error: {str(e)}")

    def list_access_keys(self, user_identifier: str) -> list[dict[str, Any]]:
        """List access keys for a user."""
        try:
            response = self.client.list_access_keys(UserName=user_identifier)

            keys = []
            for key in response.get("AccessKeyMetadata", []):
                keys.append(
                    {
                        "access_key_id": key["AccessKeyId"],
                        "status": key["Status"],
                        "created_at": key["CreateDate"].isoformat(),
                        "user": user_identifier,
                    }
                )

            return keys

        except ClientError as e:
            raise Exception(
                f"AWS IAM list access keys error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM list access keys error: {str(e)}")

    def delete_access_key(
        self, user_identifier: str, access_key_id: str
    ) -> dict[str, Any]:
        """Delete an access key."""
        try:
            self.client.delete_access_key(
                UserName=user_identifier, AccessKeyId=access_key_id
            )

            return {
                "message": f"Access key {access_key_id} deleted for user {user_identifier}"
            }

        except ClientError as e:
            raise Exception(
                f"AWS IAM delete access key error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM delete access key error: {str(e)}")

    # Group Management (AWS IAM supports groups)

    def list_groups(
        self, limit: int | None = None, next_token: str | None = None
    ) -> dict[str, Any]:
        """List all IAM groups."""
        try:
            params = {}
            if limit:
                params["MaxItems"] = min(limit, 1000)
            if next_token:
                params["Marker"] = next_token

            response = self.client.list_groups(**params)

            groups = []
            for group in response.get("Groups", []):
                groups.append(
                    {
                        "id": group["GroupId"],
                        "name": group["GroupName"],
                        "path": group["Path"],
                        "arn": group["Arn"],
                        "created_at": group["CreateDate"].isoformat(),
                    }
                )

            return {
                "groups": groups,
                "next_token": response.get("Marker"),
                "truncated": response.get("IsTruncated", False),
                "supported": True,
            }

        except ClientError as e:
            raise Exception(
                f"AWS IAM list groups error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM list groups error: {str(e)}")

    def create_group(
        self, group_name: str, description: str | None = None
    ) -> dict[str, Any]:
        """Create a new IAM group."""
        try:
            create_params = {"GroupName": group_name}

            response = self.client.create_group(**create_params)
            group = response["Group"]

            return {
                "id": group["GroupId"],
                "name": group["GroupName"],
                "path": group["Path"],
                "arn": group["Arn"],
                "created_at": group["CreateDate"].isoformat(),
            }

        except ClientError as e:
            raise Exception(
                f"AWS IAM create group error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM create group error: {str(e)}")

    def delete_group(self, group_identifier: str) -> dict[str, Any]:
        """Delete an IAM group."""
        try:
            # Must detach policies and remove users first
            try:
                policies_response = self.client.list_attached_group_policies(
                    GroupName=group_identifier
                )
                for policy in policies_response.get("AttachedPolicies", []):
                    self.client.detach_group_policy(
                        GroupName=group_identifier, PolicyArn=policy["PolicyArn"]
                    )
            except Exception:
                pass

            # Delete inline policies
            try:
                inline_policies = self.client.list_group_policies(
                    GroupName=group_identifier
                )
                for policy_name in inline_policies.get("PolicyNames", []):
                    self.client.delete_group_policy(
                        GroupName=group_identifier, PolicyName=policy_name
                    )
            except Exception:
                pass

            # Delete the group
            self.client.delete_group(GroupName=group_identifier)

            return {"message": f"Group {group_identifier} deleted successfully"}

        except ClientError as e:
            raise Exception(
                f"AWS IAM delete group error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM delete group error: {str(e)}")

    def add_user_to_group(
        self, user_identifier: str, group_identifier: str
    ) -> dict[str, Any]:
        """Add user to group."""
        try:
            self.client.add_user_to_group(
                UserName=user_identifier, GroupName=group_identifier
            )

            return {
                "message": f"User {user_identifier} added to group {group_identifier}"
            }

        except ClientError as e:
            raise Exception(
                f"AWS IAM add user to group error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM add user to group error: {str(e)}")

    def remove_user_from_group(
        self, user_identifier: str, group_identifier: str
    ) -> dict[str, Any]:
        """Remove user from group."""
        try:
            self.client.remove_user_from_group(
                UserName=user_identifier, GroupName=group_identifier
            )

            return {
                "message": f"User {user_identifier} removed from group {group_identifier}"
            }

        except ClientError as e:
            raise Exception(
                f"AWS IAM remove user from group error: {e.response['Error']['Message']}"
            )
        except Exception as e:
            raise Exception(f"AWS IAM remove user from group error: {str(e)}")

    # Utility Methods

    def test_connection(self) -> bool:
        """Test AWS IAM connectivity."""
        try:
            # Simple API call to test connectivity
            self.client.list_users(MaxItems=1)
            return True
        except Exception:
            return False

    def sync_from_provider(self) -> dict[str, Any]:
        """Sync IAM resources from AWS to Elder database."""
        errors = []
        users_synced = 0
        roles_synced = 0
        policies_synced = 0

        try:
            # Sync users
            try:
                users_response = self.list_users(limit=1000)
                users_synced = len(users_response.get("users", []))
            except Exception as e:
                errors.append(f"Error syncing users: {str(e)}")

            # Sync roles
            try:
                roles_response = self.list_roles(limit=1000)
                roles_synced = len(roles_response.get("roles", []))
            except Exception as e:
                errors.append(f"Error syncing roles: {str(e)}")

            # Sync policies (local only to avoid overwhelming with AWS managed policies)
            try:
                policies_response = self.list_policies(scope="Local", limit=1000)
                policies_synced = len(policies_response.get("policies", []))
            except Exception as e:
                errors.append(f"Error syncing policies: {str(e)}")

        except Exception as e:
            errors.append(f"General sync error: {str(e)}")

        return {
            "users_synced": users_synced,
            "roles_synced": roles_synced,
            "policies_synced": policies_synced,
            "errors": errors,
        }
