"""Elder gRPC servicer implementation.

Every RPC except Login/RefreshToken/HealthCheck runs for a caller verified
by ``apps.api.grpc.auth.GrpcAuthInterceptor``. Each handler is wrapped by
``_rpc``, which re-checks that a verified caller is bound (defence in depth
if the interceptor is ever missing), refuses platform identities that carry
no tenant, and enforces the same ``resource:action`` scopes as the REST API.
All queries are then scoped to the caller's tenant: lists filter, foreign
ids resolve to NOT_FOUND, and creates verify every referenced row is
in-tenant. Nothing in a request message is trusted for tenancy.
"""

# flake8: noqa: E501

import functools
import itertools
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import grpc
import networkx as nx
import structlog
from werkzeug.security import check_password_hash, generate_password_hash

from apps.api.auth.jwt_handler import generate_token, verify_token
from apps.api.grpc.auth import GrpcCaller, current_caller, run_in_app_context
from apps.api.grpc.converters import (
    dependency_to_proto,
    dependency_type_to_proto,
    entity_to_proto,
    entity_type_from_proto,
    entity_type_to_proto,
    identity_to_proto,
    organization_to_proto,
)
from apps.api.grpc.generated import (
    auth_pb2 as _auth_pb2,
)
from apps.api.grpc.generated import (
    common_pb2 as _common_pb2,
)
from apps.api.grpc.generated import (
    dependency_pb2 as _dependency_pb2,
)
from apps.api.grpc.generated import (
    elder_pb2_grpc,
)
from apps.api.grpc.generated import (
    entity_pb2 as _entity_pb2,
)
from apps.api.grpc.generated import (
    graph_pb2 as _graph_pb2,
)
from apps.api.grpc.generated import (
    organization_pb2 as _organization_pb2,
)
from apps.api.models import DependencyDTO, EntityDTO, IdentityDTO, OrganizationDTO
from apps.api.models.dataclasses import from_pydal_row
from apps.api.modules.infrastructure.routes.graph import (
    GRAPH_MAX_NODES,
    _entity_edges,
    _get_entity_subgraph,
    _resolve_depth,
    _select_capped,
    _tenant_org_ids,
)
from apps.api.utils.pagination import DEFAULT_LIMIT, MAX_LIMIT, MAX_OFFSET, clamp_int
from apps.api.utils.tenant_scoping import get_tenant_scoped

# The protobuf modules are generated at runtime descriptors and ship no type
# stubs, so their message classes are invisible to mypy; bind them as Any.
auth_pb2: Any = _auth_pb2
common_pb2: Any = _common_pb2
dependency_pb2: Any = _dependency_pb2
entity_pb2: Any = _entity_pb2
graph_pb2: Any = _graph_pb2
organization_pb2: Any = _organization_pb2

logger = structlog.get_logger(__name__)

# Scopes accepted per resource and action. The REST routes guard
# organizations, entities and dependencies with ``infrastructure:*``; the
# resource-specific scopes are accepted too because they are what
# per-resource roles and service workers are issued.
ORG_READ = ("organizations:read", "infrastructure:read")
ORG_WRITE = ("organizations:write", "infrastructure:write")
ENTITY_READ = ("entities:read", "infrastructure:read")
ENTITY_WRITE = ("entities:write", "infrastructure:write")
DEP_READ = ("dependencies:read", "infrastructure:read")
DEP_WRITE = ("dependencies:write", "infrastructure:write")
GRAPH_READ = ("infrastructure:read",)
USERS_READ = ("users:read", "identities:read")
USERS_WRITE = ("users:write", "identities:write")

# Upper bound on cycles reported by AnalyzeGraph (enumeration is exponential).
ANALYZE_MAX_CYCLES = 10
# Upper bound on organization nesting walked when resolving a hierarchy root.
ORG_MAX_ANCESTORS = 100


