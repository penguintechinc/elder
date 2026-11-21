"""Catalog-vs-real-route parity, path-converter safety, and non-JSON exclusion tests."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from apps.mcp import catalog as catalog_mod
from apps.mcp.catalog import (
    DEFAULT_EXCLUSIONS_PATH,
    APIOperation,
    generate_catalog,
    generate_exclusions,
    get_operation,
    is_route_excluded,
    load_catalog,
    resolve_operation_path,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_CONVERTER_RE = re.compile(r"<(?:\w+(?:\([^)]*\))?:)?(\w+)>")
_ROUTE_DUMP = (
    "import json, sys; sys.path.insert(0, '.');"
    "from apps.api.main import create_app;"
    "app = create_app();"
    "print('ROUTES=' + json.dumps([[m, r.rule] for r in app.url_map.iter_rules()"
    " for m in sorted(r.methods or [])]))"
)


@pytest.fixture(scope="module")
def real_routes() -> list[tuple[str, str]]:
    """Return every ``(METHOD, rule)`` registered on the real hub-api Quart app."""
    out = subprocess.run(
        [sys.executable, "-c", _ROUTE_DUMP],
        cwd=REPO_ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=True,
    )
    line = next(ln for ln in out.stdout.splitlines() if ln.startswith("ROUTES="))
    routes = [(m, r) for m, r in json.loads(line[len("ROUTES=") :])]
    assert len(routes) > 1000, "route dump looks truncated"
    return routes


def _ops_by_key() -> dict[tuple[str, str], APIOperation]:
    """Index catalog operations by ``(METHOD, path)``."""
    return {(op.method, op.path): op for op in load_catalog(force_reload=True)}


def test_every_catalog_path_equals_a_real_registered_route(
    real_routes: list[tuple[str, str]],
) -> None:
    """Catalog paths (incl. helpdesk and strict trailing slashes) match the live url_map exactly."""
    real = {(m, _CONVERTER_RE.sub(r"{\1}", rule)) for m, rule in real_routes}
    ops = load_catalog(force_reload=True)
    assert len(ops) >= 640
    mismatched = sorted(
        (op.method, op.path) for op in ops if (op.method, op.path) not in real
    )
    assert mismatched == [], f"catalog paths not registered in hub-api: {mismatched}"


def test_catalog_covers_every_real_eligible_route(
    real_routes: list[tuple[str, str]],
) -> None:
    """Every real ``/api`` route is cataloged, deny-listed, excluded as non-JSON, or a websocket."""
    cataloged = set(_ops_by_key())
    excluded = {
        (e["method"], e["path"])
        for e in json.loads(DEFAULT_EXCLUSIONS_PATH.read_text(encoding="utf-8"))
    }
    unexplained = []
    for method, rule in real_routes:
        path = _CONVERTER_RE.sub(r"{\1}", rule)
        if (
            method not in _METHODS
            or not path.startswith("/api/")
            or "/internal/" in path
        ):
            continue
        if catalog_mod.is_route_denied(method, path) or path.endswith("/collab/ws"):
            continue
        if (method, path) not in cataloged and (method, path) not in excluded:
            unexplained.append((method, path))
    assert unexplained == []


def test_wildcard_params_match_real_path_converters(
    real_routes: list[tuple[str, str]],
) -> None:
    """The catalog flags exactly the parameters the real routes declare with ``<path:...>``."""
    expected: dict[tuple[str, str], tuple[str, ...]] = {}
    for method, rule in real_routes:
        if method in _METHODS and rule.startswith("/api/"):
            names = tuple(re.findall(r"<path:(\w+)>", rule))
            if names:
                expected[(method, _CONVERTER_RE.sub(r"{\1}", rule))] = names
    actual = {
        key: op.wildcard_path_params
        for key, op in _ops_by_key().items()
        if op.wildcard_path_params
    }
    covered = {k: v for k, v in expected.items() if k in _ops_by_key()}
    assert covered and actual == covered


def test_helpdesk_paths_use_function_local_prefix() -> None:
    """Helpdesk operations resolve under ``/api/v1/helpdesk`` and never the bare generic prefix."""
    paths = {(op.method, op.path) for op in load_catalog(force_reload=True)}
    assert ("GET", "/api/v1/helpdesk/tickets") in paths
    assert ("POST", "/api/v1/helpdesk/tickets/{ticket_id}/merge") in paths
    assert ("GET", "/api/v1/helpdesk/dashboard/stats") in paths
    assert ("GET", "/api/v1/tickets") not in paths
    assert ("GET", "/api/v1/teams") not in paths
    assert ("GET", "/api/v1/dashboard/stats") not in paths


def test_declared_trailing_slash_is_preserved() -> None:
    """Strict-slash routes keep their declared trailing slash in the catalog and in joins."""
    assert (
        catalog_mod._join_paths("/api/v1/entity-types", "/") == "/api/v1/entity-types/"
    )
    assert catalog_mod._join_paths("/api/v1/x", "") == "/api/v1/x"
    assert catalog_mod._join_paths("/api/v1/x/", "/y") == "/api/v1/x/y"
    paths = {op.path for op in load_catalog(force_reload=True)}
    assert "/api/v1/entity-types/" in paths
    assert "/api/v1/entity-types" not in paths
    op = next(o for o in load_catalog() if o.path == "/api/v1/entity-types/")
    assert resolve_operation_path(op) == "/api/v1/entity-types/"


def test_catalog_paths_agree_with_openapi_spec() -> None:
    """Every OpenAPI-declared eligible operation is cataloged at the identical path."""
    spec = yaml.safe_load(
        (REPO_ROOT / "openapi" / "v4.yaml").read_text(encoding="utf-8")
    )
    cataloged = set(_ops_by_key())
    excluded = {
        (e["method"], e["path"])
        for e in json.loads(DEFAULT_EXCLUSIONS_PATH.read_text(encoding="utf-8"))
    }
    checked = 0
    for raw_path, methods in spec["paths"].items():
        path = catalog_mod._normalize_rule(str(raw_path))
        for method, details in methods.items():
            m = str(method).upper()
            if m not in _METHODS or not isinstance(details, dict):
                continue
            if not catalog_mod._is_catalog_eligible(path, m):
                continue
            checked += 1
            assert (m, path) in cataloged or (m, path) in excluded, (m, path)
    assert checked > 50


@pytest.mark.parametrize(
    ("operation_service", "operation_name", "params", "expected"),
    [
        (
            "builtin-secrets",
            "get_secret",
            {"secret_path": "app/db/password"},
            "/api/v1/builtin-secrets/app/db/password",
        ),
        (
            "builtin-secrets",
            "get_secret",
            {"secret_path": "single"},
            "/api/v1/builtin-secrets/single",
        ),
        (
            "builtin-secrets",
            "get_secret",
            {"secret_path": "my app/key name"},
            "/api/v1/builtin-secrets/my%20app/key%20name",
        ),
        (
            "builtin-secrets",
            "get_secret",
            {"secret_path": "a%2fb"},
            "/api/v1/builtin-secrets/a%252fb",
        ),
    ],
)
def test_path_converter_accepts_multi_segment_values(
    operation_service: str,
    operation_name: str,
    params: dict[str, Any],
    expected: str,
) -> None:
    """``<path:...>`` parameters accept slash-separated values, encoded per segment."""
    op = get_operation(operation_service, operation_name)
    assert op.wildcard_path_params == ("secret_path",)
    assert resolve_operation_path(op, params) == expected


def test_path_converter_mixed_int_and_wildcard_params() -> None:
    """A route mixing an int param and a path param encodes both correctly."""
    op = next(
        o
        for o in load_catalog()
        if o.method == "GET"
        and o.path
        == "/api/v1/google-workspace/providers/{provider_id}/groups/{group_key}"
    )
    assert op.wildcard_path_params == ("group_key",)
    resolved = resolve_operation_path(op, {"provider_id": 7, "group_key": "eng/team-a"})
    assert resolved == "/api/v1/google-workspace/providers/7/groups/eng/team-a"
    with pytest.raises(ValueError):
        resolve_operation_path(op, {"provider_id": "7/../x", "group_key": "ok"})


@pytest.mark.parametrize(
    "bad",
    [
        "..",
        "../etc/passwd",
        "a/../b",
        "a/..",
        "a/./b",
        "./a",
        "a//b",
        "/leading",
        "trailing/",
        "a\\b",
        "a/b\\c",
        "a?x=1",
        "a/b#frag",
        "a\x00b",
        "a\r\nb",
        "a\tb",
        "a/\x07b",
        "%2e%2e/secret",
        "a/%2e%2e/b",
        "%252e%252e/b",
        "a%2f..%2fb",
        "a%2f%2fb",
        "%2fetc/passwd",
        "http://evil.example/x",
        "a/http://evil.example",
        "a%3a%2f%2fevil",
        "a%5cb",
        "a%3fb",
        "a%23b",
        "   ",
        "",
    ],
)
def test_path_converter_rejects_traversal_and_injection(bad: str) -> None:
    """Wildcard params still reject traversal, empty segments, separators tricks, and control chars."""
    op = get_operation("builtin-secrets", "get_secret")
    with pytest.raises(ValueError):
        resolve_operation_path(op, {"secret_path": bad})


@pytest.mark.parametrize("bad", ["a/b", "a/../b", "%2fx", "a%2fb%2f..%2fc", "a\tb"])
def test_single_segment_params_still_reject_slashes(bad: str) -> None:
    """Non-converter parameters remain strictly single-segment."""
    op = get_operation("id", "lookup_village_id")
    assert op.wildcard_path_params == ()
    with pytest.raises(ValueError):
        resolve_operation_path(op, {"village_id": bad})


_EXPECTED_EXCLUSIONS = {
    ("POST", "/api/v1/backup/import"),
    ("GET", "/api/v1/backup/{backup_id}/download"),
    ("GET", "/api/v1/backup/export/{filename}/download"),
    ("POST", "/api/v1/import/{resource}"),
    ("GET", "/api/v1/import/templates/{resource}/download"),
}


def test_non_json_operations_are_excluded_and_documented() -> None:
    """Upload/download routes are absent from the catalog, recorded with a reason, and in the README."""
    exclusions = json.loads(DEFAULT_EXCLUSIONS_PATH.read_text(encoding="utf-8"))
    assert exclusions == generate_exclusions()
    recorded = {(e["method"], e["path"]) for e in exclusions}
    assert _EXPECTED_EXCLUSIONS <= recorded
    assert all(e["reason"] for e in exclusions)

    cataloged = set(_ops_by_key())
    assert recorded.isdisjoint(cataloged)
    assert not any(
        op.operation in {"import_data", "download_backup"}
        for op in load_catalog()
        if op.service == "backup"
    )

    readme = (REPO_ROOT / "apps" / "mcp" / "README.md").read_text(encoding="utf-8")
    for method, path in recorded:
        assert path in readme, f"{method} {path} missing from README exclusion list"


def test_excluded_operations_cannot_be_described_or_invoked() -> None:
    """Excluded operations are unknown to lookup and rejected even as synthetic operations."""
    with pytest.raises(ValueError, match="Unknown API operation"):
        get_operation("backup", "import_data")
    with pytest.raises(ValueError, match="Unknown API operation"):
        get_operation("backup", "download_backup")
    for method, path in sorted(_EXPECTED_EXCLUSIONS):
        assert is_route_excluded(method, path)
        names = tuple(re.findall(r"\{(\w+)\}", path))
        synthetic = APIOperation(
            service="backup",
            operation="synthetic",
            method=method,
            path=path,
            path_params=names,
            summary="",
            has_body=method == "POST",
        )
        with pytest.raises(ValueError, match="not plain JSON"):
            resolve_operation_path(synthetic, {n: "1" for n in names})


def test_openapi_non_json_media_types_drive_exclusion(tmp_path: Path) -> None:
    """OpenAPI requestBody/response media types other than JSON exclude an operation."""
    spec = {
        "openapi": "3.0.3",
        "paths": {
            "/api/v1/zz/ok": {
                "get": {
                    "operationId": "get_ok",
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {}}}}
                    },
                }
            },
            "/api/v1/zz/upload": {
                "post": {
                    "operationId": "post_upload",
                    "requestBody": {"content": {"multipart/form-data": {"schema": {}}}},
                    "responses": {"200": {"description": "ok"}},
                }
            },
            "/api/v1/zz/blob": {
                "get": {
                    "operationId": "get_blob",
                    "responses": {
                        "200": {"content": {"application/zip": {"schema": {}}}}
                    },
                }
            },
            "/api/v1/zz/csv": {
                "get": {
                    "operationId": "get_csv",
                    "responses": {"200": {"content": {"text/csv": {"schema": {}}}}},
                }
            },
            "/api/v1/zz/ld": {
                "post": {
                    "operationId": "post_ld",
                    "requestBody": {
                        "content": {"application/vnd.api+json": {"schema": {}}}
                    },
                    "responses": {"204": {"description": "none"}},
                }
            },
        },
    }
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(yaml.safe_dump(spec), encoding="utf-8")
    entries = generate_catalog(openapi_path=spec_path, api_root=tmp_path / "none")
    paths = {e["path"] for e in entries}
    assert paths == {"/api/v1/zz/ok", "/api/v1/zz/ld"}
    excluded = {
        e["path"]: e["reason"]
        for e in generate_exclusions(spec_path, tmp_path / "none")
    }
    assert set(excluded) == {"/api/v1/zz/upload", "/api/v1/zz/blob", "/api/v1/zz/csv"}
    assert "multipart/form-data" in excluded["/api/v1/zz/upload"]
    assert "application/zip" in excluded["/api/v1/zz/blob"]
    assert "text/csv" in excluded["/api/v1/zz/csv"]
