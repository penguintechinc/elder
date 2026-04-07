"""CycloneDX SBOM exporter.

Exports component data to CycloneDX 1.4+ format in both JSON and XML.
Includes component metadata, licenses, and vulnerability information.
"""

# flake8: noqa: E501


import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4


class CycloneDXExporter:
    """Exporter for CycloneDX SBOM format.

    Supports CycloneDX 1.4+ specification with both JSON and XML output.
    Includes component information, licenses, external references, and hashes.
    """

    SPEC_VERSION = "1.4"
    XMLNS = "http://cyclonedx.org/schema/bom/1.4"

    def export_json(
        self,
        components: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Export components to CycloneDX JSON format.

        Args:
            components: List of component dictionaries from database.
            metadata: Optional metadata about the BOM (name, version, etc.).

        Returns:
            JSON string in CycloneDX format.
        """
        bom = self._build_cyclonedx_dict(components, metadata)
        return json.dumps(bom, indent=2)

    def export_xml(
        self,
        components: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Export components to CycloneDX XML format.

        Args:
            components: List of component dictionaries from database.
            metadata: Optional metadata about the BOM (name, version, etc.).

        Returns:
            XML string in CycloneDX format.
        """
        bom_dict = self._build_cyclonedx_dict(components, metadata)
        root = self._dict_to_xml(bom_dict)

        # Convert to string with XML declaration
        ET.register_namespace("", self.XMLNS)
        tree = ET.ElementTree(root)
        # Use a custom approach to get XML declaration
        import io

        output = io.BytesIO()
        tree.write(output, encoding="utf-8", xml_declaration=True)
        return output.getvalue().decode("utf-8")

    def _build_cyclonedx_dict(
        self,
        components: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build CycloneDX dictionary structure.

        Args:
            components: List of component dictionaries.
            metadata: Optional BOM metadata.

        Returns:
            Dictionary representing CycloneDX BOM.
        """
        bom_serial = str(uuid4())
        timestamp = datetime.now(timezone.utc).isoformat() + "Z"

        bom = {
            "bomFormat": "CycloneDX",
            "specVersion": self.SPEC_VERSION,
            "serialNumber": f"urn:uuid:{bom_serial}",
            "version": 1,
            "metadata": {
                "timestamp": timestamp,
                "tools": [
                    {
                        "vendor": "PenguinTech",
                        "name": "Elder",
                        "version": "1.0.0",
                    }
                ],
            },
            "components": [],
        }

        # Add custom metadata if provided
        if metadata:
            if "name" in metadata:
                bom["metadata"]["component"] = {
                    "type": "application",
                    "name": metadata["name"],
                    "version": metadata.get("version", "unknown"),
                }
                if "description" in metadata:
                    bom["metadata"]["component"]["description"] = metadata[
                        "description"
                    ]

        # Convert components
        for comp in components:
            cdx_component = self._convert_component_to_cyclonedx(comp)
            if cdx_component:
                bom["components"].append(cdx_component)

        return bom

    def _convert_component_to_cyclonedx(
        self, component: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Convert Elder component to CycloneDX format.

        Args:
            component: Component dictionary from database.

        Returns:
            CycloneDX component dictionary or None if invalid.
        """
        name = component.get("name")
        if not name:
            return None

        cdx_comp = {
            "type": "library",
            "name": name,
            "version": component.get("version", "unknown"),
        }

        # Add PURL if available
        if component.get("purl"):
            cdx_comp["purl"] = component["purl"]

        # Add description
        if component.get("description"):
            cdx_comp["description"] = component["description"]

        # Add licenses
        licenses = self._build_license_array(component)
        if licenses:
            cdx_comp["licenses"] = licenses

        # Add external references
        external_refs = []
        if component.get("repository_url"):
            external_refs.append({"type": "vcs", "url": component["repository_url"]})
        if component.get("homepage_url"):
            external_refs.append({"type": "website", "url": component["homepage_url"]})
        if external_refs:
            cdx_comp["externalReferences"] = external_refs

        # Add hashes
        hashes = []
        if component.get("hash_sha256"):
            hashes.append({"alg": "SHA-256", "content": component["hash_sha256"]})
        if component.get("hash_sha512"):
            hashes.append({"alg": "SHA-512", "content": component["hash_sha512"]})
        if hashes:
            cdx_comp["hashes"] = hashes

        # Add metadata fields
        metadata = component.get("metadata", {})
        if isinstance(metadata, dict):
            if metadata.get("group"):
                cdx_comp["group"] = metadata["group"]
            if metadata.get("publisher"):
                cdx_comp["publisher"] = metadata["publisher"]
            if metadata.get("author"):
                cdx_comp["author"] = metadata["author"]

        # Add scope
        scope = component.get("scope", "runtime")
        if scope and scope != "runtime":
            cdx_comp["scope"] = scope

        return cdx_comp

    def _build_license_array(self, component: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Build CycloneDX licenses array from component.

        Args:
            component: Component dictionary.

        Returns:
            List of license dictionaries in CycloneDX format.
        """
        licenses = []

        license_id = component.get("license_id")
        license_name = component.get("license_name")
        license_url = component.get("license_url")

        if license_id or license_name:
            lic = {}
            if license_id:
                lic["id"] = license_id
            if license_name and not license_id:
                lic["name"] = license_name
            if license_url:
                lic["url"] = license_url

            licenses.append({"license": lic})

        return licenses

    def _dict_to_xml(self, data: Dict[str, Any]) -> ET.Element:
        """Convert CycloneDX dictionary to XML ElementTree.

        Args:
            data: CycloneDX dictionary.

        Returns:
            XML Element representing the BOM.
        """
        # Create root element with namespace
        root = ET.Element(
            "{%s}bom" % self.XMLNS,
            attrib={
                "serialNumber": data["serialNumber"],
                "version": str(data["version"]),
            },
        )

        # Add metadata
        metadata_elem = ET.SubElement(root, "{%s}metadata" % self.XMLNS)
        timestamp_elem = ET.SubElement(metadata_elem, "{%s}timestamp" % self.XMLNS)
        timestamp_elem.text = data["metadata"]["timestamp"]

        # Add tools
        tools_elem = ET.SubElement(metadata_elem, "{%s}tools" % self.XMLNS)
        for tool in data["metadata"].get("tools", []):
            tool_elem = ET.SubElement(tools_elem, "{%s}tool" % self.XMLNS)
            if "vendor" in tool:
                vendor_elem = ET.SubElement(tool_elem, "{%s}vendor" % self.XMLNS)
                vendor_elem.text = tool["vendor"]
            if "name" in tool:
                name_elem = ET.SubElement(tool_elem, "{%s}name" % self.XMLNS)
                name_elem.text = tool["name"]
            if "version" in tool:
                version_elem = ET.SubElement(tool_elem, "{%s}version" % self.XMLNS)
                version_elem.text = tool["version"]

        # Add main component if present
        if "component" in data["metadata"]:
            comp = data["metadata"]["component"]
            comp_elem = ET.SubElement(metadata_elem, "{%s}component" % self.XMLNS)
            comp_elem.set("type", comp.get("type", "application"))

            name_elem = ET.SubElement(comp_elem, "{%s}name" % self.XMLNS)
            name_elem.text = comp["name"]

            version_elem = ET.SubElement(comp_elem, "{%s}version" % self.XMLNS)
            version_elem.text = comp.get("version", "unknown")

            if "description" in comp:
                desc_elem = ET.SubElement(comp_elem, "{%s}description" % self.XMLNS)
                desc_elem.text = comp["description"]

        # Add components
        components_elem = ET.SubElement(root, "{%s}components" % self.XMLNS)
        for comp in data.get("components", []):
            self._add_component_to_xml(components_elem, comp)

        return root

    def _add_component_to_xml(
        self, parent: ET.Element, component: Dict[str, Any]
    ) -> None:
        """Add a component to XML parent element.

        Args:
            parent: Parent XML element (components).
            component: Component dictionary.
        """
        comp_elem = ET.SubElement(parent, "{%s}component" % self.XMLNS)
        comp_elem.set("type", component.get("type", "library"))

        # Name
        name_elem = ET.SubElement(comp_elem, "{%s}name" % self.XMLNS)
        name_elem.text = component["name"]

        # Version
        version_elem = ET.SubElement(comp_elem, "{%s}version" % self.XMLNS)
        version_elem.text = component.get("version", "unknown")

        # Description
        if "description" in component:
            desc_elem = ET.SubElement(comp_elem, "{%s}description" % self.XMLNS)
            desc_elem.text = component["description"]

        # PURL
        if "purl" in component:
            purl_elem = ET.SubElement(comp_elem, "{%s}purl" % self.XMLNS)
            purl_elem.text = component["purl"]

        # Licenses
        if "licenses" in component:
            licenses_elem = ET.SubElement(comp_elem, "{%s}licenses" % self.XMLNS)
            for lic_entry in component["licenses"]:
                license_elem = ET.SubElement(licenses_elem, "{%s}license" % self.XMLNS)
                lic = lic_entry["license"]
                if "id" in lic:
                    id_elem = ET.SubElement(license_elem, "{%s}id" % self.XMLNS)
                    id_elem.text = lic["id"]
                elif "name" in lic:
                    name_elem = ET.SubElement(license_elem, "{%s}name" % self.XMLNS)
                    name_elem.text = lic["name"]

        # External references
        if "externalReferences" in component:
            refs_elem = ET.SubElement(comp_elem, "{%s}externalReferences" % self.XMLNS)
            for ref in component["externalReferences"]:
                ref_elem = ET.SubElement(refs_elem, "{%s}reference" % self.XMLNS)
                ref_elem.set("type", ref["type"])
                url_elem = ET.SubElement(ref_elem, "{%s}url" % self.XMLNS)
                url_elem.text = ref["url"]

        # Hashes
        if "hashes" in component:
            hashes_elem = ET.SubElement(comp_elem, "{%s}hashes" % self.XMLNS)
            for hash_entry in component["hashes"]:
                hash_elem = ET.SubElement(hashes_elem, "{%s}hash" % self.XMLNS)
                hash_elem.set("alg", hash_entry["alg"])
                hash_elem.text = hash_entry["content"]
