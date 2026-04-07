"""SPDX SBOM exporter.

Exports component data to SPDX 2.3 format in JSON.
Includes package information, licenses, checksums, and relationships.
"""

# flake8: noqa: E501


import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4


class SPDXExporter:
    """Exporter for SPDX SBOM format.

    Supports SPDX 2.3 specification in JSON format.
    Includes package information, licenses, external references, and checksums.
    """

    SPDX_VERSION = "SPDX-2.3"
    DATA_LICENSE = "CC0-1.0"

    def export_json(
        self,
        components: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Export components to SPDX JSON format.

        Args:
            components: List of component dictionaries from database.
            metadata: Optional metadata about the SBOM (name, version, etc.).

        Returns:
            JSON string in SPDX format.
        """
        spdx = self._build_spdx_dict(components, metadata)
        return json.dumps(spdx, indent=2)

    def _build_spdx_dict(
        self,
        components: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build SPDX dictionary structure.

        Args:
            components: List of component dictionaries.
            metadata: Optional SBOM metadata.

        Returns:
            Dictionary representing SPDX document.
        """
        doc_namespace = f"https://elder.penguintech.io/spdx/{uuid4()}"
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Build document name
        doc_name = "Elder-SBOM"
        if metadata and "name" in metadata:
            doc_name = f"{metadata['name']}-SBOM"

        spdx = {
            "spdxVersion": self.SPDX_VERSION,
            "dataLicense": self.DATA_LICENSE,
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": doc_name,
            "documentNamespace": doc_namespace,
            "creationInfo": {
                "created": timestamp,
                "creators": [
                    "Tool: Elder-SBOM",
                    "Organization: PenguinTech",
                ],
                "licenseListVersion": "3.21",
            },
            "packages": [],
            "relationships": [],
        }

        # Add document describes relationship if we have metadata component
        if metadata and "name" in metadata:
            # Create main package
            main_package = {
                "SPDXID": "SPDXRef-Package-Main",
                "name": metadata["name"],
                "versionInfo": metadata.get("version", "unknown"),
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
            }
            if "description" in metadata:
                main_package["description"] = metadata["description"]

            spdx["packages"].append(main_package)

            # Add document describes relationship
            spdx["relationships"].append(
                {
                    "spdxElementId": "SPDXRef-DOCUMENT",
                    "relatedSpdxElement": "SPDXRef-Package-Main",
                    "relationshipType": "DESCRIBES",
                }
            )

        # Convert components to SPDX packages
        for idx, comp in enumerate(components):
            spdx_package = self._convert_component_to_spdx(comp, idx)
            if spdx_package:
                spdx["packages"].append(spdx_package)

                # Add dependency relationship if we have a main package
                if metadata and "name" in metadata:
                    spdx["relationships"].append(
                        {
                            "spdxElementId": "SPDXRef-Package-Main",
                            "relatedSpdxElement": spdx_package["SPDXID"],
                            "relationshipType": "DEPENDS_ON",
                        }
                    )

        return spdx

    def _convert_component_to_spdx(
        self, component: Dict[str, Any], index: int
    ) -> Optional[Dict[str, Any]]:
        """Convert Elder component to SPDX package format.

        Args:
            component: Component dictionary from database.
            index: Component index for unique SPDX ID.

        Returns:
            SPDX package dictionary or None if invalid.
        """
        name = component.get("name")
        if not name:
            return None

        # Create unique SPDX ID
        safe_name = name.replace("@", "-").replace("/", "-")
        spdx_id = f"SPDXRef-Package-{safe_name}-{index}"

        spdx_pkg = {
            "SPDXID": spdx_id,
            "name": name,
            "versionInfo": component.get("version", "unknown"),
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "copyrightText": "NOASSERTION",
        }

        # Add description
        if component.get("description"):
            spdx_pkg["description"] = component["description"]
            spdx_pkg["summary"] = component["description"]

        # Add supplier/originator from metadata
        metadata = component.get("metadata", {})
        if isinstance(metadata, dict):
            if metadata.get("publisher"):
                spdx_pkg["supplier"] = f"Organization: {metadata['publisher']}"
            if metadata.get("author"):
                spdx_pkg["originator"] = f"Person: {metadata['author']}"

        # Add homepage
        if component.get("homepage_url"):
            spdx_pkg["homepage"] = component["homepage_url"]

        # Add license information
        license_id = component.get("license_id")
        if license_id:
            spdx_pkg["licenseConcluded"] = license_id
            spdx_pkg["licenseDeclared"] = license_id

        # Add external references
        external_refs = []

        # Add PURL reference
        if component.get("purl"):
            external_refs.append(
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": component["purl"],
                }
            )

        # Add VCS reference
        if component.get("repository_url"):
            external_refs.append(
                {
                    "referenceCategory": "OTHER",
                    "referenceType": "vcs",
                    "referenceLocator": component["repository_url"],
                }
            )

        if external_refs:
            spdx_pkg["externalRefs"] = external_refs

        # Add checksums
        checksums = []
        if component.get("hash_sha256"):
            checksums.append(
                {
                    "algorithm": "SHA256",
                    "checksumValue": component["hash_sha256"],
                }
            )
        if component.get("hash_sha512"):
            checksums.append(
                {
                    "algorithm": "SHA512",
                    "checksumValue": component["hash_sha512"],
                }
            )
        if checksums:
            spdx_pkg["checksums"] = checksums

        return spdx_pkg
