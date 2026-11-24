"""Networking resources service for network topology and visualization."""

# flake8: noqa: E501

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from quart import current_app

from apps.api.utils.village_id import mint_village_id

logger = logging.getLogger(__name__)


class DuplicateResourceError(Exception):
    """Raised when creating a networking resource that already exists."""

    def __init__(self, message: str, existing_id: int):
        """Initialize with error message and existing resource ID."""
        super().__init__(message)
        self.existing_id = existing_id


class NetworkingService:
    """Service for managing networking resources, topology, and entity mappings."""

    def __init__(self):
        """Initialize networking service."""
        self.db = current_app.db

    # Tenant scoping helpers
    #
    # networking_resources, network_topology and network_entity_mappings carry
    # no tenant column of their own; ownership is derived from the owning
    # organization (networks, entities) or from both endpoint networks
    # (connections, mappings). Every public method takes the caller's verified
    # tenant id and resolves rows only through these helpers. A falsy tenant
    # id always means "no access", never "no filter".

    def _tenant_org_ids(self, tenant_id: int | None) -> list[int]:
        """Return ids of every organization owned by ``tenant_id``.

        Empty when the tenant is missing, so callers fail closed.
        """
        if not tenant_id:
            return []
        rows = self.db(self.db.organizations.tenant_id == tenant_id).select(
            self.db.organizations.id
        )
        return [row.id for row in rows]

    def _tenant_network_ids(self, tenant_id: int | None) -> list[int]:
        """Return ids of every networking resource owned by ``tenant_id``.

        Used to require that both endpoints of a connection or mapping are
        in the caller's tenant.
        """
        org_ids = self._tenant_org_ids(tenant_id)
        if not org_ids:
            return []
        rows = self.db(
            self.db.networking_resources.organization_id.belongs(org_ids)
        ).select(self.db.networking_resources.id)
        return [row.id for row in rows]

    def _org_in_tenant(
        self, organization_id: int | None, tenant_id: int | None
    ) -> bool:
        """Return True when ``organization_id`` belongs to ``tenant_id``."""
        if not organization_id or not tenant_id:
            return False
        org = (
            self.db(
                (self.db.organizations.id == organization_id)
                & (self.db.organizations.tenant_id == tenant_id)
            )
            .select(self.db.organizations.id)
            .first()
        )
        return org is not None

    def _scoped_network(self, network_id: int | None, tenant_id: int | None) -> Any:
        """Return the networking resource row when owned by ``tenant_id``, else None.

        Foreign and missing ids are indistinguishable so callers can map
        both to a 404 without revealing another tenant's ids.
        """
        if not network_id or not tenant_id:
            return None
        org_ids = self._tenant_org_ids(tenant_id)
        if not org_ids:
            return None
        return (
            self.db(
                (self.db.networking_resources.id == network_id)
                & (self.db.networking_resources.organization_id.belongs(org_ids))
            )
            .select()
            .first()
        )

    def _scoped_entity(self, entity_id: int | None, tenant_id: int | None) -> Any:
        """Return the entity row when its organization belongs to ``tenant_id``, else None."""
        if not entity_id or not tenant_id:
            return None
        org_ids = self._tenant_org_ids(tenant_id)
        if not org_ids:
            return None
        return (
            self.db(
                (self.db.entities.id == entity_id)
                & (self.db.entities.organization_id.belongs(org_ids))
            )
            .select()
            .first()
        )

    def _scoped_connection(self, connection_id: int, tenant_id: int | None) -> Any:
        """Return a topology connection only when both endpoints are in ``tenant_id``."""
        if not connection_id or not tenant_id:
            return None
        connection = (
            self.db(self.db.network_topology.id == connection_id).select().first()
        )
        if not connection:
            return None
        network_ids = set(self._tenant_network_ids(tenant_id))
        if (
            connection.source_network_id not in network_ids
            or connection.target_network_id not in network_ids
        ):
            return None
        return connection

    def _scoped_mapping(self, mapping_id: int, tenant_id: int | None) -> Any:
        """Return an entity mapping only when its network and entity are in ``tenant_id``."""
        if not mapping_id or not tenant_id:
            return None
        mapping = (
            self.db(self.db.network_entity_mappings.id == mapping_id).select().first()
        )
        if not mapping:
            return None
        if not self._scoped_network(mapping.network_id, tenant_id):
            return None
        if not self._scoped_entity(mapping.entity_id, tenant_id):
            return None
        return mapping

    # Networking Resources CRUD

    def create_network(
        self,
        name: str,
        network_type: str,
        organization_id: int,
        *,
        tenant_id: int | None,
        description: str | None = None,
        parent_id: int | None = None,
        region: str | None = None,
        location: str | None = None,
        cidr: str | None = None,
        gateway: str | None = None,
        vlan_id: int | None = None,
        mtu: int | None = None,
        poc: str | None = None,
        organizational_unit: str | None = None,
        attributes: dict[str, Any] | None = None,
        status_metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Create a new networking resource, or overwrite an existing duplicate."""
        network, _ = self.create_or_update_network(
            name=name,
            network_type=network_type,
            organization_id=organization_id,
            tenant_id=tenant_id,
            description=description,
            parent_id=parent_id,
            region=region,
            location=location,
            cidr=cidr,
            gateway=gateway,
            vlan_id=vlan_id,
            mtu=mtu,
            poc=poc,
            organizational_unit=organizational_unit,
            attributes=attributes,
            status_metadata=status_metadata,
            tags=tags,
            overwrite=overwrite,
        )
        return network

    def create_or_update_network(
        self,
        name: str,
        network_type: str,
        organization_id: int,
        *,
        tenant_id: int | None,
        description: str | None = None,
        parent_id: int | None = None,
        region: str | None = None,
        location: str | None = None,
        cidr: str | None = None,
        gateway: str | None = None,
        vlan_id: int | None = None,
        mtu: int | None = None,
        poc: str | None = None,
        organizational_unit: str | None = None,
        attributes: dict[str, Any] | None = None,
        status_metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        overwrite: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        """Create a networking resource or overwrite an existing duplicate; returns (resource, created).

        The organization and any parent network must belong to the caller's
        tenant; otherwise ValueError (mapped to 404) is raised.
        """
        if not self._org_in_tenant(organization_id, tenant_id):
            raise ValueError(f"Organization {organization_id} not found")
        if parent_id is not None and not self._scoped_network(parent_id, tenant_id):
            raise ValueError(f"Parent network {parent_id} not found")

        try:
            merged_attributes = dict(attributes or {})
            if cidr is not None:
                merged_attributes["cidr"] = cidr
            if gateway is not None:
                merged_attributes["gateway"] = gateway
            if vlan_id is not None:
                merged_attributes["vlan_id"] = vlan_id
            if mtu is not None:
                merged_attributes["mtu"] = mtu

            existing_rows = self.db(
                self.db.networking_resources.organization_id == organization_id
            ).select()
            existing = None
            for row in existing_rows:
                if row.name == name:
                    existing = row
                    break
                if cidr is not None:
                    row_attrs = row.attributes
                    if isinstance(row_attrs, str):
                        try:
                            row_attrs = json.loads(row_attrs)
                        except ValueError:
                            row_attrs = {}
                    if isinstance(row_attrs, dict) and row_attrs.get("cidr") == cidr:
                        existing = row
                        break

            if existing is not None:
                if not overwrite:
                    raise DuplicateResourceError(
                        f"Networking resource '{name}' already exists in this organization",
                        existing_id=existing.id,
                    )

                self.db(self.db.networking_resources.id == existing.id).update(
                    name=name,
                    description=description,
                    network_type=network_type,
                    parent_id=parent_id,
                    region=region,
                    location=location,
                    poc=poc,
                    organizational_unit=organizational_unit,
                    attributes=merged_attributes,
                    status_metadata=status_metadata or {},
                    tags=tags or [],
                    updated_at=datetime.now(),
                )
                self.db.commit()
                logger.info(
                    f"Overwrote networking resource (ID: {existing.id}, Type: {network_type})"
                )
                return self.get_network(existing.id, tenant_id=tenant_id), False

            network_id = self.db.networking_resources.insert(
                name=name,
                description=description,
                network_type=network_type,
                organization_id=organization_id,
                parent_id=parent_id,
                region=region,
                location=location,
                poc=poc,
                organizational_unit=organizational_unit,
                attributes=merged_attributes,
                status_metadata=status_metadata or {},
                tags=tags or [],
                village_id=mint_village_id(tenant_id),
            )

            self.db.commit()

            logger.info(
                f"Created networking resource (ID: {network_id}, Type: {network_type})"
            )

            return self.get_network(network_id, tenant_id=tenant_id), True

        except (DuplicateResourceError, ValueError):
            raise
        except Exception as e:
            logger.error(f"Failed to create networking resource: {str(e)}")
            raise Exception(f"Failed to create networking resource: {str(e)}")

    def get_network(self, network_id: int, *, tenant_id: int | None) -> dict[str, Any]:
        """Get a networking resource by ID within the caller's tenant."""
        try:
            network = self._scoped_network(network_id, tenant_id)

            if not network:
                raise ValueError(f"Networking resource {network_id} not found")

            return self._serialize_network(network)

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to get networking resource: {str(e)}")
            raise Exception(f"Failed to get networking resource: {str(e)}")

    def list_networks(
        self,
        *,
        tenant_id: int | None,
        organization_id: int | None = None,
        network_type: str | None = None,
        parent_id: int | None = None,
        region: str | None = None,
        limit: int | None = None,
        offset: int | None = 0,
    ) -> dict[str, Any]:
        """List networking resources in the caller's tenant with optional filters.

        An ``organization_id`` filter outside the tenant yields an empty page.
        """
        offset = offset or 0
        empty = {"networks": [], "total": 0, "limit": limit, "offset": offset}
        try:
            org_ids = self._tenant_org_ids(tenant_id)
            if not org_ids:
                return empty
            if organization_id:
                if organization_id not in org_ids:
                    return empty
                query = self.db.networking_resources.organization_id == organization_id
            else:
                query = self.db.networking_resources.organization_id.belongs(org_ids)

            if network_type:
                query &= self.db.networking_resources.network_type == network_type

            if parent_id is not None:
                query &= self.db.networking_resources.parent_id == parent_id

            if region:
                query &= self.db.networking_resources.region == region

            total_count = self.db(query).count()

            networks_query = self.db(query).select(
                orderby=self.db.networking_resources.name,
                limitby=(offset, offset + limit) if limit else None,
            )

            networks = [self._serialize_network(net) for net in networks_query]

            return {
                "networks": networks,
                "total": total_count,
                "limit": limit,
                "offset": offset,
            }

        except Exception as e:
            logger.error(f"Failed to list networking resources: {str(e)}")
            raise Exception(f"Failed to list networking resources: {str(e)}")

    def update_network(
        self,
        network_id: int,
        *,
        tenant_id: int | None,
        name: str | None = None,
        description: str | None = None,
        region: str | None = None,
        location: str | None = None,
        poc: str | None = None,
        organizational_unit: str | None = None,
        gateway: str | None = None,
        vlan_id: int | None = None,
        mtu: int | None = None,
        is_active: bool | None = None,
        attributes: dict[str, Any] | None = None,
        status_metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Update a networking resource owned by the caller's tenant.

        ``gateway``, ``vlan_id`` and ``mtu`` are stored in ``attributes``,
        matching how create stores them.
        """
        try:
            network = self._scoped_network(network_id, tenant_id)

            if not network:
                raise ValueError(f"Networking resource {network_id} not found")

            update_data: dict[str, Any] = {"updated_at": datetime.now()}

            if name:
                update_data["name"] = name
            if description is not None:
                update_data["description"] = description
            if region is not None:
                update_data["region"] = region
            if location is not None:
                update_data["location"] = location
            if poc is not None:
                update_data["poc"] = poc
            if organizational_unit is not None:
                update_data["organizational_unit"] = organizational_unit
            if is_active is not None:
                update_data["is_active"] = is_active

            merged_attributes = (
                dict(attributes)
                if attributes is not None
                else dict(network.attributes or {})
            )
            attrs_changed = attributes is not None
            for key, value in (
                ("gateway", gateway),
                ("vlan_id", vlan_id),
                ("mtu", mtu),
            ):
                if value is not None:
                    merged_attributes[key] = value
                    attrs_changed = True
            if attrs_changed:
                update_data["attributes"] = merged_attributes
            if status_metadata is not None:
                update_data["status_metadata"] = status_metadata
            if tags is not None:
                update_data["tags"] = tags

            self.db(self.db.networking_resources.id == network.id).update(**update_data)
            self.db.commit()

            logger.info(f"Updated networking resource {network_id}")

            return self.get_network(network_id, tenant_id=tenant_id)

        except ValueError:
            raise
        except Exception as e:
            # penguin_dal Database does not expose .rollback(); log and re-raise
            logger.error(f"Failed to update networking resource: {str(e)}")
            raise Exception(f"Failed to update networking resource: {str(e)}")

    def delete_network(
        self, network_id: int, *, tenant_id: int | None, hard_delete: bool = False
    ) -> dict[str, Any]:
        """Delete a networking resource owned by the caller's tenant (soft by default)."""
        try:
            network = self._scoped_network(network_id, tenant_id)

            if not network:
                raise ValueError(f"Networking resource {network_id} not found")

            if hard_delete:
                # Remove all topology connections touching this network
                self.db(
                    (self.db.network_topology.source_network_id == network.id)
                    | (self.db.network_topology.target_network_id == network.id)
                ).delete()

                # Remove all entity mappings
                self.db(
                    self.db.network_entity_mappings.network_id == network.id
                ).delete()

                # Delete the network
                self.db(self.db.networking_resources.id == network.id).delete()

                logger.info(f"Hard deleted networking resource {network_id}")
            else:
                # Soft delete (mark as inactive)
                self.db(self.db.networking_resources.id == network.id).update(
                    is_active=False, updated_at=datetime.now()
                )

                logger.info(f"Soft deleted networking resource {network_id}")

            self.db.commit()

            return {
                "success": True,
                "network_id": network_id,
                "hard_delete": hard_delete,
            }

        except ValueError:
            raise
        except Exception as e:
            # penguin_dal Database does not expose .rollback(); log and re-raise
            logger.error(f"Failed to delete networking resource: {str(e)}")
            raise Exception(f"Failed to delete networking resource: {str(e)}")

    # Network Topology Management

    def create_topology_connection(
        self,
        source_network_id: int,
        target_network_id: int,
        connection_type: str,
        *,
        tenant_id: int | None,
        bandwidth: int | None = None,
        latency: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a topology connection; both networks must be in the caller's tenant."""
        try:
            if not self._scoped_network(source_network_id, tenant_id):
                raise ValueError(f"Source network {source_network_id} not found")

            if not self._scoped_network(target_network_id, tenant_id):
                raise ValueError(f"Target network {target_network_id} not found")

            connection_id = self.db.network_topology.insert(
                source_network_id=source_network_id,
                target_network_id=target_network_id,
                connection_type=connection_type,
                bandwidth=bandwidth,
                latency=latency,
                metadata=metadata or {},
            )

            self.db.commit()

            logger.info(
                f"Created network topology connection: {source_network_id} -> {target_network_id} ({connection_type})"
            )

            return self.get_topology_connection(connection_id, tenant_id=tenant_id)

        except ValueError:
            raise
        except Exception as e:
            # penguin_dal Database does not expose .rollback(); log and re-raise
            logger.error(f"Failed to create topology connection: {str(e)}")
            raise Exception(f"Failed to create topology connection: {str(e)}")

    def get_topology_connection(
        self, connection_id: int, *, tenant_id: int | None
    ) -> dict[str, Any]:
        """Get a topology connection by ID within the caller's tenant."""
        try:
            connection = self._scoped_connection(connection_id, tenant_id)

            if not connection:
                raise ValueError(f"Topology connection {connection_id} not found")

            return self._serialize_topology_connection(connection)

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to get topology connection: {str(e)}")
            raise Exception(f"Failed to get topology connection: {str(e)}")

    def list_topology_connections(
        self,
        *,
        tenant_id: int | None,
        network_id: int | None = None,
        connection_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List topology connections whose endpoints are both in the caller's tenant."""
        try:
            network_ids = self._tenant_network_ids(tenant_id)
            if not network_ids:
                return []
            if network_id and network_id not in network_ids:
                return []

            query = self.db.network_topology.source_network_id.belongs(
                network_ids
            ) & self.db.network_topology.target_network_id.belongs(network_ids)

            if network_id:
                query &= (self.db.network_topology.source_network_id == network_id) | (
                    self.db.network_topology.target_network_id == network_id
                )

            if connection_type:
                query &= self.db.network_topology.connection_type == connection_type

            connections = self.db(query).select()

            return [self._serialize_topology_connection(conn) for conn in connections]

        except Exception as e:
            logger.error(f"Failed to list topology connections: {str(e)}")
            raise Exception(f"Failed to list topology connections: {str(e)}")

    def delete_topology_connection(
        self, connection_id: int, *, tenant_id: int | None
    ) -> dict[str, Any]:
        """Delete a topology connection within the caller's tenant."""
        try:
            connection = self._scoped_connection(connection_id, tenant_id)

            if not connection:
                raise ValueError(f"Topology connection {connection_id} not found")

            self.db(self.db.network_topology.id == connection.id).delete()
            self.db.commit()

            logger.info(f"Deleted topology connection {connection_id}")

            return {"success": True, "connection_id": connection_id}

        except ValueError:
            raise
        except Exception as e:
            # penguin_dal Database does not expose .rollback(); log and re-raise
            logger.error(f"Failed to delete topology connection: {str(e)}")
            raise Exception(f"Failed to delete topology connection: {str(e)}")

    # Network-Entity Mappings

    def map_entity_to_network(
        self,
        network_id: int,
        entity_id: int,
        relationship_type: str,
        *,
        tenant_id: int | None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Map an entity to a network; both must be in the caller's tenant."""
        try:
            if not self._scoped_network(network_id, tenant_id):
                raise ValueError(f"Network {network_id} not found")

            if not self._scoped_entity(entity_id, tenant_id):
                raise ValueError(f"Entity {entity_id} not found")

            mapping_id = self.db.network_entity_mappings.insert(
                network_id=network_id,
                entity_id=entity_id,
                relationship_type=relationship_type,
                metadata=metadata or {},
            )

            self.db.commit()

            logger.info(
                f"Mapped entity {entity_id} to network {network_id} ({relationship_type})"
            )

            return self.get_entity_mapping(mapping_id, tenant_id=tenant_id)

        except ValueError:
            raise
        except Exception as e:
            # penguin_dal Database does not expose .rollback(); log and re-raise
            logger.error(f"Failed to map entity to network: {str(e)}")
            raise Exception(f"Failed to map entity to network: {str(e)}")

    def get_entity_mapping(
        self, mapping_id: int, *, tenant_id: int | None
    ) -> dict[str, Any]:
        """Get an entity-network mapping by ID within the caller's tenant."""
        try:
            mapping = self._scoped_mapping(mapping_id, tenant_id)

            if not mapping:
                raise ValueError(f"Entity mapping {mapping_id} not found")

            return self._serialize_entity_mapping(mapping)

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to get entity mapping: {str(e)}")
            raise Exception(f"Failed to get entity mapping: {str(e)}")

    def list_entity_mappings(
        self,
        *,
        tenant_id: int | None,
        network_id: int | None = None,
        entity_id: int | None = None,
        relationship_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List entity-network mappings whose network and entity are in the caller's tenant."""
        try:
            org_ids = self._tenant_org_ids(tenant_id)
            network_ids = self._tenant_network_ids(tenant_id)
            if not org_ids or not network_ids:
                return []
            if network_id and network_id not in network_ids:
                return []

            query = self.db.network_entity_mappings.network_id.belongs(network_ids)

            if network_id:
                query &= self.db.network_entity_mappings.network_id == network_id

            if entity_id:
                query &= self.db.network_entity_mappings.entity_id == entity_id

            if relationship_type:
                query &= (
                    self.db.network_entity_mappings.relationship_type
                    == relationship_type
                )

            mappings = list(self.db(query).select())
            entity_ids = sorted({m.entity_id for m in mappings})
            if not entity_ids:
                return []
            own_entities = {
                row.id
                for row in self.db(
                    (self.db.entities.id.belongs(entity_ids))
                    & (self.db.entities.organization_id.belongs(org_ids))
                ).select(self.db.entities.id)
            }

            return [
                self._serialize_entity_mapping(m)
                for m in mappings
                if m.entity_id in own_entities
            ]

        except Exception as e:
            logger.error(f"Failed to list entity mappings: {str(e)}")
            raise Exception(f"Failed to list entity mappings: {str(e)}")

    def delete_entity_mapping(
        self, mapping_id: int, *, tenant_id: int | None
    ) -> dict[str, Any]:
        """Delete an entity-network mapping within the caller's tenant."""
        try:
            mapping = self._scoped_mapping(mapping_id, tenant_id)

            if not mapping:
                raise ValueError(f"Entity mapping {mapping_id} not found")

            self.db(self.db.network_entity_mappings.id == mapping.id).delete()
            self.db.commit()

            logger.info(f"Deleted entity mapping {mapping_id}")

            return {"success": True, "mapping_id": mapping_id}

        except ValueError:
            raise
        except Exception as e:
            # penguin_dal Database does not expose .rollback(); log and re-raise
            logger.error(f"Failed to delete entity mapping: {str(e)}")
            raise Exception(f"Failed to delete entity mapping: {str(e)}")

    # Visualization and Topology Queries

    def get_network_topology_graph(
        self,
        organization_id: int,
        *,
        tenant_id: int | None,
        include_entities: bool = False,
    ) -> dict[str, Any]:
        """Get an organization's network topology as a graph for visualization.

        The organization must belong to the caller's tenant. Connections and
        entity nodes are limited to objects in that tenant.
        """
        if not self._org_in_tenant(organization_id, tenant_id):
            raise ValueError(f"Organization {organization_id} not found")
        try:
            networks = list(
                self.db(
                    self.db.networking_resources.organization_id == organization_id
                ).select()
            )
            network_ids = [n.id for n in networks]

            connections: list[Any] = []
            if network_ids:
                connections = list(
                    self.db(
                        (
                            self.db.network_topology.source_network_id.belongs(
                                network_ids
                            )
                        )
                        & (
                            self.db.network_topology.target_network_id.belongs(
                                network_ids
                            )
                        )
                    ).select()
                )

            result: dict[str, Any] = {
                "nodes": [self._serialize_network_node(n) for n in networks],
                "edges": [self._serialize_topology_edge(c) for c in connections],
            }

            if include_entities:
                result.update(self._topology_entities(network_ids, tenant_id))

            return result

        except Exception as e:
            logger.error(f"Failed to get network topology graph: {str(e)}")
            raise Exception(f"Failed to get network topology graph: {str(e)}")

    def _topology_entities(
        self, network_ids: list[int], tenant_id: int | None
    ) -> dict[str, list[dict[str, Any]]]:
        """Build entity nodes and mapping edges for ``network_ids``.

        Two plain queries instead of a joined ``.ALL`` select (unsupported by
        the DAL); entities outside the caller's tenant are dropped.
        """
        entity_nodes: list[dict[str, Any]] = []
        entity_edges: list[dict[str, Any]] = []
        org_ids = self._tenant_org_ids(tenant_id)
        if not network_ids or not org_ids:
            return {"entity_nodes": entity_nodes, "entity_edges": entity_edges}

        mappings = list(
            self.db(
                self.db.network_entity_mappings.network_id.belongs(network_ids)
            ).select()
        )
        entity_ids = sorted({m.entity_id for m in mappings})
        entities: dict[int, Any] = {}
        if entity_ids:
            entities = {
                row.id: row
                for row in self.db(
                    (self.db.entities.id.belongs(entity_ids))
                    & (self.db.entities.organization_id.belongs(org_ids))
                ).select()
            }

        seen: set[int] = set()
        for mapping in mappings:
            entity = entities.get(mapping.entity_id)
            if entity is None:
                continue
            if entity.id not in seen:
                seen.add(entity.id)
                entity_nodes.append(
                    {
                        "id": f"entity_{entity.id}",
                        "type": "entity",
                        "label": entity.name,
                        "entity_type": entity.type,
                    }
                )
            entity_edges.append(
                {
                    "id": f"mapping_{mapping.id}",
                    "source": f"network_{mapping.network_id}",
                    "target": f"entity_{mapping.entity_id}",
                    "type": mapping.relationship_type,
                }
            )

        return {"entity_nodes": entity_nodes, "entity_edges": entity_edges}

    # Helper methods

    def _serialize_network(self, network: Any) -> dict[str, Any]:
        """Serialize networking resource to dict."""
        attrs = network.attributes or {}
        status_meta = network.status_metadata or {}
        return {
            "id": network.id,
            "name": network.name,
            "description": network.description,
            "network_type": network.network_type,
            "organization_id": network.organization_id,
            "parent_id": network.parent_id,
            "region": network.region,
            "location": network.location,
            "cidr": attrs.get("cidr"),
            "poc": network.poc,
            "organizational_unit": network.organizational_unit,
            "attributes": network.attributes,
            "status_metadata": network.status_metadata,
            "tags": network.tags,
            "is_active": network.get("is_active", True),
            "is_public": attrs.get("is_public", False),
            "status": status_meta.get("status"),
            "village_id": getattr(network, "village_id", None),
            "created_at": (
                network.created_at.isoformat() if network.created_at else None
            ),
            "updated_at": (
                network.updated_at.isoformat() if network.updated_at else None
            ),
        }

    def _serialize_topology_connection(self, connection: Any) -> dict[str, Any]:
        """Serialize topology connection to dict."""
        return {
            "id": connection.id,
            "source_network_id": connection.source_network_id,
            "target_network_id": connection.target_network_id,
            "connection_type": connection.connection_type,
            "bandwidth": connection.bandwidth,
            "latency": connection.latency,
            "metadata": connection.metadata,
        }

    def _serialize_entity_mapping(self, mapping: Any) -> dict[str, Any]:
        """Serialize entity mapping to dict."""
        return {
            "id": mapping.id,
            "network_id": mapping.network_id,
            "entity_id": mapping.entity_id,
            "relationship_type": mapping.relationship_type,
            "metadata": mapping.metadata,
        }

    def _serialize_network_node(self, network: Any) -> dict[str, Any]:
        """Serialize network for graph visualization."""
        return {
            "id": f"network_{network.id}",
            "type": "network",
            "label": network.name,
            "network_type": network.network_type,
            "region": network.region,
            "status": (
                network.status_metadata.get("status")
                if network.status_metadata
                else None
            ),
        }

    def _serialize_topology_edge(self, connection: Any) -> dict[str, Any]:
        """Serialize topology connection for graph visualization."""
        return {
            "id": f"connection_{connection.id}",
            "source": f"network_{connection.source_network_id}",
            "target": f"network_{connection.target_network_id}",
            "type": connection.connection_type,
            "bandwidth": connection.bandwidth,
            "latency": connection.latency,
        }
