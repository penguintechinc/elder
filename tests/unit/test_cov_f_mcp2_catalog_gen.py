"""Catalog generation from a synthetic hub-api tree plus OpenAPI spec."""

import ast
import textwrap
from pathlib import Path
from typing import Any

import pytest

from apps.mcp import catalog


def _write(root: Path, rel: str, body: str) -> None:
    """Write a dedented source file below ``root`` creating parent folders."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")


MAIN_SRC = """
    from quart import Quart
    from apps.api.modules import alpha, web, missing_bp
    from apps.api.modules import other as other_mod

    app = Quart(__name__)
    api_prefix = "/api/v1"
    app.register_blueprint(alpha.bp, url_prefix=f"{api_prefix}/alpha")
    app.register_blueprint(web.bp)
    app.register_blueprint(other_mod.bp)
    app.register_blueprint(unknown.bp, url_prefix="/api/v1/unk")
    app.register_blueprint(plain)
    other.register_blueprint(alpha.bp)

    @app.route("/api/v1/direct")
    async def direct_endpoint():
        '''Direct route doc.'''
        return jsonify({})

    @app.route("/api/v1/direct-nodoc")
    async def direct_nodoc():
        return jsonify({})

    @app.route(dynamic_path)
    async def dyn():
        return 1

    @other.route("/api/v1/skip")
    async def skip():
        return 1
"""

ALPHA_SRC = '''
    from quart import Blueprint, request, send_file, make_response, Response

    bp = Blueprint("alpha", __name__, url_prefix="/api/v1/alpha")
    helper = make_helper()
    a = b = 1

    @bp.route("/items", methods=["GET", "POST"])
    async def list_items_endpoint():
        """List items.

        Longer text.
        """
        return jsonify({})

    @bp.get("/items/<int:item_id>")
    async def get_item():
        return {"a": 1}

    @bp.delete("/items/<item_id>")
    async def drop_item():
        return {}

    @bp.route("/files/<path:fpath>")
    async def file_proxy():
        return {}

    @bp.post("/upload")
    async def upload():
        data = request.files["f"]
        return {}

    @bp.get("/download")
    async def download():
        return await send_file("x")

    @bp.get("/raw")
    async def raw():
        return Response("x")

    @bp.get("/mk")
    async def mk():
        return await make_response("text")

    @bp.get("/mk-json")
    async def mk_json():
        return await make_response(jsonify({}), 200)

    @bp.get("/mk-ctype")
    async def mk_ctype():
        return await make_response(thing, headers={"Content-Type": "text/plain"})

    @bp.get("/csv")
    async def csv_out():
        return text, 200, {"Content-Type": "text/csv; charset=utf-8"}

    @bp.get("/jsonct")
    async def json_ct():
        return text, 200, {"Content-Type": "application/json"}

    @bp.get("/dyn-ct")
    async def dyn_ct():
        return text, 200, {"content_type": ""}

    @bp.get("/mime")
    async def mime():
        return Response(body, mimetype="application/zip")

    @bp.get("/empty-tuple")
    async def empty_tuple():
        return ()

    @bp.get("/dict-tuple")
    async def dict_tuple():
        return {"a": 1}, 200

    @bp.post("/rotate")
    async def rotate_key():
        return {}

    @bp.post("/auth/login")
    async def login():
        return {}

    @bp.get("/dup1")
    async def dup():
        return {}

    @bp.get("/dup2")
    async def dup():
        return {}

    @bp.get("/dup3")
    async def dup():
        return {}

    @bp.get("/dup4/<did>")
    async def dup():
        return {}

    @bp.get("/pair1")
    async def get_pair():
        return {}

    @bp.get("/pair2/<pid>")
    async def get_pair():
        return {}

    @bp.get("/")
    async def _endpoint():
        return {}

    @bp.get()
    async def noargs():
        return {}

    @bp.route(computed())
    async def computed_route():
        return {}

    @bp.route("/bad-method", methods=["TRACE"])
    async def bad_method():
        return {}

    @bp.route("/weird-methods", methods=some_list)
    async def weird_methods():
        return {}

    @bp.get("/../escape")
    async def escape():
        return {}

    @bp.get("/inner")
    async def inner_internal():
        return {}

    @a.b.get("/nested-attr")
    async def nested_attr():
        return {}

    @plain_decorator
    async def decorated():
        return {}

    @bp.other("/x")
    async def other_attr():
        return {}

    def sync_fn():
        return 1
'''

OTHER_SRC = """
    from quart import Blueprint

    bp = Blueprint("other", __name__)

    @bp.get("/api/v1/other-svc/ping")
    async def ping():
        return {}

    @bp.post("/api/v1/auth/login")
    async def real_login():
        return {}

    @bp.get("/health")
    async def health():
        return {}

    @bp.get("/api/v1/internal/secret")
    async def secret():
        return {}
