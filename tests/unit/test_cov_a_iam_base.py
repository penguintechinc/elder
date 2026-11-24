"""Unit tests for the BaseIAMProvider default (non-abstract) behaviours."""

import pytest

from apps.api.services.iam.base import BaseIAMProvider


def _concrete_cls():
    """Build a concrete subclass with every abstract method stubbed out."""
    ns = {
        name: (lambda self, *a, **k: None)
        for name in BaseIAMProvider.__abstractmethods__
    }
    return type("ConcreteProvider", (BaseIAMProvider,), ns)


def test_abstract_class_cannot_be_instantiated():
    """Instantiating the abstract base directly raises TypeError."""
    with pytest.raises(TypeError):
        BaseIAMProvider({})


def test_init_stores_config_and_provider_type():
    """Constructor keeps config and exposes provider_type from it."""
    prov = _concrete_cls()({"provider_type": "aws"})
    assert prov.config == {"provider_type": "aws"}
    assert prov.provider_type == "aws"


def test_init_provider_type_missing_is_none():
    """A config without provider_type yields provider_type None."""
    assert _concrete_cls()({}).provider_type is None


def test_default_list_groups_reports_unsupported():
    """list_groups default signals groups are unsupported with empty result."""
    assert _concrete_cls()({}).list_groups(limit=5, next_token="t") == {
        "groups": [],
        "next_token": None,
        "supported": False,
    }


@pytest.mark.parametrize(
    "method,args",
    [
        ("create_group", ("g",)),
        ("delete_group", ("g",)),
        ("add_user_to_group", ("u", "g")),
        ("remove_user_from_group", ("u", "g")),
    ],
)
def test_default_group_mutations_raise_not_implemented(method, args):
    """Group mutation defaults raise NotImplementedError."""
    with pytest.raises(NotImplementedError, match="Groups not supported"):
        getattr(_concrete_cls()({}), method)(*args)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            {
                "id": "i",
                "username": "u",
                "display_name": "D",
                "email": "e@x",
                "created_at": 1,
                "arn": "a",
            },
            {
                "id": "i",
                "username": "u",
                "display_name": "D",
                "email": "e@x",
                "created_at": 1,
                "arn": "a",
            },
        ),
        (
            {
                "UserId": "U1",
                "UserName": "bob",
                "DisplayName": "Bob",
                "Email": "b@x",
                "CreateDate": 2,
                "Arn": "A",
            },
            {
                "id": "U1",
                "username": "bob",
                "display_name": "Bob",
                "email": "b@x",
                "created_at": 2,
                "arn": "A",
            },
        ),
        (
            {"name": "n", "email": "m@x"},
            {
                "id": "n",
                "username": "m@x",
                "display_name": None,
                "email": "m@x",
                "created_at": None,
                "arn": None,
            },
        ),
    ],
)
def test_normalize_user_maps_provider_keys(raw, expected):
    """_normalize_user maps generic, AWS-style and fallback keys."""
    assert _concrete_cls()({})._normalize_user(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            {"id": "i", "name": "n", "description": "d", "created_at": 1, "arn": "a"},
            {"id": "i", "name": "n", "description": "d", "created_at": 1, "arn": "a"},
        ),
        (
            {
                "RoleId": "R",
                "RoleName": "rn",
                "Description": "D",
                "CreateDate": 2,
                "Arn": "A",
            },
            {"id": "R", "name": "rn", "description": "D", "created_at": 2, "arn": "A"},
        ),
        (
            {"name": "only"},
            {
                "id": "only",
                "name": "only",
                "description": None,
                "created_at": None,
                "arn": None,
            },
        ),
    ],
)
def test_normalize_role_maps_provider_keys(raw, expected):
    """_normalize_role maps generic, AWS-style and fallback keys."""
    assert _concrete_cls()({})._normalize_role(raw) == expected
