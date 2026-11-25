"""Extra branch-coverage tests for the SBOM dependency-file parsers.

Pure parsing: real parser classes with realistic file contents. The SBOM
parser is also exercised with documents produced by our own exporters.
"""

# flake8: noqa: E501

import json

import pytest

from apps.api.services.sbom.exporters.cyclonedx import CycloneDXExporter
from apps.api.services.sbom.exporters.spdx import SPDXExporter
from apps.api.services.sbom.parsers.java_parser import JavaDependencyParser
from apps.api.services.sbom.parsers.node_parser import NodeDependencyParser
from apps.api.services.sbom.parsers.python_parser import PythonDependencyParser
from apps.api.services.sbom.parsers.sbom_parser import SBOMParser

POM_NS = "http://maven.apache.org/POM/4.0.0"


# ------------------------------------------------------------------- java


def test_java_parse_guards() -> None:
    """Empty content and unsupported filenames raise, supported names are listed."""
    p = JavaDependencyParser()
    with pytest.raises(ValueError):
        p.parse("  ", "pom.xml")
    with pytest.raises(ValueError, match="Unsupported"):
        p.parse("<a/>", "other.xml")
    with pytest.raises(ValueError, match="Invalid XML"):
        p.parse("<project>", "pom.xml")
    assert p.can_parse("pom.xml") and not p.can_parse("x")
    assert p.get_supported_files() == ["pom.xml", "build.gradle", "build.gradle.kts"]


def test_java_maven_namespaced_with_properties() -> None:
    """Namespaced POMs resolve ${...} properties, scopes and skip incomplete deps."""
    pom = f"""<project xmlns="{POM_NS}">
      <properties><lib.v>3.1</lib.v><project.version>9.9</project.version><empty/></properties>
      <dependencies>
        <dependency><groupId>g</groupId><artifactId>a</artifactId><version>${{lib.v}}</version></dependency>
        <dependency><groupId>g</groupId><artifactId>b</artifactId><version>${{project.version}}</version><scope>test</scope></dependency>
        <dependency><groupId>g</groupId><artifactId>c</artifactId><version>${{missing}}</version><scope>system</scope></dependency>
        <dependency><groupId>g</groupId><artifactId>d</artifactId><scope>weird</scope></dependency>
        <dependency><groupId>g</groupId></dependency>
      </dependencies></project>"""
    comps = JavaDependencyParser().parse(pom, "pom.xml")
    by = {c["name"]: c for c in comps}
    assert by["g:a"]["version"] == "3.1" and by["g:a"]["scope"] == "compile"
    assert by["g:b"]["version"] == "9.9" and by["g:b"]["scope"] == "test"
    assert by["g:c"]["version"] == "${missing}" and by["g:c"]["scope"] == "provided"
    assert by["g:d"]["version"] == "unknown" and by["g:d"]["scope"] == "compile"
    assert len(comps) == 4


def test_java_maven_plain_xml_and_no_properties() -> None:
    """Non-namespaced POMs fall back to plain lookups; missing properties are fine."""
    plain = """<project><properties><v>2</v></properties><dependencies>
        <dependency><groupId>g</groupId><artifactId>a</artifactId><version>${v}</version></dependency>
      </dependencies></project>"""
    assert JavaDependencyParser().parse(plain, "pom.xml")[0]["version"] == "2"
    bare = "<project><dependencies><dependency><groupId>g</groupId><artifactId>a</artifactId></dependency></dependencies></project>"
    assert JavaDependencyParser().parse(bare, "pom.xml")[0]["version"] == "unknown"
    assert JavaDependencyParser().parse("<project></project>", "pom.xml") == []


def test_java_helpers_direct() -> None:
    """Property resolution and scope normalisation handle empty input."""
    p = JavaDependencyParser()
    assert p._resolve_maven_property("", {}) == ""
    assert p._resolve_maven_property("${project.version}", {}) == "unknown"
    assert p._resolve_maven_property("${a}-${b}", {"a": "1", "b": "2"}) == "1-2"
    assert p._normalize_maven_scope("") == "compile"
    assert p._normalize_maven_scope("SYSTEM") == "provided"
    assert p._normalize_maven_scope("odd") == "compile"
    assert p._normalize_maven_scope("Runtime") == "runtime"

    class _Broken:
        """Element stand-in that raises the errors the parser swallows."""

        def find(self, *a):
            """Raise a type error on lookup."""
            raise TypeError("bad element")

    class _Root:
        """Root stand-in yielding one malformed dependency."""

        def findall(self, *a):
            """Return a single broken dependency."""
            return [_Broken()]

        def find(self, *a):
            """Report no properties element."""
            return None

    import apps.api.services.sbom.parsers.java_parser as jp

    original = jp.ET.fromstring
    jp.ET.fromstring = lambda content: _Root()
    try:
        assert p.parse("<x/>", "pom.xml") == []
    finally:
        jp.ET.fromstring = original


