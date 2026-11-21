"""Deterministic hub-api catalog generation, loading, and safe route resolution."""

from __future__ import annotations

import ast
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_PATH_PARAM_RE = re.compile(r"\{([^}]+)\}")
_RULE_PARAM_RE = re.compile(r"<(?:\w+(?:\([^)]*\))?:)?(\w+)>")
_WILDCARD_RULE_RE = re.compile(r"<path:(\w+)>")

DEFAULT_CATALOG_PATH = Path(__file__).with_name("api_catalog.json")
EXCLUSIONS_FILENAME = "api_catalog_exclusions.json"
DEFAULT_EXCLUSIONS_PATH = Path(__file__).with_name(EXCLUSIONS_FILENAME)


@dataclass(frozen=True)
class APIOperation:
    """Metadata describing a single cataloged ``hub-api`` REST operation."""

    service: str
    operation: str
    method: str
    path: str
    path_params: tuple[str, ...]
    summary: str
    has_body: bool
    wildcard_path_params: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize operation metadata to JSON-compatible dict."""
        return {
            "service": self.service,
            "operation": self.operation,
            "method": self.method,
            "path": self.path,
            "path_params": list(self.path_params),
            "wildcard_path_params": list(self.wildcard_path_params),
            "summary": self.summary,
            "has_body": self.has_body,
        }


def _normalize_rule(rule_str: str) -> str:
    """Convert Quart ``<conv:name>`` placeholders to OpenAPI ``{name}`` form."""
    return _RULE_PARAM_RE.sub(r"{\1}", rule_str)


def _wildcard_params(rule_str: str) -> tuple[str, ...]:
    """Return names of parameters declared with the ``path`` converter (multi-segment)."""
    return tuple(_WILDCARD_RULE_RE.findall(rule_str))


def _join_paths(prefix: str, suffix: str) -> str:
    """Join a blueprint prefix and route rule the way Quart registers them.

    The declared trailing slash is preserved because ``strict_slashes`` routes
    answer the slash-less form with a redirect, so the cataloged path must match
    the registered rule exactly.
    """
    clean_prefix = (prefix or "").rstrip("/")
    if not suffix:
        return clean_prefix or "/"
    return f"{clean_prefix}/{suffix.lstrip('/')}"


def _eval_str(node: ast.AST, env: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        return None
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                evaluated = _eval_str(value.value, env)
                if evaluated is None:
                    return None
                parts.append(evaluated)
        return "".join(parts)
    return None


def _parse_blueprint_prefixes(py_path: Path) -> dict[str, str]:
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    prefixes: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            val = node.value
            if (
                isinstance(val, ast.Call)
                and isinstance(val.func, ast.Name)
                and val.func.id == "Blueprint"
            ):
                prefix = ""
                for kw in val.keywords:
                    if kw.arg == "url_prefix":
                        prefix = _eval_str(kw.value, {}) or ""
                prefixes[node.targets[0].id] = prefix
    return prefixes


_Route = tuple[str, str, str, str, str, str]
_Mount = tuple[Path, "str | _Route", str]
_BINARY_RESPONSE_CALLS = frozenset({"send_file", "send_from_directory"})


def _non_json_reason(func: ast.AST) -> str:
    """Return why a handler cannot be driven as plain JSON, or ``""`` when it can.

    Detects multipart uploads (``request.files``) and non-JSON responses
    (``send_file``/``send_from_directory``/raw ``Response``) in the handler body.
    """
    reasons: list[str] = []
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "files"
            and isinstance(node.value, ast.Name)
            and node.value.id == "request"
        ):
            reasons.append("multipart/form-data request body (file upload)")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _BINARY_RESPONSE_CALLS:
                reasons.append("binary file download response")
            elif node.func.id == "Response":
                reasons.append("non-JSON response body (raw Response)")
    return "; ".join(dict.fromkeys(reasons))


def _parse_routes_in_file(py_path: Path) -> list[_Route]:
    """Parse ``@bp.<verb>`` route decorators into ``(bp, method, rule, fn, summary, reason)``."""
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    routes: list[_Route] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node) or ""
            summary = doc.strip().splitlines()[0].strip() if doc.strip() else ""
            reason = _non_json_reason(node)
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    attr = dec.func.attr
                    if attr in (
                        "route",
                        "get",
                        "post",
                        "put",
                        "patch",
                        "delete",
                    ) and isinstance(dec.func.value, ast.Name):
                        bp_var = dec.func.value.id
                        if not dec.args:
                            continue
                        rpath = _eval_str(dec.args[0], {})
                        if rpath is None:
                            continue
                        methods = ["GET"] if attr == "route" else [attr.upper()]
                        for kw in dec.keywords:
                            if kw.arg == "methods" and isinstance(
                                kw.value, (ast.List, ast.Tuple)
                            ):
                                methods = [
                                    elt.value.upper()
                                    for elt in kw.value.elts
                                    if isinstance(elt, ast.Constant)
                                    and isinstance(elt.value, str)
                                ]
                        for method in methods:
                            if method in _HTTP_METHODS:
                                routes.append(
                                    (bp_var, method, rpath, node.name, summary, reason)
                                )
    return routes


def _local_string_env(func: ast.AST, base: dict[str, str]) -> dict[str, str]:
    """Extend ``base`` with string constants assigned inside ``func``.

    Blueprint loader functions define their own ``api_prefix``; evaluating their
    prefixes with a global value yields wrong paths (e.g. helpdesk).
    """
    env = dict(base)
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            value = _eval_str(node.value, env)
            if value is not None:
                env[node.targets[0].id] = value
    return env


def _resolve_api_mounts(repo_root: Path) -> list[_Mount]:
    """Resolve every blueprint mount (file, blueprint var, effective prefix) under ``apps/api``."""
    mounts: list[_Mount] = []
    env = {"api_prefix": "/api/v1"}

    main_path = repo_root / "apps" / "api" / "main.py"
    if not main_path.is_file():
        return mounts

    main_tree = ast.parse(
        main_path.read_text(encoding="utf-8"), filename=str(main_path)
    )
    mod_map: dict[str, str] = {}
    for node in ast.walk(main_tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                mod_map[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register_blueprint"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "app"
            and node.args
        ):
            arg0 = node.args[0]
            if isinstance(arg0, ast.Attribute) and isinstance(arg0.value, ast.Name):
                mod_alias = arg0.value.id
                if mod_alias == "web":
                    continue
                mod_full = mod_map.get(mod_alias)
                prefix = ""
                for kw in node.keywords:
                    if kw.arg == "url_prefix":
                        prefix = _eval_str(kw.value, env) or ""
                if mod_full:
                    mounts.append(
                        (
                            repo_root / (mod_full.replace(".", "/") + ".py"),
                            arg0.attr,
                            prefix,
                        )
                    )

    for node in ast.walk(main_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "route"
                    and isinstance(dec.func.value, ast.Name)
                    and dec.func.value.id == "app"
                    and dec.args
                ):
                    rpath = _eval_str(dec.args[0], env)
                    if rpath:
                        doc = ast.get_docstring(node) or ""
                        summary = (
                            doc.strip().splitlines()[0].strip() if doc.strip() else ""
                        )
                        mounts.append(
                            (
                                main_path,
                                (
                                    "DIRECT",
                                    "GET",
                                    rpath,
                                    node.name,
                                    summary,
                                    _non_json_reason(node),
                                ),
                                "",
                            )
                        )

    mods_path = repo_root / "apps" / "api" / "modules" / "__init__.py"
    if not mods_path.is_file():
        return mounts

    mods_tree = ast.parse(
        mods_path.read_text(encoding="utf-8"), filename=str(mods_path)
    )
    for fn in mods_tree.body:
        if isinstance(fn, ast.FunctionDef) and fn.name.endswith("_blueprints"):
            fmod_map: dict[str, str] = {}
            fn_env = _local_string_env(fn, env)
            for node in ast.walk(fn):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for alias in node.names:
                        fmod_map[alias.asname or alias.name] = (
                            f"{node.module}.{alias.name}"
                        )
            for node in ast.walk(fn):
                if isinstance(node, ast.Return) and isinstance(node.value, ast.List):
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Tuple) and len(elt.elts) == 2:
                            bp_ref, pref_node = elt.elts
                            if isinstance(bp_ref, ast.Attribute) and isinstance(
                                bp_ref.value, ast.Name
                            ):
                                mod_full = fmod_map.get(bp_ref.value.id)
                                prefix = _eval_str(pref_node, fn_env) or ""
                                if not mod_full:
                                    continue
                                fpath = repo_root / (mod_full.replace(".", "/") + ".py")
                                if not fpath.is_file():
                                    fpath = (
                                        repo_root
                                        / mod_full.replace(".", "/")
                                        / "__init__.py"
                                    )
                                if fpath.name == "on_call_rotations.py":
                                    _append_sub_blueprints(
                                        repo_root, fpath, prefix, mounts
                                    )
                                else:
                                    mounts.append((fpath, bp_ref.attr, prefix))
    return mounts


def _append_sub_blueprints(
    repo_root: Path,
    parent_file: Path,
    parent_prefix: str,
    mounts: list[_Mount],
) -> None:
    """Append sub-blueprints registered by a parent blueprint file under its prefix."""
    oc_tree = ast.parse(
        parent_file.read_text(encoding="utf-8"), filename=str(parent_file)
    )
    oc_map: dict[str, str] = {}
    for node in ast.walk(oc_tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                oc_map[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    for node in ast.walk(oc_tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register_blueprint"
            and node.args
        ):
            arg0 = node.args[0]
            if isinstance(arg0, ast.Attribute) and isinstance(arg0.value, ast.Name):
                sub_mod = oc_map.get(arg0.value.id)
                if not sub_mod:
                    continue
                sub_prefix = ""
                for kw in node.keywords:
                    if kw.arg == "url_prefix":
                        sub_prefix = _eval_str(kw.value, {}) or ""
                mounts.append(
                    (
                        repo_root / (sub_mod.replace(".", "/") + ".py"),
                        arg0.attr,
                        _join_paths(parent_prefix, sub_prefix),
                    )
                )


_DENIED_ROUTE_SPECS: tuple[tuple[str, str], ...] = (
    # Credential-establishing / unauthenticated auth routes.
    ("GET", "/api/v1/auth/captcha-challenge"),
    ("GET", "/api/v1/auth/guest-enabled"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/portal-auth/login"),
    ("POST", "/api/v1/portal-auth/logout"),
    ("POST", "/api/v1/portal-auth/refresh"),
    ("POST", "/api/v1/portal-auth/register"),
    ("POST", "/api/v1/portal-auth/password/reset"),
    ("POST", "/api/v1/portal-auth/mfa/verify"),
    # SSO / SAML / OIDC browser-flow endpoints (unauthenticated or token-minting).
    ("GET", "/api/v1/sso/saml/login/{}"),
    ("POST", "/api/v1/sso/saml/acs/{}"),
    ("GET", "/api/v1/sso/saml/slo/{}"),
    ("POST", "/api/v1/sso/saml/slo/{}"),
    ("GET", "/api/v1/sso/saml/metadata"),
    ("GET", "/api/v1/sso/saml/metadata/{}"),
    ("GET", "/api/v1/sso/oidc/authorize/{}"),
    ("GET", "/api/v1/sso/oidc/callback"),
    ("POST", "/api/v1/sso/oidc/refresh/{}"),
    # Public share / public document routes (no caller credentials required).
    ("GET", "/api/v1/diagrams/shared/{}"),
    ("GET", "/api/v1/documents/public"),
    ("GET", "/api/v1/documents/public/{}"),
    # Inbound webhook trigger routes (secret-in-path or signature authenticated).
    ("POST", "/api/v1/webhooks/receive/{}"),
    ("POST", "/api/v1/flows-hooks/{}"),
    ("POST", "/api/v1/sync/webhooks/{}/{}"),
    ("POST", "/api/v1/on-call/webhooks/alertmanager/webhook"),
    *(
        (method, "/api/v1/hooks/{}")
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE")
    ),
    *(("GET", "/api/v1/hooks/{}/test"), ("POST", "/api/v1/hooks/{}/test")),
)


def _canonical_route(path: str) -> str:
    """Normalize every ``{param}`` placeholder to ``{}`` for name-independent matching."""
    return _PATH_PARAM_RE.sub("{}", path).rstrip("/") or "/"


DENIED_ROUTES: frozenset[tuple[str, str]] = frozenset(_DENIED_ROUTE_SPECS)


def is_route_denied(method: str, path: str) -> bool:
    """Return True when a route is on the explicit never-invokable deny list.

    The deny list blocks unauthenticated and credential-establishing routes so
    hub-mcp can never act as a brute-force or session-minting proxy for hub-api.
    """
    return (str(method).upper(), _canonical_route(path)) in DENIED_ROUTES


@lru_cache(maxsize=1)
def load_exclusions() -> frozenset[tuple[str, str]]:
    """Load the committed non-JSON exclusion list as canonical ``(METHOD, route)`` keys."""
    if not DEFAULT_EXCLUSIONS_PATH.is_file():
        raise FileNotFoundError(
            f"{DEFAULT_EXCLUSIONS_PATH} is missing; regenerate with "
            "`python3 -m apps.mcp.catalog` (failing closed rather than "
            "exposing upload/download routes)"
        )
    raw = json.loads(DEFAULT_EXCLUSIONS_PATH.read_text(encoding="utf-8"))
    return frozenset(
        (str(item["method"]).upper(), _canonical_route(str(item["path"])))
        for item in raw
    )


def is_route_excluded(method: str, path: str) -> bool:
    """Return True when a route is excluded because it is not plain-JSON invokable.

    Covers multipart uploads and binary/CSV/XML downloads that the JSON-only
    client cannot drive; the tool must never claim to support them.
    """
    return (str(method).upper(), _canonical_route(path)) in load_exclusions()


def _is_catalog_eligible(path: str, method: str | None = None) -> bool:
    """Return True for non-internal ``/api/*`` routes that are not deny-listed."""
    if not (path.startswith("/api/") and "/internal/" not in path and ".." not in path):
        return False
    if method is not None and is_route_denied(method, path):
        return False
    return True


def _service_from_path(path: str) -> str:
    parts = [seg for seg in path.split("/") if seg]
    if len(parts) >= 3 and parts[:2] == ["api", "v1"]:
        return parts[2]
    if len(parts) >= 2 and parts[0] == "api":
        return parts[1]
    return parts[0] if parts else "core"


def _clean_operation_base(raw_name: str, method: str, path: str) -> str:
    name = raw_name.strip()
    if name.endswith("_endpoint"):
        name = name[: -len("_endpoint")]
    if not name:
        parts = [seg.strip("{}").replace("-", "_") for seg in path.split("/") if seg]
        name = f"{method.lower()}_{'_'.join(parts[2:] or parts)}"
    return re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_").lower()


def _load_openapi_operations(
    openapi_path: Path,
) -> dict[tuple[str, str], dict[str, Any]]:
    if not openapi_path.is_file():
        return {}
    import yaml

    spec = yaml.safe_load(openapi_path.read_text(encoding="utf-8")) or {}
    paths = spec.get("paths") or {}
    oa_ops: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        norm_path = _normalize_rule(str(raw_path))
        if not _is_catalog_eligible(norm_path):
            continue
        for raw_method, details in methods.items():
            method = str(raw_method).upper()
            if method not in _HTTP_METHODS or not isinstance(details, dict):
                continue
            oa_ops[(method, norm_path)] = details
    return oa_ops


def _is_json_media_type(media_type: str) -> bool:
    """Return True for ``application/json`` and ``application/*+json`` media types."""
    base = media_type.split(";", 1)[0].strip().lower()
    return base == "application/json" or base.endswith("+json")


def _openapi_non_json_reason(details: dict[str, Any]) -> str:
    """Return why an OpenAPI operation is not plain-JSON invokable, or ``""``.

    A declared request body without a JSON media type (multipart, octet-stream)
    or a declared 2xx response with content but no JSON media type (zip, csv,
    octet-stream) cannot be driven by the JSON-only client.
    """
    reasons: list[str] = []
    body = details.get("requestBody")
    content = body.get("content") if isinstance(body, dict) else None
    if isinstance(content, dict) and content:
        if not any(_is_json_media_type(str(t)) for t in content):
            reasons.append(
                "non-JSON request body (" + ", ".join(sorted(map(str, content))) + ")"
            )
    responses = details.get("responses")
    if isinstance(responses, dict):
        for code, resp in sorted(responses.items(), key=lambda kv: str(kv[0])):
            rcontent = resp.get("content") if isinstance(resp, dict) else None
            if (
                str(code).startswith("2")
                and isinstance(rcontent, dict)
                and rcontent
                and not any(_is_json_media_type(str(t)) for t in rcontent)
            ):
                reasons.append(
                    f"non-JSON {code} response ("
                    + ", ".join(sorted(map(str, rcontent)))
                    + ")"
                )
    return "; ".join(reasons)


def _lookup_openapi(
    oa_ops: dict[tuple[str, str], dict[str, Any]], method: str, path: str
) -> dict[str, Any]:
    """Find OpenAPI details for a route, tolerating a differing trailing slash."""
    for candidate in (path, path.rstrip("/"), path + "/"):
        found = oa_ops.get((method, candidate))
        if found is not None:
            return found
    return {}


def _build_catalog(
    openapi_path: Path | None = None,
    api_root: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Build the invokable catalog and the list of excluded non-JSON operations."""
    repo_root = Path(__file__).resolve().parents[2]
    resolved_openapi = openapi_path or (repo_root / "openapi" / "v4.yaml")
    resolved_api_root = api_root or (repo_root / "apps" / "api")

    oa_ops = _load_openapi_operations(resolved_openapi)

    # Discovered routes keyed by (method, path) -> (raw_op_name, doc_summary, non-JSON reason)
    discovered: dict[tuple[str, str], tuple[str, str, str]] = {}
    wildcards: dict[tuple[str, str], tuple[str, ...]] = {}

    if (resolved_api_root / "main.py").is_file():
        mounts = _resolve_api_mounts(resolved_api_root.parents[1])
        for fpath, bp_var, mount_prefix in mounts:
            if isinstance(bp_var, tuple) and bp_var[0] == "DIRECT":
                _, method, rpath, fn_name, summary, reason = bp_var
                norm_path = _normalize_rule(rpath)
                if _is_catalog_eligible(norm_path, method):
                    discovered.setdefault(
                        (method, norm_path), (fn_name, summary, reason)
                    )
                    wildcards.setdefault((method, norm_path), _wildcard_params(rpath))
                continue
            if not fpath.is_file():
                continue
            bp_prefixes = _parse_blueprint_prefixes(fpath)
            eff_prefix = mount_prefix or bp_prefixes.get(bp_var, "")
            for r_bp, method, rpath, fn_name, summary, reason in _parse_routes_in_file(
                fpath
            ):
                if r_bp != bp_var:
                    continue
                full_rule = _join_paths(eff_prefix, rpath)
                norm_path = _normalize_rule(full_rule)
                if _is_catalog_eligible(norm_path, method):
                    discovered.setdefault(
                        (method, norm_path), (fn_name, summary, reason)
                    )
                    wildcards.setdefault(
                        (method, norm_path), _wildcard_params(full_rule)
                    )
    else:
        for (method, path), details in oa_ops.items():
            raw_id = str(details.get("operationId") or "")
            for prefix in ("get_", "post_", "put_", "patch_", "delete_"):
                if raw_id.startswith(prefix):
                    raw_id = raw_id[len(prefix) :]
                    break
            summary = str(details.get("summary") or "").strip()
            discovered[(method, path)] = (raw_id, summary, "")

    # Exclude operations the JSON-only client cannot drive (uploads, binary/CSV/XML downloads).
    exclusions: list[dict[str, str]] = []
    # First pass: compute base (service, operation_base)
    prelim: list[tuple[str, str, str, str, str, bool]] = []
    for (method, path), (raw_name, ast_summary, ast_reason) in sorted(
        discovered.items()
    ):
        if is_route_denied(method, path):
            continue
        oa = _lookup_openapi(oa_ops, method, path)
        reason = "; ".join(
            dict.fromkeys(r for r in (ast_reason, _openapi_non_json_reason(oa)) if r)
        )
        if reason:
            exclusions.append({"method": method, "path": path, "reason": reason})
            continue
        oa_summary = str(oa.get("summary") or "").strip()
        if not oa_summary and oa.get("description"):
            oa_summary = str(oa["description"]).strip().splitlines()[0].strip()
        summary = oa_summary or ast_summary or f"{method} {path}"
        has_body = bool(oa.get("requestBody")) or method in ("POST", "PUT", "PATCH")
        service = _service_from_path(path)
        op_base = _clean_operation_base(raw_name, method, path)
        prelim.append((service, op_base, method, path, summary, has_body))

    # Disambiguate any collisions inside the same service deterministically
    base_counts = Counter((svc, op) for svc, op, *_ in prelim)
    disambiguated: list[tuple[str, str, str, str, str, bool]] = []
    for svc, op_base, method, path, summary, has_body in prelim:
        op_name = op_base
        if base_counts[(svc, op_base)] > 1:
            method_lower = method.lower()
            if op_name.startswith(f"{method_lower}_"):
                op_name = op_name
            else:
                op_name = f"{method_lower}_{op_name}"
        disambiguated.append((svc, op_name, method, path, summary, has_body))

    # Second pass for any remaining collisions (e.g. same method + handler name on 2 paths)
    pair_counts = Counter((svc, op) for svc, op, *_ in disambiguated)
    used_pairs: set[tuple[str, str]] = set()
    entries: list[dict[str, Any]] = []

    for svc, op_name, method, path, summary, has_body in sorted(
        disambiguated, key=lambda item: (item[0], item[1], item[3], item[2])
    ):
        final_op = op_name
        if pair_counts[(svc, op_name)] > 1:
            path_params = _PATH_PARAM_RE.findall(path)
            if path_params:
                candidate = f"{op_name}_by_{'_'.join(path_params)}"
            else:
                candidate = op_name
            if (svc, candidate) in used_pairs:
                idx = 2
                while (svc, f"{candidate}_{idx}") in used_pairs:
                    idx += 1
                candidate = f"{candidate}_{idx}"
            final_op = candidate

        if (svc, final_op) in used_pairs:
            raise ValueError(f"Duplicate catalog operation {(svc, final_op)!r}")
        used_pairs.add((svc, final_op))

        path_params = _PATH_PARAM_RE.findall(path)
        entries.append(
            APIOperation(
                service=svc,
                operation=final_op,
                method=method,
                path=path,
                path_params=tuple(path_params),
                summary=summary,
                has_body=has_body,
                wildcard_path_params=wildcards.get((method, path), ()),
            ).to_dict()
        )

    entries.sort(key=lambda e: (e["service"], e["operation"], e["method"], e["path"]))
    exclusions.sort(key=lambda e: (e["path"], e["method"]))
    return entries, exclusions


def generate_catalog(
    openapi_path: Path | None = None,
    api_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Build deterministic ``hub-api`` catalog dicts from ``openapi/v4.yaml`` and ``apps/api``."""
    return _build_catalog(openapi_path, api_root)[0]


def generate_exclusions(
    openapi_path: Path | None = None,
    api_root: Path | None = None,
) -> list[dict[str, str]]:
    """List the operations kept out of the catalog because they are not plain JSON."""
    return _build_catalog(openapi_path, api_root)[1]


def write_catalog(
    output_path: Path | None = None,
    *,
    openapi_path: Path | None = None,
    api_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Generate and write ``api_catalog.json`` deterministically."""
    target = output_path or DEFAULT_CATALOG_PATH
    entries, exclusions = _build_catalog(openapi_path, api_root)
    target.write_text(
        json.dumps(entries, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    target.with_name(EXCLUSIONS_FILENAME).write_text(
        json.dumps(exclusions, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    _load_cached_catalog.cache_clear()
    load_exclusions.cache_clear()
    return entries


@lru_cache(maxsize=4)
def _load_cached_catalog(resolved_path: str) -> tuple[APIOperation, ...]:
    path = Path(resolved_path)
    if path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        raw = generate_catalog()

    ops: list[APIOperation] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if is_route_denied(str(item["method"]), str(item["path"])) or (
            is_route_excluded(str(item["method"]), str(item["path"]))
        ):
            continue
        key = (str(item["service"]), str(item["operation"]))
        if key in seen:
            raise ValueError(f"Duplicate catalog entry {key!r} in {path}")
        seen.add(key)
        ops.append(
            APIOperation(
                service=str(item["service"]),
                operation=str(item["operation"]),
                method=str(item["method"]).upper(),
                path=str(item["path"]),
                path_params=tuple(str(p) for p in item.get("path_params", [])),
                summary=str(item.get("summary") or ""),
                has_body=bool(item.get("has_body")),
                wildcard_path_params=tuple(
                    str(p) for p in item.get("wildcard_path_params", [])
                ),
            )
        )
    return tuple(ops)


def load_catalog(
    catalog_path: Path | None = None,
    *,
    force_reload: bool = False,
) -> tuple[APIOperation, ...]:
    """Load the catalog of ``hub-api`` operations."""
    if force_reload:
        _load_cached_catalog.cache_clear()
    target = (catalog_path or DEFAULT_CATALOG_PATH).resolve()
    return _load_cached_catalog(str(target))


def list_services(
    catalog: Iterable[APIOperation] | None = None,
) -> list[dict[str, Any]]:
    """Return sorted list of service namespaces and operation counts."""
    ops = load_catalog() if catalog is None else tuple(catalog)
    counts: dict[str, int] = defaultdict(int)
    for op in ops:
        counts[op.service] += 1
    return [{"service": svc, "operation_count": counts[svc]} for svc in sorted(counts)]


def list_operations(
    service: str | None = None,
    method: str | None = None,
    search: str | None = None,
    catalog: Iterable[APIOperation] | None = None,
) -> list[APIOperation]:
    """Filter catalog operations by service, HTTP method, and/or search substring."""
    ops = load_catalog() if catalog is None else tuple(catalog)
    norm_service = (
        service.strip().lower()
        if isinstance(service, str) and service.strip()
        else None
    )
    norm_method = (
        method.strip().upper() if isinstance(method, str) and method.strip() else None
    )
    norm_search = (
        search.strip().lower() if isinstance(search, str) and search.strip() else None
    )

    matched: list[APIOperation] = []
    for op in ops:
        if norm_service is not None and op.service.lower() != norm_service:
            continue
        if norm_method is not None and op.method != norm_method:
            continue
        if norm_search is not None:
            haystack = f"{op.service} {op.operation} {op.method} {op.path} {op.summary}".lower()
            if norm_search not in haystack:
                continue
        matched.append(op)
    return matched


def get_operation(
    service: str,
    operation: str,
    catalog: Iterable[APIOperation] | None = None,
) -> APIOperation:
    """Look up a single cataloged operation by ``(service, operation)``."""
    norm_service = (service or "").strip().lower()
    norm_op = (operation or "").strip().lower()
    if not norm_service or not norm_op:
        raise ValueError("service and operation must be non-empty strings")

    ops = load_catalog() if catalog is None else tuple(catalog)
    for op in ops:
        if op.service.lower() == norm_service and op.operation.lower() == norm_op:
            return op
    raise ValueError(f"Unknown API operation: {service!r}.{operation!r}")


def _has_control_chars(value: str) -> bool:
    """Return True when ``value`` contains any ASCII control character."""
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)


def _decoded_forms(raw: str) -> list[str]:
    """Return the raw value plus its once- and twice-percent-decoded forms."""
    once = unquote(raw)
    return [raw, once, unquote(once)]


def _coerce_param(name: str, value: Any) -> str:
    """Convert a path-parameter value to a stripped, non-empty string."""
    if value is None or isinstance(value, (bool, dict, list)):
        raise ValueError(f"Invalid path parameter {name!r}")
    raw = str(value).strip()
    if not raw:
        raise ValueError(f"Path parameter {name!r} must not be empty")
    return raw


def _validate_path_segment(name: str, value: Any) -> str:
    """Validate a single-segment path parameter and percent-encode it."""
    raw = _coerce_param(name, value)

    # Reject path traversal, extra slashes, query/fragment injection, and control chars
    # across raw and multi-decoded forms.
    for cand in _decoded_forms(raw):
        if (
            ".." in cand
            or "/" in cand
            or "\\" in cand
            or "?" in cand
            or "#" in cand
            or _has_control_chars(cand)
        ):
            raise ValueError(
                f"Unsafe characters or path traversal in path parameter {name!r}"
            )
    return quote(raw, safe="")


def _validate_wildcard_path(name: str, value: Any) -> str:
    """Validate a ``<path:...>`` parameter: multi-segment, each segment checked.

    Slashes are allowed only as separators between non-empty, non-dot segments;
    traversal, backslashes, query/fragment markers, control characters, scheme
    markers and percent-encoded variants of those (decoded up to twice) are
    rejected. Each segment is percent-encoded individually.
    """
    raw = _coerce_param(name, value)
    for cand in _decoded_forms(raw):
        if (
            ".." in cand
            or "\\" in cand
            or "?" in cand
            or "#" in cand
            or "://" in cand
            or _has_control_chars(cand)
        ):
            raise ValueError(
                f"Unsafe characters or path traversal in path parameter {name!r}"
            )
        segments = cand.split("/")
        if any(seg.strip() in ("", ".") for seg in segments):
            raise ValueError(
                f"Empty or dot segment in path parameter {name!r} is not allowed"
            )
    return "/".join(quote(seg, safe="") for seg in raw.split("/"))


def resolve_operation_path(
    op: APIOperation,
    path_params: dict[str, Any] | None = None,
) -> str:
    """Validate and substitute path parameters into a cataloged ``/api/...`` route."""
    if not _is_catalog_eligible(op.path, op.method):
        raise ValueError(
            f"Operation {op.method} {op.path!r} is not eligible for invocation"
        )

    if is_route_excluded(op.method, op.path):
        raise ValueError(
            f"Operation {op.method} {op.path!r} is not plain JSON "
            "(upload or binary/CSV/XML download) and cannot be invoked"
        )

    provided = path_params or {}
    if not isinstance(provided, dict):
        raise ValueError("path_params must be a JSON object")

    expected = set(op.path_params)
    missing = [p for p in op.path_params if p not in provided or provided[p] is None]
    if missing:
        raise ValueError(
            f"Missing required path parameter(s) for {op.service}.{op.operation}: "
            f"{', '.join(missing)}"
        )

    unexpected = sorted(set(provided.keys()) - expected)
    if unexpected:
        raise ValueError(
            f"Unexpected path parameter(s) for {op.service}.{op.operation}: "
            f"{', '.join(unexpected)}"
        )

    resolved = op.path
    for param in op.path_params:
        if param in op.wildcard_path_params:
            encoded = _validate_wildcard_path(param, provided[param])
        else:
            encoded = _validate_path_segment(param, provided[param])
        resolved = resolved.replace(f"{{{param}}}", encoded)

    if (
        not resolved.startswith("/api/")
        or "/internal/" in resolved
        or ".." in resolved
        or "//" in resolved
        or "://" in resolved
        or "?" in resolved
        or "#" in resolved
    ):
        raise ValueError(f"Resolved path failed safety validation: {resolved!r}")

    return resolved


if __name__ == "__main__":
    written = write_catalog()
    print(
        f"Wrote {len(written)} operations across {len(list_services())} services to {DEFAULT_CATALOG_PATH}"
    )
