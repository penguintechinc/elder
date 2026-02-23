"""Provider-agnostic group membership operations interface.

This module defines the interface for connectors that support bidirectional
group membership synchronization (write-back to identity providers).

Enterprise feature - requires Enterprise license.
"""

# flake8: noqa: E501


from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class GroupOperationType(Enum):
    """Type of group operation."""

    ADD_MEMBER = "add_member"
    REMOVE_MEMBER = "remove_member"


@dataclass
class GroupMembershipResult:
    """Result of a group membership operation."""

    success: bool
    group_id: str
    user_id: str
    operation: str  # "add" or "remove"
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "success": self.success,
            "group_id": self.group_id,
            "user_id": self.user_id,
            "operation": self.operation,
            "error": self.error,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


@dataclass
class GroupSyncResult:
    """Result of a bulk group sync operation."""

    total_operations: int = 0
    successful: int = 0
    failed: int = 0
    results: List[GroupMembershipResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        """Check if there were any errors."""
        return self.failed > 0 or len(self.errors) > 0

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "total_operations": self.total_operations,
            "successful": self.successful,
            "failed": self.failed,
            "has_errors": self.has_errors,
            "results": [r.to_dict() for r in self.results],
            "errors": self.errors,
        }


class GroupOperationsMixin(ABC):
    """
    Mixin for connectors that support group write-back operations.

    Connectors implementing this mixin can synchronize group membership
    changes from Elder back to the identity provider (LDAP, Okta, etc.).

    Usage:
        class LDAPConnector(BaseConnector, GroupOperationsMixin):
            async def add_group_member(self, group_id, user_id):
                # LDAP-specific implementation
                pass
    """

    @abstractmethod
    async def add_group_member(
        self,
        group_id: str,
        user_id: str,
    ) -> GroupMembershipResult:
        """
        Add a user to a group in the provider system.

        Args:
            group_id: Provider-specific group identifier
                     (LDAP DN, Okta group ID, etc.)
            user_id: Provider-specific user identifier
                    (LDAP DN, Okta user ID, etc.)

        Returns:
            GroupMembershipResult with operation status

        Raises:
            Exception: On provider communication failure
        """

    @abstractmethod
    async def remove_group_member(
        self,
        group_id: str,
        user_id: str,
    ) -> GroupMembershipResult:
        """
        Remove a user from a group in the provider system.

        Args:
            group_id: Provider-specific group identifier
            user_id: Provider-specific user identifier

        Returns:
            GroupMembershipResult with operation status

        Raises:
            Exception: On provider communication failure
        """

    @abstractmethod
    async def get_group_members(
        self,
        group_id: str,
    ) -> List[str]:
        """
        Get current members of a group from the provider.

        Args:
            group_id: Provider-specific group identifier

        Returns:
            List of provider-specific user identifiers

        Raises:
            Exception: On provider communication failure
        """

    async def sync_group_members(
        self,
        group_id: str,
        desired_members: List[str],
        mode: str = "replace",
    ) -> GroupSyncResult:
        """
        Synchronize group membership to match desired state.

        Args:
            group_id: Provider-specific group identifier
            desired_members: List of user IDs that should be in the group
            mode: Sync mode:
                  - "replace": Set group to exactly these members
                  - "add": Add these members (keep existing)
                  - "remove": Remove these members (keep others)

        Returns:
            GroupSyncResult with details of all operations
        """
        result = GroupSyncResult()

        if mode == "replace":
            # Get current members
            current_members = set(await self.get_group_members(group_id))
            desired_set = set(desired_members)

            # Remove members not in desired list
            to_remove = current_members - desired_set
            for user_id in to_remove:
                op_result = await self.remove_group_member(group_id, user_id)
                result.results.append(op_result)
                result.total_operations += 1
                if op_result.success:
                    result.successful += 1
                else:
                    result.failed += 1

            # Add new members
            to_add = desired_set - current_members
            for user_id in to_add:
                op_result = await self.add_group_member(group_id, user_id)
                result.results.append(op_result)
                result.total_operations += 1
                if op_result.success:
                    result.successful += 1
                else:
                    result.failed += 1

        elif mode == "add":
            for user_id in desired_members:
                op_result = await self.add_group_member(group_id, user_id)
                result.results.append(op_result)
                result.total_operations += 1
                if op_result.success:
                    result.successful += 1
                else:
                    result.failed += 1

        elif mode == "remove":
            for user_id in desired_members:
                op_result = await self.remove_group_member(group_id, user_id)
                result.results.append(op_result)
                result.total_operations += 1
                if op_result.success:
                    result.successful += 1
                else:
                    result.failed += 1

        return result

    async def validate_group_exists(self, group_id: str) -> bool:
        """
        Check if a group exists in the provider system.

        Default implementation tries to get members, subclasses may override.

        Args:
            group_id: Provider-specific group identifier

        Returns:
            True if group exists, False otherwise
        """
        try:
            await self.get_group_members(group_id)
            return True
        except Exception:
            return False
