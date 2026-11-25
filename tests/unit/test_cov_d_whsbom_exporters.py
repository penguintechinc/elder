"""Coverage tests for the SBOM exporters and the SBOM service facade.

Pure-function exporters are exercised with realistic component dicts; the
service facade is exercised with a real parser subclass and a temp directory.
"""

import json
import xml.etree.ElementTree as ET

import pytest

from apps.api.services.sbom.base import BaseDependencyParser
from apps.api.services.sbom.exporters.cyclonedx import CycloneDXExporter
from apps.api.services.sbom.exporters.spdx import SPDXExporter
from apps.api.services.sbom.service import SBOMService

NS = "{http://cyclonedx.org/schema/bom/1.4}"


def _full_component(name: str = "left-pad") -> dict:
    """Build a component dict with every optional field populated."""
    return {
        "name": name,
        "version": "1.2.3",
        "purl": f"pkg:npm/{name}@1.2.3",
        "description": "pads strings",
        "license_id": "MIT",
        "license_url": "https://example.com/mit",
        "repository_url": "https://example.com/repo",
        "homepage_url": "https://example.com",
        "hash_sha256": "a" * 64,
        "hash_sha512": "b" * 128,
        "metadata": {"group": "grp", "publisher": "Pub", "author": "Auth"},
        "scope": "optional",
    }


def test_cdx_json_full_and_invalid_component() -> None:
    """JSON export includes every optional field and skips nameless components."""
    exp = CycloneDXExporter()
    raw = exp.export_json(
        [_full_component(), {"version": "1"}],
        {"name": "app", "version": "2", "description": "d"},
    )
    bom = json.loads(raw)
    assert bom["bomFormat"] == "CycloneDX"
    assert bom["metadata"]["component"]["description"] == "d"
    assert len(bom["components"]) == 1
    comp = bom["components"][0]
    assert comp["group"] == "grp" and comp["publisher"] == "Pub"
    assert comp["author"] == "Auth" and comp["scope"] == "optional"
    assert len(comp["hashes"]) == 2 and len(comp["externalReferences"]) == 2
    assert comp["licenses"][0]["license"] == {
        "id": "MIT",
        "url": "https://example.com/mit",
    }


def test_cdx_minimal_component_and_license_name() -> None:
    """Minimal components omit optional keys; name-only licenses use ``name``."""
    exp = CycloneDXExporter()
    bom = json.loads(
        exp.export_json(
            [
                {"name": "x", "license_name": "Custom", "metadata": "notadict"},
                {"name": "y", "scope": "runtime"},
            ],
            {"name": "app"},
        )
    )
    first, second = bom["components"]
    assert first["licenses"] == [{"license": {"name": "Custom"}}]
    assert first["version"] == "unknown"
    assert "scope" not in second and "licenses" not in second
    assert "description" not in bom["metadata"]["component"]
    no_meta = json.loads(exp.export_json([], {"version": "1"}))
    assert "component" not in no_meta["metadata"]
    assert no_meta["components"] == []


def test_cdx_xml_roundtrip() -> None:
    """XML export contains metadata component, licenses, refs and hashes."""
    exp = CycloneDXExporter()
    comps = [
        _full_component(),
        {"name": "named", "license_name": "Custom"},
        {"name": "bare"},
    ]
    xml = exp.export_xml(comps, {"name": "app", "version": "9", "description": "dd"})
    assert xml.startswith("<?xml")
    root = ET.fromstring(xml)  # noqa: S314 - parsing our own output
    assert root.find(f"{NS}metadata/{NS}component/{NS}description").text == "dd"
    assert (
        root.find(f"{NS}metadata/{NS}tools/{NS}tool/{NS}vendor").text == "PenguinTech"
    )
    components = root.findall(f"{NS}components/{NS}component")
    assert len(components) == 3
    assert components[0].find(f"{NS}licenses/{NS}license/{NS}id").text == "MIT"
    assert components[0].find(f"{NS}purl") is not None
    assert len(components[0].findall(f"{NS}hashes/{NS}hash")) == 2
    assert len(components[0].findall(f"{NS}externalReferences/{NS}reference")) == 2
    assert components[1].find(f"{NS}licenses/{NS}license/{NS}name").text == "Custom"
    assert components[2].find(f"{NS}licenses") is None