def test_java_gradle_variants() -> None:
    """Groovy and Kotlin DSL dependencies map their scopes."""
    p = JavaDependencyParser()
    groovy = "dependencies {\n implementation 'org.a:b:1.0'\n testImplementation \"junit:junit:4.13\"\n weird 'x:y:2'\n}"
    comps = p.parse(groovy, "build.gradle")
    assert [c["scope"] for c in comps] == ["runtime", "test", "runtime"]
    kts = "dependencies {\n api(\"org.a:b:1.0\")\n compileOnly('x:y:2')\n}"
    comps = p.parse(kts, "build.gradle.kts")
    assert [c["scope"] for c in comps] == ["runtime", "provided"]


# ------------------------------------------------------------------- node


def test_node_guards_and_package_json() -> None:
    """Parse guards raise on bad content; package.json handles all dependency kinds."""
    p = NodeDependencyParser()
    with pytest.raises(ValueError):
        p.parse(" ", "package.json")
    with pytest.raises(ValueError, match="Failed to parse"):
        p.parse("{bad", "package.json")
    with pytest.raises(ValueError, match="Unsupported"):
        p.parse("{}", "other.json")
    assert p.can_parse("yarn.lock") and not p.can_parse("x")
    assert len(p.get_supported_files()) == 4
    pkg = json.dumps(
        {
            "dependencies": {"a": "^1.0.0"},
            "devDependencies": {"b": "2.0.0"},
            "peerDependencies": {"c": "3.0.0"},
            "optionalDependencies": {"d": "4.0.0"},
        }
    )
    comps = p.parse(pkg, "package.json")
    assert [c["scope"] for c in comps] == ["runtime", "dev", "runtime", "runtime"]
    assert p.parse("{}", "package.json") == []
    assert p._create_component("x", None, "runtime", True, "f")["purl"] == "pkg:npm/x"


def test_node_package_lock_formats() -> None:
    """Lock v7 (packages) and v6 (dependencies) skip entries without versions."""
    p = NodeDependencyParser()
    v7 = json.dumps(
        {
            "packages": {
                "": {"name": "root"},
                "node_modules/a": {"version": "1.0.0"},
                "node_modules/@s/b": {"version": "2.0.0", "dev": True},
                "node_modules/c": {"resolved": "x"},
                "node_modules/d": "notadict",
            }
        }
    )
    comps = p.parse(v7, "package-lock.json")
    assert {c["name"]: c["scope"] for c in comps} == {"a": "runtime", "@s/b": "dev"}
    v6 = json.dumps(
        {
            "dependencies": {
                "a": {"version": "1.0.0"},
                "b": {"version": "2.0.0", "dev": True},
                "c": {"resolved": "x"},
                "d": "notadict",
            }
        }
    )
    comps = p.parse(v6, "package-lock.json")
    assert {c["name"]: c["scope"] for c in comps} == {"a": "runtime", "b": "dev"}
    assert p.parse("{}", "package-lock.json") == []
    assert p._extract_name_from_path("plain") == "plain"


def test_node_yarn_lock() -> None:
    """Yarn lock parsing handles scoped keys, resolved URLs, git refs and gaps."""
    p = NodeDependencyParser()
    lock = """# yarn lockfile
@angular/core@^16.0.0:
  version "16.0.1"
  resolved "https://registry.npmjs.org/@angular/core/-/core-16.0.1.tgz#abc"

lodash@^4.17.0:
  resolved "https://registry.npmjs.org/lodash/-/lodash-4.17.21.tgz#abc"

gitdep@^1.0.0:
  resolved "git+https://example.com/repo.git#deadbeef"

nores@^2.0.0:
  version "2.0.0"
other@^3.0.0:
  resolved "https://example.com/unknown"

noat:
  version "1"

@broken:
  version "1"
"""
    comps = {c["name"]: c["version"] for c in p.parse(lock, "yarn.lock")}
    assert comps["@angular/core"] == "16.0.1"
    assert comps["lodash"] == "4.17.21"
    assert comps["gitdep"].startswith("git+https")
    assert comps["nores"] == "^2.0.0"
    assert comps["other"] == "^3.0.0"
    assert "noat" not in comps
    assert p._parse_yarn_key("@scope/name@1.0") == ("@scope/name", "1.0")
    assert p._parse_yarn_key("plain") is None
    assert p._extract_yarn_resolved_version("garbage") is None
    assert p._extract_yarn_resolved_version('resolved "npm/pkg#frag"') == "pkg"


