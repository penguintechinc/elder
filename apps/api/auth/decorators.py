"""Authentication and authorization decorators using PyDAL."""

# flake8: noqa: E501


import inspect
from functools import wraps
from typing import Callable, List

from flask import current_app, g, jsonify, request
from penguin_dal import Row

from apps.api.auth.jwt_handler import get_current_user


def login_required(f: Callable) -> Callable:
    """
    Decorator to require authentication for an endpoint.

    Supports both sync and async functions.
    Makes current_user available via flask.g.current_user.

    Usage:
        @bp.route('/protected')
        @login_required
        async def protected_route():
            from flask import g
            return jsonify({"user": g.current_user.username})
    """

    @wraps(f)
    async def decorated_function(*args, **kwargs):
        user = get_current_user()

        if not user:
            return jsonify({"error": "Authentication required"}), 401

        g.current_user = user

        # Call function without modifying arguments
        if inspect.iscoroutinefunction(f):
            return await f(*args, **kwargs)
        else:
            return f(*args, **kwargs)

    return decorated_function


def permission_required(permission_name: str) -> Callable:
    """
    Decorator to require specific permission for an endpoint.

    Args:
        permission_name: Name of required permission (e.g., 'create_entity')

    Usage:
        @bp.route('/entities', methods=['POST'])
        @permission_required('create_entity')
        def create_entity():
            # Create entity logic
            pass
    """

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        async def decorated_function(*args, **kwargs):
            user = get_current_user()

            if not user:
                return jsonify({"error": "Authentication required"}), 401

            # Superusers have all permissions
            if user.is_superuser:
                g.current_user = user
                # Call the wrapped function (it's async)
                if inspect.iscoroutinefunction(f):
                    return await f(*args, **kwargs)
                else:
                    return f(*args, **kwargs)

            # Check if user has required permission
            has_permission = _check_user_permission(user, permission_name)

            if not has_permission:
                return (
                    jsonify(
                        {
                            "error": "Insufficient permissions",
                            "required_permission": permission_name,
                        }
                    ),
                    403,
                )

            g.current_user = user
            # Call the wrapped function (it's async)
            if inspect.iscoroutinefunction(f):
                return await f(*args, **kwargs)
            else:
                return f(*args, **kwargs)

        return decorated_function

    return decorator


def permissions_required(
    permission_names: List[str], require_all: bool = True
) -> Callable:
    """
    Decorator to require multiple permissions.

    Args:
        permission_names: List of permission names
        require_all: If True, require all permissions; if False, require any one

    Usage:
        @bp.route('/admin/config', methods=['POST'])
        @permissions_required(['edit_config', 'manage_users'])
        def update_config():
            pass
    """

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        async def decorated_function(*args, **kwargs):
            user = get_current_user()

            if not user:
                return jsonify({"error": "Authentication required"}), 401

            # Superusers have all permissions
            if user.is_superuser:
                g.current_user = user
                if inspect.iscoroutinefunction(f):
                    return await f(*args, **kwargs)
                else:
                    return f(*args, **kwargs)

            # Check permissions
            checks = [_check_user_permission(user, perm) for perm in permission_names]

            if require_all:
                has_permission = all(checks)
            else:
                has_permission = any(checks)

            if not has_permission:
                return (
                    jsonify(
                        {
                            "error": "Insufficient permissions",
                            "required_permissions": permission_names,
                            "require_all": require_all,
                        }
                    ),
                    403,
                )

            g.current_user = user
            if inspect.iscoroutinefunction(f):
                return await f(*args, **kwargs)
            else:
                return f(*args, **kwargs)

        return decorated_function

    return decorator


