"""Unit tests for the .NET dependency parser (csproj/fsproj/packages.config).

Covers PackageReference extraction, legacy packages.config, scope detection,
and the .NET version-normalization helper. Pure parser logic.
"""

import pytest

from apps.api.services.sbom.parsers.dotnet_parser import DotnetParser


@pytest.fixture
def parser():
    return DotnetParser()


class TestCanParse:
    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("MyApp.csproj", True),
            ("MyApp.fsproj", True),
            ("packages.config", True),
            ("package.json", False),
            ("requirements.txt", False),
        ],
    )
    def test_can_parse(self, parser, filename, expected):
        assert parser.can_parse(filename) is expected

    def test_get_supported_files_is_copy(self, parser):
        files = parser.get_supported_files()
        files.append("mutation")
        assert "mutation" not in parser.get_supported_files()


class TestProjectFile:
    def test_package_references(self, parser):
        content = """<?xml version="1.0"?>
<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <ItemGroup>
    <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />
    <PackageReference Include="xunit" Version="2.6.0" PrivateAssets="All" />
  </ItemGroup>
</Project>"""
        comps = parser.parse(content, "App.csproj")
        by_name = {c["name"]: c for c in comps}
        assert by_name["Newtonsoft.Json"]["version"] == "13.0.3"
        assert by_name["Newtonsoft.Json"]["purl"] == (
            "pkg:nuget/Newtonsoft.Json@13.0.3"
        )
        assert by_name["Newtonsoft.Json"]["package_type"] == "nuget"
        assert by_name["Newtonsoft.Json"]["scope"] == "runtime"
        # PrivateAssets="All" marks a dev dependency.
        assert by_name["xunit"]["scope"] == "dev"

    def test_reference_missing_version_skipped(self, parser):
        content = """<?xml version="1.0"?>
<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <ItemGroup>
    <PackageReference Include="NoVersion" />
  </ItemGroup>
</Project>"""
        assert parser.parse(content, "App.csproj") == []

    def test_condition_test_marks_dev(self, parser):
        content = """<?xml version="1.0"?>
<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <ItemGroup>
    <PackageReference Include="TestPkg" Version="1.0.0" Condition="'$(Config)'=='Test'" />
  </ItemGroup>
</Project>"""
        comps = parser.parse(content, "App.csproj")
        assert comps[0]["scope"] == "dev"

    def test_invalid_xml_raises_valueerror(self, parser):
        with pytest.raises(ValueError, match="Invalid XML"):
            parser.parse("<Project><broken>", "App.csproj")

    def test_empty_content_returns_empty(self, parser):
        assert parser.parse("", "App.csproj") == []


class TestPackagesConfig:
    def test_legacy_packages(self, parser):
        content = """<?xml version="1.0" encoding="utf-8"?>
<packages>
  <package id="EntityFramework" version="6.4.4" targetFramework="net48" />
  <package id="Moq" version="4.18.0" developmentDependency="true" />
</packages>"""
        comps = parser.parse(content, "packages.config")
        by_name = {c["name"]: c for c in comps}
        assert by_name["EntityFramework"]["version"] == "6.4.4"
        assert by_name["EntityFramework"]["scope"] == "runtime"
        assert by_name["Moq"]["scope"] == "dev"

    def test_package_missing_version_skipped(self, parser):
        content = """<?xml version="1.0"?>
<packages><package id="X" /></packages>"""
        assert parser.parse(content, "packages.config") == []


class TestNormalizeVersion:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1.0.0", "1.0.0"),
            ("1.0.0-beta", "1.0.0-beta"),
            ("1.0.*", "1.0.0"),
            ("[1.0.0,2.0.0)", "1.0.0"),
            ("[1.2.3]", "1.2.3"),
            ("  2.0.0  ", "2.0.0"),
            ("", "unknown"),
        ],
    )
    def test_normalize_version(self, parser, raw, expected):
        assert parser.normalize_version(raw) == expected
