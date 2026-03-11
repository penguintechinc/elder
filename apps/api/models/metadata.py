"""Typed metadata system for Elder enterprise features."""

# flake8: noqa: E501


import enum
import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Column,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, relationship, validates

from apps.api.models.base import Base, IDMixin, TimestampMixin


class MetadataFieldType(enum.Enum):
    """Supported metadata field types."""

    STRING = "string"
    NUMBER = "number"
    DATE = "date"
    BOOLEAN = "boolean"
    JSON = "json"


class MetadataField(Base, IDMixin, TimestampMixin):
    """
    Typed metadata field for entities and organizations.

    Provides structured metadata with type validation and coercion.
    Supports string, number, date, boolean, and JSON field types.

    Permission Requirements:
    - Maintainer: Full CRUD on metadata
    - Operator: Read-only access
    - Viewer: Read-only access
    """

    __tablename__ = "metadata_fields"

    # Resource association (entity or organization)
    resource_type = Column(
        String(20),
        nullable=False,
        index=True,
        comment="Type of resource (entity or organization)",
    )

    resource_id = Column(
        Integer,
        nullable=False,
        index=True,
        comment="ID of the entity or organization",
    )

    # Field definition
    field_key = Column(
        String(100),
        nullable=False,
        index=True,
        comment="Metadata field key/name",
    )

    field_type = Column(
        Enum(MetadataFieldType),
        nullable=False,
        comment="Data type of this field",
    )

    # Value stored as JSON-encoded string for flexibility
    field_value = Column(
        Text,
        nullable=False,
        comment="JSON-encoded field value",
    )

    # System metadata is read-only
    is_system = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="System metadata cannot be deleted or modified by users",
    )

    # Creator tracking
    created_by_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="User who created this metadata field",
    )

    # Relationships
    created_by: Mapped[Optional["Identity"]] = relationship(
        "Identity",
        backref="created_metadata_fields",
    )

    # village_id for cross-system reference
    village_id = Column(String(32), unique=True, nullable=True, index=True)

    # Ensure unique field keys per resource
    __table_args__ = (
        UniqueConstraint(
            "resource_type",
            "resource_id",
            "field_key",
            name="uix_metadata_field",
        ),
    )

    def __repr__(self) -> str:
        """String representation of metadata field."""
        return f"<MetadataField(id={self.id}, key='{self.field_key}', type={self.field_type.value}, resource_type={self.resource_type}, resource_id={self.resource_id})>"

    @validates("field_value")
    def validate_field_value(self, key, value):
        """Validate field value matches field type."""
        if not value:
            return value

        # Value is stored as JSON string, validate it can be decoded
        try:
            decoded = json.loads(value)
            # Type-specific validation happens in set_value method
            return value
        except (json.JSONDecodeError, TypeError):
            raise ValueError(f"Invalid JSON value for metadata field: {value}")

    def get_value(self) -> Any:
        """
        Get the typed value of this metadata field.

        Returns:
            Decoded value with appropriate Python type
        """
        if not self.field_value:
            return None

        decoded = json.loads(self.field_value)

        # Type coercion based on field_type
        if self.field_type == MetadataFieldType.STRING:
            return str(decoded) if decoded is not None else None

        elif self.field_type == MetadataFieldType.NUMBER:
            if decoded is None:
                return None
            return (
                float(decoded) if isinstance(decoded, (int, float)) else float(decoded)
            )

        elif self.field_type == MetadataFieldType.DATE:
            if decoded is None:
                return None
            # Return datetime object
            if isinstance(decoded, str):
                return datetime.fromisoformat(decoded.replace("Z", "+00:00"))
            return decoded

        elif self.field_type == MetadataFieldType.BOOLEAN:
            return bool(decoded) if decoded is not None else None

        elif self.field_type == MetadataFieldType.JSON:
            return decoded

        return decoded

    def set_value(self, value: Any) -> None:
        """
        Set the value of this metadata field with type validation.

        Args:
            value: Value to set (will be coerced to field_type)

        Raises:
            ValueError: If value cannot be coerced to field_type
        """
        if value is None:
            self.field_value = json.dumps(None)
            return

        # Type-specific validation and coercion
        if self.field_type == MetadataFieldType.STRING:
            if not isinstance(value, str):
                value = str(value)
            if len(value) > 1000:
                raise ValueError("String metadata values must be <= 1000 characters")
            self.field_value = json.dumps(value)

        elif self.field_type == MetadataFieldType.NUMBER:
            try:
                if isinstance(value, str):
                    # Try to parse string as number
                    value = float(value)
                elif not isinstance(value, (int, float)):
                    raise ValueError(f"Cannot coerce {type(value).__name__} to number")
                self.field_value = json.dumps(value)
            except (ValueError, TypeError) as e:
                raise ValueError(f"Invalid number value: {value}") from e

        elif self.field_type == MetadataFieldType.DATE:
            try:
                if isinstance(value, str):
                    # Parse ISO8601 datetime string
                    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    self.field_value = json.dumps(dt.isoformat())
                elif isinstance(value, datetime):
                    self.field_value = json.dumps(value.isoformat())
                else:
                    raise ValueError(f"Cannot coerce {type(value).__name__} to date")
            except (ValueError, TypeError) as e:
                raise ValueError(
                    f"Invalid date value (must be ISO8601): {value}"
                ) from e

        elif self.field_type == MetadataFieldType.BOOLEAN:
            # Coerce to boolean
            if isinstance(value, str):
                value = value.lower() in ("true", "1", "yes", "on")
            else:
                value = bool(value)
            self.field_value = json.dumps(value)

        elif self.field_type == MetadataFieldType.JSON:
            # Validate it's valid JSON
            try:
                if isinstance(value, str):
                    # Parse and re-encode to validate
                    parsed = json.loads(value)
                    self.field_value = json.dumps(parsed)
                else:
                    # Encode directly
                    self.field_value = json.dumps(value)
            except (json.JSONDecodeError, TypeError) as e:
                raise ValueError(f"Invalid JSON value") from e

    @classmethod
    def get_metadata(cls, resource_type: str, resource_id: int) -> dict:
        """
        Get all metadata for a resource as a dictionary.

        Args:
            resource_type: Type of resource (entity or organization)
            resource_id: Resource ID

        Returns:
            Dictionary mapping field_key to typed value
        """
        from shared.database import db

        fields = (
            db.session.query(cls)
            .filter_by(resource_type=resource_type, resource_id=resource_id)
            .all()
        )

        return {field.field_key: field.get_value() for field in fields}

    @classmethod
    def set_metadata(
        cls,
        resource_type: str,
        resource_id: int,
        field_key: str,
        field_type: MetadataFieldType,
        value: Any,
        created_by_id: int,
    ) -> "MetadataField":
        """
        Set or update a metadata field.

        Args:
            resource_type: Type of resource
            resource_id: Resource ID
            field_key: Field key
            field_type: Field type
            value: Field value
            created_by_id: Identity ID creating/updating the field

        Returns:
            MetadataField instance
        """
        from shared.database import db

        # Check if field exists
        field = (
            db.session.query(cls)
            .filter_by(
                resource_type=resource_type,
                resource_id=resource_id,
                field_key=field_key,
            )
            .first()
        )

        if field:
            # Update existing field
            field.field_type = field_type
            field.set_value(value)
        else:
            # Create new field
            field = cls(
                resource_type=resource_type,
                resource_id=resource_id,
                field_key=field_key,
                field_type=field_type,
                created_by_id=created_by_id,
            )
            field.set_value(value)
            db.session.add(field)

        return field