def org_permission_required(permission_name: str, org_id_param: str = "id") -> Callable:
    """
    Decorator to check organization-scoped permissions.

    Args:
        permission_name: Name of required permission
        org_id_param: Name of route parameter containing organization ID

    Usage:
        @bp.route('/organizations/<int:id>/entities', methods=['POST'])
        @org_permission_required('create_entity', org_id_param='id')
        def create_org_entity(id):
            # Create entity in organization
            pass
    """

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        async def decorated_function(*args, **kwargs):
            user = get_current_user()

            if not user:
                return jsonify({"error": "Authentication required"}), 401

            # Superusers have all permissions
            if user.is_superuser:
                g.current_user = user
                if inspect.iscoroutinefunction(f):
                    return await f(*args, **kwargs)
                else:
                    return f(*args, **kwargs)

            # Get organization ID from route params
            org_id = kwargs.get(org_id_param)

            if not org_id:
                return jsonify({"error": "Organization ID required"}), 400

            # Check if user has permission for this organization
            has_permission = _check_org_permission(user, permission_name, org_id)

            if not has_permission:
                return (
                    jsonify(
                        {
                            "error": "Insufficient permissions for this organization",
                            "required_permission": permission_name,
                            "organization_id": org_id,
                        }
                    ),
                    403,
                )

            g.current_user = user
            if inspect.iscoroutinefunction(f):
                return await f(*args, **kwargs)
            else:
                return f(*args, **kwargs)

        return decorated_function

    return decorator


def _check_user_permission(user: Row, permission_name: str) -> bool:
    """
    Check if user has a specific permission (global or org-scoped).

    Args:
        user: PyDAL Row representing identity
        permission_name: Permission name to check

    Returns:
        True if user has permission (currently simplified - returns True for all authenticated users)

    TODO: Implement full RBAC permission checking with PyDAL
    """
    # Simplified permission check - superusers have all permissions
    # For non-superusers, we'll need to implement RBAC tables and logic
    # For now, allow authenticated users to proceed
    return True


def _check_org_permission(user: Row, permission_name: str, org_id: int) -> bool:
    """
    Check if user has permission for a specific organization.

    Args:
        user: PyDAL Row representing identity
        permission_name: Permission name to check
        org_id: Organization ID

    Returns:
        True if user has permission for this organization (currently simplified)

    TODO: Implement full organization-scoped RBAC with PyDAL
    """
    # Simplified org permission check
    # For now, allow authenticated users to proceed
    return True


