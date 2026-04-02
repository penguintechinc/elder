"""Search Service for Elder v1.2.0 (Phase 10)."""

# flake8: noqa: E501


from typing import Any, Dict, List, Optional

from penguin_dal import DAL


class SearchService:
    """Service for advanced search across Elder resources."""

    def __init__(self, db: DAL):
        """
        Initialize SearchService.

        Args:
            db: penguin-dal database instance
        """
        self.db = db

    # ===========================
    # Universal Search Methods
    # ===========================

    def search_all(
        self,
        query: str,
        resource_types: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Search across all resource types.

        Args:
            query: Search query string
            resource_types: List of resource types to search (entity, organization, issue)
            filters: Additional filters
            limit: Maximum results per type
            offset: Pagination offset

        Returns:
            Combined search results from all types
        """
        if resource_types is None:
            resource_types = ["entity", "organization", "issue"]

        results = {}
        total_count = 0

        if "entity" in resource_types:
            entity_results = self.search_entities(
                query=query, filters=filters, limit=limit, offset=offset
            )
            results["entities"] = entity_results["entities"]
            results["entities_count"] = entity_results["total_count"]
            total_count += entity_results["total_count"]

        if "organization" in resource_types:
            org_results = self.search_organizations(
                query=query, limit=limit, offset=offset
            )
            results["organizations"] = org_results["organizations"]
            results["organizations_count"] = org_results["total_count"]
            total_count += org_results["total_count"]

        if "issue" in resource_types:
            issue_results = self.search_issues(
                query=query, filters=filters, limit=limit, offset=offset
            )
            results["issues"] = issue_results["issues"]
            results["issues_count"] = issue_results["total_count"]
            total_count += issue_results["total_count"]

        results["total_count"] = total_count
        results["query"] = query
        results["resource_types"] = resource_types

        return results

    # ===========================
    # Entity Search Methods
    # ===========================

    def search_entities(
        self,
        query: Optional[str] = None,
        entity_type: Optional[str] = None,
        sub_type: Optional[str] = None,
        organization_id: Optional[int] = None,
        tags: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Search entities with advanced filters.

        Args:
            query: Search query for name/description
            entity_type: Filter by entity type
            sub_type: Filter by sub-type
            organization_id: Filter by organization
            tags: Filter by tags
            filters: Additional attribute filters
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Entity search results
        """
        db_query = self.db.entities.id > 0

        # Text search on name and description (case-insensitive)
        if query:
            search_pattern = f"%{query}%"
            db_query &= self.db.entities.name.ilike(
                search_pattern
            ) | self.db.entities.description.ilike(search_pattern)

        # Entity type filter
        if entity_type:
            db_query &= self.db.entities.entity_type == entity_type

        # Sub-type filter
        if sub_type:
            db_query &= self.db.entities.sub_type == sub_type

        # Organization filter
        if organization_id:
            db_query &= self.db.entities.organization_id == organization_id

        # Tags filter (check if any tag matches)
        if tags:
            for tag in tags:
                db_query &= self.db.entities.metadata.contains(tag)

        # Get total count
        total_count = self.db(db_query).count()

        # Execute query with pagination
        entities = self.db(db_query).select(
            orderby=~self.db.entities.updated_at, limitby=(offset, offset + limit)
        )

        return {
            "entities": [e.as_dict() for e in entities],
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
        }

    # ===========================
    # Organization Search Methods
    # ===========================

    def search_organizations(
        self,
        query: Optional[str] = None,
        organization_type: Optional[str] = None,
        parent_id: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Search organizations.

        Args:
            query: Search query for name/description
            organization_type: Filter by type
            parent_id: Filter by parent organization
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Organization search results
        """
        db_query = self.db.organizations.id > 0

        # Text search (case-insensitive)
        if query:
            search_pattern = f"%{query}%"
            db_query &= self.db.organizations.name.ilike(
                search_pattern
            ) | self.db.organizations.description.ilike(search_pattern)

        # Type filter
        if organization_type:
            db_query &= self.db.organizations.organization_type == organization_type

        # Parent filter
        if parent_id is not None:
            db_query &= self.db.organizations.parent_id == parent_id

        # Get total count
        total_count = self.db(db_query).count()

        # Execute query
        organizations = self.db(db_query).select(
            orderby=~self.db.organizations.updated_at, limitby=(offset, offset + limit)
        )

        return {
            "organizations": [o.as_dict() for o in organizations],
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
        }

    # ===========================
    # Issue Search Methods
    # ===========================

    def search_issues(
        self,
        query: Optional[str] = None,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        assignee_id: Optional[int] = None,
        organization_id: Optional[int] = None,
        labels: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Search issues with advanced filters.

        Args:
            query: Search query for title/description
            status: Filter by status
            priority: Filter by priority
            assignee_id: Filter by assignee
            organization_id: Filter by organization
            labels: Filter by labels
            filters: Additional filters
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Issue search results
        """
        db_query = self.db.issues.id > 0

        # Text search (case-insensitive)
        if query:
            search_pattern = f"%{query}%"
            db_query &= self.db.issues.title.ilike(
                search_pattern
            ) | self.db.issues.description.ilike(search_pattern)

        # Status filter
        if status:
            db_query &= self.db.issues.status == status

        # Priority filter
        if priority:
            db_query &= self.db.issues.priority == priority

        # Assignee filter
        if assignee_id:
            db_query &= self.db.issues.assignee_id == assignee_id

        # Organization filter
        if organization_id:
            db_query &= self.db.issues.organization_id == organization_id

        # Get total count
        total_count = self.db(db_query).count()

        # Execute query
        issues = self.db(db_query).select(
            orderby=~self.db.issues.updated_at, limitby=(offset, offset + limit)
        )

        return {
            "issues": [i.as_dict() for i in issues],
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
        }

    # ===========================
    # Graph Search Methods
    # ===========================

    def search_graph(
        self,
        start_entity_id: int,
        max_depth: int = 3,
        dependency_types: Optional[List[str]] = None,
        entity_filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Graph-based search for entities and dependencies.

        Args:
            start_entity_id: Starting entity ID
            max_depth: Maximum traversal depth
            dependency_types: Filter by dependency types
            entity_filters: Filters for entities in graph

        Returns:
            Graph search results with nodes and edges
        """
        # Verify starting entity exists
        start_entity = self.db.entities[start_entity_id]
        if not start_entity:
            raise Exception(f"Entity {start_entity_id} not found")

        visited_entities = set()
        visited_dependencies = set()
        nodes = []
        edges = []

        # BFS traversal
        queue = [(start_entity_id, 0)]  # (entity_id, depth)

        while queue:
            entity_id, depth = queue.pop(0)

            if entity_id in visited_entities or depth > max_depth:
                continue

            visited_entities.add(entity_id)

            # Add entity as node
            entity = self.db.entities[entity_id]
            if entity:
                nodes.append(
                    {
                        "id": entity.id,
                        "name": entity.name,
                        "entity_type": entity.entity_type,
                        "sub_type": entity.sub_type,
                        "depth": depth,
                    }
                )

                # Find dependencies where this entity is source
                # Dependencies table uses source_type/source_id, not source_entity_id
                deps_out = self.db(
                    (self.db.dependencies.source_type == "entity")
                    & (self.db.dependencies.source_id == entity_id)
                ).select()

                for dep in deps_out:
                    if dependency_types and dep.dependency_type not in dependency_types:
                        continue

                    if dep.id not in visited_dependencies:
                        visited_dependencies.add(dep.id)
                        edges.append(
                            {
                                "source": dep.source_id,
                                "target": dep.target_id,
                                "type": dep.dependency_type,
                                "metadata": dep.metadata,
                            }
                        )

                        # Add target to queue (only if target is also an entity)
                        if (
                            dep.target_type == "entity"
                            and dep.target_id not in visited_entities
                        ):
                            queue.append((dep.target_id, depth + 1))

                # Find dependencies where this entity is target
                deps_in = self.db(
                    (self.db.dependencies.target_type == "entity")
                    & (self.db.dependencies.target_id == entity_id)
                ).select()

                for dep in deps_in:
                    if dependency_types and dep.dependency_type not in dependency_types:
                        continue

                    if dep.id not in visited_dependencies:
                        visited_dependencies.add(dep.id)
                        edges.append(
                            {
                                "source": dep.source_id,
                                "target": dep.target_id,
                                "type": dep.dependency_type,
                                "metadata": dep.metadata,
                            }
                        )

                        # Add source to queue (only if source is also an entity)
                        if (
                            dep.source_type == "entity"
                            and dep.source_id not in visited_entities
                        ):
                            queue.append((dep.source_id, depth + 1))

        return {
            "start_entity_id": start_entity_id,
            "max_depth": max_depth,
            "nodes": nodes,
            "edges": edges,
            "node_count": len(nodes),
            "edge_count": len(edges),
        }

    # ===========================
    # Saved Search Methods
    # ===========================

    def list_saved_searches(
        self, user_id: int, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        List user's saved searches.

        Args:
            user_id: User ID
            limit: Maximum results

        Returns:
            List of saved searches
        """
        searches = self.db(self.db.saved_searches.identity_id == user_id).select(
            orderby=~self.db.saved_searches.created_at, limitby=(0, limit)
        )

        return [s.as_dict() for s in searches]

    def get_saved_search(self, search_id: int, user_id: int) -> Dict[str, Any]:
        """
        Get saved search details.

        Args:
            search_id: Saved search ID
            user_id: User ID (for ownership check)

        Returns:
            Saved search dictionary

        Raises:
            Exception: If search not found or not owned by user
        """
        search = self.db.saved_searches[search_id]

        if not search:
            raise Exception(f"Saved search {search_id} not found")

        if search.identity_id != user_id:
            raise Exception(f"Saved search {search_id} not owned by user {user_id}")

        return search.as_dict()

    def create_saved_search(
        self,
        user_id: int,
        name: str,
        query: str,
        resource_type: str,
        filters: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Save a search query.

        Args:
            user_id: User ID
            name: Search name
            query: Search query string
            resource_type: Resource type (entity, organization, issue, all)
            filters: Additional filters
            description: Optional description

        Returns:
            Created saved search dictionary
        """
        search_id = self.db.saved_searches.insert(
            identity_id=user_id,
            name=name,
            query=query,
            filters=filters,
        )

        self.db.commit()

        search = self.db.saved_searches[search_id]
        return search.as_dict()

    def update_saved_search(
        self,
        search_id: int,
        user_id: int,
        name: Optional[str] = None,
        query: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update saved search.

        Args:
            search_id: Saved search ID
            user_id: User ID (for ownership check)
            name: New name
            query: New query
            filters: New filters
            description: New description

        Returns:
            Updated saved search dictionary

        Raises:
            Exception: If search not found or not owned by user
        """
        search = self.db.saved_searches[search_id]

        if not search:
            raise Exception(f"Saved search {search_id} not found")

        if search.identity_id != user_id:
            raise Exception(f"Saved search {search_id} not owned by user {user_id}")

        update_data = {}

        if name is not None:
            update_data["name"] = name

        if query is not None:
            update_data["query"] = query

        if filters is not None:
            update_data["filters"] = filters

        self.db(self.db.saved_searches.id == search_id).update(**update_data)
        self.db.commit()

        search = self.db.saved_searches[search_id]
        return search.as_dict()

    def delete_saved_search(self, search_id: int, user_id: int) -> Dict[str, str]:
        """
        Delete saved search.

        Args:
            search_id: Saved search ID
            user_id: User ID (for ownership check)

        Returns:
            Success message

        Raises:
            Exception: If search not found or not owned by user
        """
        search = self.db.saved_searches[search_id]

        if not search:
            raise Exception(f"Saved search {search_id} not found")

        if search.identity_id != user_id:
            raise Exception(f"Saved search {search_id} not owned by user {user_id}")

        self.db(self.db.saved_searches.id == search_id).delete()
        self.db.commit()

        return {"message": "Saved search deleted successfully"}

    def execute_saved_search(
        self, search_id: int, user_id: int, limit: int = 50, offset: int = 0
    ) -> Dict[str, Any]:
        """
        Execute a saved search.

        Args:
            search_id: Saved search ID
            user_id: User ID (for ownership check)
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Search results

        Raises:
            Exception: If search not found or not owned by user
        """
        search = self.db.saved_searches[search_id]

        if not search:
            raise Exception(f"Saved search {search_id} not found")

        if search.identity_id != user_id:
            raise Exception(f"Saved search {search_id} not owned by user {user_id}")

        # Parse filters (already stored as JSON in table)
        filters = search.filters if hasattr(search, "filters") else None

        # Execute search across all resource types
        return self.search_all(
            query=search.query, filters=filters, limit=limit, offset=offset
        )

    # ===========================
    # Search Analytics Methods
    # ===========================

    def get_popular_searches(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get most popular/frequent searches.

        Args:
            limit: Maximum results

        Returns:
            List of popular searches
        """
        searches = self.db(self.db.saved_searches.id > 0).select(
            orderby=~self.db.saved_searches.created_at, limitby=(0, limit)
        )

        return [
            {
                "id": s.id,
                "name": s.name,
                "query": s.query,
                "resource_type": s.resource_type,
                "use_count": s.use_count,
                "last_used_at": s.last_used_at,
            }
            for s in searches
        ]

    def get_search_suggestions(
        self, partial_query: str, resource_type: Optional[str] = None, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get search suggestions/autocomplete.

        Args:
            partial_query: Partial query string
            resource_type: Resource type for suggestions
            limit: Maximum suggestions

        Returns:
            List of search suggestions
        """
        suggestions = []

        # Entity name suggestions - use groupby instead of distinct to avoid ORDER BY issues
        if not resource_type or resource_type == "entity":
            entities = self.db(self.db.entities.name.contains(partial_query)).select(
                self.db.entities.name, groupby=self.db.entities.name, limitby=(0, limit)
            )
            suggestions.extend(
                [
                    {"text": e.name, "type": "entity", "category": "name"}
                    for e in entities
                ]
            )

        # Organization name suggestions - use groupby instead of distinct
        if not resource_type or resource_type == "organization":
            orgs = self.db(self.db.organizations.name.contains(partial_query)).select(
                self.db.organizations.name,
                groupby=self.db.organizations.name,
                limitby=(0, limit),
            )
            suggestions.extend(
                [
                    {"text": o.name, "type": "organization", "category": "name"}
                    for o in orgs
                ]
            )

        # Issue title suggestions - use groupby instead of distinct
        if not resource_type or resource_type == "issue":
            issues = self.db(self.db.issues.title.contains(partial_query)).select(
                self.db.issues.title, groupby=self.db.issues.title, limitby=(0, limit)
            )
            suggestions.extend(
                [
                    {"text": i.title, "type": "issue", "category": "title"}
                    for i in issues
                ]
            )

        # Limit total suggestions
        return suggestions[:limit]
