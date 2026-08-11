"""Java/Maven/Gradle dependency parser for SBOM service.

Parses Java dependency files including:
- pom.xml (Maven)
- build.gradle (Gradle Groovy DSL)
- build.gradle.kts (Gradle Kotlin DSL)

Extracts dependency information and returns standardized component data.
"""

# flake8: noqa: E501

import re
from typing import Any, Dict, List, Optional
from xml.etree.ElementTree import Element  # For type hints only

import defusedxml.ElementTree as ET

from apps.api.services.sbom.base import BaseDependencyParser


class JavaDependencyParser(BaseDependencyParser):
    """Parser for Java/Maven/Gradle dependency files.

    Handles parsing of pom.xml (Maven), build.gradle (Gradle Groovy DSL),
    and build.gradle.kts (Gradle Kotlin DSL) files to extract dependency
    information in standardized format.

    Attributes:
        MAVEN_NAMESPACE: Maven POM namespace URL for XML parsing.
        SUPPORTED_FILES: List of supported filenames and patterns.
        SCOPE_MAPPING: Maps Gradle scopes to standard Maven scopes.
    """

    # Maven POM namespace
    MAVEN_NAMESPACE = "http://maven.apache.org/POM/4.0.0"

    # Supported dependency files
    SUPPORTED_FILES = ["pom.xml", "build.gradle", "build.gradle.kts"]

    # Gradle scope to Maven scope mapping
    SCOPE_MAPPING = {
        "implementation": "runtime",
        "api": "runtime",
        "compileOnly": "provided",
        "runtimeOnly": "runtime",
        "testImplementation": "test",
        "testCompileOnly": "test",
        "testRuntimeOnly": "test",
        "providedCompile": "provided",
        "providedRuntime": "provided",
    }

    def can_parse(self, filename: str) -> bool:
        """Check if this parser can handle the given dependency file.

        Args:
            filename: Name of the dependency file.

        Returns:
            True if filename matches supported Java/Maven/Gradle files.
        """
        return filename in self.SUPPORTED_FILES

    def get_supported_files(self) -> list[str]:
        """Return list of supported dependency filenames.

        Returns:
            List of supported filenames: ['pom.xml', 'build.gradle', 'build.gradle.kts'].
        """
        return self.SUPPORTED_FILES.copy()

    def parse(self, content: str, filename: str) -> list[dict[str, Any]]:
        """Parse Java dependency file and extract components.

        Routes to appropriate parser based on filename:
        - pom.xml → _parse_maven()
        - build.gradle → _parse_gradle()
        - build.gradle.kts → _parse_gradle_kts()

        Args:
            content: Raw file content as a string.
            filename: Name of the file being parsed.

        Returns:
            List of dictionaries containing component information with fields:
            - name: str - "groupId:artifactId" format (Maven) or "group:artifact" (Gradle)
            - version: str - Semantic version
            - purl: str - Package URL format "pkg:maven/{groupId}/{artifactId}@{version}"
            - package_type: str - "maven"
            - scope: str - "runtime", "test", "provided", "compile"
            - direct: bool - True (indicates direct dependency)
            - source_file: str - Filename being parsed

        Raises:
            ValueError: If content is invalid or unparseable.
        """
        if not self.validate_content(content):
            raise ValueError(f"Empty or invalid content for {filename}")

        if filename == "pom.xml":
            return self._parse_maven(content, filename)
        elif filename == "build.gradle":
            return self._parse_gradle(content, filename)
        elif filename == "build.gradle.kts":
            return self._parse_gradle_kts(content, filename)
        else:
            raise ValueError(f"Unsupported file format: {filename}")

    def _parse_maven(self, content: str, filename: str) -> list[dict[str, Any]]:
        """Parse Maven pom.xml file.

        Extracts dependencies from <dependencies><dependency> blocks,
        handling Maven namespace and optional version properties.

        Args:
            content: Raw pom.xml content.
            filename: Name of the file ("pom.xml").

        Returns:
            List of parsed dependency dictionaries.

        Raises:
            ValueError: If XML is invalid or malformed.
        """
        dependencies: list[dict[str, Any]] = []

        try:
            root = ET.fromstring(content)
        except ET.ParseError as e:
            raise ValueError(f"Invalid XML in {filename}: {str(e)}")

        # Define namespace map for XPath queries
        namespaces = {"": self.MAVEN_NAMESPACE}

        # Extract property values for version variable substitution
        properties = self._extract_maven_properties(root, namespaces)

        # Find all dependency elements
        # Handle both namespaced and non-namespaced XML
        dep_elements = root.findall(".//dependency", namespaces)
        if not dep_elements:
            # Try without namespace
            dep_elements = root.findall(".//dependency")

        for dep in dep_elements:
            try:
                group_id = self._get_element_text(dep, "groupId", namespaces)
                artifact_id = self._get_element_text(dep, "artifactId", namespaces)
                version = self._get_element_text(dep, "version", namespaces)
                scope = self._get_element_text(dep, "scope", namespaces) or "compile"

                # Skip if essential fields are missing
                if not (group_id and artifact_id):
                    continue

                # Resolve property references (e.g., ${project.version})
                version = self._resolve_maven_property(version, properties)

                if not version:
                    version = "unknown"

                # Build component dictionary
                component = {
                    "name": f"{group_id}:{artifact_id}",
                    "version": version,
                    "purl": f"pkg:maven/{group_id}/{artifact_id}@{version}",
                    "package_type": "maven",
                    "scope": self._normalize_maven_scope(scope),
                    "direct": True,
                    "source_file": filename,
                }

                dependencies.append(component)

            except (AttributeError, TypeError) as e:
                # Log and skip malformed dependency entries
                continue

        return dependencies

    def _parse_gradle(self, content: str, filename: str) -> list[dict[str, Any]]:
        """Parse Gradle build.gradle file (Groovy DSL).

        Uses regex to extract dependencies from build.gradle format:
        - implementation 'group:artifact:version'
        - testImplementation 'group:artifact:version'
        - api 'group:artifact:version'
        - etc.

        Args:
            content: Raw build.gradle content.
            filename: Name of the file ("build.gradle").

        Returns:
            List of parsed dependency dictionaries.
        """
        dependencies: list[dict[str, Any]] = []

        # Regex patterns for Gradle dependencies
        # Matches: configuration 'group:artifact:version'
        # Examples:
        #   implementation 'org.springframework:spring-core:5.3.0'
        #   testImplementation "junit:junit:4.13.2"
        gradle_pattern = r"(\w+)\s+['\"]([^'\":]+):([^'\":]+):([^'\"]+)['\"]"

        matches = re.finditer(gradle_pattern, content)

        for match in matches:
            scope_keyword = match.group(
                1
            )  # e.g., "implementation", "testImplementation"
            group_id = match.group(2)  # e.g., "org.springframework"
            artifact_id = match.group(3)  # e.g., "spring-core"
            version = match.group(4)  # e.g., "5.3.0"

            # Map Gradle scope to Maven scope
            scope = self.SCOPE_MAPPING.get(scope_keyword, "runtime")

            # Build component dictionary
            component = {
                "name": f"{group_id}:{artifact_id}",
                "version": version,
                "purl": f"pkg:maven/{group_id}/{artifact_id}@{version}",
                "package_type": "maven",
                "scope": scope,
                "direct": True,
                "source_file": filename,
            }

            dependencies.append(component)

        # Also handle dependencies block format:
        # dependencies {
        #     implementation 'group:artifact:version'
        # }
        # This is already handled by the regex above

        return dependencies

    def _parse_gradle_kts(self, content: str, filename: str) -> list[dict[str, Any]]:
        """Parse Gradle build.gradle.kts file (Kotlin DSL).

        Uses regex to extract dependencies from Kotlin DSL format:
        - implementation("group:artifact:version")
        - testImplementation("group:artifact:version")
        - api("group:artifact:version")
        - etc.

        Args:
            content: Raw build.gradle.kts content.
            filename: Name of the file ("build.gradle.kts").

        Returns:
            List of parsed dependency dictionaries.
        """
        dependencies: list[dict[str, Any]] = []

        # Regex patterns for Gradle Kotlin DSL dependencies
        # Matches: configuration("group:artifact:version") or configuration('group:artifact:version')
        # Examples:
        #   implementation("org.springframework:spring-core:5.3.0")
        #   testImplementation('junit:junit:4.13.2')
        kotlin_pattern = r"(\w+)\(['\"]([^'\":]+):([^'\":]+):([^'\"]+)['\"]\)"

        matches = re.finditer(kotlin_pattern, content)

        for match in matches:
            scope_keyword = match.group(
                1
            )  # e.g., "implementation", "testImplementation"
            group_id = match.group(2)  # e.g., "org.springframework"
            artifact_id = match.group(3)  # e.g., "spring-core"
            version = match.group(4)  # e.g., "5.3.0"

            # Map Gradle scope to Maven scope
            scope = self.SCOPE_MAPPING.get(scope_keyword, "runtime")

            # Build component dictionary
            component = {
                "name": f"{group_id}:{artifact_id}",
                "version": version,
                "purl": f"pkg:maven/{group_id}/{artifact_id}@{version}",
                "package_type": "maven",
                "scope": scope,
                "direct": True,
                "source_file": filename,
            }

            dependencies.append(component)

        return dependencies

    def _get_element_text(
        self,
        element: Element,
        tag_name: str,
        namespaces: dict[str, str],
    ) -> str | None:
        """Extract text content from XML element.

        Handles both namespaced and non-namespaced XML elements.

        Args:
            element: XML element to search within.
            tag_name: Name of the tag to find.
            namespaces: Namespace mapping for XPath queries.

        Returns:
            Text content of element, or None if element not found or empty.
        """
        # Try with namespace first
        ns_tag = f"{{{self.MAVEN_NAMESPACE}}}{tag_name}"
        child = element.find(ns_tag)

        if child is None:
            # Try without namespace
            child = element.find(tag_name)

        if child is not None and child.text:
            return child.text.strip()

        return None

    def _extract_maven_properties(
        self, root: Element, namespaces: dict[str, str]
    ) -> dict[str, str]:
        """Extract Maven properties for variable substitution.

        Parses <properties> section of pom.xml to extract property definitions
        used in version strings.

        Args:
            root: Root XML element of pom.xml.
            namespaces: Namespace mapping.

        Returns:
            Dictionary mapping property names to their values.
        """
        properties: dict[str, str] = {}

        # Find properties element
        props_elem = root.find(f"{{{self.MAVEN_NAMESPACE}}}properties")
        if props_elem is None:
            props_elem = root.find("properties")

        if props_elem is None:
            return properties

        # Extract all property elements
        for prop in props_elem:
            # Strip namespace from tag name
            prop_name = prop.tag
            if "}" in prop_name:
                prop_name = prop_name.split("}", 1)[1]

            if prop.text:
                properties[prop_name] = prop.text.strip()

        return properties

    def _resolve_maven_property(self, value: str, properties: dict[str, str]) -> str:
        """Resolve Maven property references in values.

        Replaces ${property.name} references with actual values from properties dict.

        Args:
            value: String that may contain property references.
            properties: Dictionary of available property values.

        Returns:
            String with property references resolved.
        """
        if not value:
            return value

        # Handle ${project.version} and similar built-in properties
        if value == "${project.version}":
            return properties.get("project.version", "unknown")

        # Handle custom property references like ${some.property}
        def replace_property(match: re.Match[str]) -> str:
            prop_name = match.group(1)
            return properties.get(prop_name, match.group(0))

        resolved = re.sub(r"\$\{([^}]+)\}", replace_property, value)
        return resolved if resolved else "unknown"

    def _normalize_maven_scope(self, scope: str) -> str:
        """Normalize Maven scope to standard values.

        Maps Maven scope values to standard scope keywords:
        - compile → compile
        - provided → provided
        - runtime → runtime
        - test → test
        - system → provided (system scope treated as provided)
        - empty/None → compile (default)

        Args:
            scope: Maven scope value (e.g., "compile", "test").

        Returns:
            Normalized scope value.
        """
        scope = scope.lower().strip() if scope else "compile"

        # Normalize known scopes
        if scope in ("compile", "provided", "runtime", "test"):
            return scope

        # Map system to provided (similar visibility/usage)
        if scope == "system":
            return "provided"

        # Default to compile for unknown scopes
        return "compile"
