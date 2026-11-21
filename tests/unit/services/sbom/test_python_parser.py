"""Unit tests for the Python dependency parser.

Covers requirements.txt, pyproject.toml (Poetry + PEP 621), Pipfile, and
setup.py parsing, plus the version-extraction and PURL-building helpers. Pure
parser logic — no external services required.
"""

import pytest

from apps.api.services.sbom.parsers.python_parser import PythonDependencyParser


@pytest.fixture
def parser():
    return PythonDependencyParser()


class TestCanParse:
    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("requirements.txt", True),
            ("REQUIREMENTS.TXT", True),
            ("requirements-dev.txt", True),
            ("requirements-test.txt", True),
            ("pyproject.toml", True),
            ("Pipfile", True),
            ("pipfile", True),
            ("setup.py", True),
            ("package.json", False),
            ("go.mod", False),
            ("requirements.in", False),
            ("", False),
        ],
    )
    def test_can_parse(self, parser, filename, expected):
        assert parser.can_parse(filename) is expected

    def test_get_supported_files(self, parser):
        files = parser.get_supported_files()
        assert "requirements.txt" in files
        assert "pyproject.toml" in files
        assert "Pipfile" in files


class TestRequirementsTxt:
    def test_basic_pinned_dependency(self, parser):
        comps = parser.parse("requests==2.31.0\n", "requirements.txt")
        assert len(comps) == 1
        c = comps[0]
        assert c["name"] == "requests"
        assert c["version"] == "2.31.0"
        assert c["purl"] == "pkg:pypi/requests@2.31.0"
        assert c["package_type"] == "pypi"
        assert c["scope"] == "runtime"
        assert c["direct"] is True
        assert c["source_file"] == "requirements.txt"

    def test_skips_comments_and_blank_lines(self, parser):
        content = "# a comment\n\nflask==3.0.0\n   \n# trailing\n"
        comps = parser.parse(content, "requirements.txt")
        assert [c["name"] for c in comps] == ["flask"]

    def test_skips_vcs_and_url_and_include_refs(self, parser):
        content = (
            "git+https://github.com/x/y.git\n"
            "-r other.txt\n"
            "-e .\n"
            "https://example.com/pkg.whl\n"
            "hg+https://h/repo\n"
            "click>=8.0\n"
        )
        comps = parser.parse(content, "requirements.txt")
        assert [c["name"] for c in comps] == ["click"]
        assert comps[0]["version"] == "8.0"

    def test_name_without_version(self, parser):
        comps = parser.parse("rich\n", "requirements.txt")
        assert comps[0]["version"] is None
        assert comps[0]["purl"] == "pkg:pypi/rich"

    def test_inline_comment_stripped(self, parser):
        comps = parser.parse("numpy==1.26.0  # pinned\n", "requirements.txt")
        assert comps[0]["version"] == "1.26.0"

    def test_complex_version_range_takes_first_number(self, parser):
        comps = parser.parse("django>=4.2,<5.0\n", "requirements.txt")
        assert comps[0]["name"] == "django"
        assert comps[0]["version"] == "4.2"

    def test_requirements_dash_variant_routes_correctly(self, parser):
        comps = parser.parse("pytest==8.0.0\n", "requirements-dev.txt")
        assert comps[0]["name"] == "pytest"

    def test_empty_content_returns_empty(self, parser):
        assert parser.parse("", "requirements.txt") == []
        assert parser.parse("   \n", "requirements.txt") == []


class TestPyprojectToml:
    def test_poetry_dependencies(self, parser):
        content = """
[tool.poetry]
name = "demo"

[tool.poetry.dependencies]
python = "^3.11"
requests = "^2.31.0"
httpx = { version = ">=0.24.0" }

[tool.poetry.dev-dependencies]
pytest = "^8.0.0"
"""
        comps = parser.parse(content, "pyproject.toml")
        by_name = {c["name"]: c for c in comps}
        assert "python" not in by_name  # python constraint skipped
        assert by_name["requests"]["version"] == "2.31.0"
        assert by_name["requests"]["scope"] == "runtime"
        assert by_name["httpx"]["version"] == "0.24.0"
        assert by_name["pytest"]["scope"] == "dev"

    def test_poetry_wildcard_version_is_none(self, parser):
        content = """
[tool.poetry.dependencies]
anything = "*"
"""
        comps = parser.parse(content, "pyproject.toml")
        assert comps[0]["version"] is None
        assert comps[0]["purl"] == "pkg:pypi/anything"

    def test_pep621_dependencies_and_optional(self, parser):
        content = """
[project]
name = "demo"
dependencies = ["requests>=2.31.0", "click==8.1.7"]

[project.optional-dependencies]
dev = ["pytest>=8.0.0"]
docs = ["sphinx>=7.0.0"]
"""
        comps = parser.parse(content, "pyproject.toml")
        by_name = {c["name"]: c for c in comps}
        assert by_name["requests"]["scope"] == "runtime"
        assert by_name["pytest"]["scope"] == "dev"  # 'dev' group
        assert by_name["sphinx"]["scope"] == "runtime"  # 'docs' not dev-like

    def test_invalid_toml_raises_valueerror(self, parser):
        with pytest.raises(ValueError):
            parser.parse("this is = = not valid toml [[[", "pyproject.toml")


class TestPipfile:
    def test_packages_and_dev_packages(self, parser):
        content = """
[packages]
requests = "==2.31.0"
flask = { version = ">=3.0.0" }
star = "*"

[dev-packages]
pytest = "==8.0.0"
"""
        comps = parser.parse(content, "Pipfile")
        by_name = {c["name"]: c for c in comps}
        assert by_name["requests"]["version"] == "2.31.0"
        assert by_name["requests"]["scope"] == "runtime"
        assert by_name["flask"]["version"] == "3.0.0"
        assert by_name["star"]["version"] is None
        assert by_name["pytest"]["scope"] == "dev"

    def test_invalid_pipfile_raises(self, parser):
        with pytest.raises(ValueError):
            parser.parse("= = = bad", "Pipfile")


class TestSetupPy:
    def test_install_requires_and_extras(self, parser):
        content = """
from setuptools import setup
setup(
    name="demo",
    install_requires=[
        "requests>=2.31.0",
        "click==8.1.7",
    ],
    extras_require={
        "dev": ["pytest>=8.0.0", "ruff"],
        "docs": ["sphinx"],
    },
)
"""
        comps = parser.parse(content, "setup.py")
        by_name = {c["name"]: c for c in comps}
        assert by_name["requests"]["scope"] == "runtime"
        assert by_name["click"]["version"] == "8.1.7"
        assert by_name["pytest"]["scope"] == "dev"
        assert by_name["ruff"]["scope"] == "dev"
        # 'docs' extra is not a dev-like group, so sphinx is not captured here.
        assert "sphinx" not in by_name

    def test_setup_py_without_install_requires(self, parser):
        comps = parser.parse("setup(name='x')\n", "setup.py")
        assert comps == []


class TestRouting:
    def test_unknown_filename_returns_empty(self, parser):
        assert parser.parse("whatever", "unknown.cfg") == []
