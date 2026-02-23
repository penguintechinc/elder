"""LDAP/LDAPS connector for syncing directory services to Elder."""

# flake8: noqa: E501


import ssl
from typing import Dict, List, Optional

import ldap3
from ldap3 import ALL, SUBTREE, Connection, Server
from ldap3.core.exceptions import LDAPException

from apps.worker.config.settings import settings
from apps.worker.connectors.base import BaseConnector, SyncResult
from apps.worker.connectors.group_operations import (
    GroupMembershipResult,
    GroupOperationsMixin,
)
from apps.worker.utils.elder_client import ElderAPIClient, Entity, Organization


class LDAPConnector(BaseConnector, GroupOperationsMixin):
    """Connector for LDAP/LDAPS directory services."""

    def __init__(self):
        """Initialize LDAP connector."""
        super().__init__("ldap")
        self.elder_client: Optional[ElderAPIClient] = None
        self.ldap_conn: Optional[Connection] = None
        self.organization_cache: Dict[str, int] = {}
        self.ou_cache: Dict[str, int] = {}  # Map DN to Elder org ID

    async def connect(self) -> None:
        """Establish connection to LDAP server and Elder API."""
        self.logger.info("Connecting to LDAP server", server=settings.ldap_server)

        try:
            # Configure TLS if LDAPS is enabled
            use_ssl = settings.ldap_use_ssl
            tls = None

            if use_ssl:
                # Create TLS configuration
                tls_config = ldap3.Tls(
                    validate=(
                        ssl.CERT_REQUIRED
                        if settings.ldap_verify_cert
                        else ssl.CERT_NONE
                    ),
                )
                tls = tls_config

            # Create LDAP server connection
            server = Server(
                settings.ldap_server,
                port=settings.ldap_port,
                use_ssl=use_ssl,
                tls=tls,
                get_info=ALL,
            )

            # Establish connection
            self.ldap_conn = Connection(
                server,
                user=settings.ldap_bind_dn,
                password=settings.ldap_bind_password,
                auto_bind=True,
            )

            self.logger.info(
                "LDAP connection established",
                server=settings.ldap_server,
                use_ssl=use_ssl,
            )

        except LDAPException as e:
            self.logger.error("Failed to connect to LDAP server", error=str(e))
            raise

        # Initialize Elder API client
        self.elder_client = ElderAPIClient()
        await self.elder_client.connect()

        self.logger.info("LDAP connector connected successfully")

    async def disconnect(self) -> None:
        """Disconnect from LDAP server and Elder API."""
        if self.ldap_conn:
            self.ldap_conn.unbind()
            self.ldap_conn = None

        if self.elder_client:
            await self.elder_client.close()

        self.organization_cache.clear()
        self.ou_cache.clear()
        self.logger.info("LDAP connector disconnected")

    async def _get_or_create_organization(
        self,
        name: str,
        description: str,
        parent_id: Optional[int] = None,
        ldap_dn: Optional[str] = None,
    ) -> int:
        """
        Get or create an organization in Elder.

        Args:
            name: Organization name
            description: Organization description
            parent_id: Parent organization ID
            ldap_dn: LDAP Distinguished Name

        Returns:
            Organization ID
        """
        cache_key = f"{parent_id or 'root'}:{name}"
        if cache_key in self.organization_cache:
            return self.organization_cache[cache_key]

        # Search for existing organization
        response = await self.elder_client.list_organizations(per_page=1000)
        for org in response.get("items", []):
            if org["name"] == name and org.get("parent_id") == parent_id:
                self.organization_cache[cache_key] = org["id"]
                return org["id"]

        # Create new organization
        if settings.create_missing_organizations:
            org_data = Organization(
                name=name,
                description=description,
                parent_id=parent_id,
                ldap_dn=ldap_dn,
            )
            created = await self.elder_client.create_organization(org_data)
            org_id = created["id"]
            self.organization_cache[cache_key] = org_id
            return org_id
        elif settings.default_organization_id:
            return settings.default_organization_id
        else:
            raise ValueError(
                f"Organization '{name}' not found and auto-creation disabled"
            )

    def _parse_dn_components(self, dn: str) -> List[tuple]:
        """
        Parse DN into components.

        Args:
            dn: Distinguished Name

        Returns:
            List of (type, value) tuples
        """
        components = []
        for part in dn.split(","):
            if "=" in part:
                key, value = part.split("=", 1)
                components.append((key.strip(), value.strip()))
        return components

    async def _sync_organizational_units(self, ldap_org_id: int) -> tuple[int, int]:
        """
        Sync LDAP organizational units.

        Args:
            ldap_org_id: Elder organization ID for LDAP root

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            # Search for organizational units
            self.ldap_conn.search(
                search_base=settings.ldap_base_dn,
                search_filter="(objectClass=organizationalUnit)",
                search_scope=SUBTREE,
                attributes=["ou", "description", "distinguishedName"],
            )

            entries = self.ldap_conn.entries

            # Sort by DN depth to create parent orgs first
            entries.sort(key=lambda e: str(e.entry_dn).count(","))

            for entry in entries:
                dn = str(entry.entry_dn)
                ou_name = str(entry.ou) if hasattr(entry, "ou") else None
                description = (
                    str(entry.description) if hasattr(entry, "description") else ""
                )

                if not ou_name:
                    # Extract OU name from DN
                    components = self._parse_dn_components(dn)
                    ou_name = next(
                        (v for k, v in components if k.lower() == "ou"), None
                    )

                if not ou_name:
                    continue

                # Determine parent organization
                parent_dn = ",".join(dn.split(",")[1:])
                if parent_dn == settings.ldap_base_dn or not parent_dn:
                    parent_org_id = ldap_org_id
                else:
                    parent_org_id = self.ou_cache.get(parent_dn, ldap_org_id)

                # Create or get organization in Elder
                org_id = await self._get_or_create_organization(
                    name=ou_name,
                    description=description or f"LDAP organizational unit: {dn}",
                    parent_id=parent_org_id,
                    ldap_dn=dn,
                )

                # Cache the mapping
                self.ou_cache[dn] = org_id
                created += 1

        except LDAPException as e:
            self.logger.error("Failed to sync organizational units", error=str(e))

        return created, updated

    async def _sync_users(self, ldap_org_id: int) -> tuple[int, int]:
        """
        Sync LDAP users.

        Args:
            ldap_org_id: Elder organization ID for LDAP root

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            # Search for users
            self.ldap_conn.search(
                search_base=settings.ldap_base_dn,
                search_filter=settings.ldap_user_filter,
                search_scope=SUBTREE,
                attributes=[
                    "cn",
                    "uid",
                    "mail",
                    "givenName",
                    "sn",
                    "displayName",
                    "memberOf",
                    "distinguishedName",
                    "userAccountControl",  # For Active Directory
                ],
            )

            entries = self.ldap_conn.entries

            for entry in entries:
                dn = str(entry.entry_dn)
                cn = str(entry.cn) if hasattr(entry, "cn") else None
                uid = str(entry.uid) if hasattr(entry, "uid") else None
                mail = str(entry.mail) if hasattr(entry, "mail") else None
                given_name = (
                    str(entry.givenName) if hasattr(entry, "givenName") else None
                )
                surname = str(entry.sn) if hasattr(entry, "sn") else None
                display_name = (
                    str(entry.displayName) if hasattr(entry, "displayName") else None
                )

                # Determine user's name
                name = display_name or cn or uid or mail
                if not name:
                    continue

                # Determine organization based on DN
                parent_dn = ",".join(dn.split(",")[1:])
                org_id = self.ou_cache.get(parent_dn, ldap_org_id)

                # Check if user is disabled (Active Directory)
                is_active = True
                if hasattr(entry, "userAccountControl"):
                    uac = int(str(entry.userAccountControl))
                    # Bit 2 indicates disabled account
                    is_active = not (uac & 2)

                entity = Entity(
                    name=name,
                    entity_type="user",
                    organization_id=org_id,
                    description=f"LDAP user: {dn}",
                    attributes={
                        "ldap_dn": dn,
                        "cn": cn,
                        "uid": uid,
                        "email": mail,
                        "first_name": given_name,
                        "last_name": surname,
                        "display_name": display_name,
                        "provider": "ldap",
                        "ldap_server": settings.ldap_server,
                    },
                    tags=["ldap", "user", "identity"],
                    is_active=is_active,
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=org_id,
                    entity_type="user",
                )

                found = None
                for item in existing.get("items", []):
                    if item.get("attributes", {}).get("ldap_dn") == dn:
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except LDAPException as e:
            self.logger.error("Failed to sync users", error=str(e))

        return created, updated

    async def _sync_groups(self, ldap_org_id: int) -> tuple[int, int]:
        """
        Sync LDAP groups.

        Args:
            ldap_org_id: Elder organization ID for LDAP root

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            # Search for groups
            self.ldap_conn.search(
                search_base=settings.ldap_base_dn,
                search_filter=settings.ldap_group_filter,
                search_scope=SUBTREE,
                attributes=["cn", "description", "member", "distinguishedName"],
            )

            entries = self.ldap_conn.entries

            for entry in entries:
                dn = str(entry.entry_dn)
                cn = str(entry.cn) if hasattr(entry, "cn") else None
                description = (
                    str(entry.description) if hasattr(entry, "description") else ""
                )

                if not cn:
                    continue

                # Determine organization based on DN
                parent_dn = ",".join(dn.split(",")[1:])
                org_id = self.ou_cache.get(parent_dn, ldap_org_id)

                # Get member count
                members = entry.member if hasattr(entry, "member") else []
                member_count = len(members) if members else 0

                entity = Entity(
                    name=f"Group: {cn}",
                    entity_type="user",  # Groups are user-related entities
                    organization_id=org_id,
                    description=description or f"LDAP group: {dn}",
                    attributes={
                        "ldap_dn": dn,
                        "cn": cn,
                        "group_name": cn,
                        "member_count": member_count,
                        "provider": "ldap",
                        "ldap_server": settings.ldap_server,
                        "type": "group",
                    },
                    tags=["ldap", "group", "identity"],
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=org_id,
                    entity_type="user",
                )

                found = None
                for item in existing.get("items", []):
                    attrs = item.get("attributes", {})
                    if attrs.get("ldap_dn") == dn and attrs.get("type") == "group":
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except LDAPException as e:
            self.logger.error("Failed to sync groups", error=str(e))

        return created, updated

    async def sync(self) -> SyncResult:
        """
        Synchronize LDAP resources to Elder.

        Returns:
            SyncResult with statistics
        """
        result = SyncResult(connector_name=self.name)
        self.logger.info("Starting LDAP sync", server=settings.ldap_server)

        try:
            # Create LDAP root organization
            ldap_org_id = await self._get_or_create_organization(
                f"LDAP: {settings.ldap_server}",
                f"LDAP directory server: {settings.ldap_server}",
                ldap_dn=settings.ldap_base_dn,
            )
            result.organizations_created += 1

            # Sync organizational units (creates organizations in Elder)
            ou_created, ou_updated = await self._sync_organizational_units(ldap_org_id)
            result.organizations_created += ou_created
            result.organizations_updated += ou_updated

            # Sync users
            users_created, users_updated = await self._sync_users(ldap_org_id)
            result.entities_created += users_created
            result.entities_updated += users_updated

            # Sync groups
            groups_created, groups_updated = await self._sync_groups(ldap_org_id)
            result.entities_created += groups_created
            result.entities_updated += groups_updated

            self.logger.info(
                "LDAP sync completed",
                total_ops=result.total_operations,
                orgs_created=result.organizations_created,
                entities_created=result.entities_created,
                entities_updated=result.entities_updated,
            )

        except Exception as e:
            error_msg = f"LDAP sync failed: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            result.errors.append(error_msg)

        return result

    async def health_check(self) -> bool:
        """Check LDAP server connectivity."""
        try:
            if not self.ldap_conn or not self.ldap_conn.bound:
                return False

            # Simple search to verify connectivity
            self.ldap_conn.search(
                search_base=settings.ldap_base_dn,
                search_filter="(objectClass=*)",
                search_scope=ldap3.BASE,
                attributes=["objectClass"],
            )
            return True
        except Exception as e:
            self.logger.warning("LDAP health check failed", error=str(e))
            return False

    # ==================== GroupOperationsMixin Implementation ====================

    async def add_group_member(
        self,
        group_id: str,
        user_id: str,
    ) -> GroupMembershipResult:
        """
        Add a user to an LDAP group.

        Args:
            group_id: LDAP group DN (e.g., cn=admins,ou=groups,dc=example,dc=com)
            user_id: LDAP user DN (e.g., cn=jdoe,ou=users,dc=example,dc=com)

        Returns:
            GroupMembershipResult with operation status
        """
        try:
            if not self.ldap_conn or not self.ldap_conn.bound:
                return GroupMembershipResult(
                    success=False,
                    group_id=group_id,
                    user_id=user_id,
                    operation="add",
                    error="LDAP connection not established",
                )

            # Add member using MODIFY_ADD
            self.ldap_conn.modify(group_id, {"member": [(ldap3.MODIFY_ADD, [user_id])]})

            success = self.ldap_conn.result["result"] == 0
            error = None if success else self.ldap_conn.result.get("description")

            self.logger.info(
                "LDAP add_group_member",
                group_dn=group_id,
                member_dn=user_id,
                success=success,
                error=error,
            )

            return GroupMembershipResult(
                success=success,
                group_id=group_id,
                user_id=user_id,
                operation="add",
                error=error,
            )

        except LDAPException as e:
            self.logger.error(
                "LDAP add_group_member failed",
                group_dn=group_id,
                member_dn=user_id,
                error=str(e),
            )
            return GroupMembershipResult(
                success=False,
                group_id=group_id,
                user_id=user_id,
                operation="add",
                error=str(e),
            )

    async def remove_group_member(
        self,
        group_id: str,
        user_id: str,
    ) -> GroupMembershipResult:
        """
        Remove a user from an LDAP group.

        Args:
            group_id: LDAP group DN
            user_id: LDAP user DN

        Returns:
            GroupMembershipResult with operation status
        """
        try:
            if not self.ldap_conn or not self.ldap_conn.bound:
                return GroupMembershipResult(
                    success=False,
                    group_id=group_id,
                    user_id=user_id,
                    operation="remove",
                    error="LDAP connection not established",
                )

            # Remove member using MODIFY_DELETE
            self.ldap_conn.modify(
                group_id, {"member": [(ldap3.MODIFY_DELETE, [user_id])]}
            )

            success = self.ldap_conn.result["result"] == 0
            error = None if success else self.ldap_conn.result.get("description")

            self.logger.info(
                "LDAP remove_group_member",
                group_dn=group_id,
                member_dn=user_id,
                success=success,
                error=error,
            )

            return GroupMembershipResult(
                success=success,
                group_id=group_id,
                user_id=user_id,
                operation="remove",
                error=error,
            )

        except LDAPException as e:
            self.logger.error(
                "LDAP remove_group_member failed",
                group_dn=group_id,
                member_dn=user_id,
                error=str(e),
            )
            return GroupMembershipResult(
                success=False,
                group_id=group_id,
                user_id=user_id,
                operation="remove",
                error=str(e),
            )

    async def get_group_members(self, group_id: str) -> List[str]:
        """
        Get current members of an LDAP group.

        Args:
            group_id: LDAP group DN

        Returns:
            List of member DNs
        """
        try:
            if not self.ldap_conn or not self.ldap_conn.bound:
                self.logger.warning(
                    "LDAP get_group_members: connection not established"
                )
                return []

            # Search for group and get member attribute
            self.ldap_conn.search(
                search_base=group_id,
                search_filter="(objectClass=*)",
                search_scope=ldap3.BASE,
                attributes=["member"],
            )

            if not self.ldap_conn.entries:
                return []

            entry = self.ldap_conn.entries[0]
            members = entry.member.values if hasattr(entry, "member") else []

            self.logger.info(
                "LDAP get_group_members",
                group_dn=group_id,
                member_count=len(members),
            )

            return list(members)

        except LDAPException as e:
            self.logger.error(
                "LDAP get_group_members failed",
                group_dn=group_id,
                error=str(e),
            )
            return []
