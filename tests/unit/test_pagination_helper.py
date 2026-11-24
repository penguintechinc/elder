"""Edge-case tests for the shared pagination bounds helper."""

import pytest

from apps.api.utils.pagination import (
    MAX_LIMIT,
    MAX_OFFSET,
    Pagination,
    clamp_int,
    env_bounded_int,
    parse_limit,
    parse_offset,
    parse_pagination,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 50),
        ("", 50),
        ("10", 10),
        (10, 10),
        (" 25 ", 25),
        ("0", 50),
        ("-1", 50),
        ("-999999999", 50),
        ("200", 200),
        ("201", 200),
        ("999999999999999999999", 200),
        ("9" * 5000, 50),
        ("abc", 50),
        ("1.5", 50),
        ("1e3", 50),
        ("null", 50),
        (True, 50),
        ([], 50),
    ],
)
def test_parse_limit_pagination_bounds(raw, expected):
    """Limit is always inside [1, maximum] whatever the caller sends."""
    args = {} if raw is None else {"limit": raw}
    assert parse_limit(args, 50, 200) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 0),
        ("0", 0),
        ("15", 15),
        ("-5", 0),
        ("abc", 0),
        ("", 0),
        (str(MAX_OFFSET + 1), MAX_OFFSET),
        ("999999999999999999999", MAX_OFFSET),
    ],
)
def test_parse_offset_pagination_bounds(raw, expected):
    """Offset is never negative and never beyond the hard ceiling."""
    args = {} if raw is None else {"offset": raw}
    assert parse_offset(args) == expected


def test_parse_pagination_defaults_and_custom_bounds():
    """Defaults apply when absent; per-endpoint default/maximum are honoured."""
    assert parse_pagination({}) == Pagination(limit=50, offset=0)
    assert parse_pagination({"limit": "100000", "offset": "-3"}) == Pagination(
        limit=MAX_LIMIT, offset=0
    )
    assert parse_pagination({"limit": "500"}, default=100, maximum=1000).limit == 500
    assert parse_pagination({"limit": "x"}, default=100, maximum=1000).limit == 100
    # A default above the maximum can never widen the bound.
    assert parse_pagination({}, default=500, maximum=200).limit == 200


def test_parse_limit_pagination_custom_key():
    """The page-size key is configurable (per_page style endpoints)."""
    assert parse_limit({"per_page": "5000"}, 50, 1000, key="per_page") == 1000
    assert parse_limit({"per_page": "-2"}, 50, 1000, key="per_page") == 50


def test_clamp_int_pagination_range():
    """clamp_int pulls values to the nearest bound and never raises."""
    assert clamp_int("7", 1, 0, 5) == 5
    assert clamp_int("-7", 1, 0, 5) == 0
    assert clamp_int(object(), 3, 0, 5) == 3
    assert clamp_int(None, 99, 0, 5) == 5


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, 6),
        ("", 6),
        ("4", 4),
        ("0", 6),
        ("-3", 6),
        ("not-a-number", 6),
        ("1e9", 6),
        ("999999", 25),
    ],
)
def test_env_bounded_int_pagination_never_crashes(monkeypatch, value, expected):
    """Bad environment values fall back to the default; huge ones are capped."""
    name = "ELDER_TEST_PAGINATION_BOUND"
    if value is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, value)
    assert env_bounded_int(name, 6, 25) == expected
