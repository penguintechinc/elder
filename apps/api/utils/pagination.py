"""Bounded pagination parsing shared by list endpoints.

Every list endpoint must bound how many rows a single request can pull.
These helpers clamp caller-supplied ``limit``/``offset`` values instead of
raising, so a malformed or oversized value degrades to a safe page rather
than an error or an unbounded query.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from apps.mcp.envutil import env_int

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
# Offsets beyond this are never a legitimate page; bounding it keeps a
# hostile value from forcing the database to walk an arbitrarily deep scan.
MAX_OFFSET = 1_000_000


@dataclass(slots=True, frozen=True)
class Pagination:
    """Validated ``limit``/``offset`` pair for a list query.

    Both values are already clamped to safe bounds, so they can be handed
    straight to a service or a ``limitby`` without further checks.
    """

    limit: int
    offset: int


def clamp_int(raw: Any, default: int, minimum: int, maximum: int) -> int:
    """Coerce ``raw`` to an int inside ``[minimum, maximum]``.

    Missing, empty, boolean, or non-numeric input falls back to ``default``
    (itself clamped); numeric input outside the range is pulled to the
    nearest bound. Never raises, so it is safe on untrusted query strings.
    """
    value = default
    if raw is not None and not isinstance(raw, bool):
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            value = default
    return max(minimum, min(value, maximum))


def parse_limit(
    args: Mapping[str, Any],
    default: int = DEFAULT_LIMIT,
    maximum: int = MAX_LIMIT,
    key: str = "limit",
) -> int:
    """Read a page-size parameter from ``args`` clamped to ``[1, maximum]``.

    Zero, negative, and non-numeric values resolve to a usable page size
    rather than an empty or unbounded result set.
    """
    value = clamp_int(args.get(key), default, 0, maximum)
    if value < 1:
        # An explicit zero/negative page size is meaningless; use the default.
        value = default
    return max(1, min(value, maximum))


def parse_offset(
    args: Mapping[str, Any], key: str = "offset", maximum: int = MAX_OFFSET
) -> int:
    """Read a row offset from ``args`` clamped to ``[0, maximum]``.

    Negative or non-numeric offsets resolve to the first row.
    """
    return clamp_int(args.get(key), 0, 0, maximum)


def parse_pagination(
    args: Mapping[str, Any],
    default: int = DEFAULT_LIMIT,
    maximum: int = MAX_LIMIT,
) -> Pagination:
    """Parse ``limit`` and ``offset`` from request args into safe bounds.

    The single entry point list endpoints should use: ``default`` is the
    page size when none is supplied and ``maximum`` is the hard ceiling.
    """
    return Pagination(
        limit=parse_limit(args, default, maximum),
        offset=parse_offset(args),
    )


def env_bounded_int(name: str, default: int, maximum: int) -> int:
    """Read a positive integer limit from the environment, capped at ``maximum``.

    Unset, malformed, or non-positive values fall back to ``default`` without
    raising, so a bad setting can never crash the service at import; the
    ceiling keeps an operator typo from disabling a safety limit.
    """
    return min(env_int(name, default), maximum)
