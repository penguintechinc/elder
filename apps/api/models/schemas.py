"""Pydantic schemas for API request/response validation."""

import re
from typing import Annotated, Optional

from pydantic import AfterValidator, BaseModel, Field, field_validator

# Custom email validator that allows .local domains for development
# Standard Email rejects .local as it's a special-use TLD, but we need it for dev
EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def validate_email_with_local(value: str) -> str:
    """Validate email format, allowing .local domains for development."""
    if not EMAIL_PATTERN.match(value):
        raise ValueError("value is not a valid email address: Invalid email format")
    return value.lower()  # Normalize to lowercase


# Custom email type that allows .local domains
Email = Annotated[str, AfterValidator(validate_email_with_local)]


# ============================================================================
# Authentication Schemas
# ============================================================================


class RegisterRequest(BaseModel):
    """
    User registration request schema.

    For portal authentication (API/WebUI login), username MUST be a valid email.
    For external system tracking (integrations, scans), username can be any string.
    """

    username: Email = Field(
        ...,
        description="Email address to use as username for portal authentication",
    )
    password: str = Field(
        ..., min_length=8, description="Password (minimum 8 characters)"
    )
    email: Email = Field(
        ..., description="Email address (must match username for portal auth)"
    )
    full_name: str | None = Field(None, max_length=255, description="Full name")

    @field_validator("email")
    @classmethod
    def email_must_match_username(cls, v, info):
        """Ensure email matches username for portal authentication."""
        if "username" in info.data and v != info.data["username"]:
            raise ValueError("Email must match username for portal authentication")
        return v


class LoginRequest(BaseModel):
    """
    User login request schema.

    For portal authentication, username MUST be a valid email address.
    """

    username: Email = Field(..., description="Email address for authentication")
    password: str = Field(..., min_length=1, description="Password")


class PortalRegisterRequest(BaseModel):
    """Portal user registration request schema."""

    email: Email = Field(..., description="Email address")
    password: str = Field(
        ..., min_length=8, description="Password (minimum 8 characters)"
    )
    full_name: str | None = Field(None, max_length=255, description="Full name")
    tenant: str | None = Field(None, description="Tenant slug or ID")


class PortalLoginRequest(BaseModel):
    """Portal user login request schema."""

    email: Email = Field(..., description="Email address")
    password: str = Field(..., min_length=1, description="Password")
    tenant: str | None = Field(None, description="Tenant slug or ID")


class ChangePasswordRequest(BaseModel):
    """Change password request schema."""

    current_password: str = Field(..., min_length=1, description="Current password")
    new_password: str = Field(
        ..., min_length=8, description="New password (minimum 8 characters)"
    )


class TokenRefreshRequest(BaseModel):
    """Token refresh request schema."""

    refresh_token: str = Field(..., description="Refresh token")


# ============================================================================
# Response Schemas
# ============================================================================


class UserResponse(BaseModel):
    """User response schema."""

    id: int
    username: str
    email: str
    full_name: str | None = None


class LoginResponse(BaseModel):
    """Login response schema."""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: UserResponse


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str


class ErrorResponse(BaseModel):
    """Error response schema."""

    error: str
    details: dict | None = None
