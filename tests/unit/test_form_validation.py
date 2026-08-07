"""Unit tests for dynamic Pydantic intake-form submission validation.

Covers success, validation-failure (missing required + type mismatch),
rejection of unsupported field types (e.g. ``file``), and rejection of an
oversized string field (security review: bounded string length so a huge
payload is a clean validation failure, not a DB-layer blowup).
"""

import pytest

from apps.api.modules.helpdesk.services.form_validation import (
    _MAX_STRING_LENGTH,
    build_form_model,
    validate_submission,
)

FIELDS = [
    {"id": "email", "label": "Email", "type": "email", "required": True},
    {"id": "age", "label": "Age", "type": "number", "required": False},
]


def test_valid_submission():
    ok, errs = validate_submission(FIELDS, {"email": "a@b.com", "age": 30})
    assert errs == [] and ok["email"] == "a@b.com" and ok["age"] == 30


def test_missing_required_and_bad_email():
    ok, errs = validate_submission(FIELDS, {"age": "notanumber"})
    assert ok is None and errs  # email missing + age not int


def test_file_type_unsupported():
    with pytest.raises(ValueError):
        build_form_model([{"id": "f", "label": "F", "type": "file", "required": False}])


def test_oversized_string_field_rejected():
    """A `text` field far exceeding `_MAX_STRING_LENGTH` fails validation
    cleanly (returns errors) instead of raising or reaching the DB layer."""
    fields = [{"id": "notes", "label": "Notes", "type": "text", "required": True}]
    ok, errs = validate_submission(fields, {"notes": "x" * (_MAX_STRING_LENGTH + 1)})
    assert ok is None
    assert errs


def test_string_field_at_max_length_accepted():
    """A `text` field exactly at the cap is still valid — the bound is
    inclusive, not off-by-one."""
    fields = [{"id": "notes", "label": "Notes", "type": "text", "required": True}]
    ok, errs = validate_submission(fields, {"notes": "x" * _MAX_STRING_LENGTH})
    assert errs == []
    assert len(ok["notes"]) == _MAX_STRING_LENGTH
