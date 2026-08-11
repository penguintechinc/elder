"""SBOM format parser for CycloneDX and SPDX.

Parses standard SBOM formats (CycloneDX 1.4+ and SPDX 2.3) and extracts
component information. Supports both JSON and XML formats for CycloneDX,
and JSON format for SPDX.
"""

# flake8: noqa: E501

import json
import re
from typing import Any, Dict, List, Optional
from xml.etree.ElementTree import Element  # For type hints only

import defusedxml.ElementTree as ET

from ..base import BaseDependencyParser


class SBOMParser(BaseDependencyParser):
    """Parser for CycloneDX and SPDX SBOM files.

    Supports:
    - CycloneDX 1.4+ (JSON and XML)
    - SPDX 2.3 (JSON)

    Extracts component information including name, version, PURL,
    license information, and relationships.
    """

    def can_parse(self, filename: str) -> bool:
        """Check if this parser can handle the given SBOM file.

        Args:
            filename: Name of the file to check.

        Returns:
            True if file is a supported SBOM format, False otherwise.
        """
        # CycloneDX files
        if any(
            pattern in filename.lower()
            for pattern in ["cyclonedx", "bom.json", "bom.xml", "sbom.json", "sbom.xml"]
        ):
            return True

        # SPDX files
        if any(pattern in filename.lower() for pattern in ["spdx", "spdx.json"]):
            return True

        return False

    def parse(self, content: str, filename: str) -> list[dict[str, Any]]:
        """Parse SBOM file and extract components.

        Args:
            content: Raw file content as a string.
            filename: Name of the file being parsed.

        Returns:
            List of component dictionaries with standardized fields.

        Raises:
            ValueError: If content is invalid or format is not recognized.
        """
        if not self.validate_content(content):
            raise ValueError("Invalid or empty SBOM content")

        # Try to determine format
        content_stripped = content.strip()

        # Try CycloneDX JSON
        if content_stripped.startswith("{"):
            try:
                data = json.loads(content)
                if "bomFormat" in data and data["bomFormat"] == "CycloneDX":
                    return self._parse_cyclonedx_json(data, filename)
                elif "spdxVersion" in data:
                    return self._parse_spdx_json(data, filename)
                else:
                    raise ValueError("Unknown JSON SBOM format")
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON: {e}")

        # Try CycloneDX XML
        elif content_stripped.startswith("<"):
            try:
                return self._parse_cyclonedx_xml(content, filename)
            except ET.ParseError as e:
                raise ValueError(f"Invalid XML: {e}")

        else:
            raise ValueError("Unsupported SBOM format (must be JSON or XML)")

    def get_supported_files(self) -> list[str]:
        """Return list of supported SBOM filenames and patterns.

        Returns:
            List of filename patterns this parser supports.
        """
        return [
            "cyclonedx.json",
            "cyclonedx.xml",
            "bom.json",
            "bom.xml",
            "sbom.json",
            "sbom.xml",
            "spdx.json",
        ]

    def _parse_cyclonedx_json(
        self, data: dict[str, Any], filename: str
    ) -> list[dict[str, Any]]:
        """Parse CycloneDX JSON format.

        Args:
            data: Parsed JSON data.
            filename: Source filename.

        Returns:
            List of component dictionaries.
        """
        components = []

        # Extract version for validation
        spec_version = data.get("specVersion", "")
        if not spec_version.startswith("1."):
            raise ValueError(f"Unsupported CycloneDX version: {spec_version}")

        # Parse components
        for component in data.get("components", []):
            comp_dict = self._parse_cyclonedx_component(component, filename)
            if comp_dict:
                components.append(comp_dict)

        # Parse metadata.component (main component)
        metadata = data.get("metadata", {})
        if "component" in metadata:
            comp_dict = self._parse_cyclonedx_component(
                metadata["component"], filename, is_main=True
            )
            if comp_dict:
                components.append(comp_dict)

        return components

    def _parse_cyclonedx_component(
        self, component: dict[str, Any], filename: str, is_main: bool = False
    ) -> dict[str, Any] | None:
        """Parse a single CycloneDX component.

        Args:
            component: Component dictionary from CycloneDX.
            filename: Source filename.
            is_main: Whether this is the main component.

        Returns:
            Component dictionary or None if invalid.
        """
        name = component.get("name")
        version = component.get("version", "unknown")

        if not name:
            return None

        # Extract PURL
        purl = component.get("purl", "")

        # Determine package type from PURL or component type
        package_type = self._extract_package_type_from_purl(purl)
        if not package_type:
            comp_type = component.get("type", "library")
            package_type = "unknown"

        # Extract license information
        license_info = self._extract_cyclonedx_licenses(component.get("licenses", []))

        # Extract external references
        repository_url = None
        homepage_url = None
        for ref in component.get("externalReferences", []):
            ref_type = ref.get("type", "")
            if ref_type == "vcs":
                repository_url = ref.get("url")
            elif ref_type == "website":
                homepage_url = ref.get("url")

        # Extract hashes
        hashes = component.get("hashes", [])
        hash_sha256 = None
        hash_sha512 = None
        for h in hashes:
            alg = h.get("alg", "").lower()
            if alg == "sha-256":
                hash_sha256 = h.get("content")
            elif alg == "sha-512":
                hash_sha512 = h.get("content")

        return {
            "name": name,
            "version": version,
            "purl": purl,
            "package_type": package_type,
            "scope": "runtime" if is_main else component.get("scope", "runtime"),
            "direct": is_main or component.get("scope") == "required",
            "license_id": license_info.get("id"),
            "license_name": license_info.get("name"),
            "license_url": license_info.get("url"),
            "source_file": filename,
            "repository_url": repository_url,
            "homepage_url": homepage_url,
            "description": component.get("description"),
            "hash_sha256": hash_sha256,
            "hash_sha512": hash_sha512,
            "metadata": {
                "group": component.get("group"),
                "publisher": component.get("publisher"),
                "author": component.get("author"),
                "type": component.get("type"),
            },
        }

    def _parse_cyclonedx_xml(self, content: str, filename: str) -> list[dict[str, Any]]:
        """Parse CycloneDX XML format.

        Args:
            content: Raw XML content.
            filename: Source filename.

        Returns:
            List of component dictionaries.
        """
        components = []

        try:
            root = ET.fromstring(content)
        except ET.ParseError as e:
            raise ValueError(f"Invalid CycloneDX XML: {e}")

        # Extract namespace if present
        ns_match = re.match(r"\{(.*?)\}", root.tag)
        ns = {"": ns_match.group(1)} if ns_match else {}

        # Parse components
        for component_elem in root.findall(".//component", ns):
            comp_dict = self._parse_cyclonedx_xml_component(
                component_elem, filename, ns
            )
            if comp_dict:
                components.append(comp_dict)

        return components

    def _parse_cyclonedx_xml_component(
        self, elem: Element, filename: str, ns: dict[str, str]
    ) -> dict[str, Any] | None:
        """Parse a single CycloneDX XML component element.

        Args:
            elem: XML element representing component.
            filename: Source filename.
            ns: XML namespace mapping.

        Returns:
            Component dictionary or None if invalid.
        """
        name_elem = elem.find("name", ns)
        version_elem = elem.find("version", ns)

        if name_elem is None or name_elem.text is None:
            return None

        name = name_elem.text
        version = version_elem.text if version_elem is not None else "unknown"

        # Extract PURL
        purl_elem = elem.find("purl", ns)
        purl = purl_elem.text if purl_elem is not None else ""

        # Determine package type
        package_type = self._extract_package_type_from_purl(purl)
        if not package_type:
            package_type = "unknown"

        # Extract licenses
        licenses = []
        licenses_elem = elem.find("licenses", ns)
        if licenses_elem is not None:
            for license_elem in licenses_elem.findall("license", ns):
                license_id_elem = license_elem.find("id", ns)
                license_name_elem = license_elem.find("name", ns)
                if license_id_elem is not None:
                    licenses.append({"id": license_id_elem.text})
                elif license_name_elem is not None:
                    licenses.append({"name": license_name_elem.text})

        license_info = self._extract_cyclonedx_licenses(licenses)

        # Extract description
        desc_elem = elem.find("description", ns)
        description = desc_elem.text if desc_elem is not None else None

        return {
            "name": name,
            "version": version,
            "purl": purl,
            "package_type": package_type,
            "scope": elem.get("scope", "runtime"),
            "direct": True,
            "license_id": license_info.get("id"),
            "license_name": license_info.get("name"),
            "source_file": filename,
            "description": description,
            "metadata": {"type": elem.get("type", "library")},
        }

    def _parse_spdx_json(
        self, data: dict[str, Any], filename: str
    ) -> list[dict[str, Any]]:
        """Parse SPDX JSON format.

        Args:
            data: Parsed SPDX JSON data.
            filename: Source filename.

        Returns:
            List of component dictionaries.
        """
        components = []

        # Check SPDX version
        spdx_version = data.get("spdxVersion", "")
        if not spdx_version.startswith("SPDX-2."):
            raise ValueError(f"Unsupported SPDX version: {spdx_version}")

        # Parse packages
        for package in data.get("packages", []):
            comp_dict = self._parse_spdx_package(package, filename)
            if comp_dict:
                components.append(comp_dict)

        return components

    def _parse_spdx_package(
        self, package: dict[str, Any], filename: str
    ) -> dict[str, Any] | None:
        """Parse a single SPDX package.

        Args:
            package: Package dictionary from SPDX.
            filename: Source filename.

        Returns:
            Component dictionary or None if invalid.
        """
        name = package.get("name")
        version = package.get("versionInfo", "unknown")

        if not name:
            return None

        # Extract PURL from external refs
        purl = ""
        repository_url = None
        homepage_url = None

        for ref in package.get("externalRefs", []):
            ref_type = ref.get("referenceType", "")
            if ref_type == "purl":
                purl = ref.get("referenceLocator", "")
            elif ref_type == "vcs":
                repository_url = ref.get("referenceLocator")

        # Determine package type from PURL
        package_type = self._extract_package_type_from_purl(purl)
        if not package_type:
            package_type = "unknown"

        # Extract license information
        license_concluded = package.get("licenseConcluded", "NOASSERTION")
        license_declared = package.get("licenseDeclared", "NOASSERTION")

        license_id = None
        license_name = None

        if license_concluded != "NOASSERTION" and license_concluded != "NONE":
            license_id = license_concluded
        elif license_declared != "NOASSERTION" and license_declared != "NONE":
            license_id = license_declared

        # Extract homepage
        if package.get("homepage"):
            homepage_url = package["homepage"]

        # Extract checksums
        hash_sha256 = None
        hash_sha512 = None
        for checksum in package.get("checksums", []):
            alg = checksum.get("algorithm", "")
            if alg == "SHA256":
                hash_sha256 = checksum.get("checksumValue")
            elif alg == "SHA512":
                hash_sha512 = checksum.get("checksumValue")

        return {
            "name": name,
            "version": version,
            "purl": purl,
            "package_type": package_type,
            "scope": "runtime",
            "direct": True,
            "license_id": license_id,
            "license_name": license_name,
            "source_file": filename,
            "repository_url": repository_url,
            "homepage_url": homepage_url,
            "description": package.get("description") or package.get("summary"),
            "hash_sha256": hash_sha256,
            "hash_sha512": hash_sha512,
            "metadata": {
                "supplier": package.get("supplier"),
                "originator": package.get("originator"),
                "download_location": package.get("downloadLocation"),
                "spdx_id": package.get("SPDXID"),
            },
        }

    def _extract_package_type_from_purl(self, purl: str) -> str | None:
        """Extract package type from PURL.

        Args:
            purl: Package URL string.

        Returns:
            Package type string or None if not found.
        """
        if not purl:
            return None

        # PURL format: pkg:type/namespace/name@version
        match = re.match(r"^pkg:([^/]+)/", purl)
        if match:
            purl_type = match.group(1)
            # Map PURL types to our package types
            type_map = {
                "pypi": "pypi",
                "npm": "npm",
                "golang": "go",
                "cargo": "cargo",
                "maven": "maven",
                "nuget": "nuget",
                "gem": "gem",
            }
            return type_map.get(purl_type, purl_type)

        return None

    def _extract_cyclonedx_licenses(
        self, licenses: list[dict[str, Any]]
    ) -> dict[str, str | None]:
        """Extract license information from CycloneDX licenses array.

        Args:
            licenses: List of license dictionaries.

        Returns:
            Dictionary with id, name, and url keys.
        """
        if not licenses:
            return {"id": None, "name": None, "url": None}

        # Take first license if multiple
        first_license = licenses[0]

        # License can be specified as id, name, or expression
        license_id = None
        license_name = None
        license_url = None

        if "license" in first_license:
            lic = first_license["license"]
            license_id = lic.get("id")
            license_name = lic.get("name")
            license_url = lic.get("url")
        elif "expression" in first_license:
            license_id = first_license["expression"]

        # Direct fields (old format)
        if not license_id:
            license_id = first_license.get("id")
        if not license_name:
            license_name = first_license.get("name")

        return {"id": license_id, "name": license_name, "url": license_url}
