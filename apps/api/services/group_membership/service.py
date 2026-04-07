"""Group Membership Management Service.

Enterprise feature providing:
- Group ownership (individual or group)
- Access request workflow with configurable approval modes
- Provider write-back (LDAP, Okta)
- Audit logging for all membership changes
"""

# flake8: noqa: E501

import datetime
import logging
import secrets
from datetime import timezone
from typing import Any, Dict, List, Optional

from apps.api.services.audit.service import AuditService

logger = logging.getLogger(__name__)


class GroupMembershipService:
    """Service for managing group membership requests and approvals."""

    # Approval modes
    APPROVAL_MODE_ANY = "any"  # Any owner can approve
    APPROVAL_MODE_ALL = "all"  # All owners must approve
    APPROVAL_MODE_THRESHOLD = "threshold"  # N approvals required

    # Request statuses
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_DENIED = "denied"
    STATUS_EXPIRED = "expired"
    STATUS_CANCELLED = "cancelled"

    # Provider types
    PROVIDER_INTERNAL = "internal"
    PROVIDER_LDAP = "ldap"
    PROVIDER_OKTA = "okta"

    def __init__(self, db):
        """Initialize service with database connection."""
        self.db = db

    def _generate_village_id(self) -> str:
        """Generate a unique village ID for requests."""
        return secrets.token_hex(16)

    # ==================== Group Management ====================

    def list_groups(
        self,
        tenant_id: int = 1,
        include_members: bool = False,
        include_pending: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List all groups with optional member counts and pending requests."""
        db = self.db

        query = db.identity_groups.is_active == True  # noqa: E712

        total = db(query).count()
        groups = db(query).select(
            orderby=db.identity_groups.name, limitby=(offset, offset + limit)
        )

        result = []
        for group in groups:
            group_data = self._group_to_dict(group)

            if include_members:
                group_data["member_count"] = db(
                    db.identity_group_memberships.group_id == group.id
                ).count()

            if include_pending:
                group_data["pending_request_count"] = db(
                    (db.group_access_requests.group_id == group.id)
                    & (db.group_access_requests.status == self.STATUS_PENDING)
                ).count()

            result.append(group_data)

        return {"groups": result, "total": total, "limit": limit, "offset": offset}

    def get_group(self, group_id: int) -> Optional[Dict[str, Any]]:
        """Get group details including members and ownership info."""
        db = self.db

        group = db.identity_groups[group_id]
        if not group:
            return None

        group_data = self._group_to_dict(group)

        # Get owner info
        if group.owner_identity_id:
            owner = db.identities[group.owner_identity_id]
            if owner:
                group_data["owner"] = {
                    "id": owner.id,
                    "display_name": owner.display_name,
                    "email": owner.email,
                }

        if group.owner_group_id:
            owner_group = db.identity_groups[group.owner_group_id]
            if owner_group:
                group_data["owner_group"] = {
                    "id": owner_group.id,
                    "name": owner_group.name,
                }

        # Get pending request count
        group_data["pending_request_count"] = db(
            (db.group_access_requests.group_id == group_id)
            & (db.group_access_requests.status == self.STATUS_PENDING)
        ).count()

        return group_data

    def update_group(
        self,
        group_id: int,
        owner_identity_id: Optional[int] = None,
        owner_group_id: Optional[int] = None,
        approval_mode: Optional[str] = None,
        approval_threshold: Optional[int] = None,
        provider: Optional[str] = None,
        provider_group_id: Optional[str] = None,
        sync_enabled: Optional[bool] = None,
        updated_by: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update group ownership and provider settings."""
        db = self.db

        group = db.identity_groups[group_id]
        if not group:
            return None

        # Track changes for audit
        old_values = {
            "owner_identity_id": group.owner_identity_id,
            "owner_group_id": group.owner_group_id,
            "approval_mode": group.approval_mode,
            "provider": group.provider,
            "sync_enabled": group.sync_enabled,
        }

        # Update fields
        updates = {}
        if owner_identity_id is not None:
            updates["owner_identity_id"] = owner_identity_id
        if owner_group_id is not None:
            updates["owner_group_id"] = owner_group_id
        if approval_mode is not None:
            updates["approval_mode"] = approval_mode
        if approval_threshold is not None:
            updates["approval_threshold"] = approval_threshold
        if provider is not None:
            updates["provider"] = provider
        if provider_group_id is not None:
            updates["provider_group_id"] = provider_group_id
        if sync_enabled is not None:
            updates["sync_enabled"] = sync_enabled

        if updates:
            db(db.identity_groups.id == group_id).update(**updates)
            db.commit()

            # Audit log
            AuditService.log(
                action="update",
                resource_type="identity_group",
                resource_id=group_id,
                identity_id=updated_by,
                details={
                    "category": "group_ownership_changed",
                    "updates": updates,
                },
                old_values=old_values,
                new_values=updates,
            )

        return self.get_group(group_id)

    # ==================== Access Requests ====================

    def create_access_request(
        self,
        group_id: int,
        requester_id: int,
        reason: Optional[str] = None,
        expires_at: Optional[datetime.datetime] = None,
        tenant_id: int = 1,
    ) -> Dict[str, Any]:
        """Create an access request for a group."""
        db = self.db

        # Check if already a member
        existing = (
            db(
                (db.identity_group_memberships.group_id == group_id)
                & (db.identity_group_memberships.identity_id == requester_id)
            )
            .select()
            .first()
        )
        if existing:
            raise ValueError("Already a member of this group")

        # Check for existing pending request
        pending = (
            db(
                (db.group_access_requests.group_id == group_id)
                & (db.group_access_requests.requester_id == requester_id)
                & (db.group_access_requests.status == self.STATUS_PENDING)
            )
            .select()
            .first()
        )
        if pending:
            raise ValueError("Already have a pending request for this group")

        # Create request
        now = datetime.datetime.now(timezone.utc)
        request_id = db.group_access_requests.insert(
            tenant_id=tenant_id,
            group_id=group_id,
            requester_id=requester_id,
            status=self.STATUS_PENDING,
            reason=reason,
            expires_at=expires_at,
            village_id=self._generate_village_id(),
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Audit log
        AuditService.log(
            action="create",
            resource_type="group_access_request",
            resource_id=request_id,
            identity_id=requester_id,
            details={
                "category": "group_access_requested",
                "group_id": group_id,
                "reason": reason,
            },
        )

        return self.get_request(request_id)

    def get_request(self, request_id: int) -> Optional[Dict[str, Any]]:
        """Get access request details."""
        db = self.db

        request = db.group_access_requests[request_id]
        if not request:
            return None

        return self._request_to_dict(request)

    def list_requests(
        self,
        group_id: Optional[int] = None,
        requester_id: Optional[int] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List access requests with optional filters."""
        db = self.db

        query = db.group_access_requests.id > 0

        if group_id:
            query &= db.group_access_requests.group_id == group_id
        if requester_id:
            query &= db.group_access_requests.requester_id == requester_id
        if status:
            query &= db.group_access_requests.status == status

        total = db(query).count()
        requests = db(query).select(
            orderby=~db.group_access_requests.created_at,
            limitby=(offset, offset + limit),
        )

        return {
            "requests": [self._request_to_dict(r) for r in requests],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def get_pending_requests_for_owner(
        self,
        owner_identity_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Get all pending requests for groups owned by this identity."""
        db = self.db

        # Get groups owned directly or via owning group membership
        owned_group_ids = self._get_owned_group_ids(owner_identity_id)

        if not owned_group_ids:
            return {"requests": [], "total": 0, "limit": limit, "offset": offset}

        query = db.group_access_requests.group_id.belongs(owned_group_ids) & (
            db.group_access_requests.status == self.STATUS_PENDING
        )

        total = db(query).count()
        requests = db(query).select(
            orderby=~db.group_access_requests.created_at,
            limitby=(offset, offset + limit),
        )

        return {
            "requests": [self._request_to_dict(r) for r in requests],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    # ==================== Approval/Denial ====================

    def approve_request(
        self,
        request_id: int,
        approver_id: int,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record an approval for an access request."""
        db = self.db

        request = db.group_access_requests[request_id]
        if not request:
            raise ValueError("Request not found")

        if request.status != self.STATUS_PENDING:
            raise ValueError(f"Request is not pending (status: {request.status})")

        # Verify approver is an owner
        if not self.is_group_owner(approver_id, request.group_id):
            raise ValueError("Not authorized to approve this request")

        # Record approval
        now = datetime.datetime.now(timezone.utc)
        db.group_access_approvals.insert(
            tenant_id=request.tenant_id,
            request_id=request_id,
            approver_id=approver_id,
            decision="approved",
            comment=comment,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Check if approval is complete based on mode
        if self._check_approval_complete(request_id):
            self._finalize_approval(request_id, approver_id)

        # Audit log
        AuditService.log(
            action="approve",
            resource_type="group_access_request",
            resource_id=request_id,
            identity_id=approver_id,
            details={
                "category": "group_access_approved",
                "group_id": request.group_id,
                "requester_id": request.requester_id,
                "comment": comment,
            },
        )

        return self.get_request(request_id)

    def deny_request(
        self,
        request_id: int,
        denier_id: int,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Deny an access request."""
        db = self.db

        request = db.group_access_requests[request_id]
        if not request:
            raise ValueError("Request not found")

        if request.status != self.STATUS_PENDING:
            raise ValueError(f"Request is not pending (status: {request.status})")

        # Verify denier is an owner
        if not self.is_group_owner(denier_id, request.group_id):
            raise ValueError("Not authorized to deny this request")

        # Record denial
        now = datetime.datetime.now(timezone.utc)
        db.group_access_approvals.insert(
            tenant_id=request.tenant_id,
            request_id=request_id,
            approver_id=denier_id,
            decision="denied",
            comment=comment,
            created_at=now,
            updated_at=now,
        )

        # Update request status
        db(db.group_access_requests.id == request_id).update(
            status=self.STATUS_DENIED,
            decided_at=datetime.datetime.now(datetime.timezone.utc),
            decided_by_id=denier_id,
            decision_comment=comment,
        )
        db.commit()

        # Audit log
        AuditService.log(
            action="deny",
            resource_type="group_access_request",
            resource_id=request_id,
            identity_id=denier_id,
            details={
                "category": "group_access_denied",
                "group_id": request.group_id,
                "requester_id": request.requester_id,
                "comment": comment,
            },
        )

        return self.get_request(request_id)

    def cancel_request(self, request_id: int, canceller_id: int) -> Dict[str, Any]:
        """Cancel own access request."""
        db = self.db

        request = db.group_access_requests[request_id]
        if not request:
            raise ValueError("Request not found")

        if request.requester_id != canceller_id:
            raise ValueError("Can only cancel your own requests")

        if request.status != self.STATUS_PENDING:
            raise ValueError(f"Request is not pending (status: {request.status})")

        db(db.group_access_requests.id == request_id).update(
            status=self.STATUS_CANCELLED,
        )
        db.commit()

        return self.get_request(request_id)

    def bulk_approve_requests(
        self,
        request_ids: List[int],
        approver_id: int,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Bulk approve multiple requests."""
        results = {"approved": [], "failed": []}

        for request_id in request_ids:
            try:
                self.approve_request(request_id, approver_id, comment)
                results["approved"].append(request_id)
            except Exception as e:
                results["failed"].append({"id": request_id, "error": str(e)})

        return results

    # ==================== Membership Management ====================

    def get_group_members(
        self,
        group_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Get all members of a group."""
        db = self.db

        query = db.identity_group_memberships.group_id == group_id

        total = db(query).count()
        memberships = db(query).select(
            orderby=db.identity_group_memberships.created_at,
            limitby=(offset, offset + limit),
        )

        members = []
        for m in memberships:
            identity = db.identities[m.identity_id]
            if identity:
                members.append(
                    {
                        "membership_id": m.id,
                        "identity_id": m.identity_id,
                        "display_name": identity.display_name,
                        "email": identity.email,
                        "expires_at": (
                            m.expires_at.isoformat() if m.expires_at else None
                        ),
                        "provider_synced": m.provider_synced,
                        "created_at": (
                            m.created_at.isoformat() if m.created_at else None
                        ),
                    }
                )

        return {"members": members, "total": total, "limit": limit, "offset": offset}

    def add_member(
        self,
        group_id: int,
        identity_id: int,
        added_by: int,
        expires_at: Optional[datetime.datetime] = None,
        provider_member_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Directly add a member to a group (admin only)."""
        db = self.db

        # Check if already a member
        existing = (
            db(
                (db.identity_group_memberships.group_id == group_id)
                & (db.identity_group_memberships.identity_id == identity_id)
            )
            .select()
            .first()
        )
        if existing:
            raise ValueError("Already a member of this group")

        # Add membership
        now = datetime.datetime.now(timezone.utc)
        membership_id = db.identity_group_memberships.insert(
            group_id=group_id,
            identity_id=identity_id,
            expires_at=expires_at,
            provider_member_id=provider_member_id,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Sync to provider if enabled
        group = db.identity_groups[group_id]
        if group.sync_enabled and group.provider != self.PROVIDER_INTERNAL:
            self._sync_membership_to_provider(group_id, identity_id, "add")

        # Audit log
        AuditService.log(
            action="create",
            resource_type="identity_group_membership",
            resource_id=membership_id,
            identity_id=added_by,
            details={
                "category": "group_member_added",
                "group_id": group_id,
                "member_identity_id": identity_id,
            },
        )

        return {"membership_id": membership_id, "group_id": group_id}

    def remove_member(
        self,
        group_id: int,
        identity_id: int,
        removed_by: int,
    ) -> Dict[str, Any]:
        """Remove a member from a group."""
        db = self.db

        membership = (
            db(
                (db.identity_group_memberships.group_id == group_id)
                & (db.identity_group_memberships.identity_id == identity_id)
            )
            .select()
            .first()
        )

        if not membership:
            raise ValueError("Not a member of this group")

        # Sync to provider if enabled
        group = db.identity_groups[group_id]
        if group.sync_enabled and group.provider != self.PROVIDER_INTERNAL:
            self._sync_membership_to_provider(group_id, identity_id, "remove")

        # Delete membership
        db(db.identity_group_memberships.id == membership.id).delete()
        db.commit()

        # Audit log
        AuditService.log(
            action="delete",
            resource_type="identity_group_membership",
            resource_id=membership.id,
            identity_id=removed_by,
            details={
                "category": "group_member_removed",
                "group_id": group_id,
                "member_identity_id": identity_id,
            },
        )

        return {"removed": True, "group_id": group_id, "identity_id": identity_id}

    # ==================== Helper Methods ====================

    def is_group_owner(self, identity_id: int, group_id: int) -> bool:
        """Check if identity is an owner of the group (direct or via owning group)."""
        db = self.db

        group = db.identity_groups[group_id]
        if not group:
            return False

        # Direct owner
        if group.owner_identity_id == identity_id:
            return True

        # Member of owning group
        if group.owner_group_id:
            membership = (
                db(
                    (db.identity_group_memberships.group_id == group.owner_group_id)
                    & (db.identity_group_memberships.identity_id == identity_id)
                )
                .select()
                .first()
            )
            if membership:
                return True

        return False

    def _get_owned_group_ids(self, identity_id: int) -> List[int]:
        """Get all group IDs owned by this identity."""
        db = self.db

        # Directly owned
        direct = db(db.identity_groups.owner_identity_id == identity_id).select(
            db.identity_groups.id
        )
        owned_ids = [g.id for g in direct]

        # Get groups where identity is member of owning group
        memberships = db(
            db.identity_group_memberships.identity_id == identity_id
        ).select(db.identity_group_memberships.group_id)

        member_group_ids = [m.group_id for m in memberships]

        if member_group_ids:
            via_group = db(
                db.identity_groups.owner_group_id.belongs(member_group_ids)
            ).select(db.identity_groups.id)
            owned_ids.extend([g.id for g in via_group])

        return list(set(owned_ids))

    def _check_approval_complete(self, request_id: int) -> bool:
        """Check if request has enough approvals based on group's approval mode."""
        db = self.db

        request = db.group_access_requests[request_id]
        group = db.identity_groups[request.group_id]

        # Count approvals (not denials)
        approvals = db(
            (db.group_access_approvals.request_id == request_id)
            & (db.group_access_approvals.decision == "approved")
        ).count()

        if group.approval_mode == self.APPROVAL_MODE_ANY:
            return approvals >= 1

        elif group.approval_mode == self.APPROVAL_MODE_ALL:
            # Count total owners
            total_owners = self._count_group_owners(request.group_id)
            return approvals >= total_owners

        elif group.approval_mode == self.APPROVAL_MODE_THRESHOLD:
            return approvals >= (group.approval_threshold or 1)

        return False

    def _count_group_owners(self, group_id: int) -> int:
        """Count total number of owners for a group."""
        db = self.db

        group = db.identity_groups[group_id]
        count = 0

        if group.owner_identity_id:
            count += 1

        if group.owner_group_id:
            count += db(
                db.identity_group_memberships.group_id == group.owner_group_id
            ).count()

        return max(count, 1)

    def _finalize_approval(self, request_id: int, final_approver_id: int) -> None:
        """Finalize an approved request - create membership and sync."""
        db = self.db

        request = db.group_access_requests[request_id]
        group = db.identity_groups[request.group_id]

        # Get provider member ID from identity
        identity = db.identities[request.requester_id]
        provider_member_id = None
        if identity and identity.attributes:
            attrs = identity.attributes
            if isinstance(attrs, str):
                import json

                attrs = json.loads(attrs)
            if group.provider == self.PROVIDER_LDAP:
                provider_member_id = attrs.get("ldap_dn")
            elif group.provider == self.PROVIDER_OKTA:
                provider_member_id = attrs.get("okta_id")

        # Create membership
        now = datetime.datetime.now(timezone.utc)
        membership_id = db.identity_group_memberships.insert(
            group_id=request.group_id,
            identity_id=request.requester_id,
            expires_at=request.expires_at,
            granted_via_request_id=request_id,
            provider_member_id=provider_member_id,
            created_at=now,
            updated_at=now,
        )

        # Update request status
        db(db.group_access_requests.id == request_id).update(
            status=self.STATUS_APPROVED,
            decided_at=datetime.datetime.now(datetime.timezone.utc),
            decided_by_id=final_approver_id,
        )
        db.commit()

        # Sync to provider if enabled
        if group.sync_enabled and group.provider != self.PROVIDER_INTERNAL:
            self._sync_membership_to_provider(
                request.group_id, request.requester_id, "add"
            )

    def _sync_membership_to_provider(
        self,
        group_id: int,
        identity_id: int,
        action: str,
    ) -> bool:
        """Sync membership change to external provider."""
        db = self.db

        group = db.identity_groups[group_id]
        if not group.sync_enabled or not group.provider_group_id:
            return False

        # Get identity's provider ID
        identity = db.identities[identity_id]
        if not identity:
            return False

        membership = (
            db(
                (db.identity_group_memberships.group_id == group_id)
                & (db.identity_group_memberships.identity_id == identity_id)
            )
            .select()
            .first()
        )

        provider_member_id = membership.provider_member_id if membership else None
        if not provider_member_id:
            logger.warning(
                f"No provider_member_id for identity {identity_id}, skipping sync"
            )
            return False

        try:
            # TODO: Get connector from registry and call sync
            # This will be implemented when connectors are integrated
            logger.info(
                f"Provider sync: {action} member {provider_member_id} "
                f"{'to' if action == 'add' else 'from'} group {group.provider_group_id}"
            )

            # Update sync status
            if membership:
                db(db.identity_group_memberships.id == membership.id).update(
                    provider_synced=True,
                    provider_synced_at=datetime.datetime.now(datetime.timezone.utc),
                )
                db.commit()

            # Audit log
            AuditService.log(
                action="sync",
                resource_type="identity_group_membership",
                resource_id=membership.id if membership else None,
                details={
                    "category": "group_provider_sync",
                    "provider": group.provider,
                    "group_id": group_id,
                    "identity_id": identity_id,
                    "action": action,
                },
            )

            return True

        except Exception as e:
            logger.error(f"Provider sync failed: {e}")
            AuditService.log(
                action="sync_failed",
                resource_type="identity_group_membership",
                details={
                    "category": "group_provider_sync_failed",
                    "provider": group.provider,
                    "group_id": group_id,
                    "identity_id": identity_id,
                    "error": str(e),
                },
                success=False,
            )
            return False

    def _group_to_dict(self, group) -> Dict[str, Any]:
        """Convert group record to dictionary."""
        return {
            "id": group.id,
            "name": group.name,
            "description": group.description,
            "ldap_dn": group.ldap_dn,
            "saml_group": group.saml_group,
            "is_active": group.is_active,
            "owner_identity_id": group.owner_identity_id,
            "owner_group_id": group.owner_group_id,
            "approval_mode": group.approval_mode or self.APPROVAL_MODE_ANY,
            "approval_threshold": group.approval_threshold or 1,
            "provider": group.provider or self.PROVIDER_INTERNAL,
            "provider_group_id": group.provider_group_id,
            "sync_enabled": group.sync_enabled or False,
            "created_at": group.created_at.isoformat() if group.created_at else None,
            "updated_at": group.updated_at.isoformat() if group.updated_at else None,
        }

    def _request_to_dict(self, request) -> Dict[str, Any]:
        """Convert request record to dictionary."""
        db = self.db

        # Get requester info
        requester = db.identities[request.requester_id]
        requester_info = None
        if requester:
            requester_info = {
                "id": requester.id,
                "display_name": requester.display_name,
                "email": requester.email,
            }

        # Get group info
        group = db.identity_groups[request.group_id]
        group_info = None
        if group:
            group_info = {"id": group.id, "name": group.name}

        # Get approvals
        approvals = db(db.group_access_approvals.request_id == request.id).select()

        approval_list = []
        for a in approvals:
            approver = db.identities[a.approver_id]
            approval_list.append(
                {
                    "approver_id": a.approver_id,
                    "approver_name": approver.display_name if approver else None,
                    "decision": a.decision,
                    "comment": a.comment,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
            )

        return {
            "id": request.id,
            "village_id": request.village_id,
            "group_id": request.group_id,
            "group": group_info,
            "requester_id": request.requester_id,
            "requester": requester_info,
            "status": request.status,
            "reason": request.reason,
            "expires_at": (
                request.expires_at.isoformat() if request.expires_at else None
            ),
            "decided_at": (
                request.decided_at.isoformat() if request.decided_at else None
            ),
            "decided_by_id": request.decided_by_id,
            "decision_comment": request.decision_comment,
            "approvals": approval_list,
            "created_at": (
                request.created_at.isoformat() if request.created_at else None
            ),
            "updated_at": (
                request.updated_at.isoformat() if request.updated_at else None
            ),
        }

    # ==================== Scheduled Tasks ====================

    def process_expired_memberships(self) -> Dict[str, Any]:
        """
        Process expired group memberships.

        This method should be called periodically (e.g., daily) to:
        1. Find memberships with expires_at in the past
        2. Remove them and sync to providers
        3. Log audit events

        Returns:
            Dict with processing results
        """
        db = self.db
        now = datetime.datetime.now(datetime.timezone.utc)

        # Find expired memberships
        expired = db(
            (db.identity_group_memberships.expires_at.isnotnull())
            & (db.identity_group_memberships.expires_at < now)
        ).select()

        results = {
            "processed": 0,
            "removed": 0,
            "sync_failed": 0,
            "errors": [],
        }

        for membership in expired:
            results["processed"] += 1
            try:
                group = db.identity_groups[membership.group_id]

                # Sync removal to provider if enabled
                if (
                    group
                    and group.sync_enabled
                    and group.provider != self.PROVIDER_INTERNAL
                ):
                    sync_success = self._sync_membership_to_provider(
                        membership.group_id,
                        membership.identity_id,
                        "remove",
                    )
                    if not sync_success:
                        results["sync_failed"] += 1

                # Delete membership
                db(db.identity_group_memberships.id == membership.id).delete()
                db.commit()
                results["removed"] += 1

                # Audit log
                AuditService.log(
                    action="delete",
                    resource_type="identity_group_membership",
                    resource_id=membership.id,
                    details={
                        "category": "membership_expired",
                        "group_id": membership.group_id,
                        "identity_id": membership.identity_id,
                        "expired_at": membership.expires_at.isoformat(),
                    },
                )

                logger.info(
                    f"Removed expired membership: identity {membership.identity_id} "
                    f"from group {membership.group_id}"
                )

            except Exception as e:
                logger.error(
                    f"Error processing expired membership {membership.id}: {e}"
                )
                results["errors"].append(
                    {
                        "membership_id": membership.id,
                        "error": str(e),
                    }
                )

        logger.info(
            f"Processed {results['processed']} expired memberships: "
            f"{results['removed']} removed, {results['sync_failed']} sync failures"
        )

        return results

    def process_stale_requests(self, days: int = 30) -> Dict[str, Any]:
        """
        Process stale pending requests.

        Automatically expire requests that have been pending for too long.

        Args:
            days: Number of days after which pending requests are stale

        Returns:
            Dict with processing results
        """
        db = self.db
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=days
        )

        # Find stale pending requests
        stale = db(
            (db.group_access_requests.status == self.STATUS_PENDING)
            & (db.group_access_requests.created_at < cutoff)
        ).select()

        results = {
            "processed": 0,
            "expired": 0,
            "errors": [],
        }

        for request in stale:
            results["processed"] += 1
            try:
                # Update to cancelled/expired status
                db(db.group_access_requests.id == request.id).update(
                    status="expired",
                    decided_at=datetime.datetime.now(datetime.timezone.utc),
                    decision_comment=f"Automatically expired after {days} days",
                )
                db.commit()
                results["expired"] += 1

                # Audit log
                AuditService.log(
                    action="update",
                    resource_type="group_access_request",
                    resource_id=request.id,
                    details={
                        "category": "request_auto_expired",
                        "group_id": request.group_id,
                        "requester_id": request.requester_id,
                        "days_pending": days,
                    },
                )

                logger.info(
                    f"Expired stale request {request.id} from user {request.requester_id}"
                )

            except Exception as e:
                logger.error(f"Error processing stale request {request.id}: {e}")
                results["errors"].append(
                    {
                        "request_id": request.id,
                        "error": str(e),
                    }
                )

        logger.info(
            f"Processed {results['processed']} stale requests: "
            f"{results['expired']} expired"
        )

        return results
