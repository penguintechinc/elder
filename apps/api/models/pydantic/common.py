"""
Pydantic 2 common models for Elder applications.

Provides shared domain models for pagination, bulk operations, and error handling
with proper validation, immutability for responses, and generic type support.
"""

# flake8: noqa: E501


from typing import Generic, Literal, Optional, TypeVar

from penguin_libs.pydantic.base import ImmutableModel, RequestModel
from pydantic import Field, field_validator

# Generic type variable for paginated items
T = TypeVar("T")

# Sort order enumeration
SortOrder = Literal["asc", "desc"]


class PaginationParams(RequestModel):
    """
    Request model for pagination parameters.

    Provides standard pagination fields with validation for page numbers,
    items per page, and sorting options.

    Fields:
        page: Page number (1-indexed), default 1
        per_page: Items per page, default 20, max 100
        sort_by: Field name to sort by, optional
        sort_order: Sort order ('asc' or 'desc'), default 'asc'
    """

    page: int = Field(default=1, ge=1, description="Page number (1-indexed)")
    per_page: int = Field(
        default=20, ge=1, le=100, description="Items per page (1-100)"
    )
    sort_by: Optional[str] = Field(None, max_length=255, description="Field to sort by")
    sort_order: SortOrder = Field(
        default="asc", description="Sort order: 'asc' or 'desc'"
    )

    @field_validator("sort_by")
    @classmethod
    def sort_by_not_empty(cls, v: Optional[str]) -> Optional[str]:
        """Ensure sort_by is not just whitespace if provided."""
        if v is not None and v.strip() == "":
            raise ValueError("sort_by cannot be empty or whitespace-only")
        return v.strip() if v else None


class PaginatedResponse(ImmutableModel, Generic[T]):
    """
    Generic immutable paginated response wrapper.

    Provides standard pagination metadata with a list of items of any type.
    Uses Pydantic 2 generics for type-safe item lists.

    Type Parameters:
        T: Type of items in the paginated list

    Fields:
        items: List of paginated items
        total: Total number of items across all pages
        page: Current page number (1-indexed)
        per_page: Items per page
        pages: Total number of pages

    Example:
        >>> from datetime import datetime
        >>> class UserDTO(ImmutableModel):
        ...     id: int
        ...     name: str
        >>> response = PaginatedResponse[UserDTO](
        ...     items=[UserDTO(id=1, name="Alice")],
        ...     total=100,
        ...     page=1,
        ...     per_page=20,
        ...     pages=5
        ... )
    """

    items: list[T] = Field(description="List of paginated items")
    total: int = Field(ge=0, description="Total number of items")
    page: int = Field(ge=1, description="Current page number (1-indexed)")
    per_page: int = Field(ge=1, le=100, description="Items per page")
    pages: int = Field(ge=0, description="Total number of pages")

    @field_validator("pages")
    @classmethod
    def validate_pages(cls, v: int, info) -> int:
        """Ensure pages count matches total and per_page."""
        total = info.data.get("total", 0)
        per_page = info.data.get("per_page", 1)

        expected_pages = (total + per_page - 1) // per_page if total > 0 else 0
        if v != expected_pages:
            raise ValueError(
                f"pages={v} does not match calculated pages={expected_pages} "
                f"from total={total} and per_page={per_page}"
            )
        return v


class BulkOperationResult(ImmutableModel):
    """
    Immutable result model for bulk operations.

    Provides summary of bulk operation outcomes including success/failure counts
    and detailed error information for failed items.

    Fields:
        succeeded: Number of successfully processed items
        failed: Number of failed items
        errors: Optional list of error details (one per failed item)

    Example:
        >>> result = BulkOperationResult(
        ...     succeeded=95,
        ...     failed=5,
        ...     errors=[
        ...         {"index": 0, "message": "Validation failed"},
        ...         {"index": 45, "message": "Duplicate key"}
        ...     ]
        ... )
    """

    succeeded: int = Field(ge=0, description="Number of successfully processed items")
    failed: int = Field(ge=0, description="Number of failed items")
    errors: Optional[list[dict]] = Field(
        None, description="List of error details (one per failed item)"
    )

    @field_validator("errors")
    @classmethod
    def errors_matches_failed_count(cls, v: Optional[list], info) -> Optional[list]:
        """Ensure errors list length matches failed count."""
        failed = info.data.get("failed", 0)

        if v is None:
            if failed > 0:
                raise ValueError("failed > 0 but errors is None")
            return None

        if len(v) != failed:
            raise ValueError(f"len(errors)={len(v)} does not match failed={failed}")
        return v


class ErrorResponse(ImmutableModel):
    """
    Immutable error response model.

    Provides standardized error information for API error responses including
    a high-level error code, descriptive message, and optional detailed context.

    Fields:
        error: Error code or type (e.g., 'VALIDATION_ERROR')
        message: Human-readable error message
        details: Optional additional error context or structured details

    Example:
        >>> error = ErrorResponse(
        ...     error="VALIDATION_ERROR",
        ...     message="Failed to create resource",
        ...     details={
        ...         "field": "email",
        ...         "reason": "Invalid email format"
        ...     }
        ... )
    """

    error: str = Field(max_length=255, description="Error code or type")
    message: str = Field(max_length=1000, description="Human-readable error message")
    details: Optional[dict] = Field(
        None, description="Optional additional error context or structured details"
    )

    @field_validator("error")
    @classmethod
    def error_not_empty(cls, v: str) -> str:
        """Ensure error code is not just whitespace."""
        if v.strip() == "":
            raise ValueError("error cannot be empty or whitespace-only")
        return v.strip()

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        """Ensure message is not just whitespace."""
        if v.strip() == "":
            raise ValueError("message cannot be empty or whitespace-only")
        return v.strip()


__all__ = [
    "SortOrder",
    "PaginationParams",
    "PaginatedResponse",
    "BulkOperationResult",
    "ErrorResponse",
]
