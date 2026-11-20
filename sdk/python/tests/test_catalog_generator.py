"""Tests verifying canonical api-catalog.json parity and deterministic output."""

from __future__ import annotations

import json
import re
from pathlib import Path

from elder_sdk.catalog import ApiCatalog

REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_CATALOG_PATH = REPO_ROOT / "openapi" / "api-catalog.json"
SDK_CATALOG_PATH = (
    REPO_ROOT / "sdk" / "python" / "src" / "elder_sdk" / "api_catalog.json"
)


def test_repo_and_bundled_catalog_are_identical() -> None:
    assert REPO_CATALOG_PATH.exists()
    assert SDK_CATALOG_PATH.exists()
    assert REPO_CATALOG_PATH.read_text(encoding="utf-8") == SDK_CATALOG_PATH.read_text(
        encoding="utf-8"
    )


def test_catalog_entries_schema_and_uniqueness() -> None:
    raw = json.loads(SDK_CATALOG_PATH.read_text(encoding="utf-8"))
    ops = raw["operations"]
    assert raw["operation_count"] == len(ops)
    assert raw["service_count"] == len(raw["services"])

    required_keys = {
        "service",
        "operation",
        "method",
        "path",
        "path_params",
        "summary",
        "has_body",
    }
    seen: set[tuple[str, str]] = set()
    param_re = re.compile(r"\{([^}]+)\}")

    for entry in ops:
        assert set(entry.keys()) == required_keys
        key = (entry["service"], entry["operation"])
        assert key not in seen
        seen.add(key)
        assert entry["service"].isidentifier()
        assert entry["operation"].isidentifier()
        assert entry["method"] in {"GET", "POST", "PUT", "PATCH", "DELETE"}
        assert entry["path_params"] == param_re.findall(entry["path"])
        assert isinstance(entry["has_body"], bool)
        assert entry["summary"]

    # Ensure ApiCatalog.load_default() loads the same operations
    catalog = ApiCatalog.load_default()
    assert len(catalog) == raw["operation_count"]
    assert catalog.services() == raw["services"]