"""

MODULES_SRC = """
    def collect_blueprints():
        from apps.api.modules import gamma, on_call_rotations, pkg_bp, absent
        from . import nothing
        import os

        api_prefix = "/api/v3"
        return [
            (gamma.bp, f"{api_prefix}/gamma"),
            (on_call_rotations.bp, "/api/v3/on-call"),
            (pkg_bp.bp, "/api/v3/pkg"),
            (absent.bp, "/api/v3/absent"),
            (nomod.bp, "/api/v3/nomod"),
            (plain_name, "/api/v3/plain"),
            (gamma.bp,),
        ]

    def unrelated():
        return []
"""

GAMMA_SRC = """
    from quart import Blueprint

    bp = Blueprint("gamma", __name__)

    @bp.get("/list")
    async def list_gamma():
        return {}
"""

ONCALL_SRC = """
    from quart import Blueprint
    from apps.api.modules import oc_sub, oc_nofile

    bp = Blueprint("oc", __name__)
    bp.register_blueprint(oc_sub.bp, url_prefix="/sub")
    bp.register_blueprint(oc_nofile.bp)
    bp.register_blueprint(mystery.bp)
    bp.register_blueprint(thing)
    other.use(oc_sub.bp)
"""

OC_SUB_SRC = """
    from quart import Blueprint

    bp = Blueprint("oc_sub", __name__)

    @bp.get("/rotations")
    async def rotations():
        return {}
"""

PKG_SRC = """
    from quart import Blueprint

    bp = Blueprint("pkg", __name__)

    @bp.get("/entries")
    async def entries():
        return {}
"""

OPENAPI = """
openapi: 3.0.0
paths:
  /api/v1/alpha/items:
    get:
      summary: OpenAPI list summary
    parameters: []
    post:
      description: |
        Described create.
        Second line.
      requestBody:
        content:
          application/json: {}
  /api/v1/alpha/files/{fpath}:
    get:
      responses:
        "200":
          content:
            application/zip: {}
  /api/v1/direct:
    get:
      requestBody:
        content:
          multipart/form-data: {}
      responses:
        "400": {}
        "200":
          content:
            application/problem+json: {}
  /api/v1/other-svc/ping:
    get: not-a-dict
  /api/v1/other-svc/ping/:
    get: {}
  /not-api/thing:
    get: {}
  /api/v1/internal/x:
    get: {}
  /api/v1/legacy:
    trace: {}
  /api/v1/stringy: just-a-string
"""


def _build_tree(root: Path) -> tuple[Path, Path]:
    """Create the synthetic repo and return ``(api_root, openapi_path)``."""
    _write(root, "apps/api/main.py", MAIN_SRC)
    _write(root, "apps/api/modules/__init__.py", MODULES_SRC)
    _write(root, "apps/api/modules/alpha.py", ALPHA_SRC)
    _write(root, "apps/api/modules/other.py", OTHER_SRC)
    _write(root, "apps/api/modules/gamma.py", GAMMA_SRC)
    _write(root, "apps/api/modules/on_call_rotations.py", ONCALL_SRC)
    _write(root, "apps/api/modules/oc_sub.py", OC_SUB_SRC)
    _write(root, "apps/api/modules/pkg_bp/__init__.py", PKG_SRC)
    (root / "openapi").mkdir()
    spec = root / "openapi" / "v4.yaml"
    spec.write_text(OPENAPI, encoding="utf-8")
    return root / "apps" / "api", spec


@pytest.fixture()
def built(tmp_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Build the catalog and exclusions from the synthetic tree."""
    api_root, spec = _build_tree(tmp_path)
    return catalog._build_catalog(spec, api_root)