def resource_role_required(required_role: str, resource_param: str = "id") -> Callable:
    """
    Decorator to check resource-level role requirements using PyDAL.

    Checks if the current user has the required role (maintainer/operator/viewer)
    on the specific resource (entity or organization) being accessed.

    Role hierarchy: viewer < operator < maintainer
    - maintainer: Full CRUD, can manage roles
    - operator: Create/close issues, add comments/labels, read metadata
    - viewer: View, create issues, add comments

    Args:
        required_role: Minimum role required (viewer, operator, maintainer)
        resource_param: Name of route parameter containing resource ID

    Usage:
        @bp.route('/entities/<int:id>/metadata', methods=['POST'])
        @login_required
        @license_required('enterprise')
        @resource_role_required('maintainer', resource_param='id')
        def create_entity_metadata(id):
            # Only maintainers can create metadata
            pass

        @bp.route('/issues', methods=['POST'])
        @login_required
        @license_required('enterprise')
        @resource_role_required('viewer')
        def create_issue():
            # Viewers can create issues
            # Must provide entity_id or organization_id in request body
            pass
    """
    # Role hierarchy levels (higher number = more permissions)
    role_hierarchy = {
        "viewer": 1,
        "operator": 2,
        "maintainer": 3,
    }

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        async def decorated_function(*args, **kwargs):
            user = get_current_user()

            if not user:
                return jsonify({"error": "Authentication required"}), 401

            # Superusers bypass resource role checks
            if user.is_superuser:
                g.current_user = user
                if inspect.iscoroutinefunction(f):
                    return await f(*args, **kwargs)
                else:
                    return f(*args, **kwargs)

            # Get resource ID and type
            resource_id = kwargs.get(resource_param)
            resource_type = None

            # If not in route params, check request body (for POST/PATCH)
            if not resource_id and request.is_json:
                data = request.get_json()
                if "entity_id" in data:
                    resource_id = data["entity_id"]
                    resource_type = "entity"
                elif "organization_id" in data:
                    resource_id = data["organization_id"]
                    resource_type = "organization"
                else:
                    return (
                        jsonify(
                            {
                                "error": "Resource ID required (entity_id or organization_id)"
                            }
                        ),
                        400,
                    )
            elif resource_id:
                # Determine resource type from route context
                # Check if we're in an entity or organization route
                if "/entities/" in request.path:
                    resource_type = "entity"
                elif "/organizations/" in request.path:
                    resource_type = "organization"
                else:
                    # Can't determine resource type
                    return jsonify({"error": "Unable to determine resource type"}), 400
            else:
                return jsonify({"error": "Resource ID required"}), 400

            # Get required role level
            required_level = role_hierarchy.get(required_role)
            if not required_level:
                return jsonify({"error": f"Invalid role: {required_role}"}), 500

            # Check if user has required role on this resource using PyDAL
            db = current_app.db
            try:
                user_roles = db(
                    (db.resource_roles.identity_id == user.id)
                    & (db.resource_roles.resource_type == resource_type)
                    & (db.resource_roles.resource_id == resource_id)
                ).select()

                has_role = False
                for user_role in user_roles:
                    user_level = role_hierarchy.get(user_role.role, 0)
                    # Check if user's role level meets or exceeds required level
                    if user_level >= required_level:
                        has_role = True
                        break
            except Exception:
                # resource_roles table may not exist or query failed
                # Deny access gracefully instead of returning 500
                has_role = False

            if not has_role:
                return (
                    jsonify(
                        {
                            "error": "Insufficient permissions",
                            "message": f"This action requires '{required_role}' role on this resource",
                            "required_role": required_role,
                            "resource_type": resource_type,
                            "resource_id": resource_id,
                        }
                    ),
                    403,
                )

            g.current_user = user
            if inspect.iscoroutinefunction(f):
                return await f(*args, **kwargs)
            else:
                return f(*args, **kwargs)

        return decorated_function

    return decorator


def admin_required(f):
    """Decorator to require admin role for an endpoint. Alias for @role_required('admin')."""
    return role_required("admin")(f)


def role_required(allowed_roles):
    """
    Decorator to require specific portal roles for an endpoint.

    Args:
        allowed_roles: String or list of allowed portal roles ('admin', 'editor', 'observer')

    Usage:
        @bp.route('/admin-only')
        @login_required
        @role_required('admin')
        async def admin_route():
            return jsonify({"message": "Admin access"})

        @bp.route('/write-access')
        @login_required
        @role_required(['admin', 'editor'])
        async def write_route():
            return jsonify({"message": "Write access"})
    """
    # Normalize to list
    if isinstance(allowed_roles, str):
        allowed_roles = [allowed_roles]

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        async def decorated_function(*args, **kwargs):
            user = get_current_user()

            if not user:
                return jsonify({"error": "Authentication required"}), 401

            # Superusers bypass all role checks
            if user.is_superuser:
                g.current_user = user
                if inspect.iscoroutinefunction(f):
                    return await f(*args, **kwargs)
                else:
                    return f(*args, **kwargs)

            # Check portal role
            user_role = user.get("portal_role", "observer")
            if user_role not in allowed_roles:
                return (
                    jsonify(
                        {
                            "error": "Insufficient permissions",
                            "required_roles": allowed_roles,
                            "your_role": user_role,
                        }
                    ),
                    403,
                )

            g.current_user = user
            if inspect.iscoroutinefunction(f):
                return await f(*args, **kwargs)
            else:
                return f(*args, **kwargs)

        return decorated_function

    return decorator
