"""Unit tests for the CycloneDX / SPDX SBOM parser.

Covers format detection, CycloneDX JSON + XML, SPDX JSON, the PURL
type-extraction helper, license extraction, and error paths. Pure parser
logic — no external services.
"""

import json

import pytest

from apps.api.services.sbom.parsers.sbom_parser import SBOMParser


@pytest.fixture
def parser():
    return SBOMParser()


class TestCanParse:
    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("bom.json", True),
            ("cyclonedx.xml", True),
            ("my-sbom.json", True),
            ("project.spdx.json", True),
            ("SPDX-document.json", True),
            ("package.json", False),
            ("requirements.txt", False),
        ],
    )
    def test_can_parse(self, parser, filename, expected):
        assert parser.can_parse(filename) is expected

    def test_get_supported_files(self, parser):
        assert "cyclonedx.json" in parser.get_supported_files()
        assert "spdx.json" in parser.get_supported_files()


class TestFormatDetection:
    def test_empty_content_raises(self, parser):
        with pytest.raises(ValueError, match="Invalid or empty"):
            parser.parse("", "bom.json")

    def test_invalid_json_raises(self, parser):
        with pytest.raises(ValueError, match="Invalid JSON"):
            parser.parse("{not json", "bom.json")

    def test_unknown_json_format_raises(self, parser):
        with pytest.raises(ValueError, match="Unknown JSON SBOM format"):
            parser.parse(json.dumps({"foo": "bar"}), "bom.json")

    def test_unsupported_format_raises(self, parser):
        with pytest.raises(ValueError, match="must be JSON or XML"):
            parser.parse("plain text content", "bom.txt")

    def test_invalid_xml_raises(self, parser):
        with pytest.raises(ValueError):
            parser.parse("<bom><broken>", "bom.xml")


class TestCycloneDxJson:
    def _doc(self, **extra):
        base = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.4",
            "components": [
                {
                    "name": "requests",
                    "version": "2.31.0",
                    "purl": "pkg:pypi/requests@2.31.0",
                    "type": "library",
                    "scope": "required",
                    "licenses": [{"license": {"id": "Apache-2.0"}}],
                    "externalReferences": [
                        {"type": "vcs", "url": "https://github.com/psf/requests"},
                        {"type": "website", "url": "https://requests.rtfd.io"},
                    ],
                    "hashes": [
                        {"alg": "SHA-256", "content": "abc"},
                        {"alg": "SHA-512", "content": "def"},
                    ],
                    "description": "HTTP for humans",
                }
            ],
        }
        base.update(extra)
        return json.dumps(base)

    def test_parses_component_fields(self, parser):
        comps = parser.parse(self._doc(), "bom.json")
        assert len(comps) == 1
        c = comps[0]
        assert c["name"] == "requests"
        assert c["version"] == "2.31.0"
        assert c["package_type"] == "pypi"
        assert c["license_id"] == "Apache-2.0"
        assert c["repository_url"] == "https://github.com/psf/requests"
        assert c["homepage_url"] == "https://requests.rtfd.io"
        assert c["hash_sha256"] == "abc"
        assert c["hash_sha512"] == "def"
        assert c["direct"] is True  # scope == required

    def test_main_component_from_metadata(self, parser):
        doc = json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.5",
                "metadata": {"component": {"name": "myapp", "version": "1.0.0"}},
                "components": [],
            }
        )
        comps = parser.parse(doc, "bom.json")
        assert len(comps) == 1
        assert comps[0]["name"] == "myapp"
        assert comps[0]["direct"] is True

    def test_component_without_name_skipped(self, parser):
        doc = json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.4",
                "components": [{"version": "1.0"}],
            }
        )
        assert parser.parse(doc, "bom.json") == []

    def test_unsupported_cyclonedx_version_raises(self, parser):
        doc = json.dumps(
            {"bomFormat": "CycloneDX", "specVersion": "2.0", "components": []}
        )
        with pytest.raises(ValueError, match="Unsupported CycloneDX version"):
            parser.parse(doc, "bom.json")

    def test_license_expression(self, parser):
        doc = json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.4",
                "components": [
                    {
                        "name": "x",
                        "version": "1",
                        "licenses": [{"expression": "MIT OR Apache-2.0"}],
                    }
                ],
            }
        )
        c = parser.parse(doc, "bom.json")[0]
        assert c["license_id"] == "MIT OR Apache-2.0"