def test_cdx_xml_without_metadata_component() -> None:
    """XML export with no metadata omits the main component element."""
    root = ET.fromstring(CycloneDXExporter().export_xml([]))  # noqa: S314
    assert root.find(f"{NS}metadata/{NS}component") is None
    assert root.find(f"{NS}components") is not None


def test_cdx_xml_tool_missing_fields() -> None:
    """Direct dict conversion tolerates tools without vendor/name/version."""
    exp = CycloneDXExporter()
    data = {
        "serialNumber": "urn:uuid:x",
        "version": 1,
        "metadata": {"timestamp": "t", "tools": [{}]},
        "components": [],
    }
    root = exp._dict_to_xml(data)
    assert root.find(f"{NS}metadata/{NS}tools/{NS}tool") is not None
    assert root.find(f"{NS}metadata/{NS}tools/{NS}tool/{NS}name") is None
    data["metadata"]["tools"] = []
    exp._dict_to_xml(data)


def test_spdx_full_and_main_package() -> None:
    """SPDX export includes main package, relationships and all optional fields."""
    exp = SPDXExporter()
    spdx = json.loads(
        exp.export_json(
            [_full_component("@scope/pkg"), {"description": "no name"}],
            {"name": "app", "version": "3", "description": "desc"},
        )
    )
    assert spdx["name"] == "app-SBOM"
    main, pkg = spdx["packages"]
    assert main["description"] == "desc" and main["versionInfo"] == "3"
    assert pkg["SPDXID"] == "SPDXRef-Package--scope-pkg-0"
    assert pkg["supplier"] == "Organization: Pub"
    assert pkg["originator"] == "Person: Auth"
    assert pkg["licenseConcluded"] == "MIT" and pkg["homepage"].startswith("https")
    assert {r["referenceType"] for r in pkg["externalRefs"]} == {"purl", "vcs"}
    assert len(pkg["checksums"]) == 2
    kinds = [r["relationshipType"] for r in spdx["relationships"]]
    assert kinds == ["DESCRIBES", "DEPENDS_ON"]


def test_spdx_minimal_without_metadata() -> None:
    """Without metadata there is no main package or relationships."""
    exp = SPDXExporter()
    spdx = json.loads(
        exp.export_json(
            [{"name": "x", "metadata": "bad"}, {"name": "y", "version": "2"}]
        )
    )
    assert spdx["name"] == "Elder-SBOM"
    assert len(spdx["packages"]) == 2 and spdx["relationships"] == []
    assert "externalRefs" not in spdx["packages"][0]
    assert "checksums" not in spdx["packages"][0]
    meta_no_desc = json.loads(exp.export_json([{"name": "z"}], {"name": "n"}))
    assert "description" not in meta_no_desc["packages"][0]
    assert json.loads(exp.export_json([], {"version": "1"}))["packages"] == []


class _DummyParser(BaseDependencyParser):
    """Minimal concrete parser used to exercise the service facade."""

    def can_parse(self, filename: str) -> bool:
        """Accept only ``deps.txt`` files."""
        return filename == "deps.txt"

    def parse(self, content: str, filename: str) -> list[dict]:
        """Return one component per non-empty line."""
        return [{"name": ln} for ln in content.splitlines() if ln]

    def get_supported_files(self) -> list[str]:
        """Return the single supported filename."""
        return ["deps.txt"]


def test_sbom_service_facade(tmp_path) -> None:
    """Service registers parsers, resolves by filename, parses and scans."""
    svc = SBOMService()
    with pytest.raises(ValueError):
        svc.register_parser(object())  # type: ignore[arg-type]
    assert svc.parse_dependency_file("deps.txt", "a") == []
    parser = _DummyParser()
    svc.register_parser(parser)
    assert svc.get_parser_for_file("deps.txt") is parser
    assert svc.get_parser_for_file("other") is None
    assert svc.parse_dependency_file("deps.txt", "a\nb") == [
        {"name": "a"},
        {"name": "b"},
    ]
    assert svc.parse_dependency_file("other", "a") == []
    res = svc.scan_repository(str(tmp_path))
    assert res["status"] == "success" and res["files_scanned"] == 0
    with pytest.raises(ValueError):
        svc.scan_repository(str(tmp_path / "missing"))


def test_base_parser_helpers() -> None:
    """Base-class validation and version normalization helpers."""
    p = _DummyParser()
    assert p.validate_content(" x ") is True
    assert p.validate_content("   ") is False
    assert p.normalize_version(" 1.0 ") == "1.0"
    assert p.normalize_version("") == "unknown"
