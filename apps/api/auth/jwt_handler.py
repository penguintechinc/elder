"""JWT token handling for Elder authentication using PyDAL."""

# flake8: noqa: E501


import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import jwt
from flask import current_app, g, request
from penguin_dal import Row
from werkzeug.security import check_password_hash

logger = logging.getLogger(__name__)


def generate_token(identity: Row, token_type: str = "access") -> str:
    """
    Generate JWT token for an identity.

    Args:
        identity: Identity model instance
        token_type: 'access' or 'refresh'

    Returns:
        JWT token string
    """
    if token_type == "access":
        expires_delta = current_app.config["JWT_ACCESS_TOKEN_EXPIRES"]
    else:
        expires_delta = current_app.config["JWT_REFRESH_TOKEN_EXPIRES"]

    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(identity.id),  # JWT spec requires sub to be a string
        "username": identity.username,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }

    secret = current_app.config["JWT_SECRET_KEY"] or current_app.config["SECRET_KEY"]
    algorithm = current_app.config["JWT_ALGORITHM"]

    token = jwt.encode(payload, secret, algorithm=algorithm)
    return token


def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Verify and decode JWT token.

    Args:
        token: JWT token string

    Returns:
        Decoded payload dict or None if invalid
    """
    try:
        secret = (
            current_app.config["JWT_SECRET_KEY"] or current_app.config["SECRET_KEY"]
        )
        algorithm = current_app.config["JWT_ALGORITHM"]

        logger.debug(f"Verifying token with algorithm {algorithm}")
        logger.debug(f"Token (first 20 chars): {token[:20]}...")
        logger.debug(
            f"Using secret key: {secret[:10]}..." if secret else "No secret key!"
        )

        payload = jwt.decode(token, secret, algorithms=[algorithm])
        logger.debug(f"Token verified successfully. Payload: {payload}")
        return payload
    except jwt.ExpiredSignatureError as e:
        logger.warning(f"Token expired: {e}")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token: {e}")
        return None


def get_token_from_header() -> Optional[str]:
    """
    Extract JWT token from Authorization header.

    Returns:
        Token string or None
    """
    auth_header = request.headers.get("Authorization")

    if not auth_header:
        logger.debug("No Authorization header found")
        return None

    parts = auth_header.split()

    if len(parts) != 2 or parts[0].lower() != "bearer":
        logger.debug(f"Invalid Authorization header format: {auth_header}")
        return None

    token = parts[1]
    logger.debug(f"Extracted token from header (first 20 chars): {token[:20]}...")
    return token


def get_current_user() -> Optional[Row]:
    """
    Get current authenticated user from request context.

    Supports both identity tokens (API/service auth) and portal_user tokens (web UI auth).
    For portal_user tokens, creates a synthetic identity-like object for compatibility.

    Returns:
        PyDAL Row representing identity or None
    """
    logger.debug("=== get_current_user called ===")

    if hasattr(g, "current_user"):
        logger.debug(f"Returning cached user: {g.current_user.username}")
        return g.current_user

    token = get_token_from_header()
    if not token:
        logger.debug("No token found in header, returning None")
        return None

    payload = verify_token(token)
    if not payload:
        logger.debug("Token verification failed, returning None")
        return None

    logger.debug(
        f"Token verified, looking up user with ID: {payload.get('sub')}, type: {payload.get('type')}"
    )
    db = current_app.db

    try:
        user_id = int(payload["sub"])  # Convert string back to integer
        token_type = payload.get("type", "access")

        # Handle portal_user tokens (from web UI login)
        if token_type == "portal_user":
            portal_user = db.portal_users[user_id]
            if not portal_user:
                logger.warning(f"Portal user not found with ID: {user_id}")
                return None

            # Check is_active - use truthy check like the portal_auth service
            if not portal_user.is_active:
                logger.warning(f"Portal user {portal_user.email} is not active")
                return None

            # Create a synthetic identity-like object from portal_user for API compatibility
            # This allows portal users to access APIs without a linked identity
            class PortalUserIdentity:
                def __init__(self, pu):
                    self.id = pu.id
                    self.username = (
                        pu.email.split("@")[0] if pu.email else f"user_{pu.id}"
                    )
                    self.email = pu.email
                    self.display_name = pu.full_name or self.username
                    self.is_active = bool(pu.is_active)
                    self.is_superuser = pu.global_role == "admin"
                    self.tenant_id = pu.tenant_id
                    self.portal_role = pu.global_role or "observer"
                    self._portal_user = pu

                def get(self, key, default=None):
                    return getattr(self, key, default)

            synthetic_identity = PortalUserIdentity(portal_user)
            logger.debug(
                f"Created synthetic identity for portal user: {synthetic_identity.email}"
            )
            g.current_user = synthetic_identity
            return synthetic_identity

        # Handle regular identity tokens
        identity = db.identities[user_id]

        if not identity:
            logger.warning(f"User not found in database with ID: {payload.get('sub')}")
            return None

        if not identity.is_active:
            logger.warning(f"User {identity.username} is not active")
            return None

        logger.debug(f"Authentication successful for user: {identity.username}")
        g.current_user = identity
        return identity
    except Exception as e:
        import traceback

        logger.error(f"Error looking up user: {e} - {traceback.format_exc()}")
        # Rollback transaction on error to prevent transaction state issues
        try:
            db.rollback()
        except Exception as rollback_error:
            logger.error(f"Error rolling back transaction: {rollback_error}")
        return None


def verify_password(identity: Row, password: str) -> bool:
    """
    Verify password for local authentication.

    Args:
        identity: PyDAL Row representing identity
        password: Plain text password

    Returns:
        True if password is correct
    """
    if not identity.password_hash:
        return False

    return check_password_hash(identity.password_hash, password)