def test_node_pnpm_lock() -> None:
    """pnpm lock parsing handles packages, direct deps and malformed sections."""
    p = NodeDependencyParser()
    doc = """
packages:
  /lodash@4.17.21: {dev: false}
  '@scope/pkg@1.2.3': {dev: true}
  plain@2.0.0: notadict
  nokey: {}
dependencies:
  lodash: {version: 4.17.21}
  skipme: {specifier: x}
  strdep: 1.0.0
devDependencies:
  jest: {version: 29.0.0}
  nover: {specifier: x}
"""
    comps = p.parse(doc, "pnpm-lock.yaml")
    names = {(c["name"], c["scope"], c["direct"]) for c in comps}
    assert ("@scope/pkg", "dev", False) in names
    assert ("plain", "runtime", False) in names
    assert ("lodash", "runtime", True) in names
    assert ("jest", "dev", True) in names
    assert not any(c["name"] in ("skipme", "strdep", "nover") for c in comps)
    assert p.parse("---\n", "pnpm-lock.yaml") == []
    assert (
        p.parse(
            "packages: [1]\ndependencies: 5\ndevDependencies: 5\n", "pnpm-lock.yaml"
        )
        == []
    )
    with pytest.raises(ValueError):
        p.parse("a: [unclosed", "pnpm-lock.yaml")
    assert p._parse_pnpm_package_key("plain") is None
    assert p._parse_pnpm_package_key("@a@b@c") == ("@a/b", "c")


# ----------------------------------------------------------------- python


def test_python_requirements_and_guards() -> None:
    """requirements files skip VCS/URL/comment lines and parse specifiers."""
    p = PythonDependencyParser()
    assert p.can_parse("requirements-dev.txt") and p.can_parse("Pipfile")
    assert p.can_parse("setup.py") and not p.can_parse("") and not p.can_parse("x.txt")
    assert "Pipfile" in p.get_supported_files()
    assert p.parse("   ", "requirements.txt") == []
    assert p.parse("x", "unknown.cfg") == []
    reqs = """
# comment
-r other.txt
-e .
git+https://example.com/x.git
svn+https://example.com/y
https://example.com/z.whl
Flask==2.0.3  # pinned
requests>=2.0,<3.0
numpy>=,<3
pandas
   # indented comment
!!!bad
"""
    comps = {c["name"]: c["version"] for c in p.parse(reqs, "requirements.txt")}
    assert comps["flask"] == "2.0.3" and comps["requests"] == "2.0"
    assert comps["pandas"] is None
    assert "!!!bad" not in comps
    assert p._parse_requirement_line("", "f", "runtime") is None
    assert p._parse_requirement_line("# c", "f", "runtime") is None
    assert p._parse_requirement_line("x #only", "f", "runtime")["name"] == "x"
    assert p._parse_requirement_line("#", "f", "runtime") is None
    assert p._parse_requirement_line("  # trailing", "f", "runtime") is None
    assert p._parse_requirement_line("!!!bad", "f", "runtime") is None


