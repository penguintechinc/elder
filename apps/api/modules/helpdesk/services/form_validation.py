"""Dynamic Pydantic validation for intake form submissions.

Compiles a form's ``fields`` spec (a list of field-definition dicts) into a
Pydantic model at request time via ``pydantic.create_model``, then validates
submitted data against it. Keeps intake-form schemas fully data-driven —
no hand-written model per form.
"""

from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, EmailStr, HttpUrl, ValidationError, create_model

#: Maps a field spec's ``type`` string to the Python/pydantic type used for
#: that field. Types not present here (including ``file``) are unsupported.
_TYPE_MAP: dict[str, type] = {
    "text": str,
    "textarea": str,
    "string": str,
    "email": EmailStr,
    "number": int,
    "int": int,
    "float": float,
    "bool": bool,
    "checkbox": bool,
    "date": date,
    "select": str,
    "url": HttpUrl,
}


def _field_type(spec: dict[str, Any]) -> type:
    """Resolve the Python/pydantic type for a single field spec.

    ``select`` fields with an ``options`` list are narrowed to a
    ``Literal`` of those options; everything else uses the static
    ``_TYPE_MAP``. Raises ``ValueError`` for unknown or ``file`` types.
    """
    field_type = spec.get("type")

    if field_type == "select" and spec.get("options"):
        return Literal[tuple(spec["options"])]  # type: ignore[return-value]

    if field_type not in _TYPE_MAP:
        raise ValueError(f"unsupported field type: {field_type}")

    return _TYPE_MAP[field_type]


def build_form_model(fields: list[dict[str, Any]]) -> type[BaseModel]:
    """Compile a form's field spec into a dynamic Pydantic model.

    Each spec dict requires ``id`` (used as the model field name) and
    ``type``; ``required`` (default ``False``) controls whether the field
    is mandatory. Required fields have no default; optional fields become
    ``Optional[T] = None``.
    """
    model_fields: dict[str, Any] = {}

    for spec in fields:
        name = spec["id"]
        python_type = _field_type(spec)

        if spec.get("required", False):
            model_fields[name] = (python_type, ...)
        else:
            model_fields[name] = (Optional[python_type], None)

    return create_model("IntakeFormSubmission", **model_fields)


def _flatten_errors(exc: ValidationError) -> list[str]:
    """Flatten a Pydantic ``ValidationError`` into human-readable strings."""
    messages = []

    for error in exc.errors():
        field_name = ".".join(str(part) for part in error["loc"]) or "<submission>"
        messages.append(f"{field_name}: {error['msg']}")

    return messages


def validate_submission(
    fields: list[dict[str, Any]], data: dict[str, Any]
) -> tuple[Optional[dict[str, Any]], list[str]]:
    """Validate submitted form data against a form's field spec.

    Builds a dynamic model from ``fields`` and validates ``data`` against
    it. Returns ``(validated_dict, [])`` on success, or ``(None,
    error_messages)`` if validation fails.
    """
    model = build_form_model(fields)

    try:
        validated = model.model_validate(data)
    except ValidationError as exc:
        return None, _flatten_errors(exc)

    return validated.model_dump(), []
