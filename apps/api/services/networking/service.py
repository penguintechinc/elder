"""Networking resources service for network topology and visualization."""

# flake8: noqa: E501


import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import current_app

logger = logging.getLogger(__name__)


class NetworkingService:
    """Service for managing networking resources, topology, and entity mappings."""

    def __init__(self):
        """Initialize networking service."""
        self.db = current_app.db

    # Networking Resources CRUD

    def create_network(
        self,
        name: str,
        network_type: str,
        organization_id: int,
        description: Optional[str] = None,
        parent_id: Optional[int] = None,
        region: Optional[str] = None,
        location: Optional[str] = None,
        cidr: Optional[str] = None,
        gateway: Optional[str] = None,
        vlan_id: Optional[int] = None,
        mtu: Optional[int] = None,
        poc: Optional[str] = None,
        organizational_unit: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None,
        status_metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        is_active: bool = True,
    ) -> Dict[str, Any]:
        """Create a new networking resource."""
        try:
            network_id = self.db.networking_resources.insert(
                name=name,
                description=description,
                network_type=network_type,
                organization_id=organization_id,
                parent_id=parent_id,
                region=region,
                location=location,
                cidr=cidr,
                gateway=gateway,
                vlan_id=vlan_id,
                mtu=mtu,
                poc=poc,
                organizational_unit=organizational_unit,
                attributes=attributes or {},
                status_metadata=status_metadata or {},
                tags=tags or [],
                is_active=is_active,
            )

            self.db.commit()

            logger.info(
                f"Created networking resource '{name}' (ID: {network_id}, Type: {network_type})"
            )

            return self.get_network(network_id)

        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to create networking resource: {str(e)}")
            raise Exception(f"Failed to create networking resource: {str(e)}")

    def get_network(self, network_id: int) -> Dict[str, Any]:
        """Get networking resource by ID."""
        try:
            network = self.db.networking_resources[network_id]

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
        organization_id: Optional[int] = None,
        network_type: Optional[str] = None,
        parent_id: Optional[int] = None,
        region: Optional[str] = None,
        is_active: bool = True,
        limit: Optional[int] = None,
        offset: Optional[int] = 0,
    ) -> Dict[str, Any]:
        """List networking resources with filters."""
        try:
            query = self.db.networking_resources.is_active == is_active

            if organization_id:
                query &= self.db.networking_resources.organization_id == organization_id

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
        name: Optional[str] = None,
        description: Optional[str] = None,
        region: Optional[str] = None,
        location: Optional[str] = None,
        poc: Optional[str] = None,
        organizational_unit: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None,
        status_metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Update networking resource."""
        try:
            network = self.db.networking_resources[network_id]

            if not network:
                raise ValueError(f"Networking resource {network_id} not found")

            update_data = {"updated_at": datetime.now()}

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
            if attributes is not None:
                update_data["attributes"] = attributes
            if status_metadata is not None:
                update_data["status_metadata"] = status_metadata
            if tags is not None:
                update_data["tags"] = tags

            current_app.db(current_app.db.networking_resources.id == network.id).update(**update_data)

            logger.info(f"Updated networking resource {network_id}")

            return self.get_network(network_id)

        except ValueError:
            raise
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to update networking resource: {str(e)}")
            raise Exception(f"Failed to update networking resource: {str(e)}")

    def delete_network(
        self, network_id: int, hard_delete: bool = False
    ) -> Dict[str, Any]:
        """Delete networking resource (soft delete by default)."""
        try:
            network = self.db.networking_resources[network_id]

            if not network:
                raise ValueError(f"Networking resource {network_id} not found")

            if hard_delete:
                # Remove all topology connections
                self.db(
                    (self.db.network_topology.source_network_id == network_id)
                    | (self.db.network_topology.target_network_id == network_id)
                ).delete()

                # Remove all entity mappings
                self.db(
                    self.db.network_entity_mappings.network_id == network_id
                ).delete()

                # Delete the network
                del self.db.networking_resources[network_id]

                logger.info(f"Hard deleted networking resource {network_id}")
            else:
                # Soft delete
                current_app.db(current_app.db.networking_resources.id == network_id).update(is_active=False, updated_at=datetime.now())

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
            self.db.rollback()
            logger.error(f"Failed to delete networking resource: {str(e)}")
            raise Exception(f"Failed to delete networking resource: {str(e)}")

    # Network Topology Management

    def create_topology_connection(
        self,
        source_network_id: int,
        target_network_id: int,
        connection_type: str,
        bandwidth: Optional[int] = None,
        latency: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a network topology connection."""
        try:
            # Verify both networks exist
            if not self.db.networking_resources[source_network_id]:
                raise ValueError(f"Source network {source_network_id} not found")

            if not self.db.networking_resources[target_network_id]:
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

            return self.get_topology_connection(connection_id)

        except ValueError:
            raise
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to create topology connection: {str(e)}")
            raise Exception(f"Failed to create topology connection: {str(e)}")

    def get_topology_connection(self, connection_id: int) -> Dict[str, Any]:
        """Get topology connection by ID."""
        try:
            connection = self.db.network_topology[connection_id]

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
        network_id: Optional[int] = None,
        connection_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List topology connections, optionally filtered by network or type."""
        try:
            query = self.db.network_topology.id > 0

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

    def delete_topology_connection(self, connection_id: int) -> Dict[str, Any]:
        """Delete a topology connection."""
        try:
            connection = self.db.network_topology[connection_id]

            if not connection:
                raise ValueError(f"Topology connection {connection_id} not found")

            del self.db.network_topology[connection_id]
            self.db.commit()

            logger.info(f"Deleted topology connection {connection_id}")

            return {"success": True, "connection_id": connection_id}

        except ValueError:
            raise
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to delete topology connection: {str(e)}")
            raise Exception(f"Failed to delete topology connection: {str(e)}")

    # Network-Entity Mappings

    def map_entity_to_network(
        self,
        network_id: int,
        entity_id: int,
        relationship_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Map an entity to a network."""
        try:
            # Verify network and entity exist
            if not self.db.networking_resources[network_id]:
                raise ValueError(f"Network {network_id} not found")

            if not self.db.entities[entity_id]:
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

            return self.get_entity_mapping(mapping_id)

        except ValueError:
            raise
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to map entity to network: {str(e)}")
            raise Exception(f"Failed to map entity to network: {str(e)}")

    def get_entity_mapping(self, mapping_id: int) -> Dict[str, Any]:
        """Get entity-network mapping by ID."""
        try:
            mapping = self.db.network_entity_mappings[mapping_id]

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
        network_id: Optional[int] = None,
        entity_id: Optional[int] = None,
        relationship_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List entity-network mappings with filters."""
        try:
            query = self.db.network_entity_mappings.id > 0

            if network_id:
                query &= self.db.network_entity_mappings.network_id == network_id

            if entity_id:
                query &= self.db.network_entity_mappings.entity_id == entity_id

            if relationship_type:
                query &= (
                    self.db.network_entity_mappings.relationship_type
                    == relationship_type
                )

            mappings = self.db(query).select()

            return [self._serialize_entity_mapping(m) for m in mappings]

        except Exception as e:
            logger.error(f"Failed to list entity mappings: {str(e)}")
            raise Exception(f"Failed to list entity mappings: {str(e)}")

    def delete_entity_mapping(self, mapping_id: int) -> Dict[str, Any]:
        """Delete an entity-network mapping."""
        try:
            mapping = self.db.network_entity_mappings[mapping_id]

            if not mapping:
                raise ValueError(f"Entity mapping {mapping_id} not found")

            del self.db.network_entity_mappings[mapping_id]
            self.db.commit()

            logger.info(f"Deleted entity mapping {mapping_id}")

            return {"success": True, "mapping_id": mapping_id}

        except ValueError:
            raise
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to delete entity mapping: {str(e)}")
            raise Exception(f"Failed to delete entity mapping: {str(e)}")

    # Visualization and Topology Queries

    def get_network_topology_graph(
        self,
        organization_id: int,
        include_entities: bool = False,
    ) -> Dict[str, Any]:
        """Get network topology as a graph structure for visualization."""
        try:
            # Get all networks for the organization
            networks = self.db(
                (self.db.networking_resources.organization_id == organization_id)
                & (self.db.networking_resources.is_active is True)
            ).select()

            # Get all topology connections for these networks
            network_ids = [n.id for n in networks]

            connections = self.db(
                (self.db.network_topology.source_network_id.belongs(network_ids))
                & (self.db.network_topology.target_network_id.belongs(network_ids))
            ).select()

            # Build nodes and edges for visualization
            nodes = [self._serialize_network_node(n) for n in networks]

            edges = [self._serialize_topology_edge(c) for c in connections]

            result = {
                "nodes": nodes,
                "edges": edges,
            }

            # Optionally include entities
            if include_entities:
                # Get entity mappings
                mappings = self.db(
                    self.db.network_entity_mappings.network_id.belongs(network_ids)
                ).select(
                    self.db.network_entity_mappings.ALL,
                    self.db.entities.ALL,
                    left=self.db.entities.on(
                        self.db.network_entity_mappings.entity_id == self.db.entities.id
                    ),
                )

                entity_nodes = []
                entity_edges = []

                for mapping in mappings:
                    if mapping.entities.id:
                        entity_nodes.append(
                            {
                                "id": f"entity_{mapping.entities.id}",
                                "type": "entity",
                                "label": mapping.entities.name,
                                "entity_type": mapping.entities.entity_type,
                            }
                        )

                        entity_edges.append(
                            {
                                "id": f"mapping_{mapping.network_entity_mappings.id}",
                                "source": f"network_{mapping.network_entity_mappings.network_id}",
                                "target": f"entity_{mapping.network_entity_mappings.entity_id}",
                                "type": mapping.network_entity_mappings.relationship_type,
                            }
                        )

                result["entity_nodes"] = entity_nodes
                result["entity_edges"] = entity_edges

            return result

        except Exception as e:
            logger.error(f"Failed to get network topology graph: {str(e)}")
            raise Exception(f"Failed to get network topology graph: {str(e)}")

    # Helper methods

    def _serialize_network(self, network: Any) -> Dict[str, Any]:
        """Serialize networking resource to dict."""
        return {
            "id": network.id,
            "name": network.name,
            "description": network.description,
            "network_type": network.network_type,
            "organization_id": network.organization_id,
            "parent_id": network.parent_id,
            "region": network.region,
            "location": network.location,
            "poc": network.poc,
            "organizational_unit": network.organizational_unit,
            "attributes": network.attributes,
            "status_metadata": network.status_metadata,
            "tags": network.tags,
            "is_active": network.is_active,
            "created_at": (
                network.created_at.isoformat() if network.created_at else None
            ),
            "updated_at": (
                network.updated_at.isoformat() if network.updated_at else None
            ),
        }

    def _serialize_topology_connection(self, connection: Any) -> Dict[str, Any]:
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

    def _serialize_entity_mapping(self, mapping: Any) -> Dict[str, Any]:
        """Serialize entity mapping to dict."""
        return {
            "id": mapping.id,
            "network_id": mapping.network_id,
            "entity_id": mapping.entity_id,
            "relationship_type": mapping.relationship_type,
            "metadata": mapping.metadata,
        }

    def _serialize_network_node(self, network: Any) -> Dict[str, Any]:
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

    def _serialize_topology_edge(self, connection: Any) -> Dict[str, Any]:
        """Serialize topology connection for graph visualization."""
        return {
            "id": f"connection_{connection.id}",
            "source": f"network_{connection.source_network_id}",
            "target": f"network_{connection.target_network_id}",
            "type": connection.connection_type,
            "bandwidth": connection.bandwidth,
            "latency": connection.latency,
        }