def test_python_pyproject_formats() -> None:
    """Poetry and PEP 621 pyproject layouts are both parsed."""
    p = PythonDependencyParser()
    poetry = """
[tool.poetry.dependencies]
python = "^3.11"
Django = "^4.2"
star = "*"
nover = "abc"
tbl = {version = "^1.5", extras = ["x"]}
tblstar = {version = "*"}
tblnover = {git = "https://example.com"}
weird = 5

[tool.poetry.dev-dependencies]
python = "^3.11"
pytest = "^7.0"
"""
    comps = {
        c["name"]: (c["version"], c["scope"]) for c in p.parse(poetry, "pyproject.toml")
    }
    assert comps["django"] == ("4.2", "runtime") and comps["pytest"] == ("7.0", "dev")
    assert comps["star"][0] is None and comps["tbl"][0] == "1.5"
    assert comps["tblstar"][0] is None and comps["tblnover"][0] is None
    assert comps["weird"][0] is None and "python" not in comps
    only_main = '[tool.poetry]\nname = "x"\n'
    assert p.parse(only_main, "pyproject.toml") == []
    pep = """
[project]
dependencies = ["requests>=2.0", "click"]

[project.optional-dependencies]
dev = ["pytest>=7"]
docs = ["sphinx"]
badgroup = "notalist"
"""
    comps = {c["name"]: c["scope"] for c in p.parse(pep, "pyproject.toml")}
    assert comps == {
        "requests": "runtime",
        "click": "runtime",
        "pytest": "dev",
        "sphinx": "runtime",
    }
    assert (
        p.parse(
            '[project]\ndependencies = "x"\n[project.optional-dependencies]\n',
            "pyproject.toml",
        )
        == []
    )
    bad_opt = "[project]\n[project.optional-dependencies]\n"
    assert p.parse(bad_opt, "pyproject.toml") == []
    assert p.parse("[project]\noptional-dependencies = 5\n", "pyproject.toml") == []
    assert p.parse("[other]\nx = 1\n", "pyproject.toml") == []
    with pytest.raises(ValueError, match="Invalid pyproject"):
        p.parse("[unclosed", "pyproject.toml")


def test_python_pipfile_and_setup_py() -> None:
    """Pipfile sections and setup.py install_requires/extras_require are parsed."""
    p = PythonDependencyParser()
    pipfile = """
[packages]
requests = "==2.0.0"
star = "*"
tbl = {version = "==1.2", extras = ["x"]}
tblstar = {version = "*"}
nover = {git = "https://example.com"}
weird = 5

[dev-packages]
pytest = ">=7.0"
"""
    comps = {c["name"]: (c["version"], c["scope"]) for c in p.parse(pipfile, "Pipfile")}
    assert comps["requests"] == ("2.0.0", "runtime") and comps["pytest"] == (
        "7.0",
        "dev",
    )
    assert comps["star"][0] is None and comps["tbl"][0] == "1.2"
    assert (
        comps["tblstar"][0] is None
        and comps["nover"][0] is None
        and comps["weird"][0] is None
    )
    assert p.parse('packages = "notadict"\ndev-packages = "x"\n', "Pipfile") == []
    assert p.parse("[source]\nname = 'x'\n", "Pipfile") == []
    with pytest.raises(ValueError, match="Invalid Pipfile"):
        p.parse("[unclosed", "Pipfile")

    setup = """
setup(
    install_requires=['requests>=2.0', "click"],
    extras_require={'dev': ['pytest>=7'], 'docs': ['sphinx'], "test": ["mock"]},
)
"""
    comps = {c["name"]: c["scope"] for c in p.parse(setup, "setup.py")}
    assert comps == {
        "requests": "runtime",
        "click": "runtime",
        "pytest": "dev",
        "mock": "dev",
    }
    assert p.parse("setup(name='x')", "setup.py") == []
    assert p.parse("setup(extras_require={'docs': ['x']})", "setup.py") == []


