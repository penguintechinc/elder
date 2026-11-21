"""Licensing decorator wrapper with domain bypass and graceful fallback.

Wraps ``penguin_licensing.license_required`` (when installed) as well as the
fallback passthrough decorator so that requests on PenguinTech-owned bypass
domains (see ``apps.api.common.modules.licensing.is_bypass_domain``) execute
immediately without license denial.
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any

from apps.api.common.modules.licensing import is_bypass_domain

logger = logging.getLogger(__name__)

# Try to import from penguin_licensing, falling back gracefully if unavailable.
_real_license_required: Callable[..., Any] | None
_real_get_license_client: Callable[[], Any] | None

try:
    from penguin_licensing import (
        get_license_client as _imported_get_license_client,
    )
    from penguin_licensing import (
        license_required as _imported_license_required,
    )

    _real_license_required = _imported_license_required
    _real_get_license_client = _imported_get_license_client
except ImportError:
    logger.warning(
        "penguin_licensing module not available, using fallback implementations"
    )
    _real_license_required = None
    _real_get_license_client = None


def _apply_real_decorator(
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    is_bare: bool,
) -> Callable[..., Any]:
    """Delegate decoration to ``penguin_licensing.license_required`` when present."""
    if _real_license_required is None:
        return func

    if is_bare:
        try:
            return _real_license_required()(func)
        except TypeError:
            return _real_license_required(func)

    try:
        return _real_license_required(*args, **kwargs)(func)
    except TypeError:
        # Handle signature differences (e.g. feature=... passed when upstream
        # only accepts required_tier).
        return _real_license_required()(func)


def _wrap_with_domain_bypass(
    func: Callable[..., Any],
    delegated: Callable[..., Any],
) -> Callable[..., Any]:
    """Wrap ``func`` so bypass domains skip license enforcement at request time."""
    func_is_coro = inspect.iscoroutinefunction(func)
    delegated_is_coro = inspect.iscoroutinefunction(delegated)

    if func_is_coro or delegated_is_coro:

        @functools.wraps(func)
        async def async_wrapper(*call_args: Any, **call_kwargs: Any) -> Any:
            if is_bypass_domain():
                if func_is_coro:
                    return await func(*call_args, **call_kwargs)
                return func(*call_args, **call_kwargs)
            result = delegated(*call_args, **call_kwargs)
            if inspect.isawaitable(result):
                return await result
            return result

        return async_wrapper

    @functools.wraps(func)
    def sync_wrapper(*call_args: Any, **call_kwargs: Any) -> Any:
        if is_bypass_domain():
            return func(*call_args, **call_kwargs)
        return delegated(*call_args, **call_kwargs)

    return sync_wrapper


def license_required(*args: Any, **kwargs: Any) -> Callable[..., Any]:
    """Enforce license requirements while honoring domain-based bypass.

    Supports ``@license_required``, ``@license_required("enterprise")``, and
    ``@license_required(feature="...")`` on both async and sync route handlers.
    """
    is_bare = len(args) == 1 and callable(args[0]) and not kwargs

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        delegated = _apply_real_decorator(func, args, kwargs, is_bare=is_bare)
        return _wrap_with_domain_bypass(func, delegated)

    if is_bare:
        return decorator(args[0])
    return decorator


def get_license_client() -> Any:
    """Return the active penguin_licensing client, or None when unavailable."""
    if _real_get_license_client is not None:
        return _real_get_license_client()
    return None


__all__ = ["license_required", "get_license_client"]