def _rpc(response_cls: Callable[[], Any], scopes: tuple[str, ...] = ()):
    """Guard a servicer method with caller, tenant and scope checks.

    The wrapped method receives the verified ``GrpcCaller`` as an extra
    argument. Calls with no verified caller get UNAUTHENTICATED; platform
    identities without a tenant, or callers lacking every accepted scope,
    get PERMISSION_DENIED. Status is set on the context (not raised) so the
    method bodies' broad exception handlers cannot mask it as INTERNAL.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(self: Any, request: Any, context: grpc.ServicerContext) -> Any:
            caller = current_caller()
            if caller is None:
                context.set_code(grpc.StatusCode.UNAUTHENTICATED)
                context.set_details("valid bearer token required")
                return response_cls()
            if caller.is_system:
                context.set_code(grpc.StatusCode.PERMISSION_DENIED)
                context.set_details("a tenant-bound identity is required")
                return response_cls()
            if scopes and not caller.has_any_scope(scopes):
                context.set_code(grpc.StatusCode.PERMISSION_DENIED)
                context.set_details(f"requires one of scopes: {', '.join(scopes)}")
                return response_cls()
            return fn(self, request, context, caller)

        return wrapper

    return decorator


def _not_found(context: grpc.ServicerContext, what: str, rid: Any, resp: Any) -> Any:
    """Set NOT_FOUND for a missing or foreign row and return ``resp``.

    Foreign ids get exactly the same answer as non-existent ones so callers
    cannot enumerate other tenants' ids.
    """
    context.set_code(grpc.StatusCode.NOT_FOUND)
    context.set_details(f"{what} with ID {rid} not found")
    return resp


def _invalid(context: grpc.ServicerContext, details: str, resp: Any) -> Any:
    """Set INVALID_ARGUMENT with ``details`` and return ``resp``."""
    context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
    context.set_details(details)
    return resp


class ElderServicer(elder_pb2_grpc.ElderServiceServicer):
    """Implementation of ElderService gRPC servicer."""

    def __init__(self, app):
        """
        Initialize the servicer.

        Args:
            app: Flask application instance with shared DAL
        """
        super().__init__()

        # Store reference to the app for shared database access and the
        # JWT configuration used by the REST API (single signing key).
        self.app = app

        logger.info("elder_servicer_initialized")

    @property
    def db(self):
        """Get the shared database connection from the Flask app."""
        return self.app.db

    # ========================================================================
    # Helper Methods
    # ========================================================================

    def _create_timestamp(self, dt: datetime | None) -> Any:
        """Convert datetime to protobuf Timestamp."""
        if dt is None:
            return common_pb2.Timestamp(seconds=0, nanos=0)

        timestamp = int(dt.timestamp())
        nanos = dt.microsecond * 1000
        return common_pb2.Timestamp(seconds=timestamp, nanos=nanos)

    def _page(self, pagination) -> tuple[int, int, int]:
        """Return ``(page, per_page, offset)`` bounded like the REST lists.

        ``per_page`` defaults to 50 and is capped at 200; the offset is
        capped at ``MAX_OFFSET`` so a huge page number cannot force a deep
        scan.
        """
        per_page = clamp_int(pagination.per_page or None, DEFAULT_LIMIT, 1, MAX_LIMIT)
        page = clamp_int(pagination.page or None, 1, 1, MAX_OFFSET // per_page + 1)
        return page, per_page, (page - 1) * per_page

    def _scoped_org(self, db, org_id: int, tenant_id: int | None):
        """Return the organization only if it belongs to ``tenant_id``."""
        return get_tenant_scoped(db, db.organizations, org_id, tenant_id)

    def _scoped_entity(self, db, entity_id: int, tenant_id: int | None):
        """Return the entity only if its organization belongs to ``tenant_id``."""
        return get_tenant_scoped(
            db, db.entities, entity_id, tenant_id, org_fk="organization_id"
        )

    def _scoped_dependency(self, db, dep_id: int, tenant_id: int | None):
        """Return the dependency only if it is recorded for ``tenant_id``."""
        return get_tenant_scoped(db, db.dependencies, dep_id, tenant_id)

    def _scoped_identity(self, db, identity_id: int, tenant_id: int | None):
        """Return the identity only if it belongs to ``tenant_id``."""
        return get_tenant_scoped(db, db.identities, identity_id, tenant_id)

    def _tenant_graph(self, db, tenant_id: int | None, org_id: int = 0):
        """Load the caller's entity graph, bounded by the shared graph caps.

        Returns ``(entities, edges, truncated)`` where every entity belongs
        to one of the tenant's organizations (optionally a single one) and
        both endpoints of every edge are inside that entity set.
        """
        org_ids = _tenant_org_ids(db, tenant_id)
        if org_id:
            org_ids = [org_id] if org_id in org_ids else []
        if not org_ids:
            return [], [], False
        entities, nodes_cut = _select_capped(
            db,
            db.entities.organization_id.belongs(org_ids),
            db.entities.id,
            GRAPH_MAX_NODES,
        )
        edges, edges_cut = _entity_edges(db, [e.id for e in entities])
        edges = [e for e in edges if e.tenant_id in (tenant_id, None)]
        return entities, edges, nodes_cut or edges_cut

    def _create_pagination_response(self, page: int, per_page: int, total: int) -> Any:
        """Create pagination response."""
        pages = (total + per_page - 1) // per_page if per_page > 0 else 0
        return common_pb2.PaginationResponse(
            page=page,
            per_page=per_page,
            total=total,
            pages=pages,
        )

    def _create_status_response(
        self, success: bool, message: str, details: dict | None = None
    ) -> Any:
        """Create status response."""
        return common_pb2.StatusResponse(
            success=success,
            message=message,
            details=details or {},
        )

    def _handle_exception(self, context, e: Exception, operation: str):
        """Handle exceptions and set gRPC context."""
        logger.error(f"grpc_{operation}_error", error=str(e), exc_info=True)
        context.set_code(grpc.StatusCode.INTERNAL)
        context.set_details(f"{operation} failed: {str(e)}")

    def _generate_jwt_token(self, identity, token_type: str = "access") -> str:
        """Issue a token with the REST API's issuer (same key and claims)."""
        return run_in_app_context(self.app, generate_token, identity, token_type)

    def _verify_jwt_token(self, token: str) -> dict | None:
        """Verify a token with the REST API's shared verifier."""
        if not token:
            return None
        return run_in_app_context(self.app, verify_token, token)

    def _access_expires_in(self) -> int:
        """Return the configured access-token lifetime in seconds."""
        return int(self.app.config["JWT_ACCESS_TOKEN_EXPIRES"].total_seconds())

    # ========================================================================
    # Authentication & Identity Management (11 methods)
    # ========================================================================

    def Login(self, request, context):
        """Authenticate user and return JWT tokens."""
        try:
            db = self.db

            # Validate required fields
            if not request.username:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("username is required")
                return auth_pb2.LoginResponse()
            if not request.password:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("password is required")
                return auth_pb2.LoginResponse()

            # Find user
            identity = db(db.identities.username == request.username).select().first()
            if not identity:
                context.set_code(grpc.StatusCode.UNAUTHENTICATED)
                context.set_details("Invalid username or password")
                return auth_pb2.LoginResponse()

            # Verify password
            if not identity.password_hash or not check_password_hash(
                identity.password_hash, request.password
            ):
                context.set_code(grpc.StatusCode.UNAUTHENTICATED)
                context.set_details("Invalid username or password")
                return auth_pb2.LoginResponse()

            # Check if account is active
            if not identity.is_active:
                context.set_code(grpc.StatusCode.PERMISSION_DENIED)
                context.set_details("Account is inactive")
                return auth_pb2.LoginResponse()

            # Update last login
            db(db.identities.id == identity.id).update(last_login_at=datetime.now(UTC))
            db.commit()

            # Refresh identity data
            identity = db.identities[identity.id]

            # Generate tokens
            access_token = self._generate_jwt_token(identity, "access")
            refresh_token = self._generate_jwt_token(identity, "refresh")
            expires_in = self._access_expires_in()

            identity_dto = from_pydal_row(identity, IdentityDTO)

            return auth_pb2.LoginResponse(
                access_token=access_token,
                refresh_token=refresh_token,
                token_type="Bearer",
                expires_in=expires_in,
                identity=identity_to_proto(identity_dto),
            )
        except Exception as e:
            self._handle_exception(context, e, "login")
            return auth_pb2.LoginResponse()

    def RefreshToken(self, request, context):
        """Refresh access token using refresh token."""
        try:
            db = self.db

            if not request.refresh_token:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("refresh_token is required")
                return auth_pb2.RefreshTokenResponse()

            # Verify refresh token
            payload = self._verify_jwt_token(request.refresh_token)
            if not payload or payload.get("type") != "refresh":
                context.set_code(grpc.StatusCode.UNAUTHENTICATED)
                context.set_details("Invalid refresh token")
                return auth_pb2.RefreshTokenResponse()

            # Get identity
            user_id = int(payload["sub"])
            identity = db.identities[user_id]

            if not identity or not identity.is_active:
                context.set_code(grpc.StatusCode.UNAUTHENTICATED)
                context.set_details("User not found or inactive")
                return auth_pb2.RefreshTokenResponse()

            # Generate new access token
            access_token = self._generate_jwt_token(identity, "access")
            expires_in = self._access_expires_in()

            return auth_pb2.RefreshTokenResponse(
                access_token=access_token,
                token_type="Bearer",
                expires_in=expires_in,
            )
        except Exception as e:
            self._handle_exception(context, e, "refresh_token")
            return auth_pb2.RefreshTokenResponse()

    @_rpc(auth_pb2.LogoutResponse)
    def Logout(self, request, context, caller):
        """Logout and invalidate tokens."""
        try:
            # Note: JWT tokens are stateless, so logout is handled client-side
            # This endpoint exists for consistency and future token blacklisting
            return auth_pb2.LogoutResponse(
                status=self._create_status_response(
                    success=True, message="Logged out successfully"
                )
            )
        except Exception as e:
            self._handle_exception(context, e, "logout")
            return auth_pb2.LogoutResponse()

    @_rpc(auth_pb2.GetCurrentIdentityResponse)
    def GetCurrentIdentity(self, request, context, caller):
        """Return the identity of the verified caller.

        The identity always comes from the call's bearer token; the legacy
        ``access_token`` request field is ignored so it cannot be used to
        look up somebody else.
        """
        try:
            db = self.db

            if caller.token_type != "access":
                return _not_found(
                    context,
                    "Identity",
                    caller.subject,
                    auth_pb2.GetCurrentIdentityResponse(),
                )
            identity = self._scoped_identity(db, int(caller.subject), caller.tenant_id)

            if not identity:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("User not found")
                return auth_pb2.GetCurrentIdentityResponse()

            identity_dto = from_pydal_row(identity, IdentityDTO)
            return auth_pb2.GetCurrentIdentityResponse(
                identity=identity_to_proto(identity_dto)
            )
        except Exception as e:
            self._handle_exception(context, e, "get_current_identity")
            return auth_pb2.GetCurrentIdentityResponse()

    @_rpc(auth_pb2.ChangePasswordResponse)
    def ChangePassword(self, request, context, caller):
        """Change password for current user."""
        try:
            db = self.db

            # Validate required fields
            if not request.current_password:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("current_password is required")
                return auth_pb2.ChangePasswordResponse()
            if not request.new_password:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("new_password is required")
                return auth_pb2.ChangePasswordResponse()

            # Password changes go through the REST API, which applies the
            # password policy and session revocation.
            context.set_code(grpc.StatusCode.UNIMPLEMENTED)
            context.set_details("ChangePassword is only available via the REST API")
            return auth_pb2.ChangePasswordResponse()
        except Exception as e:
            self._handle_exception(context, e, "change_password")
            return auth_pb2.ChangePasswordResponse()

    @_rpc(auth_pb2.RegisterIdentityResponse, USERS_WRITE)
    def RegisterIdentity(self, request, context, caller):
        """Create an identity in the caller's tenant.

        Requires ``users:write``. The new identity is a non-superuser
        observer, and no tokens are returned for it: the creating caller
        must not be able to act as the identity it just created.
        """
        try:
            db = self.db

            # Validate required fields
            if not request.username:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("username is required")
                return auth_pb2.RegisterIdentityResponse()
            if not request.email:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("email is required")
                return auth_pb2.RegisterIdentityResponse()
            if not request.password:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("password is required")
                return auth_pb2.RegisterIdentityResponse()

            # Check if username exists
            existing = db(db.identities.username == request.username).select().first()
            if existing:
                context.set_code(grpc.StatusCode.ALREADY_EXISTS)
                context.set_details("Username already exists")
                return auth_pb2.RegisterIdentityResponse()

            # Check if email exists
            existing_email = db(db.identities.email == request.email).select().first()
            if existing_email:
                context.set_code(grpc.StatusCode.ALREADY_EXISTS)
                context.set_details("Email already exists")
                return auth_pb2.RegisterIdentityResponse()

            # Determine identity type
            identity_type = "human"
            if request.identity_type == auth_pb2.IdentityType.SERVICE_ACCOUNT:
                identity_type = "service_account"

            # Create identity
            now = datetime.now(UTC)
            identity_id = db.identities.insert(
                username=request.username,
                email=request.email,
                full_name=request.display_name,
                identity_type=identity_type,
                auth_provider="local",
                password_hash=generate_password_hash(request.password),
                tenant_id=caller.tenant_id,
                portal_role="observer",
                must_change_password=False,
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            identity = self._scoped_identity(db, identity_id, caller.tenant_id)
            identity_dto = from_pydal_row(identity, IdentityDTO)

            return auth_pb2.RegisterIdentityResponse(
                identity=identity_to_proto(identity_dto),
            )
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "register_identity")
            return auth_pb2.RegisterIdentityResponse()

    @_rpc(auth_pb2.ValidateTokenResponse)
    def ValidateToken(self, request, context, caller):
        """Validate an access token belonging to the caller's tenant.

        Tokens of other tenants (or of non-identity callers) report
        ``valid=False`` so introspection cannot reveal foreign identities.
        """
        try:
            db = self.db

            if not request.access_token:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("access_token is required")
                return auth_pb2.ValidateTokenResponse()

            # Verify token
            payload = self._verify_jwt_token(request.access_token)
            sub = str((payload or {}).get("sub", ""))
            if (
                not payload
                or payload.get("type") != "access"
                or not (sub.isascii() and sub.isdigit())
            ):
                return auth_pb2.ValidateTokenResponse(
                    valid=False,
                    identity=None,
                    expires_at=None,
                )

            identity = self._scoped_identity(db, int(sub), caller.tenant_id)

            if not identity or not identity.is_active:
                return auth_pb2.ValidateTokenResponse(
                    valid=False,
                    identity=None,
                    expires_at=None,
                )

            # Token is valid
            identity_dto = from_pydal_row(identity, IdentityDTO)
            exp_timestamp = payload.get("exp", 0)

            return auth_pb2.ValidateTokenResponse(
                valid=True,
                identity=identity_to_proto(identity_dto),
                expires_at=common_pb2.Timestamp(seconds=exp_timestamp, nanos=0),
            )
        except Exception as e:
            self._handle_exception(context, e, "validate_token")
            return auth_pb2.ValidateTokenResponse()

    @_rpc(auth_pb2.ListIdentitiesResponse, USERS_READ)
    def ListIdentities(self, request, context, caller):
        """List the caller tenant's identities with pagination and filters."""
        try:
            db = self.db

            page, per_page, offset = self._page(request.pagination)

            query = db.identities.tenant_id == caller.tenant_id

            # Apply filters
            if request.identity_type != auth_pb2.IdentityType.IDENTITY_TYPE_UNSPECIFIED:
                identity_type_str = (
                    "human"
                    if request.identity_type == auth_pb2.IdentityType.HUMAN
                    else "service_account"
                )
                query &= db.identities.identity_type == identity_type_str

            if request.auth_provider != auth_pb2.AuthProvider.AUTH_PROVIDER_UNSPECIFIED:
                provider_map = {
                    auth_pb2.AuthProvider.LOCAL: "local",
                    auth_pb2.AuthProvider.SAML: "saml",
                    auth_pb2.AuthProvider.OAUTH2: "oauth2",
                    auth_pb2.AuthProvider.LDAP: "ldap",
                }
                auth_provider_str = provider_map.get(request.auth_provider)
                if auth_provider_str:
                    query &= db.identities.auth_provider == auth_provider_str

            if request.active_only:
                query &= db.identities.is_active == True  # noqa: E712 - SQL

            # Apply search filter if provided (from filters map)
            if request.filters and request.filters.filters:
                search_term = request.filters.filters.get("search")
                if search_term:
                    search_pattern = f"%{search_term}%"
                    query &= (
                        (db.identities.username.ilike(search_pattern))
                        | (db.identities.email.ilike(search_pattern))
                        | (db.identities.full_name.ilike(search_pattern))
                    )

            # Get total count
            total = db(query).count()

            # Execute query
            rows = db(query).select(
                db.identities.id,
                db.identities.identity_type,
                db.identities.username,
                db.identities.email,
                db.identities.full_name,
                db.identities.organization_id,
                db.identities.portal_role,
                db.identities.auth_provider,
                db.identities.auth_provider_id,
                db.identities.is_active,
                db.identities.is_superuser,
                db.identities.mfa_enabled,
                db.identities.last_login_at,
                db.identities.created_at,
                db.identities.updated_at,
                orderby=db.identities.username,
                limitby=(offset, offset + per_page),
            )

            # Convert to DTOs and proto messages
            identities = []
            for row in rows:
                identity_dto = from_pydal_row(row, IdentityDTO)
                if identity_dto:
                    identities.append(identity_to_proto(identity_dto))

            # Create response
            return auth_pb2.ListIdentitiesResponse(
                identities=identities,
                pagination=self._create_pagination_response(page, per_page, total),
            )
        except Exception as e:
            self._handle_exception(context, e, "list_identities")
            return auth_pb2.ListIdentitiesResponse()

    @_rpc(auth_pb2.GetIdentityResponse, USERS_READ)
    def GetIdentity(self, request, context, caller):
        """Get an identity of the caller's tenant by ID."""
        try:
            db = self.db
            identity = self._scoped_identity(db, request.id, caller.tenant_id)

            if not identity:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Identity with ID {request.id} not found")
                return auth_pb2.GetIdentityResponse()

            # Convert to DTO and proto
            identity_dto = from_pydal_row(identity, IdentityDTO)
            return auth_pb2.GetIdentityResponse(
                identity=identity_to_proto(identity_dto)
            )
        except Exception as e:
            self._handle_exception(context, e, "get_identity")
            return auth_pb2.GetIdentityResponse()

    @_rpc(auth_pb2.UpdateIdentityResponse, USERS_WRITE)
    def UpdateIdentity(self, request, context, caller):
        """Update an identity of the caller's tenant.

        Granting or revoking superuser is reserved for superuser callers.
        """
        try:
            db = self.db
            identity = self._scoped_identity(db, request.id, caller.tenant_id)

            if not identity:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Identity with ID {request.id} not found")
                return auth_pb2.UpdateIdentityResponse()

            if request.HasField("is_superuser") and not caller.is_superuser:
                context.set_code(grpc.StatusCode.PERMISSION_DENIED)
                context.set_details("only a superuser may change is_superuser")
                return auth_pb2.UpdateIdentityResponse()

            # Build update data dict (only include fields that are set)
            data = {}
            if request.HasField("email"):
                data["email"] = request.email
            if request.HasField("display_name"):
                data["full_name"] = request.display_name
            if request.HasField("is_active"):
                data["is_active"] = request.is_active
            if request.HasField("is_superuser"):
                # The gRPC surface has no caller authentication, so it can
                # never grant or revoke superuser: that would let any caller
                # make itself a global admin. Change it via the REST API as a
                # verified superuser instead.
                logger.warning(
                    "grpc_update_identity_superuser_refused", identity_id=request.id
                )
                context.set_code(grpc.StatusCode.PERMISSION_DENIED)
                context.set_details("is_superuser cannot be changed via gRPC")
                return auth_pb2.UpdateIdentityResponse()

            # Update identity
            if data:
                db(
                    (db.identities.id == request.id)
                    & (db.identities.tenant_id == caller.tenant_id)
                ).update(**data)
                db.commit()

            # Fetch updated identity
            updated_identity = self._scoped_identity(db, request.id, caller.tenant_id)
            identity_dto = from_pydal_row(updated_identity, IdentityDTO)

            return auth_pb2.UpdateIdentityResponse(
                identity=identity_to_proto(identity_dto)
            )
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "update_identity")
            return auth_pb2.UpdateIdentityResponse()

    @_rpc(auth_pb2.DeleteIdentityResponse, USERS_WRITE)
    def DeleteIdentity(self, request, context, caller):
        """Delete an identity of the caller's tenant."""
        try:
            db = self.db
            identity = self._scoped_identity(db, request.id, caller.tenant_id)

            if not identity:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Identity with ID {request.id} not found")
                return auth_pb2.DeleteIdentityResponse()

            db(
                (db.identities.id == request.id)
                & (db.identities.tenant_id == caller.tenant_id)
            ).delete()
            db.commit()

            return auth_pb2.DeleteIdentityResponse(
                status=self._create_status_response(
                    success=True, message=f"Identity {request.id} deleted successfully"
                )
            )
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "delete_identity")
            return auth_pb2.DeleteIdentityResponse(
                status=self._create_status_response(
                    success=False, message=f"Failed to delete identity: {str(e)}"
                )
            )

    # ========================================================================
    # Organization Management (7 methods)
    # ========================================================================

    @_rpc(organization_pb2.ListOrganizationsResponse, ORG_READ)
    def ListOrganizations(self, request, context, caller):
        """List the caller tenant's organizations with pagination and filters."""
        try:
            db = self.db

            page, per_page, offset = self._page(request.pagination)

            query = db.organizations.tenant_id == caller.tenant_id

            # Apply filters
            if request.parent_id > 0:
                query &= db.organizations.parent_id == request.parent_id

            # Apply name filter from FilterOptions if provided
            if request.filters and request.filters.filters:
                name_filter = request.filters.filters.get("name")
                if name_filter:
                    query &= db.organizations.name.ilike(f"%{name_filter}%")

            # Get total count
            total = db(query).count()

            # Execute query with pagination and ordering
            rows = db(query).select(
                orderby=db.organizations.name, limitby=(offset, offset + per_page)
            )

            # Convert to proto messages
            organizations = []
            for row in rows:
                org_dto = from_pydal_row(row, OrganizationDTO)
                if org_dto:
                    organizations.append(organization_to_proto(org_dto))

            # Create response
            return organization_pb2.ListOrganizationsResponse(
                organizations=organizations,
                pagination=self._create_pagination_response(page, per_page, total),
            )
        except Exception as e:
            self._handle_exception(context, e, "list_organizations")
            return organization_pb2.ListOrganizationsResponse()

    @_rpc(organization_pb2.GetOrganizationResponse, ORG_READ)
    def GetOrganization(self, request, context, caller):
        """Get an organization of the caller's tenant by ID."""
        try:
            db = self.db
            org = self._scoped_org(db, request.id, caller.tenant_id)

            if not org:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Organization with ID {request.id} not found")
                return organization_pb2.GetOrganizationResponse()

            # Convert to DTO and proto
            org_dto = from_pydal_row(org, OrganizationDTO)
            return organization_pb2.GetOrganizationResponse(
                organization=organization_to_proto(org_dto)
            )
        except Exception as e:
            self._handle_exception(context, e, "get_organization")
            return organization_pb2.GetOrganizationResponse()

    def _org_refs_error(
        self, db, request, tenant_id: int | None, self_id: int = 0
    ) -> str | None:
        """Validate the rows an organization write references.

        The parent organization and owner identity must belong to the
        caller's tenant. Owner groups are rejected because identity groups
        carry no tenant column, so their ownership cannot be verified.
        Returns an error message, or None when the references are valid.
        """
        if request.parent_id > 0:
            if request.parent_id == self_id or not self._scoped_org(
                db, request.parent_id, tenant_id
            ):
                return f"Parent organization {request.parent_id} not found"
        if request.owner_identity_id > 0 and not self._scoped_identity(
            db, request.owner_identity_id, tenant_id
        ):
            return f"Owner identity {request.owner_identity_id} not found"
        if request.owner_group_id > 0:
            return "owner_group_id is not supported over gRPC"
        return None

    @_rpc(organization_pb2.CreateOrganizationResponse, ORG_WRITE)
    def CreateOrganization(self, request, context, caller):
        """Create an organization in the caller's tenant."""
        try:
            db = self.db

            if not request.name:
                return _invalid(
                    context,
                    "name is required",
                    organization_pb2.CreateOrganizationResponse(),
                )
            error = self._org_refs_error(db, request, caller.tenant_id)
            if error:
                return _invalid(
                    context, error, organization_pb2.CreateOrganizationResponse()
                )

            data = {
                "name": request.name,
                "description": request.description,
                "type": "organization",
                "tenant_id": caller.tenant_id,
            }
            if request.parent_id > 0:
                data["parent_id"] = request.parent_id
            if request.owner_identity_id > 0:
                data["owner_identity_id"] = request.owner_identity_id

            now = datetime.now(UTC)
            data["created_at"] = now
            data["updated_at"] = now
            org_id = db.organizations.insert(**data)
            db.commit()

            org = self._scoped_org(db, org_id, caller.tenant_id)
            org_dto = from_pydal_row(org, OrganizationDTO)

            return organization_pb2.CreateOrganizationResponse(
                organization=organization_to_proto(org_dto)
            )
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "create_organization")
            return organization_pb2.CreateOrganizationResponse()

    @_rpc(organization_pb2.UpdateOrganizationResponse, ORG_WRITE)
    def UpdateOrganization(self, request, context, caller):
        """Update an organization of the caller's tenant."""
        try:
            db = self.db
            org = self._scoped_org(db, request.id, caller.tenant_id)

            if not org:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Organization with ID {request.id} not found")
                return organization_pb2.UpdateOrganizationResponse()

            error = self._org_refs_error(db, request, caller.tenant_id, request.id)
            if error:
                return _invalid(
                    context, error, organization_pb2.UpdateOrganizationResponse()
                )

            # Build update data dict (only include fields that are set)
            data = {}
            if request.name:
                data["name"] = request.name
            if request.description:
                data["description"] = request.description
            if request.parent_id > 0:
                data["parent_id"] = request.parent_id
            if request.owner_identity_id > 0:
                data["owner_identity_id"] = request.owner_identity_id

            if data:
                db(
                    (db.organizations.id == request.id)
                    & (db.organizations.tenant_id == caller.tenant_id)
                ).update(**data)
                db.commit()

            org = self._scoped_org(db, request.id, caller.tenant_id)
            org_dto = from_pydal_row(org, OrganizationDTO)

            return organization_pb2.UpdateOrganizationResponse(
                organization=organization_to_proto(org_dto)
            )
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "update_organization")
            return organization_pb2.UpdateOrganizationResponse()

    @_rpc(organization_pb2.DeleteOrganizationResponse, ORG_WRITE)
    def DeleteOrganization(self, request, context, caller):
        """Delete an organization of the caller's tenant."""
        try:
            db = self.db
            org = self._scoped_org(db, request.id, caller.tenant_id)

            if not org:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Organization with ID {request.id} not found")
                return organization_pb2.DeleteOrganizationResponse()

            # Check if organization has children
            children_count = db(db.organizations.parent_id == request.id).count()
            if children_count > 0:
                context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
                context.set_details(
                    "Cannot delete organization with child organizations"
                )
                return organization_pb2.DeleteOrganizationResponse()

            db(
                (db.organizations.id == request.id)
                & (db.organizations.tenant_id == caller.tenant_id)
            ).delete()
            db.commit()

            return organization_pb2.DeleteOrganizationResponse(
                status=self._create_status_response(
                    success=True,
                    message=f"Organization {request.id} deleted successfully",
                )
            )
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "delete_organization")
            return organization_pb2.DeleteOrganizationResponse()

    @_rpc(organization_pb2.GetOrganizationChildrenResponse, ORG_READ)
    def GetOrganizationChildren(self, request, context, caller):
        """Get the direct children of an organization of the caller's tenant."""
        try:
            db = self.db
            org = self._scoped_org(db, request.id, caller.tenant_id)

            if not org:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Organization with ID {request.id} not found")
                return organization_pb2.GetOrganizationChildrenResponse()

            page, per_page, offset = self._page(request.pagination)

            # Direct children only, and only those in the caller's tenant.
            query = (db.organizations.parent_id == request.id) & (
                db.organizations.tenant_id == caller.tenant_id
            )
            total = db(query).count()

            children = db(query).select(
                orderby=db.organizations.name, limitby=(offset, offset + per_page)
            )

            # Convert to proto messages
            organizations = []
            for child in children:
                org_dto = from_pydal_row(child, OrganizationDTO)
                if org_dto:
                    organizations.append(organization_to_proto(org_dto))

            return organization_pb2.GetOrganizationChildrenResponse(
                children=organizations,
                pagination=self._create_pagination_response(page, per_page, total),
            )
        except Exception as e:
            self._handle_exception(context, e, "get_organization_children")
            return organization_pb2.GetOrganizationChildrenResponse()

    @_rpc(organization_pb2.GetOrganizationHierarchyResponse, ORG_READ)
    def GetOrganizationHierarchy(self, request, context, caller):
        """Get the hierarchy around an organization of the caller's tenant.

        The walk never leaves the caller's tenant, is cycle-safe, and stops
        at ``ORG_MAX_ANCESTORS`` levels up and ``GRAPH_MAX_NODES``
        descendants.
        """
        try:
            db = self.db
            tid = caller.tenant_id
            org = self._scoped_org(db, request.id, tid)

            if not org:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Organization with ID {request.id} not found")
                return organization_pb2.GetOrganizationHierarchyResponse()

            # Find the in-tenant root by walking up the hierarchy
            current = org
            seen = {org.id}
            for _ in range(ORG_MAX_ANCESTORS):
                if not current.parent_id or current.parent_id in seen:
                    break
                parent = self._scoped_org(db, current.parent_id, tid)
                if not parent:
                    break
                seen.add(parent.id)
                current = parent

            root_dto = from_pydal_row(current, OrganizationDTO)
            root_proto = organization_to_proto(root_dto)

            # Breadth-first descendants, one query per level, bounded.
            flattened: list[Any] = []
            visited = {current.id}
            frontier = [current.id]
            while frontier and len(flattened) < GRAPH_MAX_NODES:
                children, _ = _select_capped(
                    db,
                    db.organizations.parent_id.belongs(frontier)
                    & (db.organizations.tenant_id == tid),
                    db.organizations.name,
                    GRAPH_MAX_NODES - len(flattened),
                )
                frontier = []
                for child in children:
                    if child.id in visited:
                        continue
                    visited.add(child.id)
                    frontier.append(child.id)
                    child_dto = from_pydal_row(child, OrganizationDTO)
                    flattened.append(organization_to_proto(child_dto))

            return organization_pb2.GetOrganizationHierarchyResponse(
                root=root_proto,
                flattened_hierarchy=flattened,
            )
        except Exception as e:
            self._handle_exception(context, e, "get_organization_hierarchy")
            return organization_pb2.GetOrganizationHierarchyResponse()

    # ========================================================================
    # Entity Management (7 methods)
    # ========================================================================

    @_rpc(entity_pb2.ListEntitiesResponse, ENTITY_READ)
    def ListEntities(self, request, context, caller):
        """List entities in the caller tenant's organizations."""
        try:
            db = self.db

            page, per_page, offset = self._page(request.pagination)

            # Entities have no tenant column; they are scoped through the
            # tenant's organizations. A foreign organization filter yields
            # an empty list, never another tenant's rows.
            org_ids = _tenant_org_ids(db, caller.tenant_id)
            if request.organization_id > 0:
                org_ids = [o for o in org_ids if o == request.organization_id]
            if not org_ids:
                return entity_pb2.ListEntitiesResponse(
                    entities=[],
                    pagination=self._create_pagination_response(page, per_page, 0),
                )
            query = db.entities.organization_id.belongs(org_ids)

            if request.entity_type != entity_pb2.EntityType.ENTITY_TYPE_UNSPECIFIED:
                entity_type_str = entity_type_from_proto(request.entity_type)
                query &= db.entities.type == entity_type_str

            total = db(query).count()

            # Fetch entities
            rows = db(query).select(
                orderby=db.entities.name,
                limitby=(offset, offset + per_page),
            )

            # Convert to DTOs and proto messages
            entities = []
            for row in rows:
                entity_dto = from_pydal_row(row, EntityDTO)
                entities.append(entity_to_proto(entity_dto))

            # Create pagination response
            pagination = self._create_pagination_response(page, per_page, total)

            return entity_pb2.ListEntitiesResponse(
                entities=entities,
                pagination=pagination,
            )
        except Exception as e:
            self._handle_exception(context, e, "list_entities")
            return entity_pb2.ListEntitiesResponse()

    @_rpc(entity_pb2.GetEntityResponse, ENTITY_READ)
    def GetEntity(self, request, context, caller):
        """Get an entity of the caller's tenant by ID."""
        try:
            db = self.db

            entity = self._scoped_entity(db, request.id, caller.tenant_id)

            if not entity:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Entity with ID {request.id} not found")
                return entity_pb2.GetEntityResponse()

            # Convert to DTO and proto
            entity_dto = from_pydal_row(entity, EntityDTO)
            return entity_pb2.GetEntityResponse(entity=entity_to_proto(entity_dto))
        except Exception as e:
            self._handle_exception(context, e, "get_entity")
            return entity_pb2.GetEntityResponse()

    def _entity_insert_data(self, entity_request) -> dict:
        """Map an entity create request onto the entities table columns."""
        now = datetime.now(UTC)
        return {
            "name": entity_request.name,
            "type": entity_type_from_proto(entity_request.entity_type),
            "organization_id": entity_request.organization_id,
            "tags": [],
            "created_at": now,
            "updated_at": now,
        }

    @_rpc(entity_pb2.CreateEntityResponse, ENTITY_WRITE)
    def CreateEntity(self, request, context, caller):
        """Create an entity in an organization of the caller's tenant."""
        try:
            db = self.db

            if not self._scoped_org(db, request.organization_id, caller.tenant_id):
                return _not_found(
                    context,
                    "Organization",
                    request.organization_id,
                    entity_pb2.CreateEntityResponse(),
                )

            entity_id = db.entities.insert(**self._entity_insert_data(request))
            db.commit()

            entity = self._scoped_entity(db, entity_id, caller.tenant_id)
            entity_dto = from_pydal_row(entity, EntityDTO)

            return entity_pb2.CreateEntityResponse(entity=entity_to_proto(entity_dto))
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "create_entity")
            return entity_pb2.CreateEntityResponse()

    @_rpc(entity_pb2.UpdateEntityResponse, ENTITY_WRITE)
    def UpdateEntity(self, request, context, caller):
        """Update an entity of the caller's tenant.

        Moving the entity is only allowed into another organization of the
        same tenant.
        """
        try:
            db = self.db
            entity = self._scoped_entity(db, request.id, caller.tenant_id)

            if not entity:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Entity with ID {request.id} not found")
                return entity_pb2.UpdateEntityResponse()

            data = {}
            if request.name:
                data["name"] = request.name
            if request.entity_type != entity_pb2.EntityType.ENTITY_TYPE_UNSPECIFIED:
                data["type"] = entity_type_from_proto(request.entity_type)
            if request.organization_id > 0:
                if not self._scoped_org(db, request.organization_id, caller.tenant_id):
                    return _not_found(
                        context,
                        "Organization",
                        request.organization_id,
                        entity_pb2.UpdateEntityResponse(),
                    )
                data["organization_id"] = request.organization_id

            if data:
                data["updated_at"] = datetime.now(UTC)
                db(db.entities.id == entity.id).update(**data)
                db.commit()

            updated_entity = self._scoped_entity(db, request.id, caller.tenant_id)
            entity_dto = from_pydal_row(updated_entity, EntityDTO)

            return entity_pb2.UpdateEntityResponse(entity=entity_to_proto(entity_dto))
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "update_entity")
            return entity_pb2.UpdateEntityResponse()

    @_rpc(entity_pb2.DeleteEntityResponse, ENTITY_WRITE)
    def DeleteEntity(self, request, context, caller):
        """Delete an entity of the caller's tenant."""
        try:
            db = self.db
            entity = self._scoped_entity(db, request.id, caller.tenant_id)

            if not entity:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Entity with ID {request.id} not found")
                return entity_pb2.DeleteEntityResponse()

            db(db.entities.id == entity.id).delete()
            db.commit()

            return entity_pb2.DeleteEntityResponse(
                status=self._create_status_response(
                    success=True, message=f"Entity {request.id} deleted successfully"
                )
            )
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "delete_entity")
            return entity_pb2.DeleteEntityResponse(
                status=self._create_status_response(
                    success=False, message=f"Failed to delete entity: {str(e)}"
                )
            )

    @_rpc(entity_pb2.GetEntityDependenciesResponse, DEP_READ)
    def GetEntityDependencies(self, request, context, caller):
        """Get the caller tenant's dependencies touching one of its entities."""
        try:
            db = self.db
            entity = self._scoped_entity(db, request.id, caller.tenant_id)

            if not entity:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Entity with ID {request.id} not found")
                return entity_pb2.GetEntityDependenciesResponse()

            page, per_page, offset = self._page(request.pagination)

            query = (db.dependencies.tenant_id == caller.tenant_id) & (
                (
                    (db.dependencies.source_type == "entity")
                    & (db.dependencies.source_id == request.id)
                )
                | (
                    (db.dependencies.target_type == "entity")
                    & (db.dependencies.target_id == request.id)
                )
            )
            total = db(query).count()

            rows = db(query).select(
                orderby=~db.dependencies.created_at, limitby=(offset, offset + per_page)
            )

            # Convert to proto messages
            dependencies = []
            for dep in rows:
                dep_dto = from_pydal_row(dep, DependencyDTO)
                if dep_dto:
                    dependencies.append(dependency_to_proto(dep_dto))

            return entity_pb2.GetEntityDependenciesResponse(
                dependencies=dependencies,
                pagination=self._create_pagination_response(page, per_page, total),
            )
        except Exception as e:
            self._handle_exception(context, e, "get_entity_dependencies")
            return entity_pb2.GetEntityDependenciesResponse()

    @_rpc(entity_pb2.BatchCreateEntitiesResponse, ENTITY_WRITE)
    def BatchCreateEntities(self, request, context, caller):
        """Batch create entities in organizations of the caller's tenant.

        At most ``MAX_LIMIT`` items are accepted per call.
        """
        try:
            db = self.db
            created_entities = []
            errors = []

            if len(request.entities) > MAX_LIMIT:
                return _invalid(
                    context,
                    f"at most {MAX_LIMIT} entities per batch",
                    entity_pb2.BatchCreateEntitiesResponse(),
                )
            org_ids = set(_tenant_org_ids(db, caller.tenant_id))

            for i, entity_request in enumerate(request.entities):
                try:
                    if entity_request.organization_id not in org_ids:
                        errors.append(
                            f"Index {i}: Organization {entity_request.organization_id} not found"
                        )
                        continue

                    entity_id = db.entities.insert(
                        **self._entity_insert_data(entity_request)
                    )
                    entity = self._scoped_entity(db, entity_id, caller.tenant_id)
                    entity_dto = from_pydal_row(entity, EntityDTO)
                    created_entities.append(entity_to_proto(entity_dto))

                except Exception as e:
                    errors.append(f"Index {i}: {str(e)}")

            db.commit()

            return entity_pb2.BatchCreateEntitiesResponse(
                entities=created_entities,
                errors=errors,
            )
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "batch_create_entities")
            return entity_pb2.BatchCreateEntitiesResponse()

    # ========================================================================
    # Dependency Management (7 methods)
    # ========================================================================

    @_rpc(dependency_pb2.ListDependenciesResponse, DEP_READ)
    def ListDependencies(self, request, context, caller):
        """List the caller tenant's dependencies with pagination and filters."""
        try:
            db = self.db

            page, per_page, offset = self._page(request.pagination)

            query = db.dependencies.tenant_id == caller.tenant_id

            # Apply filters
            if request.source_entity_id > 0:
                query &= db.dependencies.source_id == request.source_entity_id

            if request.target_entity_id > 0:
                query &= db.dependencies.target_id == request.target_entity_id

            # Get total count
            total = db(query).count()

            # Execute query with pagination and ordering
            rows = db(query).select(
                orderby=~db.dependencies.created_at, limitby=(offset, offset + per_page)
            )

            # Convert to DTOs and proto messages
            dependencies = []
            for row in rows:
                dep_dto = from_pydal_row(row, DependencyDTO)
                if dep_dto:
                    dependencies.append(dependency_to_proto(dep_dto))

            # Create response
            return dependency_pb2.ListDependenciesResponse(
                dependencies=dependencies,
                pagination=self._create_pagination_response(page, per_page, total),
            )
        except Exception as e:
            self._handle_exception(context, e, "list_dependencies")
            return dependency_pb2.ListDependenciesResponse()

    @_rpc(dependency_pb2.GetDependencyResponse, DEP_READ)
    def GetDependency(self, request, context, caller):
        """Get a dependency of the caller's tenant by ID."""
        try:
            db = self.db
            dep = self._scoped_dependency(db, request.id, caller.tenant_id)

            if not dep:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Dependency with ID {request.id} not found")
                return dependency_pb2.GetDependencyResponse()

            # Convert to DTO and proto
            dep_dto = from_pydal_row(dep, DependencyDTO)
            return dependency_pb2.GetDependencyResponse(
                dependency=dependency_to_proto(dep_dto)
            )
        except Exception as e:
            self._handle_exception(context, e, "get_dependency")
            return dependency_pb2.GetDependencyResponse()

    def _dependency_insert_data(
        self, db, dep_request, tenant_id: int | None
    ) -> dict | str:
        """Build a dependency row whose endpoints are both in-tenant.

        Returns the insert data, or an error message when either entity is
        missing or belongs to another tenant (reported as not found).
        """
        for label, eid in (
            ("Source", dep_request.source_entity_id),
            ("Target", dep_request.target_entity_id),
        ):
            if not self._scoped_entity(db, eid, tenant_id):
                return f"{label} entity {eid} not found"
        now = datetime.now(UTC)
        return {
            "tenant_id": tenant_id,
            "source_type": "entity",
            "source_id": dep_request.source_entity_id,
            "target_type": "entity",
            "target_id": dep_request.target_entity_id,
            "dependency_type": "depends_on",
            "metadata": dict(dep_request.metadata) if dep_request.metadata else {},
            "created_at": now,
            "updated_at": now,
        }

    @_rpc(dependency_pb2.CreateDependencyResponse, DEP_WRITE)
    def CreateDependency(self, request, context, caller):
        """Create a dependency between two entities of the caller's tenant."""
        try:
            db = self.db

            # Validate required fields
            if request.source_entity_id <= 0:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("source_entity_id is required")
                return dependency_pb2.CreateDependencyResponse()

            if request.target_entity_id <= 0:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("target_entity_id is required")
                return dependency_pb2.CreateDependencyResponse()

            data = self._dependency_insert_data(db, request, caller.tenant_id)
            if isinstance(data, str):
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(data)
                return dependency_pb2.CreateDependencyResponse()

            dep_id = db.dependencies.insert(**data)
            db.commit()

            dep = self._scoped_dependency(db, dep_id, caller.tenant_id)
            dep_dto = from_pydal_row(dep, DependencyDTO)

            return dependency_pb2.CreateDependencyResponse(
                dependency=dependency_to_proto(dep_dto)
            )
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "create_dependency")
            return dependency_pb2.CreateDependencyResponse()

    @_rpc(dependency_pb2.UpdateDependencyResponse, DEP_WRITE)
    def UpdateDependency(self, request, context, caller):
        """Update a dependency of the caller's tenant."""
        try:
            db = self.db
            dep = self._scoped_dependency(db, request.id, caller.tenant_id)

            if not dep:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Dependency with ID {request.id} not found")
                return dependency_pb2.UpdateDependencyResponse()

            # Build update data dict (only include fields that are set)
            data = {}
            if request.HasField("dependency_type"):
                # Convert proto enum to string
                data["dependency_type"] = "depends_on"  # Map from proto enum

            if request.metadata:
                data["metadata"] = dict(request.metadata)

            if data:
                db(
                    (db.dependencies.id == request.id)
                    & (db.dependencies.tenant_id == caller.tenant_id)
                ).update(**data)
                db.commit()

            dep = self._scoped_dependency(db, request.id, caller.tenant_id)
            dep_dto = from_pydal_row(dep, DependencyDTO)

            return dependency_pb2.UpdateDependencyResponse(
                dependency=dependency_to_proto(dep_dto)
            )
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "update_dependency")
            return dependency_pb2.UpdateDependencyResponse()

    @_rpc(dependency_pb2.DeleteDependencyResponse, DEP_WRITE)
    def DeleteDependency(self, request, context, caller):
        """Delete a dependency of the caller's tenant."""
        try:
            db = self.db
            dep = self._scoped_dependency(db, request.id, caller.tenant_id)

            if not dep:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Dependency with ID {request.id} not found")
                return dependency_pb2.DeleteDependencyResponse()

            db(
                (db.dependencies.id == request.id)
                & (db.dependencies.tenant_id == caller.tenant_id)
            ).delete()
            db.commit()

            return dependency_pb2.DeleteDependencyResponse(
                status=self._create_status_response(
                    success=True,
                    message=f"Dependency {request.id} deleted successfully",
                )
            )
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "delete_dependency")
            return dependency_pb2.DeleteDependencyResponse()

    @_rpc(dependency_pb2.BulkCreateDependenciesResponse, DEP_WRITE)
    def BulkCreateDependencies(self, request, context, caller):
        """Bulk create dependencies between entities of the caller's tenant.

        At most ``MAX_LIMIT`` items are accepted per call.
        """
        try:
            db = self.db
            created_dependencies = []
            errors = []

            if len(request.dependencies) > MAX_LIMIT:
                return _invalid(
                    context,
                    f"at most {MAX_LIMIT} dependencies per batch",
                    dependency_pb2.BulkCreateDependenciesResponse(),
                )

            for i, dep_request in enumerate(request.dependencies):
                try:
                    # Validate required fields
                    if dep_request.source_entity_id <= 0:
                        errors.append(f"Index {i}: source_entity_id is required")
                        continue

                    if dep_request.target_entity_id <= 0:
                        errors.append(f"Index {i}: target_entity_id is required")
                        continue

                    data = self._dependency_insert_data(
                        db, dep_request, caller.tenant_id
                    )
                    if isinstance(data, str):
                        errors.append(f"Index {i}: {data}")
                        continue

                    dep_id = db.dependencies.insert(**data)
                    dep = self._scoped_dependency(db, dep_id, caller.tenant_id)
                    dep_dto = from_pydal_row(dep, DependencyDTO)
                    created_dependencies.append(dependency_to_proto(dep_dto))

                except Exception as e:
                    errors.append(f"Index {i}: {str(e)}")

            db.commit()

            return dependency_pb2.BulkCreateDependenciesResponse(
                dependencies=created_dependencies, errors=errors
            )
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "bulk_create_dependencies")
            return dependency_pb2.BulkCreateDependenciesResponse()

    @_rpc(dependency_pb2.BulkDeleteDependenciesResponse, DEP_WRITE)
    def BulkDeleteDependencies(self, request, context, caller):
        """Bulk delete dependencies of the caller's tenant.

        At most ``MAX_LIMIT`` ids are accepted per call; foreign ids are
        reported exactly like missing ones.
        """
        try:
            db = self.db
            errors = []
            deleted_count = 0

            if len(request.ids) > MAX_LIMIT:
                return _invalid(
                    context,
                    f"at most {MAX_LIMIT} ids per batch",
                    dependency_pb2.BulkDeleteDependenciesResponse(),
                )

            for dep_id in request.ids:
                try:
                    dep = self._scoped_dependency(db, dep_id, caller.tenant_id)
                    if not dep:
                        errors.append(f"Dependency {dep_id} not found")
                        continue

                    db(
                        (db.dependencies.id == dep_id)
                        & (db.dependencies.tenant_id == caller.tenant_id)
                    ).delete()
                    deleted_count += 1

                except Exception as e:
                    errors.append(f"ID {dep_id}: {str(e)}")

            db.commit()

            return dependency_pb2.BulkDeleteDependenciesResponse(
                deleted_count=deleted_count, errors=errors
            )
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "bulk_delete_dependencies")
            return dependency_pb2.BulkDeleteDependenciesResponse()

    # ========================================================================
    # Graph Operations (4 methods)
    # ========================================================================

    def _graph_node(self, entity, properties: dict | None = None):
        """Build a GraphNode for an entity row."""
        return graph_pb2.GraphNode(
            id=entity.id,
            label=entity.name or "",
            type=entity_type_to_proto(entity.type or "compute"),
            properties=properties or {},
        )

    def _graph_edge(self, dep, label: bool = True):
        """Build a GraphEdge for a dependency row."""
        dep_type = dep.dependency_type or "depends_on"
        return graph_pb2.GraphEdge(
            id=dep.id,
            source_id=dep.source_id,
            target_id=dep.target_id,
            type=dependency_type_to_proto(dep_type),
            label=dep_type if label else "",
            properties={},
        )

    @_rpc(graph_pb2.GetDependencyGraphResponse, GRAPH_READ)
    def GetDependencyGraph(self, request, context, caller):
        """Get the dependency graph of an organization or around an entity.

        Scoped to the caller's tenant and bounded by the shared graph depth,
        node and edge caps; ``metadata["truncated"]`` reports a cut-off.
        """
        try:
            db = self.db
            tid = caller.tenant_id

            if request.organization_id > 0:
                if not self._scoped_org(db, request.organization_id, tid):
                    return _not_found(
                        context,
                        "Organization",
                        request.organization_id,
                        graph_pb2.GetDependencyGraphResponse(),
                    )
                entities, edges, truncated = self._tenant_graph(
                    db, tid, request.organization_id
                )
            elif request.entity_id > 0:
                entity = self._scoped_entity(db, request.entity_id, tid)
                if not entity:
                    return _not_found(
                        context,
                        "Entity",
                        request.entity_id,
                        graph_pb2.GetDependencyGraphResponse(),
                    )
                depth, depth_cut = _resolve_depth(request.depth or 2)
                entities, walk_cut = _get_entity_subgraph(
                    db, entity, depth, _tenant_org_ids(db, tid), tid
                )
                edges, edges_cut = _entity_edges(db, [e.id for e in entities])
                edges = [e for e in edges if e.tenant_id in (tid, None)]
                truncated = depth_cut or walk_cut or edges_cut
            else:
                return _invalid(
                    context,
                    "Either organization_id or entity_id must be specified",
                    graph_pb2.GetDependencyGraphResponse(),
                )

            nodes = [self._graph_node(e, {"entity_id": str(e.id)}) for e in entities]
            graph = graph_pb2.DependencyGraph(
                nodes=nodes,
                edges=[self._graph_edge(d) for d in edges],
                metadata={"truncated": str(bool(truncated)).lower()},
            )
            return graph_pb2.GetDependencyGraphResponse(graph=graph)
        except Exception as e:
            self._handle_exception(context, e, "get_dependency_graph")
            return graph_pb2.GetDependencyGraphResponse()

    @_rpc(graph_pb2.AnalyzeGraphResponse, GRAPH_READ)
    def AnalyzeGraph(self, request, context, caller):
        """Analyze the caller tenant's graph for cycles and orphans.

        A foreign organization id yields NOT_FOUND; cycle enumeration stops
        after ``ANALYZE_MAX_CYCLES`` cycles.
        """
        try:
            db = self.db
            tid = caller.tenant_id
            if request.organization_id > 0 and not self._scoped_org(
                db, request.organization_id, tid
            ):
                return _not_found(
                    context,
                    "Organization",
                    request.organization_id,
                    graph_pb2.AnalyzeGraphResponse(),
                )

            entities, dependencies, truncated = self._tenant_graph(
                db, tid, request.organization_id
            )

            results = []

            if not entities:
                return graph_pb2.AnalyzeGraphResponse(
                    results=results,
                    status=self._create_status_response(True, "No entities to analyze"),
                )

            G = nx.DiGraph()
            for entity in entities:
                G.add_node(entity.id, name=entity.name, entity_type=entity.type)
            for dep in dependencies:
                G.add_edge(dep.source_id, dep.target_id)

            cycle_items = []
            for cycle in itertools.islice(nx.simple_cycles(G), ANALYZE_MAX_CYCLES):
                cycle_items.append(
                    graph_pb2.AnalysisItem(
                        item_type="cycle",
                        entity_id=cycle[0] if cycle else 0,
                        severity="warning",
                        description=f"Circular dependency detected: {' -> '.join(str(n) for n in cycle)}",
                        related_entity_ids=list(cycle),
                    )
                )

            if cycle_items:
                results.append(
                    graph_pb2.GraphAnalysisResult(
                        analysis_type="circular_dependencies",
                        items=cycle_items,
                        statistics={"count": str(len(cycle_items))},
                    )
                )

            orphaned_items = []
            for n in G.nodes():
                if G.degree(n) == 0:
                    node_data = G.nodes[n]
                    orphaned_items.append(
                        graph_pb2.AnalysisItem(
                            item_type="orphaned_entity",
                            entity_id=n,
                            entity_name=node_data.get("name") or "",
                            severity="info",
                            description="Entity has no dependencies",
                        )
                    )

            if orphaned_items:
                results.append(
                    graph_pb2.GraphAnalysisResult(
                        analysis_type="orphaned_entities",
                        items=orphaned_items,
                        statistics={"count": str(len(orphaned_items))},
                    )
                )

            results.append(
                graph_pb2.GraphAnalysisResult(
                    analysis_type="statistics",
                    items=[],
                    statistics={
                        "total_nodes": str(len(entities)),
                        "total_edges": str(len(dependencies)),
                        "is_acyclic": str(nx.is_directed_acyclic_graph(G)),
                        "truncated": str(bool(truncated)).lower(),
                    },
                )
            )

            return graph_pb2.AnalyzeGraphResponse(
                results=results,
                status=self._create_status_response(True, "Analysis complete"),
            )
        except Exception as e:
            self._handle_exception(context, e, "analyze_graph")
            return graph_pb2.AnalyzeGraphResponse()

    @_rpc(graph_pb2.FindPathResponse, GRAPH_READ)
    def FindPath(self, request, context, caller):
        """Find a path between two entities of the caller's tenant.

        Only the tenant's own edges are traversed, and paths longer than the
        enforced graph depth are reported as not found.
        """
        try:
            db = self.db
            tid = caller.tenant_id
            source_id = request.source_entity_id
            target_id = request.target_entity_id

            for label, eid in (("Source", source_id), ("Target", target_id)):
                if not self._scoped_entity(db, eid, tid):
                    return _not_found(
                        context, f"{label} entity", eid, graph_pb2.FindPathResponse()
                    )

            max_hops, _ = _resolve_depth(request.max_depth or None)
            entities, dependencies, _ = self._tenant_graph(db, tid)
            entity_map = {e.id: e for e in entities}

            G = nx.DiGraph()
            G.add_nodes_from(entity_map)
            dep_map = {}
            for dep in dependencies:
                G.add_edge(dep.source_id, dep.target_id)
                dep_map[(dep.source_id, dep.target_id)] = dep

            try:
                path = nx.shortest_path(G, source_id, target_id)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                path = []
            if not path or len(path) - 1 > max_hops:
                return graph_pb2.FindPathResponse(
                    paths=[],
                    path_found=False,
                    status=self._create_status_response(True, "No path exists"),
                )

            path_nodes = [self._graph_node(entity_map[eid]) for eid in path]
            path_edges = [
                self._graph_edge(dep_map[(path[i], path[i + 1])], label=False)
                for i in range(len(path) - 1)
            ]
            graph_path = graph_pb2.GraphPath(
                nodes=path_nodes,
                edges=path_edges,
                total_hops=len(path) - 1,
            )
            return graph_pb2.FindPathResponse(
                paths=[graph_path],
                path_found=True,
                status=self._create_status_response(True, "Path found"),
            )
        except Exception as e:
            self._handle_exception(context, e, "find_path")
            return graph_pb2.FindPathResponse()

    @_rpc(graph_pb2.GetEntityImpactResponse, GRAPH_READ)
    def GetEntityImpact(self, request, context, caller):
        """Get the downstream impact of an entity within the caller's tenant.

        Only the tenant's own edges are considered, and the downstream walk
        stops at the enforced graph depth.
        """
        try:
            db = self.db
            tid = caller.tenant_id
            entity = self._scoped_entity(db, request.entity_id, tid)
            if not entity:
                return _not_found(
                    context,
                    "Entity",
                    request.entity_id,
                    graph_pb2.GetEntityImpactResponse(),
                )

            depth, _ = _resolve_depth(request.max_depth or None)
            entities, dependencies, _ = self._tenant_graph(db, tid)
            entity_map = {e.id: e for e in entities}

            G = nx.DiGraph()
            for dep in dependencies:
                G.add_edge(dep.source_id, dep.target_id)

            direct_deps = 0
            dependents = 0
            affected_entity_ids: list[int] = []

            if G.has_node(entity.id):
                direct_deps = G.out_degree(entity.id)
                dependents = G.in_degree(entity.id)
                reach = nx.single_source_shortest_path_length(
                    G, entity.id, cutoff=depth
                )
                affected_entity_ids = sorted(n for n in reach if n != entity.id)
            indirect_deps = len(affected_entity_ids)

            affected_entities = []
            for eid in affected_entity_ids:
                row = entity_map.get(eid)
                entity_dto = from_pydal_row(row, EntityDTO) if row else None
                if entity_dto:
                    affected_entities.append(entity_to_proto(entity_dto))

            impact_score = direct_deps + (indirect_deps * 2) + dependents

            impact = graph_pb2.EntityImpact(
                entity_id=entity.id,
                entity_name=entity.name or "",
                direct_dependencies=direct_deps,
                indirect_dependencies=indirect_deps,
                dependents_count=dependents,
                impact_score=impact_score,
                affected_entities=affected_entities,
            )

            return graph_pb2.GetEntityImpactResponse(
                impact=impact,
                status=self._create_status_response(True, "Impact analysis complete"),
            )
        except Exception as e:
            self._handle_exception(context, e, "get_entity_impact")
            return graph_pb2.GetEntityImpactResponse()

    # ========================================================================
    # Health & Status (1 method)
    # ========================================================================

    def HealthCheck(self, request, context):
        """Health check."""
        try:
            return self._create_status_response(
                success=True,
                message="Elder gRPC server is healthy",
                details={"version": "0.1.0", "service": "elder-grpc"},
            )
        except Exception as e:
            self._handle_exception(context, e, "health_check")
            return self._create_status_response(
                success=False, message=f"Health check failed: {str(e)}"
            )