class TestCycloneDxXml:
    def test_parses_xml_component(self, parser):
        xml = """<?xml version="1.0"?>
<bom xmlns="http://cyclonedx.org/schema/bom/1.4">
  <components>
    <component type="library" scope="required">
      <name>lodash</name>
      <version>4.17.21</version>
      <purl>pkg:npm/lodash@4.17.21</purl>
      <description>util lib</description>
      <licenses>
        <license><id>MIT</id></license>
      </licenses>
    </component>
  </components>
</bom>"""
        comps = parser.parse(xml, "bom.xml")
        assert len(comps) == 1
        c = comps[0]
        assert c["name"] == "lodash"
        assert c["version"] == "4.17.21"
        assert c["package_type"] == "npm"
        assert c["license_id"] == "MIT"
        assert c["scope"] == "required"


class TestSpdxJson:
    def test_parses_spdx_package(self, parser):
        doc = json.dumps(
            {
                "spdxVersion": "SPDX-2.3",
                "packages": [
                    {
                        "name": "openssl",
                        "versionInfo": "3.0.0",
                        "licenseConcluded": "Apache-2.0",
                        "homepage": "https://openssl.org",
                        "externalRefs": [
                            {
                                "referenceType": "purl",
                                "referenceLocator": "pkg:generic/openssl@3.0.0",
                            }
                        ],
                        "checksums": [
                            {"algorithm": "SHA256", "checksumValue": "aa"},
                            {"algorithm": "SHA512", "checksumValue": "bb"},
                        ],
                    }
                ],
            }
        )
        comps = parser.parse(doc, "spdx.json")
        assert len(comps) == 1
        c = comps[0]
        assert c["name"] == "openssl"
        assert c["version"] == "3.0.0"
        assert c["license_id"] == "Apache-2.0"
        assert c["homepage_url"] == "https://openssl.org"
        assert c["hash_sha256"] == "aa"
        assert c["hash_sha512"] == "bb"

    def test_noassertion_license_falls_back_to_declared(self, parser):
        doc = json.dumps(
            {
                "spdxVersion": "SPDX-2.2",
                "packages": [
                    {
                        "name": "x",
                        "versionInfo": "1",
                        "licenseConcluded": "NOASSERTION",
                        "licenseDeclared": "MIT",
                    }
                ],
            }
        )
        c = parser.parse(doc, "spdx.json")[0]
        assert c["license_id"] == "MIT"

    def test_package_without_name_skipped(self, parser):
        doc = json.dumps(
            {"spdxVersion": "SPDX-2.3", "packages": [{"versionInfo": "1"}]}
        )
        assert parser.parse(doc, "spdx.json") == []

    def test_unsupported_spdx_version_raises(self, parser):
        doc = json.dumps({"spdxVersion": "SPDX-3.0", "packages": []})
        with pytest.raises(ValueError, match="Unsupported SPDX version"):
            parser.parse(doc, "spdx.json")


class TestExtractPackageType:
    @pytest.mark.parametrize(
        "purl,expected",
        [
            ("pkg:pypi/requests@2.0", "pypi"),
            ("pkg:npm/lodash@4.0", "npm"),
            ("pkg:golang/x@1", "go"),
            ("pkg:cargo/serde@1", "cargo"),
            ("pkg:maven/g/a@1", "maven"),
            ("pkg:nuget/n@1", "nuget"),
            ("pkg:gem/rails@7", "gem"),
            ("pkg:deb/debian/bash@5", "deb"),  # unknown mapped to raw type
            ("", None),
            ("not-a-purl", None),
        ],
    )
    def test_extract_package_type_from_purl(self, parser, purl, expected):
        assert parser._extract_package_type_from_purl(purl) == expected


class TestExtractLicenses:
    def test_empty_licenses(self, parser):
        assert parser._extract_cyclonedx_licenses([]) == {
            "id": None,
            "name": None,
            "url": None,
        }

    def test_old_format_direct_fields(self, parser):
        info = parser._extract_cyclonedx_licenses(
            [{"id": "MIT", "name": "MIT License"}]
        )
        assert info["id"] == "MIT"
        assert info["name"] == "MIT License"
