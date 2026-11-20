#!/usr/bin/env python3
"""Deterministically generate openapi/api-catalog.json from openapi/v4.yaml.

Reads the live OpenAPI 3.1 specification (``openapi/v4.yaml``), cross-checks
route decorators across ``apps/api/`` to guarantee zero endpoints are missed,
normalizes every API operation into a collision-free ``(service, operation)``
pair, and writes:

- ``openapi/api-catalog.json`` (canonical workspace artifact)
- ``sdk/python/src/elder_sdk/api_catalog.json`` (bundled inside ``elder-sdk``)

Usage:
    python3 scripts/generate_api_catalog.py
"""

from __future__ import annotations

import ast
import json
import keyword
import pathlib
import re
from collections import defaultdict
from typing import Any

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
OPENAPI_PATH = REPO_ROOT / "openapi" / "v4.yaml"
CATALOG_OUTPUT_PATH = REPO_ROOT / "openapi" / "api-catalog.json"
SDK_CATALOG_PATH = (
    REPO_ROOT / "sdk" / "python" / "src" / "elder_sdk" / "api_catalog.json"
)
APPS_API_DIR = REPO_ROOT / "apps" / "api"

HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
PATH_PARAM_RE = re.compile(r"\{([^}]+)\}")
SINGLETON_ROOTS = frozenset({"status", "graph", "licensing", "profile"})

MULTI_SEGMENT_PREFIXES: tuple[tuple[str, str], ...] = (
    ("ai/resources", "ai"),
    ("integrations/internal", "integrations_internal"),
    ("flows-hooks", "flows"),
    ("hooks", "streams"),
    ("sbom/components", "sbom"),
    ("sbom/scans", "sbom"),
    ("sbom/schedules", "sbom"),
)


def _to_snake_case(value: str) -> str:
    """Convert an arbitrary identifier to clean snake_case."""
    step1 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    step2 = re.sub(r"[^a-zA-Z0-9]+", "_", step1)
    return step2.strip("_").lower()


def _map_service_and_subpath(path: str) -> tuple[str | None, str]:
    """Map an OpenAPI path to ``(service, sub_path)`` or ``(None, "")`` for HTML."""
    if path == "/healthz":
        return "status", "/healthz"
    if path == "/api/v1/status":
        return "status", ""
    if path == "/id/{village_id}":
        return "village_id", "/legacy"
    if path == "/api/v1/id/{village_id}":
        return "village_id", ""
    if path.startswith("/lookup"):
        return "lookup", path[len("/lookup") :]
    if not path.startswith("/api/v1/"):
        return None, ""

    rest = path[len("/api/v1/") :]
    for prefix, svc in MULTI_SEGMENT_PREFIXES:
        if rest == prefix or rest.startswith(prefix + "/"):
            sub = rest[len(prefix) :]
            if prefix in ("flows-hooks", "hooks"):
                sub = "/" + prefix.replace("-", "_") + sub
            elif prefix.startswith("sbom/"):
                sub = "/" + prefix.split("/", 1)[1] + sub
            return svc, sub

    if rest.endswith("/modules") and rest.startswith("tenants/"):
        return "tenant_modules", ""

    seg, _, tail = rest.partition("/")
    svc = seg.replace("-", "_")
    if svc == "import":
        svc = "csv_import"
    return svc, ("/" + tail if tail else "")


def _derive_operation_name(
    service: str,
    sub_path: str,
    method: str,
    operation_id: str,
    methods_on_path: set[str],
) -> str:
    """Derive a clean, collision-free snake_case operation name."""
    m_low = method.lower()

    if service == "status":
        return "healthz" if sub_path == "/healthz" else "get"
    if service == "village_id":
        return "resolve_legacy" if sub_path == "/legacy" else "resolve"
    if service == "lookup":
        return "batch" if sub_path == "/batch" else "get"
    if service == "csv_import" and sub_path == "/{resource}":
        return "import_resource"
    if service == "search" and sub_path == "":
        return "search"
    if service == "licensing" and sub_path == "/status":
        return "status"
    if service == "tenant_modules":
        return "get" if m_low == "get" else "update"
    if service == "profile" and sub_path == "/me":
        return "get_me" if m_low == "get" else "update_me"
    if service == "sso" and sub_path == "/saml/metadata/{tenant_id}":
        return "get_tenant_sp_metadata"
    if service == "sso" and sub_path == "/saml/slo/{tenant_id}":
        return f"saml_slo_{m_low}"
    if service == "streams" and sub_path == "/hooks/{token}/test":
        return f"test_hook_{m_low}"
    if service == "streams" and sub_path == "/hooks/{token}":
        return f"trigger_hook_{m_low}"

    if service not in ("sbom", "lookup") and sub_path in ("", "/"):
        if m_low == "get":
            return "get" if service in SINGLETON_ROOTS else "list"
        if m_low == "post":
            return "create"

    if service not in ("sbom", "lookup", "csv_import") and re.fullmatch(
        r"/\{[^/]+\}", sub_path
    ):
        if m_low == "get":
            return "get"
        if m_low == "put":
            return "update"
        if m_low == "patch":
            return "patch" if "PUT" in methods_on_path else "update"
        if m_low == "delete":
            return "delete"

    op_name = operation_id
    for pfx in ("get_", "post_", "put_", "patch_", "delete_"):
        if op_name.startswith(pfx):
            op_name = op_name[len(pfx) :]
            break
    if op_name.endswith("_endpoint"):
        op_name = op_name[: -len("_endpoint")]
    op_name = _to_snake_case(op_name)

    if (
        "PUT" in methods_on_path
        and "PATCH" in methods_on_path
        and m_low == "patch"
        and op_name.startswith("update_")
    ):
        op_name = "patch_" + op_name[len("update_") :]

    return op_name