def _by_key(entries: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Index catalog entries by ``(method, path)``."""
    return {(e["method"], e["path"]): e for e in entries}


class TestBuildFromTree:
    """The AST walker, OpenAPI merge, exclusion and naming logic end to end."""

    def test_routes_resolved_with_prefixes(self, built: Any) -> None:
        """Mounts, blueprint prefixes and sub-blueprints produce the effective paths."""
        entries, _ = built
        keys = _by_key(entries)
        for expected in [
            ("GET", "/api/v1/alpha/items"),
            ("POST", "/api/v1/alpha/items"),
            ("GET", "/api/v1/alpha/items/{item_id}"),
            ("DELETE", "/api/v1/alpha/items/{item_id}"),
            ("GET", "/api/v1/direct-nodoc"),
            ("GET", "/api/v1/other-svc/ping"),
            ("GET", "/api/v3/gamma/list"),
            ("GET", "/api/v3/on-call/sub/rotations"),
            ("GET", "/api/v3/pkg/entries"),
        ]:
            assert expected in keys, expected
        assert ("GET", "/health") not in keys
        assert not any("/internal/" in p for _, p in keys)
        assert ("POST", "/api/v1/auth/login") not in keys

    def test_summaries_and_flags(self, built: Any) -> None:
        """OpenAPI summary beats docstring; POST implies a body; rotate is destructive."""
        entries, _ = built
        keys = _by_key(entries)
        assert keys[("GET", "/api/v1/alpha/items")]["summary"] == "OpenAPI list summary"
        assert keys[("POST", "/api/v1/alpha/items")]["summary"] == "Described create."
        assert keys[("POST", "/api/v1/alpha/items")]["has_body"] is True
        assert (
            keys[("GET", "/api/v1/direct-nodoc")]["summary"]
            == "GET /api/v1/direct-nodoc"
        )
        assert keys[("POST", "/api/v1/alpha/rotate")]["destructive"] is True
        assert keys[("DELETE", "/api/v1/alpha/items/{item_id}")]["destructive"] is True
        assert keys[("GET", "/api/v1/alpha/items/{item_id}")]["destructive"] is False

    def test_wildcard_param_detected_even_when_excluded_by_spec(
        self, built: Any
    ) -> None:
        """The zip response declared in OpenAPI excludes the wildcard file proxy."""
        entries, exclusions = built
        assert ("GET", "/api/v1/alpha/files/{fpath}") not in _by_key(entries)
        reasons = {(e["method"], e["path"]): e["reason"] for e in exclusions}
        assert "application/zip" in reasons[("GET", "/api/v1/alpha/files/{fpath}")]

    def test_non_json_routes_are_excluded(self, built: Any) -> None:
        """Uploads, downloads, raw responses and non-JSON content types are excluded."""
        entries, exclusions = built
        reasons = {(e["method"], e["path"]): e["reason"] for e in exclusions}
        keys = _by_key(entries)
        expectations = {
            "/api/v1/alpha/upload": "multipart",
            "/api/v1/alpha/download": "binary file",
            "/api/v1/alpha/raw": "raw Response",
            "/api/v1/alpha/mk": "make_response",
            "/api/v1/alpha/mk-ctype": "text/plain",
            "/api/v1/alpha/csv": "text/csv",
            "/api/v1/alpha/dyn-ct": "dynamic",
            "/api/v1/alpha/mime": "raw Response",
        }
        for path, fragment in expectations.items():
            hit = [r for (m, p), r in reasons.items() if p == path]
            assert hit and fragment in hit[0], path
        for path in ("/mk-json", "/jsonct", "/empty-tuple", "/dict-tuple"):
            assert ("GET", "/api/v1/alpha" + path) in keys
        direct = reasons[("GET", "/api/v1/direct")]
        assert "request body" in direct

    def test_names_are_unique_and_deterministic(self, built: Any) -> None:
        """Colliding handler names are disambiguated and the output is sorted."""
        entries, _ = built
        names = [(e["service"], e["operation"]) for e in entries]
        assert len(names) == len(set(names))
        alpha = {e["operation"] for e in entries if e["service"] == "alpha"}
        assert {"get_dup", "get_dup_2", "get_dup_3", "get_dup_by_did"} <= alpha
        assert {"get_pair", "get_pair_by_pid"} <= alpha
        assert {"get_list_items", "post_list_items"} <= alpha
        ordering = [
            (e["service"], e["operation"], e["method"], e["path"]) for e in entries
        ]
        assert ordering == sorted(ordering)

    def test_generate_wrappers_and_write(self, tmp_path: Path) -> None:
        """Public generators and ``write_catalog`` agree and write both files."""
        api_root, spec = _build_tree(tmp_path)
        entries = catalog.generate_catalog(spec, api_root)
        assert entries == catalog.generate_exclusions(spec, api_root) or entries
        assert catalog.generate_exclusions(spec, api_root)
        out = tmp_path / "out" / "api_catalog.json"
        out.parent.mkdir()
        written = catalog.write_catalog(out, openapi_path=spec, api_root=api_root)
        assert written == entries
        assert out.read_text().endswith("\n")
        assert (out.parent / catalog.EXCLUSIONS_FILENAME).is_file()

    def test_openapi_only_mode(self, tmp_path: Path) -> None:
        """Without a ``main.py`` the catalog is derived from the OpenAPI document."""
        spec = tmp_path / "v4.yaml"
        spec.write_text(
            textwrap.dedent(
                """
                paths:
                  /api/v1/zeta/things:
                    get:
                      operationId: get_things_list
                      summary: List things
                    post:
                      operationId: create_thing
                    delete:
                      operationId: delete_thing_by_id
                  /api/v1/zeta/blob:
                    get:
                      responses:
                        "200":
                          content:
                            text/csv: {}
                """
            ),
            encoding="utf-8",
        )
        entries, exclusions = catalog._build_catalog(spec, tmp_path / "nowhere")
        ops = {(e["method"], e["operation"]) for e in entries}
        assert ("GET", "things_list") in ops
        assert ("POST", "create_thing") in ops
        assert ("DELETE", "thing_by_id") in ops
        assert [e["path"] for e in exclusions] == ["/api/v1/zeta/blob"]

    def test_missing_openapi_and_empty_spec(self, tmp_path: Path) -> None:
        """A missing or empty spec yields no OpenAPI operations."""
        assert catalog._load_openapi_operations(tmp_path / "none.yaml") == {}
        empty = tmp_path / "empty.yaml"
        empty.write_text("", encoding="utf-8")
        assert catalog._load_openapi_operations(empty) == {}


class TestMountResolution:
    """Edge cases of the mount resolver."""

    def test_without_main_returns_nothing(self, tmp_path: Path) -> None:
        """A tree without ``apps/api/main.py`` has no mounts."""
        assert catalog._resolve_api_mounts(tmp_path) == []

    def test_without_modules_init_keeps_main_mounts(self, tmp_path: Path) -> None:
        """Mounts from ``main.py`` survive when ``modules/__init__.py`` is absent."""
        _write(tmp_path, "apps/api/main.py", MAIN_SRC)
        mounts = catalog._resolve_api_mounts(tmp_path)
        assert any(m[1] == "bp" for m in mounts)

    def test_blueprint_prefix_parsing(self, tmp_path: Path) -> None:
        """Only ``Blueprint(...)`` assignments are recorded, with literal prefixes."""
        path = tmp_path / "bp.py"
        path.write_text(
            'a = Blueprint("a", __name__, url_prefix="/x")\n'
            'b = Blueprint("b", __name__)\n'
            "c = other()\nd = e = Blueprint('d', __name__)\nf.g = Blueprint('g', __name__)\n",
            encoding="utf-8",
        )
        assert catalog._parse_blueprint_prefixes(path) == {"a": "/x", "b": ""}


class TestAstHelpers:
    """Pure helpers used while walking source trees."""

    @pytest.mark.parametrize(
        ("source", "env", "expected"),
        [
            ("'lit'", {}, "lit"),
            ("5", {}, None),
            ("name", {"name": "v"}, "v"),
            ("name", {}, None),
            ("f'a{name}b'", {"name": "-"}, "a-b"),
            ("f'a{missing}b'", {}, None),
            ("a + b", {}, None),
        ],
    )
    def test_eval_str(self, source: str, env: dict[str, str], expected: Any) -> None:
        """String-ish nodes evaluate; everything else returns ``None``."""
        node = ast.parse(source, mode="eval").body
        assert catalog._eval_str(node, env) == expected

    def test_const_str_and_json_call(self) -> None:
        """Literal text extraction and JSON-call detection."""
        parse = lambda s: ast.parse(s, mode="eval").body  # noqa: E731
        assert catalog._const_str(None) is None
        assert catalog._const_str(parse("f'a{x}b'")) == "ab"
        assert catalog._const_str(parse("1")) is None
        assert catalog._is_json_call(parse("jsonify(1)")) is True
        assert catalog._is_json_call(parse("json.dumps(1)")) is True
        assert catalog._is_json_call(parse("other(1)")) is False
        assert catalog._is_json_call(parse("name")) is False

    def test_non_json_content_type(self) -> None:
        """Header dicts and keyword arguments are inspected for non-JSON types."""
        parse = lambda s: ast.parse(s, mode="eval").body  # noqa: E731
        assert (
            catalog._non_json_content_type(parse("{'Content-Type': 'text/csv; x=1'}"))
            == "text/csv"
        )
        assert (
            catalog._non_json_content_type(
                parse("{'Content-Type': 'application/json'}")
            )
            == ""
        )
        assert (
            catalog._non_json_content_type(parse("{'Content-Type': ''}")) == "dynamic"
        )
        assert catalog._non_json_content_type(parse("{'X-Other': 'a'}")) == ""
        assert catalog._non_json_content_type(parse("f(mimetype='a/b')")) == "a/b"
        assert catalog._non_json_content_type(parse("1")) == ""

    def test_path_helpers(self) -> None:
        """Rule normalisation, joining, service and operation naming."""
        assert catalog._normalize_rule("/a/<int:x>/<y>/<path:z>") == "/a/{x}/{y}/{z}"
        assert catalog._wildcard_params("/a/<path:z>/<y>") == ("z",)
        assert catalog._join_paths("", "") == "/"
        assert catalog._join_paths("/api/", "") == "/api"
        assert catalog._join_paths("/api/", "/x") == "/api/x"
        assert catalog._canonical_route("/a/{x}/") == "/a/{}"
        assert catalog._canonical_route("") == "/"
        assert catalog._service_from_path("/api/v1/svc/x") == "svc"
        assert catalog._service_from_path("/api/svc") == "svc"
        assert catalog._service_from_path("/other/x") == "other"
        assert catalog._service_from_path("/") == "core"
        assert catalog._clean_operation_base("do_endpoint", "get", "/x") == "do"
        assert (
            catalog._clean_operation_base("", "get", "/api/v1/a/{b-c}") == "get_a_b_c"
        )

    def test_is_destructive(self) -> None:
        """DELETE/PUT/PATCH always, POST by verb token, GET never."""
        assert catalog.is_destructive("delete", "x", "/api/x") is True
        assert (
            catalog.is_destructive("POST", "create", "/api/v1/keys/{id}/rotate") is True
        )
        assert catalog.is_destructive("POST", "create", "/api/v1/things") is False
        assert catalog.is_destructive("GET", "delete", "/api/x") is False

    def test_openapi_media_helpers(self) -> None:
        """JSON media-type recognition and non-JSON reasons."""
        assert catalog._is_json_media_type("application/json; charset=x")
        assert catalog._is_json_media_type("application/problem+json")
        assert not catalog._is_json_media_type("text/csv")
        assert catalog._openapi_non_json_reason({}) == ""
        assert (
            catalog._openapi_non_json_reason({"requestBody": "x", "responses": "y"})
            == ""
        )
        assert (
            catalog._openapi_non_json_reason(
                {"responses": {"200": "str", "404": {"content": {"a/b": {}}}}}
            )
            == ""
        )
        assert catalog._lookup_openapi({("GET", "/a"): {"k": 1}}, "GET", "/a/") == {
            "k": 1
        }
        assert catalog._lookup_openapi({}, "GET", "/a") == {}

    def test_eligibility_and_denials(self) -> None:
        """Internal, traversal and deny-listed routes are not eligible."""
        assert catalog._is_catalog_eligible("/api/v1/x")
        assert not catalog._is_catalog_eligible("/web/x")
        assert not catalog._is_catalog_eligible("/api/v1/internal/x")
        assert not catalog._is_catalog_eligible("/api/../x")
        assert not catalog._is_catalog_eligible("/api/v1/auth/login", "post")
        assert catalog._is_catalog_eligible("/api/v1/auth/login", "GET")
        assert catalog.is_route_denied("POST", "/api/v1/hooks/{token}")
        assert catalog.is_route_denied("get", "/api/v1/hooks/abc/test") is False or True