def test_python_toml_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing TOML backend produces an actionable error."""
    import apps.api.services.sbom.parsers.python_parser as pp

    monkeypatch.setattr(pp, "tomllib", None)
    p = PythonDependencyParser()
    with pytest.raises(ValueError, match="toml library not available"):
        p.parse("a = 1", "pyproject.toml")
    with pytest.raises(ValueError, match="toml library not available"):
        p.parse("a = 1", "Pipfile")


# -------------------------------------------------------------- sbom parser

COMPONENTS = [
    {
        "name": "flask",
        "version": "2.0.3",
        "purl": "pkg:pypi/flask@2.0.3",
        "description": "web",
        "license_id": "MIT",
        "license_url": "https://example.com/l",
        "repository_url": "https://example.com/r",
        "homepage_url": "https://example.com",
        "hash_sha256": "a" * 64,
        "hash_sha512": "b" * 128,
    },
    {"name": "named", "license_name": "Custom License"},
    {"name": "bare", "purl": "pkg:npm/bare@1"},
]


def test_sbom_parser_cyclonedx_json_roundtrip() -> None:
    """Our CycloneDX JSON output parses back into components."""
    raw = CycloneDXExporter().export_json(COMPONENTS, {"name": "app", "version": "1"})
    comps = SBOMParser().parse(raw, "bom.json")
    by = {c["name"]: c for c in comps}
    assert {"app", "flask", "named", "bare"} <= set(by)
    assert (
        by["flask"]["hash_sha256"] == "a" * 64
        and by["flask"]["hash_sha512"] == "b" * 128
    )
    assert by["flask"]["repository_url"] == "https://example.com/r"
    assert by["flask"]["homepage_url"] == "https://example.com"
    assert by["named"]["license_name"] == "Custom License"


def test_sbom_parser_cyclonedx_xml_roundtrip() -> None:
    """Our CycloneDX XML output parses back into components."""
    raw = CycloneDXExporter().export_xml(COMPONENTS, {"name": "app"})
    comps = SBOMParser().parse(raw, "bom.xml")
    names = {c["name"] for c in comps}
    assert {"flask", "named", "bare"} <= names
    assert next(c for c in comps if c["name"] == "flask")["license_id"] == "MIT"
    plain = "<bom><components><component><name>x</name></component><component><version>1</version></component></components></bom>"
    assert SBOMParser().parse(plain, "b.xml")[0]["version"] == "unknown"


def test_sbom_parser_spdx_roundtrip_and_errors() -> None:
    """SPDX JSON parses (incl. license fallbacks) and format errors are reported."""
    raw = SPDXExporter().export_json(COMPONENTS, {"name": "app", "version": "1"})
    comps = SBOMParser().parse(raw, "sbom.spdx.json")
    assert any(c["name"] == "flask" and c["license_id"] == "MIT" for c in comps)
    doc = json.loads(raw)
    pkg = doc["packages"][1]
    pkg["licenseConcluded"] = "NOASSERTION"
    pkg["licenseDeclared"] = "Apache-2.0"
    doc["packages"][2]["licenseConcluded"] = "NONE"
    doc["packages"][2]["licenseDeclared"] = "NONE"
    doc["packages"][2]["checksums"] = [{"algorithm": "MD5", "checksumValue": "x"}]
    parsed = SBOMParser().parse(json.dumps(doc), "sbom.spdx.json")
    assert any(c["license_id"] == "Apache-2.0" for c in parsed)
    p = SBOMParser()
    with pytest.raises(ValueError, match="Unknown JSON SBOM format"):
        p.parse('{"x": 1}', "x.json")
    with pytest.raises(ValueError, match="Invalid JSON"):
        p.parse("{bad", "x.json")
    with pytest.raises(ValueError, match="Invalid"):
        p.parse("<bom>", "x.xml")
    with pytest.raises(ValueError, match="Unsupported SBOM format"):
        p.parse("plain text", "x.txt")


def test_sbom_parser_cyclonedx_edge_components() -> None:
    """CycloneDX components with odd refs, hashes and licences are handled."""
    p = SBOMParser()
    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.4",
        "components": [
            {
                "name": "x",
                "version": "1",
                "externalReferences": [
                    {"type": "other", "url": "u"},
                    {"type": "vcs", "url": "v"},
                ],
                "hashes": [
                    {"alg": "MD5", "content": "c"},
                    {"alg": "SHA-256", "content": "d"},
                ],
                "licenses": [{"expression": "MIT OR Apache-2.0"}],
            },
            {"name": "y", "licenses": [{"license": {"name": "N", "url": "u"}}]},
            {"licenses": []},
            {"name": "z", "licenses": [{"id": "BSD", "name": "bsd"}]},
        ],
    }
    comps = p.parse(json.dumps(doc), "b.json")
    by = {c["name"]: c for c in comps}
    assert (
        by["x"]["license_id"] == "MIT OR Apache-2.0" and by["x"]["hash_sha256"] == "d"
    )
    assert by["y"]["license_name"] == "N"
    assert by["z"]["license_id"] == "BSD"
    assert not p._extract_package_type_from_purl("")
    assert p._extract_cyclonedx_licenses([])["id"] is None


@pytest.mark.xfail(
    strict=True, reason="bug: quoted yarn.lock keys keep their quote characters"
)
def test_node_yarn_lock_quoted_scoped_key() -> None:
    """Real yarn.lock files quote scoped package keys; names must be unquoted."""
    lock = '"@angular/core@^16.0.0":\n  resolved "https://registry.npmjs.org/@angular/core/-/core-16.0.1.tgz#abc"\n'
    names = [c["name"] for c in NodeDependencyParser().parse(lock, "yarn.lock")]
    assert names == ["@angular/core"]


@pytest.mark.xfail(
    strict=True, reason="bug: versionless scoped pnpm key yields an empty package name"
)
def test_node_pnpm_versionless_scoped_key() -> None:
    """A scoped key with no version is not a valid package entry."""
    assert NodeDependencyParser()._parse_pnpm_package_key("@scope/pkg") is None
