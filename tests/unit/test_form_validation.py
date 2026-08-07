"""Unit tests for dynamic Pydantic intake-form submission validation.

Covers success, validation-failure (missing required + type mismatch), and
rejection of unsupported field types (e.g. ``file``).
"""

import pytest

from apps.api.modules.helpdesk.services.form_validation import (
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
