"""Catalog loading, lookup and safe path resolution."""

import json
from pathlib import Path
from typing import Any

import pytest

from apps.mcp import catalog
from apps.mcp.catalog import APIOperation


@pytest.fixture(autouse=True)
def _clear_caches() -> Any:
    """Reset the catalog caches around every test so patched paths never leak."""
    catalog._load_cached_catalog.cache_clear()
    catalog.load_exclusions.cache_clear()
    yield
    catalog._load_cached_catalog.cache_clear()
    catalog.load_exclusions.cache_clear()


def _entry(**kw: Any) -> dict[str, Any]:
    """Build a raw catalog JSON entry with sensible defaults."""
    base = {
        "service": "svc",
        "operation": "op",
        "method": "get",
        "path": "/api/v1/svc/{sid}",
        "path_params": ["sid"],
        "summary": "S",
        "has_body": False,
    }
    base.update(kw)
    return base


def _op(
    path: str = "/api/v1/svc/{sid}", method: str = "GET", **kw: Any
) -> APIOperation:
    """Build an operation whose path parameters are derived from the path."""
    import re

    params = tuple(re.findall(r"\{([^}]+)\}", path))
    return APIOperation("svc", "op", method, path, params, "s", False, **kw)


class TestExclusions:
    """The committed exclusion list fails closed."""

    def test_missing_file_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """No exclusion file means the catalog refuses to load."""
        monkeypatch.setattr(catalog, "DEFAULT_EXCLUSIONS_PATH", tmp_path / "nope.json")
        with pytest.raises(FileNotFoundError):
            catalog.load_exclusions()

    def test_loaded_keys_are_canonical(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Entries are normalised to upper-case method and ``{}`` placeholders."""
        path = tmp_path / "ex.json"
        path.write_text(json.dumps([{"method": "get", "path": "/api/v1/x/{id}/"}]))
        monkeypatch.setattr(catalog, "DEFAULT_EXCLUSIONS_PATH", path)
        assert catalog.is_route_excluded("GET", "/api/v1/x/{other}") is True
        assert catalog.is_route_excluded("POST", "/api/v1/x/{id}") is False


class TestLoading:
    """Catalog JSON parsing and caching."""

    @pytest.fixture(autouse=True)
    def _empty_exclusions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Point exclusions at an empty list."""
        path = tmp_path / "exclusions.json"
        path.write_text("[]")
        monkeypatch.setattr(catalog, "DEFAULT_EXCLUSIONS_PATH", path)

    def test_load_from_file_skips_denied_and_excluded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Denied and excluded routes are dropped; destructive falls back to the heuristic."""
        ex = tmp_path / "ex2.json"
        ex.write_text(json.dumps([{"method": "GET", "path": "/api/v1/svc/blob"}]))
        monkeypatch.setattr(catalog, "DEFAULT_EXCLUSIONS_PATH", ex)
        path = tmp_path / "cat.json"
        path.write_text(
            json.dumps(
                [
                    _entry(),
                    _entry(operation="blob", path="/api/v1/svc/blob", path_params=[]),
                    _entry(
                        operation="login",
                        method="POST",
                        path="/api/v1/auth/login",
                        path_params=[],
                    ),
                    _entry(
                        operation="rotate",
                        method="POST",
                        path="/api/v1/svc/rotate",
                        path_params=[],
                        wildcard_path_params=["w"],
                    ),
                    _entry(
                        operation="flagged",
                        method="GET",
                        path="/api/v1/svc/f",
                        path_params=[],
                        destructive=True,
                        summary=None,
                    ),
                ]
            )
        )
        ops = catalog.load_catalog(path)
        assert [o.operation for o in ops] == ["op", "rotate", "flagged"]
        assert ops[0].method == "GET" and ops[0].destructive is False
        assert ops[1].destructive is True and ops[1].wildcard_path_params == ("w",)
        assert ops[2].destructive is True and ops[2].summary == ""

    def test_duplicates_rejected(self, tmp_path: Path) -> None:
        """Two entries with the same service/operation make loading fail."""
        path = tmp_path / "dup.json"
        path.write_text(json.dumps([_entry(), _entry(path="/api/v1/svc/other")]))
        with pytest.raises(ValueError, match="Duplicate catalog entry"):
            catalog.load_catalog(path)

    def test_missing_file_regenerates(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A missing catalog file triggers in-memory generation."""
        monkeypatch.setattr(catalog, "generate_catalog", lambda: [_entry()])
        ops = catalog.load_catalog(tmp_path / "absent.json")
        assert [o.operation for o in ops] == ["op"]

    def test_force_reload_rereads(self, tmp_path: Path) -> None:
        """``force_reload`` clears the cache so file edits are seen."""
        path = tmp_path / "cat.json"
        path.write_text(json.dumps([_entry()]))
        assert len(catalog.load_catalog(path)) == 1
        path.write_text(json.dumps([_entry(), _entry(operation="two")]))
        assert len(catalog.load_catalog(path)) == 1
        assert len(catalog.load_catalog(path, force_reload=True)) == 2


class TestQueries:
    """Listing and lookup helpers over an explicit catalog."""

    OPS = (
        APIOperation(
            "alpha",
            "list_things",
            "GET",
            "/api/v1/alpha/things",
            (),
            "List things",
            False,
        ),
        APIOperation(
            "alpha", "make_thing", "POST", "/api/v1/alpha/things", (), "Create", True
        ),
        APIOperation("beta", "ping", "GET", "/api/v1/beta/ping", (), "Health", False),
    )

    def test_list_services(self) -> None:
        """Services are sorted with their counts."""
        assert catalog.list_services(self.OPS) == [
            {"service": "alpha", "operation_count": 2},
            {"service": "beta", "operation_count": 1},
        ]

    def test_list_services_default_catalog(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without an explicit catalog the loaded one is used."""
        monkeypatch.setattr(catalog, "load_catalog", lambda: self.OPS)
        assert len(catalog.list_services()) == 2
        assert len(catalog.list_operations()) == 3
        assert catalog.get_operation("alpha", "make_thing").method == "POST"

    @pytest.mark.parametrize(
        ("kwargs", "count"),
        [
            ({}, 3),
            ({"service": " ALPHA "}, 2),
            ({"method": "post"}, 1),
            ({"search": "HEALTH"}, 1),
            ({"service": "alpha", "method": "GET", "search": "list"}, 1),
            ({"service": "  ", "method": "  ", "search": "  "}, 3),
            ({"service": "beta", "search": "nomatch"}, 0),
        ],
    )
    def test_list_operations_filters(self, kwargs: dict[str, str], count: int) -> None:
        """Each filter narrows the result and blanks are ignored."""
        assert len(catalog.list_operations(catalog=self.OPS, **kwargs)) == count

    def test_get_operation(self) -> None:
        """Lookup is case-insensitive and errors are explicit."""
        assert (
            catalog.get_operation(" ALPHA ", "LIST_THINGS", self.OPS).service == "alpha"
        )
        with pytest.raises(ValueError, match="non-empty"):
            catalog.get_operation("", "x", self.OPS)
        with pytest.raises(ValueError, match="non-empty"):
            catalog.get_operation("alpha", "  ", self.OPS)
        with pytest.raises(ValueError, match="Unknown API operation"):
            catalog.get_operation("alpha", "nope", self.OPS)


class TestResolvePath:
    """Path parameter substitution rejects every injection shape."""

    @pytest.fixture(autouse=True)
    def _empty_exclusions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Use an empty exclusion list."""
        path = tmp_path / "exclusions.json"
        path.write_text("[]")
        monkeypatch.setattr(catalog, "DEFAULT_EXCLUSIONS_PATH", path)

    def test_success_encodes_segment(self) -> None:
        """Valid values are stripped and percent-encoded."""
        assert (
            catalog.resolve_operation_path(_op(), {"sid": " a b "})
            == "/api/v1/svc/a%20b"
        )

    def test_no_params_none_given(self) -> None:
        """An operation without parameters accepts ``None``."""
        assert (
            catalog.resolve_operation_path(_op("/api/v1/svc/list"), None)
            == "/api/v1/svc/list"
        )

    def test_ineligible_and_excluded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Internal routes and excluded routes refuse invocation."""
        with pytest.raises(ValueError, match="not eligible"):
            catalog.resolve_operation_path(_op("/api/v1/internal/x"), {})
        ex = tmp_path / "ex.json"
        ex.write_text(json.dumps([{"method": "GET", "path": "/api/v1/svc/blob"}]))
        monkeypatch.setattr(catalog, "DEFAULT_EXCLUSIONS_PATH", ex)
        catalog.load_exclusions.cache_clear()
        with pytest.raises(ValueError, match="not plain JSON"):
            catalog.resolve_operation_path(_op("/api/v1/svc/blob"), {})

    def test_param_shape_errors(self) -> None:
        """Non-dict, missing and unexpected parameters are rejected."""
        with pytest.raises(ValueError, match="JSON object"):
            catalog.resolve_operation_path(_op(), ["x"])  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="Missing"):
            catalog.resolve_operation_path(_op(), {})
        with pytest.raises(ValueError, match="Missing"):
            catalog.resolve_operation_path(_op(), {"sid": None})
        with pytest.raises(ValueError, match="Unexpected"):
            catalog.resolve_operation_path(_op(), {"sid": "1", "extra": "2"})

    @pytest.mark.parametrize("value", [None, True, {"a": 1}, [1], "   "])
    def test_invalid_values(self, value: Any) -> None:
        """Non-scalar or blank values are refused."""
        with pytest.raises(ValueError):
            catalog._coerce_param("p", value)

    @pytest.mark.parametrize(
        "value",
        [
            "..",
            "a/b",
            "a\\b",
            "a?b",
            "a#b",
            "a\x01b",
            "%2e%2e",
            "a%2fb",
            "%252e%252e",
            "a\x7fb",
        ],
    )
    def test_segment_injection(self, value: str) -> None:
        """Traversal, separators, markers and encoded variants are refused."""
        with pytest.raises(ValueError, match="Unsafe"):
            catalog._validate_path_segment("p", value)

    def test_wildcard_valid(self) -> None:
        """Wildcards keep separators and encode each segment."""
        op = _op("/api/v1/svc/files/{fpath}", wildcard_path_params=("fpath",))
        assert (
            catalog.resolve_operation_path(op, {"fpath": "dir/my file.txt"})
            == "/api/v1/svc/files/dir/my%20file.txt"
        )

    @pytest.mark.parametrize(
        "value",
        [
            "a/../b",
            "a\\b",
            "a?b",
            "a#b",
            "http://x/y",
            "a\x00b",
            "a//b",
            "/a",
            "a/./b",
            "a/%2e%2e/b",
            "a/",
            "%2e/x",
        ],
    )
    def test_wildcard_injection(self, value: str) -> None:
        """Unsafe wildcard values are refused."""
        with pytest.raises(ValueError):
            catalog._validate_wildcard_path("p", value)

    def test_final_safety_check(self) -> None:
        """A path that is itself malformed fails the final validation."""
        with pytest.raises(ValueError, match="safety validation"):
            catalog.resolve_operation_path(_op("/api//svc/{sid}"), {"sid": "1"})

    def test_helpers(self) -> None:
        """Decoding and control-character helpers."""
        assert catalog._decoded_forms("%2541") == ["%2541", "%41", "A"]
        assert catalog._has_control_chars("a\tb") and not catalog._has_control_chars(
            "ab"
        )


def test_module_main_writes_catalog(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Running the module as a script writes the catalog and prints a summary."""
    import runpy

    written: list[Any] = []
    monkeypatch.setattr(catalog, "write_catalog", lambda: written.append(1) or [])
    monkeypatch.setattr(catalog, "list_services", lambda: [])
    # run_path re-executes the file, so patch the freshly defined names via a probe instead.
    ns = runpy.run_path(catalog.__file__, run_name="not_main")
    assert callable(ns["write_catalog"]) and not written