def _scan_apps_api_route_suffixes() -> list[tuple[str, str, list[str], str]]:
    """Scan ``apps/api/`` AST for Flask/Quart ``.route(...)`` decorators."""
    discovered: list[tuple[str, str, list[str], str]] = []
    if not APPS_API_DIR.exists():
        return discovered

    for py_file in sorted(APPS_API_DIR.rglob("*.py")):
        rel = py_file.relative_to(REPO_ROOT).as_posix()
        if rel == "apps/api/web/routes.py":
            continue
        source = py_file.read_text(encoding="utf-8", errors="ignore")
        if ".route(" not in source:
            continue
        tree = ast.parse(source, filename=str(py_file))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "route"
                ):
                    raw_path = (
                        dec.args[0].value
                        if dec.args and isinstance(dec.args[0], ast.Constant)
                        else ""
                    )
                    if not isinstance(raw_path, str):
                        continue
                    methods = ["GET"]
                    for kw in dec.keywords:
                        if kw.arg == "methods" and isinstance(
                            kw.value, (ast.List, ast.Tuple)
                        ):
                            methods = [
                                str(el.value).upper()
                                for el in kw.value.elts
                                if isinstance(el, ast.Constant)
                            ]
                    norm_suffix = re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", raw_path)
                    discovered.append((rel, node.name, methods, norm_suffix))
    return discovered


def _verify_spec_covers_ast_routes(paths: dict[str, Any]) -> None:
    """Fail loudly if any route decorator in ``apps/api/`` is missing in OpenAPI."""
    spec_paths = list(paths.keys())
    missing: list[str] = []
    for rel, func_name, methods, suffix in _scan_apps_api_route_suffixes():
        if suffix and not any(sp.endswith(suffix) for sp in spec_paths):
            missing.append(f"{rel}:{func_name} ({methods} {suffix})")
    if missing:
        joined = "\n  ".join(missing)
        raise RuntimeError(
            f"OpenAPI spec is missing routes discovered in apps/api:\n  {joined}"
        )


def build_catalog(spec: dict[str, Any]) -> dict[str, Any]:
    """Build the canonical API catalog dictionary from an OpenAPI dict."""
    paths = spec.get("paths", {})
    if not isinstance(paths, dict) or not paths:
        raise RuntimeError("OpenAPI spec contains no paths")

    _verify_spec_covers_ast_routes(paths)

    operations: list[dict[str, Any]] = []
    seen_pairs: dict[tuple[str, str], list[str]] = defaultdict(list)

    for path in sorted(paths):
        service, sub_path = _map_service_and_subpath(path)
        if service is None:
            continue

        path_item = paths[path]
        if not isinstance(path_item, dict):
            continue

        methods_on_path = {m.upper() for m in path_item if m.upper() in HTTP_METHODS}
        path_params = PATH_PARAM_RE.findall(path)

        for method in sorted(methods_on_path):
            op_spec = path_item.get(method.lower()) or path_item.get(method) or {}
            if not isinstance(op_spec, dict):
                op_spec = {}

            operation_id = str(op_spec.get("operationId") or "")
            operation = _derive_operation_name(
                service, sub_path, method, operation_id, methods_on_path
            )

            for ident_kind, ident_val in (
                ("service", service),
                ("operation", operation),
            ):
                if not ident_val.isidentifier() or keyword.iskeyword(ident_val):
                    raise RuntimeError(
                        f"Invalid Python identifier for {ident_kind}: "
                        f"{ident_val!r} ({method} {path})"
                    )

            seen_pairs[(service, operation)].append(f"{method} {path}")

            raw_summary = str(op_spec.get("summary") or "").strip()
            summary = (
                " ".join(raw_summary.splitlines()).strip()
                if raw_summary
                else f"{method} {path}"
            )
            has_body = bool(op_spec.get("requestBody")) or method in (
                "POST",
                "PUT",
                "PATCH",
            )

            operations.append(
                {
                    "service": service,
                    "operation": operation,
                    "method": method,
                    "path": path,
                    "path_params": path_params,
                    "summary": summary,
                    "has_body": has_body,
                }
            )

    collisions = {k: v for k, v in seen_pairs.items() if len(v) > 1}
    if collisions:
        formatted = "\n  ".join(
            f"{svc}.{op}: {routes}" for (svc, op), routes in sorted(collisions.items())
        )
        raise RuntimeError(f"Duplicate (service, operation) collisions:\n  {formatted}")

    operations.sort(key=lambda item: (item["service"], item["operation"]))
    services = sorted({item["service"] for item in operations})

    info = spec.get("info", {})
    version = str(info.get("version") or "4.0.0") if isinstance(info, dict) else "4.0.0"

    return {
        "version": version,
        "openapi": str(spec.get("openapi") or "3.1.0"),
        "service_count": len(services),
        "operation_count": len(operations),
        "services": services,
        "operations": operations,
    }


def main() -> None:
    """Load openapi/v4.yaml, generate canonical catalog, and write outputs."""
    with OPENAPI_PATH.open("r", encoding="utf-8") as fh:
        spec = yaml.safe_load(fh)

    catalog = build_catalog(spec)
    rendered = json.dumps(catalog, indent=2) + "\n"

    for output_path in (CATALOG_OUTPUT_PATH, SDK_CATALOG_PATH):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")

    print(
        f"Generated {CATALOG_OUTPUT_PATH} and {SDK_CATALOG_PATH}: "
        f"{catalog['service_count']} services, {catalog['operation_count']} operations"
    )


if __name__ == "__main__":
    main()
