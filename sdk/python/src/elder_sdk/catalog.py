"""API catalog loader and dynamic service/operation proxies for Elder SDK."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from elder_sdk.exceptions import ElderCatalogError, ElderValidationError

if TYPE_CHECKING:
    from elder_sdk.client import AsyncElderClient, ElderClient

_PATH_PLACEHOLDER_RE = re.compile(r"\{([^}]+)\}")

# Path parameters backed by Quart `<path:...>` converters where `/` must stay
# unescaped in URLs (e.g. secret paths or email/file subpaths).
_MULTI_SEGMENT_PATH_PARAMS = frozenset(
    {"secret_path", "user_key", "group_key", "member_email", "filename"}
)


@dataclass(frozen=True, slots=True)
class ApiOperation:
    """Metadata describing a single Elder REST API operation."""

    service: str
    operation: str
    method: str
    path: str
    path_params: tuple[str, ...]
    summary: str
    has_body: bool

    def format_path(
        self,
        path_params: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        """Substitute and URL-encode path parameters into ``self.path``."""
        merged: dict[str, Any] = {}
        if path_params:
            merged.update(path_params)
        merged.update(kwargs)

        def _replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in merged:
                raise ElderCatalogError(
                    f"Missing required path parameter '{name}' for "
                    f"{self.service}.{self.operation} ({self.method} {self.path})"
                )
            raw_val = merged[name]
            if raw_val is None:
                raise ElderCatalogError(
                    f"Path parameter '{name}' cannot be None for "
                    f"{self.service}.{self.operation}"
                )
            text = str(raw_val)
            if not text:
                raise ElderCatalogError(
                    f"Path parameter '{name}' cannot be empty for "
                    f"{self.service}.{self.operation}"
                )
            safe = "/" if name in _MULTI_SEGMENT_PATH_PARAMS else ""
            if name in _MULTI_SEGMENT_PATH_PARAMS:
                text = text.lstrip("/")
                if not text:
                    raise ElderCatalogError(
                        f"Path parameter '{name}' cannot be empty for "
                        f"{self.service}.{self.operation}"
                    )
            return quote(text, safe=safe)

        return _PATH_PLACEHOLDER_RE.sub(_replace, self.path)


class ApiCatalog:
    """In-memory index of Elder API services and operations."""

    def __init__(
        self, operations: list[ApiOperation], *, version: str = "0.1.0"
    ) -> None:
        self.version = version
        self._operations: list[ApiOperation] = list(operations)
        self._by_service: dict[str, dict[str, ApiOperation]] = {}
        for op in self._operations:
            svc_map = self._by_service.setdefault(op.service, {})
            if op.operation in svc_map:
                raise ElderCatalogError(
                    f"Duplicate catalog operation {op.service}.{op.operation}"
                )
            svc_map[op.operation] = op

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ApiCatalog:
        """Build an ``ApiCatalog`` from parsed JSON catalog data."""
        raw_ops = data.get("operations")
        if not isinstance(raw_ops, list):
            raise ElderCatalogError("Catalog JSON must contain an 'operations' list")

        parsed: list[ApiOperation] = []
        for entry in raw_ops:
            if not isinstance(entry, Mapping):
                raise ElderCatalogError("Catalog operation entry must be an object")
            parsed.append(
                ApiOperation(
                    service=str(entry["service"]),
                    operation=str(entry["operation"]),
                    method=str(entry["method"]).upper(),
                    path=str(entry["path"]),
                    path_params=tuple(str(p) for p in entry.get("path_params", [])),
                    summary=str(entry.get("summary") or ""),
                    has_body=bool(entry.get("has_body", False)),
                )
            )

        return cls(parsed, version=str(data.get("version") or "0.1.0"))

    @classmethod
    def from_file(cls, path: str | Path) -> ApiCatalog:
        """Load an ``ApiCatalog`` from a JSON file path."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    @lru_cache(maxsize=1)
    def load_default(cls) -> ApiCatalog:
        """Load the bundled ``api_catalog.json`` shipped with ``elder_sdk``."""
        catalog_file = resources.files("elder_sdk").joinpath("api_catalog.json")
        raw = json.loads(catalog_file.read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    def services(self) -> list[str]:
        """Return sorted list of all service names in the catalog."""
        return sorted(self._by_service.keys())

    def has_service(self, service: str) -> bool:
        """Return True if ``service`` exists in the catalog."""
        return service in self._by_service

    def operations(self, service: str | None = None) -> list[ApiOperation]:
        """Return operations for ``service``, or all operations if omitted."""
        if service is None:
            return list(self._operations)
        svc_map = self._by_service.get(service)
        if svc_map is None:
            raise ElderCatalogError(f"Unknown Elder service: '{service}'")
        return [svc_map[name] for name in sorted(svc_map)]

    def get(self, service: str, operation: str) -> ApiOperation:
        """Look up a single ``ApiOperation`` by ``(service, operation)``."""
        svc_map = self._by_service.get(service)
        if svc_map is None:
            raise ElderCatalogError(f"Unknown Elder service: '{service}'")
        op = svc_map.get(operation)
        if op is None:
            raise ElderCatalogError(
                f"Unknown operation '{operation}' on service '{service}'"
            )
        return op

    def __len__(self) -> int:
        return len(self._operations)

    def __contains__(self, item: object) -> bool:
        if isinstance(item, str):
            return item in self._by_service
        if isinstance(item, tuple) and len(item) == 2:
            svc, op = item
            return svc in self._by_service and op in self._by_service[svc]
        return False


def _prepare_operation_call(
    op: ApiOperation,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[str, str, dict[str, Any] | None, Any, dict[str, str] | None]:
    """Resolve positional/keyword arguments into HTTP request parameters."""
    work = dict(kwargs)
    explicit_path_params = work.pop("path_params", None)
    explicit_params = work.pop("params", None)
    has_body_kw = "body" in work
    has_json_kw = "json" in work
    if has_body_kw and has_json_kw:
        raise ElderValidationError(
            "Pass either 'body' or 'json', not both",
            status_code=None,
        )
    explicit_body = work.pop("body", None) if has_body_kw else work.pop("json", None)
    has_explicit_body = has_body_kw or has_json_kw
    headers = work.pop("headers", None)

    resolved_path_params: dict[str, Any] = {}
    if explicit_path_params is not None:
        if not isinstance(explicit_path_params, Mapping):
            raise ElderCatalogError("'path_params' must be a mapping")
        resolved_path_params.update(explicit_path_params)

    positional = list(args)
    if (
        len(positional) == len(op.path_params) + 1
        and op.has_body
        and not has_explicit_body
        and isinstance(positional[-1], Mapping)
    ):
        explicit_body = dict(positional.pop())
        has_explicit_body = True

    if len(positional) > len(op.path_params):
        raise ElderCatalogError(
            f"{op.service}.{op.operation} expects at most {len(op.path_params)} "
            f"positional path arguments ({', '.join(op.path_params)}), "
            f"got {len(positional)}"
        )

    for param_name, val in zip(op.path_params, positional, strict=False):
        resolved_path_params[param_name] = val

    remaining: dict[str, Any] = {}
    for key, val in work.items():
        if key in op.path_params and key not in resolved_path_params:
            resolved_path_params[key] = val
        else:
            remaining[key] = val

    query_params: dict[str, Any] | None = (
        dict(explicit_params) if explicit_params is not None else None
    )

    body_payload: Any = explicit_body
    if remaining:
        if op.has_body and not has_explicit_body:
            body_payload = remaining
        else:
            if query_params is None:
                query_params = {}
            query_params.update(remaining)

    formatted_path = op.format_path(resolved_path_params)
    resolved_headers = dict(headers) if headers is not None else None
    return op.method, formatted_path, query_params, body_payload, resolved_headers


class _BaseServiceProxy:
    """Shared metadata and lookup behavior for sync/async service proxies."""

    def __init__(self, catalog: ApiCatalog, service: str) -> None:
        ops = catalog.operations(service)
        self._catalog = catalog
        self._service = service
        self._ops: dict[str, ApiOperation] = {op.operation: op for op in ops}

    @property
    def service_name(self) -> str:
        """Return the service identifier bound to this proxy."""
        return self._service

    def operations(self) -> list[str]:
        """Return sorted operation names exposed by this service."""
        return sorted(self._ops.keys())

    def describe(self, operation: str) -> ApiOperation:
        """Return ``ApiOperation`` metadata for ``operation``."""
        return self._catalog.get(self._service, operation)

    def __dir__(self) -> list[str]:
        return sorted(set(super().__dir__()) | set(self._ops.keys()))

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(service={self._service!r}, "
            f"operations={len(self._ops)})"
        )


class ServiceProxy(_BaseServiceProxy):
    """Synchronous dynamic proxy for calling any operation on a catalog service."""

    def __init__(
        self,
        client: ElderClient,
        service: str,
        catalog: ApiCatalog | None = None,
    ) -> None:
        super().__init__(catalog or client.catalog, service)
        self._client = client

    def invoke(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        """Invoke ``operation`` synchronously using catalog metadata."""
        op = self._catalog.get(self._service, operation)
        method, path, params, body, headers = _prepare_operation_call(op, args, kwargs)
        return self._client.request(
            method,
            path,
            params=params,
            json=body,
            headers=headers,
        )

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        op = self._ops.get(name)
        if op is None:
            raise AttributeError(f"Service '{self._service}' has no operation '{name}'")

        def _caller(*args: Any, **kwargs: Any) -> Any:
            return self.invoke(name, *args, **kwargs)

        _caller.__name__ = name
        _caller.__qualname__ = f"{self._service}.{name}"
        _caller.__doc__ = op.summary
        return _caller


class AsyncServiceProxy(_BaseServiceProxy):
    """Asynchronous dynamic proxy for calling any operation on a catalog service."""

    def __init__(
        self,
        client: AsyncElderClient,
        service: str,
        catalog: ApiCatalog | None = None,
    ) -> None:
        super().__init__(catalog or client.catalog, service)
        self._client = client

    async def invoke(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        """Invoke ``operation`` asynchronously using catalog metadata."""
        op = self._catalog.get(self._service, operation)
        method, path, params, body, headers = _prepare_operation_call(op, args, kwargs)
        return await self._client.request(
            method,
            path,
            params=params,
            json=body,
            headers=headers,
        )

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        op = self._ops.get(name)
        if op is None:
            raise AttributeError(f"Service '{self._service}' has no operation '{name}'")

        async def _caller(*args: Any, **kwargs: Any) -> Any:
            return await self.invoke(name, *args, **kwargs)

        _caller.__name__ = name
        _caller.__qualname__ = f"{self._service}.{name}"
        _caller.__doc__ = op.summary
        return _caller
