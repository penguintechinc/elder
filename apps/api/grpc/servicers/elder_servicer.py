"""Elder gRPC servicer implementation.

NOTE: This servicer is currently a stub. The original implementation assumed SQLAlchemy
ORM models, but Elder uses penguin-dal. The servicer methods need to be rewritten to use
penguin-dal operations via the apps.api.api.v1 services.

For now, all methods return UNIMPLEMENTED status until proper penguin-dal integration is done.
"""

# flake8: noqa: E501


from datetime import datetime, timedelta, timezone
from typing import Optional

import grpc
import jwt
import networkx as nx
import structlog
from werkzeug.security import check_password_hash, generate_password_hash

from apps.api.grpc.converters import (
    dependency_to_proto,
    entity_to_proto,
    entity_type_from_proto,
    identity_to_proto,
    organization_to_proto,
)
from apps.api.grpc.generated import (
    auth_pb2,
    common_pb2,
    dependency_pb2,
    elder_pb2_grpc,
    entity_pb2,
    graph_pb2,
    organization_pb2,
)
from apps.api.models import DependencyDTO, EntityDTO, IdentityDTO, OrganizationDTO
from apps.api.models.dataclasses import from_pydal_row

logger = structlog.get_logger(__name__)


class ElderServicer(elder_pb2_grpc.ElderServiceServicer):
    """Implementation of ElderService gRPC servicer."""

    def __init__(self, app):
        """
        Initialize the servicer.

        Args:
            app: Flask application instance with shared DAL
        """
        super().__init__()

        # Store reference to Flask app for shared database access
        self.app = app

        # JWT configuration
        import os

        self.jwt_secret = os.getenv(
            "JWT_SECRET_KEY", "default-secret-key-change-in-production"
        )
        self.jwt_algorithm = os.getenv("JWT_ALGORITHM", "HS256")
        self.jwt_access_token_expires = timedelta(hours=1)
        self.jwt_refresh_token_expires = timedelta(days=30)

        logger.info("elder_servicer_initialized")

    @property
    def db(self):
        """Get the shared database connection from the Flask app."""
        return self.app.db

    # ========================================================================
    # Helper Methods
    # ========================================================================

    def _create_timestamp(self, dt: Optional[datetime]) -> common_pb2.Timestamp:
        """Convert datetime to protobuf Timestamp."""
        if dt is None:
            return common_pb2.Timestamp(seconds=0, nanos=0)

        timestamp = int(dt.timestamp())
        nanos = dt.microsecond * 1000
        return common_pb2.Timestamp(seconds=timestamp, nanos=nanos)

    def _create_pagination_response(
        self, page: int, per_page: int, total: int
    ) -> common_pb2.PaginationResponse:
        """Create pagination response."""
        pages = (total + per_page - 1) // per_page if per_page > 0 else 0
        return common_pb2.PaginationResponse(
            page=page,
            per_page=per_page,
            total=total,
            pages=pages,
        )

    def _create_status_response(
        self, success: bool, message: str, details: dict = None
    ) -> common_pb2.StatusResponse:
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
        """Generate JWT token for identity."""
        if token_type == "access":
            expires_delta = self.jwt_access_token_expires
        else:
            expires_delta = self.jwt_refresh_token_expires

        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(identity.id),
            "username": identity.username,
            "type": token_type,
            "iat": now,
            "exp": now + expires_delta,
        }

        token = jwt.encode(payload, self.jwt_secret, algorithm=self.jwt_algorithm)
        return token

    def _verify_jwt_token(self, token: str) -> Optional[dict]:
        """Verify and decode JWT token."""
        try:
            payload = jwt.decode(
                token, self.jwt_secret, algorithms=[self.jwt_algorithm]
            )
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("Token expired")
            return None
        except jwt.InvalidTokenError:
            logger.warning("Invalid token")
            return None

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
            db(db.identities.id == identity.id).update(
                last_login_at=datetime.now(timezone.utc)
            )
            db.commit()

            # Refresh identity data
            identity = db.identities[identity.id]

            # Generate tokens
            access_token = self._generate_jwt_token(identity, "access")
            refresh_token = self._generate_jwt_token(identity, "refresh")
            expires_in = int(self.jwt_access_token_expires.total_seconds())

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
            expires_in = int(self.jwt_access_token_expires.total_seconds())

            return auth_pb2.RefreshTokenResponse(
                access_token=access_token,
                token_type="Bearer",
                expires_in=expires_in,
            )
        except Exception as e:
            self._handle_exception(context, e, "refresh_token")
            return auth_pb2.RefreshTokenResponse()

    def Logout(self, request, context):
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

    def GetCurrentIdentity(self, request, context):
        """Get current authenticated identity."""
        try:
            db = self.db

            if not request.access_token:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("access_token is required")
                return auth_pb2.GetCurrentIdentityResponse()

            # Verify token
            payload = self._verify_jwt_token(request.access_token)
            if not payload:
                context.set_code(grpc.StatusCode.UNAUTHENTICATED)
                context.set_details("Invalid or expired token")
                return auth_pb2.GetCurrentIdentityResponse()

            # Get identity
            user_id = int(payload["sub"])
            identity = db.identities[user_id]

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

    def ChangePassword(self, request, context):
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

            # Note: This requires authentication context to get current user
            # For now, return UNIMPLEMENTED - requires auth interceptor
            context.set_code(grpc.StatusCode.UNIMPLEMENTED)
            context.set_details("ChangePassword requires authentication interceptor")
            return auth_pb2.ChangePasswordResponse()
        except Exception as e:
            self._handle_exception(context, e, "change_password")
            return auth_pb2.ChangePasswordResponse()

    def RegisterIdentity(self, request, context):
        """Register new identity."""
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
            now = datetime.now(timezone.utc)
            identity_id = db.identities.insert(
                username=request.username,
                email=request.email,
                full_name=request.display_name,
                identity_type=identity_type,
                auth_provider="local",
                password_hash=generate_password_hash(request.password),
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            # Get created identity
            identity = db.identities[identity_id]
            identity_dto = from_pydal_row(identity, IdentityDTO)

            # Generate tokens
            access_token = self._generate_jwt_token(identity, "access")
            refresh_token = self._generate_jwt_token(identity, "refresh")

            return auth_pb2.RegisterIdentityResponse(
                identity=identity_to_proto(identity_dto),
                access_token=access_token,
                refresh_token=refresh_token,
            )
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "register_identity")
            return auth_pb2.RegisterIdentityResponse()

    def ValidateToken(self, request, context):
        """Validate access token."""
        try:
            db = self.db

            if not request.access_token:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("access_token is required")
                return auth_pb2.ValidateTokenResponse()

            # Verify token
            payload = self._verify_jwt_token(request.access_token)
            if not payload:
                return auth_pb2.ValidateTokenResponse(
                    valid=False,
                    identity=None,
                    expires_at=None,
                )

            # Get identity
            user_id = int(payload["sub"])
            identity = db.identities[user_id]

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

    def ListIdentities(self, request, context):
        """List identities with pagination and filters."""
        try:
            db = self.db

            # Get pagination params
            page = request.pagination.page if request.pagination.page > 0 else 1
            per_page = min(
                request.pagination.per_page if request.pagination.per_page > 0 else 50,
                1000,
            )

            # Build query
            query = db.identities.id > 0

            # Apply filters
            if request.identity_type != auth_pb2.IdentityType.IDENTITY_TYPE_UNSPECIFIED:
                identity_type_str = (
                    "human"
                    if request.identity_type == auth_pb2.IdentityType.HUMAN
                    else "service_account"
                )
                query &= db.identities.type == identity_type_str

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
                query &= db.identities.is_active is True

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

            # Calculate pagination
            offset = (page - 1) * per_page

            # Execute query
            rows = db(query).select(
                db.identities.id,
                db.identities.type,
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

    def GetIdentity(self, request, context):
        """Get identity by ID."""
        try:
            db = self.db
            identity = db.identities[request.id]

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

    def UpdateIdentity(self, request, context):
        """Update identity."""
        try:
            db = self.db
            identity = db.identities[request.id]

            if not identity:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Identity with ID {request.id} not found")
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
                data["is_superuser"] = request.is_superuser

            # Update identity
            if data:
                db(db.identities.id == request.id).update(**data)
                db.commit()

            # Fetch updated identity
            updated_identity = db.identities[request.id]
            identity_dto = from_pydal_row(updated_identity, IdentityDTO)

            return auth_pb2.UpdateIdentityResponse(
                identity=identity_to_proto(identity_dto)
            )
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "update_identity")
            return auth_pb2.UpdateIdentityResponse()

    def DeleteIdentity(self, request, context):
        """Delete identity."""
        try:
            db = self.db
            identity = db.identities[request.id]

            if not identity:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Identity with ID {request.id} not found")
                return auth_pb2.DeleteIdentityResponse()

            # Delete identity
            del db.identities[request.id]
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

    def ListOrganizations(self, request, context):
        """List organizations with pagination and filters."""
        try:
            db = self.db

            # Get pagination params from nested pagination message
            pagination = request.pagination
            page = pagination.page if pagination.page > 0 else 1
            per_page = min(pagination.per_page if pagination.per_page > 0 else 50, 1000)

            # Build PyDAL query
            query = db.organizations.id > 0

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

            # Calculate pagination
            offset = (page - 1) * per_page
            pages = (total + per_page - 1) // per_page

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

    def GetOrganization(self, request, context):
        """Get organization by ID."""
        try:
            db = self.db
            org = db.organizations[request.id]

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

    def CreateOrganization(self, request, context):
        """Create new organization."""
        try:
            db = self.db

            # Build data dict from request
            data = {
                "name": request.name,
                "description": request.description,
                "organization_type": "organization",  # Default type
            }

            # Optional fields
            if request.parent_id > 0:
                data["parent_id"] = request.parent_id
            if request.ldap_dn:
                data["ldap_dn"] = request.ldap_dn
            if request.saml_group:
                data["saml_group"] = request.saml_group
            if request.owner_identity_id > 0:
                data["owner_identity_id"] = request.owner_identity_id
            if request.owner_group_id > 0:
                data["owner_group_id"] = request.owner_group_id

            # Create organization using PyDAL
            now = datetime.now(timezone.utc)
            data["created_at"] = now
            data["updated_at"] = now
            org_id = db.organizations.insert(**data)
            db.commit()

            # Fetch the created organization
            org = db.organizations[org_id]
            org_dto = from_pydal_row(org, OrganizationDTO)

            return organization_pb2.CreateOrganizationResponse(
                organization=organization_to_proto(org_dto)
            )
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "create_organization")
            return organization_pb2.CreateOrganizationResponse()

    def UpdateOrganization(self, request, context):
        """Update existing organization."""
        try:
            db = self.db
            org = db.organizations[request.id]

            if not org:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Organization with ID {request.id} not found")
                return organization_pb2.UpdateOrganizationResponse()

            # Build update data dict (only include fields that are set)
            data = {}
            if request.name:
                data["name"] = request.name
            if request.description:
                data["description"] = request.description
            # organization_type is not in the proto, skip it
            if request.parent_id > 0:
                data["parent_id"] = request.parent_id
            if request.ldap_dn:
                data["ldap_dn"] = request.ldap_dn
            if request.saml_group:
                data["saml_group"] = request.saml_group
            if request.owner_identity_id > 0:
                data["owner_identity_id"] = request.owner_identity_id
            if request.owner_group_id > 0:
                data["owner_group_id"] = request.owner_group_id

            # Update organization using PyDAL
            db(db.organizations.id == request.id).update(**data)
            db.commit()

            # Fetch updated organization
            org = db.organizations[request.id]
            org_dto = from_pydal_row(org, OrganizationDTO)

            return organization_pb2.UpdateOrganizationResponse(
                organization=organization_to_proto(org_dto)
            )
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "update_organization")
            return organization_pb2.UpdateOrganizationResponse()

    def DeleteOrganization(self, request, context):
        """Delete organization."""
        try:
            db = self.db
            org = db.organizations[request.id]

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

            # Delete organization
            del db.organizations[request.id]
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

    def GetOrganizationChildren(self, request, context):
        """Get organization children."""
        try:
            db = self.db
            org = db.organizations[request.id]

            if not org:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Organization with ID {request.id} not found")
                return organization_pb2.GetOrganizationChildrenResponse()

            # Get pagination params from nested pagination message if provided
            pagination = request.pagination
            page = pagination.page if pagination.page > 0 else 1
            per_page = min(pagination.per_page if pagination.per_page > 0 else 50, 1000)

            # Get direct children only (proto doesn't have recursive field)
            query = db.organizations.parent_id == request.id
            total = db(query).count()
            offset = (page - 1) * per_page

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

    def GetOrganizationHierarchy(self, request, context):
        """Get organization hierarchy."""
        try:
            db = self.db
            org = db.organizations[request.id]

            if not org:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Organization with ID {request.id} not found")
                return organization_pb2.GetOrganizationHierarchyResponse()

            # Find the root organization by walking up the hierarchy
            current = org
            while current.parent_id:
                parent = db.organizations[current.parent_id]
                if not parent:
                    break
                current = parent

            # current is now the root
            root_dto = from_pydal_row(current, OrganizationDTO)
            root_proto = organization_to_proto(root_dto)

            # Build flattened hierarchy from root down
            def get_descendants_flat(parent_id):
                """Get all descendants in a flat list, depth-first."""
                children = db(db.organizations.parent_id == parent_id).select(
                    orderby=db.organizations.name
                )
                result = []
                for child in children:
                    child_dto = from_pydal_row(child, OrganizationDTO)
                    result.append(organization_to_proto(child_dto))
                    result.extend(get_descendants_flat(child.id))
                return result

            flattened = get_descendants_flat(current.id)

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

    def ListEntities(self, request, context):
        """List entities with pagination and filters."""
        try:
            db = self.db

            # Build query
            query = db.entities.id > 0

            # Apply filters
            if request.organization_id > 0:
                query &= db.entities.organization_id == request.organization_id

            if request.entity_type != entity_pb2.EntityType.ENTITY_TYPE_UNSPECIFIED:
                entity_type_str = entity_type_from_proto(request.entity_type)
                query &= db.entities.entity_type == entity_type_str

            # Get total count
            total = db(query).count()

            # Apply pagination from nested pagination message
            pagination = request.pagination
            page = pagination.page if pagination.page > 0 else 1
            per_page = pagination.per_page if pagination.per_page > 0 else 50
            offset = (page - 1) * per_page

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

    def GetEntity(self, request, context):
        """Get entity by ID or unique_id."""
        try:
            db = self.db

            # Fetch entity by ID
            entity = db.entities[request.id]

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

    def CreateEntity(self, request, context):
        """Create new entity."""
        try:
            db = self.db

            # Build data dict from request
            entity_type_str = entity_type_from_proto(request.entity_type)
            data = {
                "name": request.name,
                "description": request.description,
                "entity_type": entity_type_str,
                "organization_id": request.organization_id,
                "attributes": {},
                "tags": [],
                "is_active": True,
            }

            # Get tenant_id from organization
            org = db.organizations[request.organization_id]
            if org:
                data["tenant_id"] = org.tenant_id

            # Create entity using PyDAL
            now = datetime.now(timezone.utc)
            data["created_at"] = now
            data["updated_at"] = now
            entity_id = db.entities.insert(**data)
            db.commit()

            # Fetch the created entity
            entity = db.entities[entity_id]
            entity_dto = from_pydal_row(entity, EntityDTO)

            return entity_pb2.CreateEntityResponse(entity=entity_to_proto(entity_dto))
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "create_entity")
            return entity_pb2.CreateEntityResponse()

    def UpdateEntity(self, request, context):
        """Update existing entity."""
        try:
            db = self.db
            entity = db.entities[request.id]

            if not entity:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Entity with ID {request.id} not found")
                return entity_pb2.UpdateEntityResponse()

            # Build update data dict (only include fields that are set)
            data = {}
            if request.name:
                data["name"] = request.name
            if request.description:
                data["description"] = request.description
            if request.entity_type != entity_pb2.EntityType.ENTITY_TYPE_UNSPECIFIED:
                data["entity_type"] = entity_type_from_proto(request.entity_type)
            if request.organization_id > 0:
                data["organization_id"] = request.organization_id
                # Update tenant_id when organization changes
                org = db.organizations[request.organization_id]
                if org:
                    data["tenant_id"] = org.tenant_id

            # Update entity
            db(db.entities.id == request.id).update(**data)
            db.commit()

            # Fetch updated entity
            updated_entity = db.entities[request.id]
            entity_dto = from_pydal_row(updated_entity, EntityDTO)

            return entity_pb2.UpdateEntityResponse(entity=entity_to_proto(entity_dto))
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "update_entity")
            return entity_pb2.UpdateEntityResponse()

    def DeleteEntity(self, request, context):
        """Delete entity."""
        try:
            db = self.db
            entity = db.entities[request.id]

            if not entity:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Entity with ID {request.id} not found")
                return entity_pb2.DeleteEntityResponse()

            # Delete entity
            del db.entities[request.id]
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

    def GetEntityDependencies(self, request, context):
        """Get entity dependencies."""
        try:
            db = self.db
            entity = db.entities[request.id]

            if not entity:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Entity with ID {request.id} not found")
                return entity_pb2.GetEntityDependenciesResponse()

            # Get pagination params from nested pagination message if provided
            pagination = request.pagination
            page = pagination.page if pagination.page > 0 else 1
            per_page = min(pagination.per_page if pagination.per_page > 0 else 50, 1000)

            # Get all dependencies where this entity is source OR target
            query = (db.dependencies.source_id == request.id) | (
                db.dependencies.target_id == request.id
            )
            total = db(query).count()
            offset = (page - 1) * per_page

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

    def BatchCreateEntities(self, request, context):
        """Batch create entities."""
        try:
            db = self.db
            created_entities = []
            errors = []

            for i, entity_request in enumerate(request.entities):
                try:
                    # Build data dict from request
                    entity_type_str = entity_type_from_proto(entity_request.entity_type)
                    data = {
                        "name": entity_request.name,
                        "description": entity_request.description,
                        "entity_type": entity_type_str,
                        "organization_id": entity_request.organization_id,
                        "attributes": {},
                        "tags": [],
                        "is_active": True,
                    }

                    # Get tenant_id from organization
                    org = db.organizations[entity_request.organization_id]
                    if not org:
                        errors.append(
                            f"Index {i}: Organization {entity_request.organization_id} not found"
                        )
                        continue

                    data["tenant_id"] = org.tenant_id

                    # Create entity
                    now = datetime.now(timezone.utc)
                    data["created_at"] = now
                    data["updated_at"] = now
                    entity_id = db.entities.insert(**data)
                    entity = db.entities[entity_id]
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

    def ListDependencies(self, request, context):
        """List dependencies with pagination and filters."""
        try:
            db = self.db

            # Get pagination params
            page = request.pagination.page if request.pagination.page > 0 else 1
            per_page = min(
                request.pagination.per_page if request.pagination.per_page > 0 else 50,
                1000,
            )

            # Build PyDAL query
            query = db.dependencies.id > 0

            # Apply filters
            if request.source_entity_id > 0:
                query &= db.dependencies.source_id == request.source_entity_id

            if request.target_entity_id > 0:
                query &= db.dependencies.target_id == request.target_entity_id

            # Get total count
            total = db(query).count()

            # Calculate pagination
            offset = (page - 1) * per_page

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

    def GetDependency(self, request, context):
        """Get dependency by ID."""
        try:
            db = self.db
            dep = db.dependencies[request.id]

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

    def CreateDependency(self, request, context):
        """Create new dependency."""
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

            # Build data dict from request
            data = {
                "source_type": "entity",  # Default to entity type
                "source_id": request.source_entity_id,
                "target_type": "entity",
                "target_id": request.target_entity_id,
                "dependency_type": "depends_on",  # Default type
                "metadata": dict(request.metadata) if request.metadata else {},
            }

            # Get tenant_id from source entity's organization
            source_entity = db.entities[request.source_entity_id]
            if not source_entity:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(
                    f"Source entity {request.source_entity_id} not found"
                )
                return dependency_pb2.CreateDependencyResponse()

            # Get tenant_id from the entity's organization
            org = db.organizations[source_entity.organization_id]
            if org:
                data["tenant_id"] = org.tenant_id
            else:
                data["tenant_id"] = 1  # Default tenant

            # Create dependency using PyDAL
            now = datetime.now(timezone.utc)
            data["created_at"] = now
            data["updated_at"] = now
            dep_id = db.dependencies.insert(**data)
            db.commit()

            # Fetch the created dependency
            dep = db.dependencies[dep_id]
            dep_dto = from_pydal_row(dep, DependencyDTO)

            return dependency_pb2.CreateDependencyResponse(
                dependency=dependency_to_proto(dep_dto)
            )
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "create_dependency")
            return dependency_pb2.CreateDependencyResponse()

    def UpdateDependency(self, request, context):
        """Update existing dependency."""
        try:
            db = self.db
            dep = db.dependencies[request.id]

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

            # Update dependency using PyDAL
            if data:
                db(db.dependencies.id == request.id).update(**data)
                db.commit()

            # Fetch updated dependency
            dep = db.dependencies[request.id]
            dep_dto = from_pydal_row(dep, DependencyDTO)

            return dependency_pb2.UpdateDependencyResponse(
                dependency=dependency_to_proto(dep_dto)
            )
        except Exception as e:
            db.rollback()
            self._handle_exception(context, e, "update_dependency")
            return dependency_pb2.UpdateDependencyResponse()

    def DeleteDependency(self, request, context):
        """Delete dependency."""
        try:
            db = self.db
            dep = db.dependencies[request.id]

            if not dep:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Dependency with ID {request.id} not found")
                return dependency_pb2.DeleteDependencyResponse()

            # Delete dependency
            del db.dependencies[request.id]
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

    def BulkCreateDependencies(self, request, context):
        """Bulk create dependencies."""
        try:
            db = self.db
            created_dependencies = []
            errors = []

            for i, dep_request in enumerate(request.dependencies):
                try:
                    # Validate required fields
                    if dep_request.source_entity_id <= 0:
                        errors.append(f"Index {i}: source_entity_id is required")
                        continue

                    if dep_request.target_entity_id <= 0:
                        errors.append(f"Index {i}: target_entity_id is required")
                        continue

                    # Build data dict
                    data = {
                        "source_type": "entity",
                        "source_id": dep_request.source_entity_id,
                        "target_type": "entity",
                        "target_id": dep_request.target_entity_id,
                        "dependency_type": "depends_on",
                        "metadata": (
                            dict(dep_request.metadata) if dep_request.metadata else {}
                        ),
                    }

                    # Get tenant_id from source entity
                    source_entity = db.entities[dep_request.source_entity_id]
                    if not source_entity:
                        errors.append(
                            f"Index {i}: Source entity {dep_request.source_entity_id} not found"
                        )
                        continue

                    data["tenant_id"] = source_entity.tenant_id

                    # Create dependency
                    now = datetime.now(timezone.utc)
                    data["created_at"] = now
                    data["updated_at"] = now
                    dep_id = db.dependencies.insert(**data)
                    dep = db.dependencies[dep_id]
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

    def BulkDeleteDependencies(self, request, context):
        """Bulk delete dependencies."""
        try:
            db = self.db
            errors = []
            deleted_count = 0

            for dep_id in request.ids:
                try:
                    dep = db.dependencies[dep_id]
                    if not dep:
                        errors.append(f"Dependency {dep_id} not found")
                        continue

                    del db.dependencies[dep_id]
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

    def GetDependencyGraph(self, request, context):
        """Get dependency graph for organization or entity."""
        try:
            db = self.db

            # Determine scope
            if request.organization_id > 0:
                org = db.organizations[request.organization_id]
                if not org:
                    context.set_code(grpc.StatusCode.NOT_FOUND)
                    context.set_details(
                        f"Organization with ID {request.organization_id} not found"
                    )
                    return graph_pb2.GetDependencyGraphResponse()
                entities = db(
                    db.entities.organization_id == request.organization_id
                ).select()
            elif request.entity_id > 0:
                entity = db.entities[request.entity_id]
                if not entity:
                    context.set_code(grpc.StatusCode.NOT_FOUND)
                    context.set_details(f"Entity with ID {request.entity_id} not found")
                    return graph_pb2.GetDependencyGraphResponse()
                entities = self._get_entity_subgraph(
                    db, entity, request.depth if request.depth > 0 else 2
                )
            else:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details(
                    "Either organization_id or entity_id must be specified"
                )
                return graph_pb2.GetDependencyGraphResponse()

            entity_ids = [e.id for e in entities]
            dependencies = db(
                (db.dependencies.source_type == "entity")
                & (db.dependencies.source_id.belongs(entity_ids))
                & (db.dependencies.target_type == "entity")
                & (db.dependencies.target_id.belongs(entity_ids))
            ).select()

            # Build GraphNode list
            from apps.api.grpc.converters import (
                dependency_type_to_proto,
                entity_type_to_proto,
            )

            nodes = []
            for entity in entities:
                nodes.append(
                    graph_pb2.GraphNode(
                        id=entity.id,
                        label=entity.name,
                        type=entity_type_to_proto(entity.entity_type or "compute"),
                        properties={"entity_id": str(entity.id)},
                    )
                )

            # Build GraphEdge list
            edges = []
            for dep in dependencies:
                edges.append(
                    graph_pb2.GraphEdge(
                        id=dep.id,
                        source_id=dep.source_id,
                        target_id=dep.target_id,
                        type=dependency_type_to_proto(
                            dep.dependency_type or "depends_on"
                        ),
                        label=dep.dependency_type or "depends_on",
                        properties={},
                    )
                )

            # Return wrapped in DependencyGraph
            graph = graph_pb2.DependencyGraph(nodes=nodes, edges=edges, metadata={})
            return graph_pb2.GetDependencyGraphResponse(graph=graph)
        except Exception as e:
            self._handle_exception(context, e, "get_dependency_graph")
            return graph_pb2.GetDependencyGraphResponse()

    def AnalyzeGraph(self, request, context):
        """Analyze graph for issues."""
        try:
            db = self.db
            query = db.entities.id > 0
            if request.organization_id > 0:
                query &= db.entities.organization_id == request.organization_id

            entities = db(query).select()
            entity_ids = [e.id for e in entities]

            results = []

            if not entity_ids:
                return graph_pb2.AnalyzeGraphResponse(
                    results=results,
                    status=self._create_status_response(True, "No entities to analyze"),
                )

            dependencies = db(
                (db.dependencies.source_type == "entity")
                & (db.dependencies.source_id.belongs(entity_ids))
                & (db.dependencies.target_type == "entity")
                & (db.dependencies.target_id.belongs(entity_ids))
            ).select()

            # Build networkx graph
            G = nx.DiGraph()
            for entity in entities:
                G.add_node(entity.id, name=entity.name, entity_type=entity.entity_type)
            for dep in dependencies:
                G.add_edge(dep.source_id, dep.target_id)

            # Analyze for cycles
            cycle_items = []
            try:
                simple_cycles = list(nx.simple_cycles(G))
                for cycle in simple_cycles[:10]:
                    cycle_items.append(
                        graph_pb2.AnalysisItem(
                            item_type="cycle",
                            entity_id=cycle[0] if cycle else 0,
                            severity="warning",
                            description=f"Circular dependency detected: {' -> '.join(str(n) for n in cycle)}",
                            related_entity_ids=list(cycle),
                        )
                    )
            except Exception:
                pass

            if cycle_items:
                results.append(
                    graph_pb2.GraphAnalysisResult(
                        analysis_type="circular_dependencies",
                        items=cycle_items,
                        statistics={"count": str(len(cycle_items))},
                    )
                )

            # Analyze for orphaned nodes
            orphaned_items = []
            for n in G.nodes():
                if G.degree(n) == 0:
                    node_data = G.nodes[n]
                    orphaned_items.append(
                        graph_pb2.AnalysisItem(
                            item_type="orphaned_entity",
                            entity_id=n,
                            entity_name=node_data.get("name", ""),
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

            # Add statistics result
            results.append(
                graph_pb2.GraphAnalysisResult(
                    analysis_type="statistics",
                    items=[],
                    statistics={
                        "total_nodes": str(len(entities)),
                        "total_edges": str(len(dependencies)),
                        "is_acyclic": str(nx.is_directed_acyclic_graph(G)),
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

    def FindPath(self, request, context):
        """Find path between two entities."""
        try:
            db = self.db
            from apps.api.grpc.converters import (
                dependency_type_to_proto,
                entity_type_to_proto,
            )

            # Use correct field names from proto
            source_id = request.source_entity_id
            target_id = request.target_entity_id

            from_entity = db.entities[source_id]
            to_entity = db.entities[target_id]

            if not from_entity:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Source entity {source_id} not found")
                return graph_pb2.FindPathResponse()

            if not to_entity:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Target entity {target_id} not found")
                return graph_pb2.FindPathResponse()

            dependencies = db(
                (db.dependencies.source_type == "entity")
                & (db.dependencies.target_type == "entity")
            ).select()

            G = nx.DiGraph()
            dep_map = {}  # Map (source, target) to dependency
            for dep in dependencies:
                G.add_edge(dep.source_id, dep.target_id)
                dep_map[(dep.source_id, dep.target_id)] = dep

            try:
                path = nx.shortest_path(G, source_id, target_id)
                entities_in_path = db(db.entities.id.belongs(path)).select()
                entity_map = {e.id: e for e in entities_in_path}

                # Build GraphPath with nodes and edges
                path_nodes = []
                for eid in path:
                    entity = entity_map.get(eid)
                    if entity:
                        path_nodes.append(
                            graph_pb2.GraphNode(
                                id=eid,
                                label=entity.name,
                                type=entity_type_to_proto(
                                    entity.entity_type or "compute"
                                ),
                                properties={},
                            )
                        )

                path_edges = []
                for i in range(len(path) - 1):
                    dep = dep_map.get((path[i], path[i + 1]))
                    if dep:
                        path_edges.append(
                            graph_pb2.GraphEdge(
                                id=dep.id,
                                source_id=dep.source_id,
                                target_id=dep.target_id,
                                type=dependency_type_to_proto(
                                    dep.dependency_type or "depends_on"
                                ),
                                properties={},
                            )
                        )

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
            except nx.NetworkXNoPath:
                return graph_pb2.FindPathResponse(
                    paths=[],
                    path_found=False,
                    status=self._create_status_response(True, "No path exists"),
                )
        except Exception as e:
            self._handle_exception(context, e, "find_path")
            return graph_pb2.FindPathResponse()

    def GetEntityImpact(self, request, context):
        """Get entity impact analysis."""
        try:
            db = self.db
            entity = db.entities[request.entity_id]
            if not entity:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Entity {request.entity_id} not found")
                return graph_pb2.GetEntityImpactResponse()

            dependencies = db(
                (db.dependencies.source_type == "entity")
                & (db.dependencies.target_type == "entity")
            ).select()

            G = nx.DiGraph()
            for dep in dependencies:
                G.add_edge(dep.source_id, dep.target_id)

            # Calculate impact metrics
            direct_deps = 0
            indirect_deps = 0
            dependents = 0
            affected_entity_ids = []

            if G.has_node(request.entity_id):
                try:
                    # Direct dependencies (entities this one depends on)
                    direct_deps = G.out_degree(request.entity_id)
                    # Dependents (entities that depend on this one)
                    dependents = G.in_degree(request.entity_id)
                    # All downstream affected entities
                    downstream = nx.descendants(G, request.entity_id)
                    indirect_deps = len(downstream)
                    affected_entity_ids = list(downstream)
                except Exception:
                    pass

            # Get affected entity details
            affected_entities = []
            if affected_entity_ids:
                affected_rows = db(db.entities.id.belongs(affected_entity_ids)).select()
                for row in affected_rows:
                    entity_dto = from_pydal_row(row, EntityDTO)
                    if entity_dto:
                        affected_entities.append(entity_to_proto(entity_dto))

            # Calculate simple impact score
            impact_score = direct_deps + (indirect_deps * 2) + dependents

            # Build EntityImpact message
            impact = graph_pb2.EntityImpact(
                entity_id=request.entity_id,
                entity_name=entity.name,
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

    def _get_entity_subgraph(self, db, entity, depth: int):
        """Get entities within depth distance from given entity."""
        if depth <= 0:
            depth = 2
        if depth > 10:
            depth = 10

        visited = {entity.id}
        current_level = [entity]
        all_entities = [entity]

        for _ in range(depth):
            if not current_level:
                break

            next_level = []

            for e in current_level:
                # Get outgoing dependencies
                outgoing = db(
                    (db.dependencies.source_type == "entity")
                    & (db.dependencies.source_id == e.id)
                ).select()
                for dep in outgoing:
                    if dep.target_id not in visited:
                        visited.add(dep.target_id)
                        target = db.entities[dep.target_id]
                        if target:
                            next_level.append(target)
                            all_entities.append(target)

                # Get incoming dependencies
                incoming = db(
                    (db.dependencies.target_type == "entity")
                    & (db.dependencies.target_id == e.id)
                ).select()
                for dep in incoming:
                    if dep.source_id not in visited:
                        visited.add(dep.source_id)
                        source = db.entities[dep.source_id]
                        if source:
                            next_level.append(source)
                            all_entities.append(source)

            current_level = next_level

        return all_entities
