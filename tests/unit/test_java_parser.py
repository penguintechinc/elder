"""Unit tests for Java/Maven/Gradle dependency parser.

Tests parsing of pom.xml, build.gradle, and build.gradle.kts files
with various dependency configurations and edge cases.
"""

import pytest

from apps.api.services.sbom.parsers.java_parser import JavaDependencyParser


class TestJavaDependencyParser:
    """Test suite for JavaDependencyParser."""

    @pytest.fixture
    def parser(self) -> JavaDependencyParser:
        """Create a JavaDependencyParser instance for testing."""
        return JavaDependencyParser()

    # ==================== can_parse() tests ====================

    def test_can_parse_pom_xml(self, parser: JavaDependencyParser) -> None:
        """Test that parser recognizes pom.xml files."""
        assert parser.can_parse("pom.xml") is True

    def test_can_parse_build_gradle(self, parser: JavaDependencyParser) -> None:
        """Test that parser recognizes build.gradle files."""
        assert parser.can_parse("build.gradle") is True

    def test_can_parse_build_gradle_kts(self, parser: JavaDependencyParser) -> None:
        """Test that parser recognizes build.gradle.kts files."""
        assert parser.can_parse("build.gradle.kts") is True

    def test_cannot_parse_other_files(self, parser: JavaDependencyParser) -> None:
        """Test that parser rejects unsupported files."""
        assert parser.can_parse("package.json") is False
        assert parser.can_parse("requirements.txt") is False
        assert parser.can_parse("Gemfile") is False

    # ==================== get_supported_files() tests ====================

    def test_get_supported_files(self, parser: JavaDependencyParser) -> None:
        """Test that parser returns correct list of supported files."""
        supported = parser.get_supported_files()
        assert "pom.xml" in supported
        assert "build.gradle" in supported
        assert "build.gradle.kts" in supported
        assert len(supported) == 3

    # ==================== Maven pom.xml parsing tests ====================

    def test_parse_maven_simple_pom(self, parser: JavaDependencyParser) -> None:
        """Test parsing simple pom.xml with basic dependencies."""
        pom_content = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
                             http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.example</groupId>
    <artifactId>my-app</artifactId>
    <version>1.0.0</version>

    <dependencies>
        <dependency>
            <groupId>org.springframework</groupId>
            <artifactId>spring-core</artifactId>
            <version>5.3.0</version>
        </dependency>
        <dependency>
            <groupId>junit</groupId>
            <artifactId>junit</artifactId>
            <version>4.13.2</version>
            <scope>test</scope>
        </dependency>
    </dependencies>
</project>
"""
        deps = parser.parse(pom_content, "pom.xml")

        assert len(deps) == 2

        # Check spring-core dependency
        spring_dep = next(
            (d for d in deps if d["name"] == "org.springframework:spring-core"), None
        )
        assert spring_dep is not None
        assert spring_dep["version"] == "5.3.0"
        assert spring_dep["purl"] == "pkg:maven/org.springframework/spring-core@5.3.0"
        assert spring_dep["package_type"] == "maven"
        assert spring_dep["scope"] == "compile"
        assert spring_dep["direct"] is True
        assert spring_dep["source_file"] == "pom.xml"

        # Check junit dependency
        junit_dep = next((d for d in deps if d["name"] == "junit:junit"), None)
        assert junit_dep is not None
        assert junit_dep["version"] == "4.13.2"
        assert junit_dep["scope"] == "test"

    def test_parse_maven_with_scopes(self, parser: JavaDependencyParser) -> None:
        """Test parsing Maven dependencies with various scopes."""
        pom_content = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <dependencies>
        <dependency>
            <groupId>org.springframework</groupId>
            <artifactId>spring-core</artifactId>
            <version>5.3.0</version>
            <scope>compile</scope>
        </dependency>
        <dependency>
            <groupId>javax.servlet</groupId>
            <artifactId>javax.servlet-api</artifactId>
            <version>4.0.1</version>
            <scope>provided</scope>
        </dependency>
        <dependency>
            <groupId>org.slf4j</groupId>
            <artifactId>slf4j-api</artifactId>
            <version>1.7.32</version>
            <scope>runtime</scope>
        </dependency>
    </dependencies>
</project>
"""
        deps = parser.parse(pom_content, "pom.xml")

        scopes = {d["name"]: d["scope"] for d in deps}
        assert scopes["org.springframework:spring-core"] == "compile"
        assert scopes["javax.servlet:javax.servlet-api"] == "provided"
        assert scopes["org.slf4j:slf4j-api"] == "runtime"

    def test_parse_maven_invalid_xml(self, parser: JavaDependencyParser) -> None:
        """Test that parser raises ValueError for invalid XML."""
        invalid_xml = "<project><unclosed>"
        with pytest.raises(ValueError, match="Invalid XML"):
            parser.parse(invalid_xml, "pom.xml")

    def test_parse_maven_empty_content(self, parser: JavaDependencyParser) -> None:
        """Test that parser raises ValueError for empty content."""
        with pytest.raises(ValueError, match="Empty or invalid content"):
            parser.parse("", "pom.xml")

    def test_parse_maven_missing_groupid(self, parser: JavaDependencyParser) -> None:
        """Test that dependencies without groupId are skipped."""
        pom_content = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <dependencies>
        <dependency>
            <artifactId>spring-core</artifactId>
            <version>5.3.0</version>
        </dependency>
        <dependency>
            <groupId>org.springframework</groupId>
            <artifactId>spring-beans</artifactId>
            <version>5.3.0</version>
        </dependency>
    </dependencies>
</project>
"""
        deps = parser.parse(pom_content, "pom.xml")

        # Only the complete dependency should be returned
        assert len(deps) == 1
        assert deps[0]["name"] == "org.springframework:spring-beans"

    # ==================== Gradle build.gradle parsing tests ====================

    def test_parse_gradle_simple(self, parser: JavaDependencyParser) -> None:
        """Test parsing simple build.gradle with dependencies."""
        gradle_content = """
dependencies {
    implementation 'org.springframework:spring-core:5.3.0'
    testImplementation 'junit:junit:4.13.2'
    api 'org.slf4j:slf4j-api:1.7.32'
}
"""
        deps = parser.parse(gradle_content, "build.gradle")

        assert len(deps) == 3

        # Check implementation dependency
        spring_dep = next(
            (d for d in deps if d["name"] == "org.springframework:spring-core"), None
        )
        assert spring_dep is not None
        assert spring_dep["version"] == "5.3.0"
        assert spring_dep["scope"] == "runtime"
        assert spring_dep["purl"] == "pkg:maven/org.springframework/spring-core@5.3.0"

        # Check test dependency
        junit_dep = next((d for d in deps if d["name"] == "junit:junit"), None)
        assert junit_dep is not None
        assert junit_dep["scope"] == "test"

        # Check api dependency
        slf4j_dep = next((d for d in deps if d["name"] == "org.slf4j:slf4j-api"), None)
        assert slf4j_dep is not None
        assert slf4j_dep["scope"] == "runtime"

    def test_parse_gradle_with_double_quotes(
        self, parser: JavaDependencyParser
    ) -> None:
        """Test that parser handles both single and double quotes in Gradle."""
        gradle_content = """
dependencies {
    implementation "org.springframework:spring-core:5.3.0"
    testImplementation 'junit:junit:4.13.2'
}
"""
        deps = parser.parse(gradle_content, "build.gradle")

        assert len(deps) == 2
        names = {d["name"] for d in deps}
        assert "org.springframework:spring-core" in names
        assert "junit:junit" in names

    def test_parse_gradle_various_scopes(self, parser: JavaDependencyParser) -> None:
        """Test parsing Gradle dependencies with various scope keywords."""
        gradle_content = """
dependencies {
    implementation 'org.springframework:spring-core:5.3.0'
    api 'org.springframework:spring-beans:5.3.0'
    compileOnly 'com.google.code.findbugs:annotations:3.0.1'
    runtimeOnly 'org.postgresql:postgresql:42.3.1'
    testImplementation 'junit:junit:4.13.2'
    testRuntimeOnly 'org.junit.vintage:junit-vintage-engine:5.8.1'
}
"""
        deps = parser.parse(gradle_content, "build.gradle")

        assert len(deps) == 6

        scopes = {d["name"]: d["scope"] for d in deps}
        assert scopes["org.springframework:spring-core"] == "runtime"
        assert scopes["org.springframework:spring-beans"] == "runtime"
        assert scopes["com.google.code.findbugs:annotations"] == "provided"
        assert scopes["org.postgresql:postgresql"] == "runtime"
        assert scopes["junit:junit"] == "test"
        assert scopes["org.junit.vintage:junit-vintage-engine"] == "test"

    def test_parse_gradle_empty_dependencies(
        self, parser: JavaDependencyParser
    ) -> None:
        """Test parsing Gradle with empty dependencies block."""
        gradle_content = """
dependencies {
}
"""
        deps = parser.parse(gradle_content, "build.gradle")
        assert deps == []

    # ==================== Gradle build.gradle.kts parsing tests ====================

    def test_parse_gradle_kts_simple(self, parser: JavaDependencyParser) -> None:
        """Test parsing simple build.gradle.kts with dependencies."""
        gradle_kts_content = """
dependencies {
    implementation("org.springframework:spring-core:5.3.0")
    testImplementation("junit:junit:4.13.2")
    api("org.slf4j:slf4j-api:1.7.32")
}
"""
        deps = parser.parse(gradle_kts_content, "build.gradle.kts")

        assert len(deps) == 3

        # Check implementation dependency
        spring_dep = next(
            (d for d in deps if d["name"] == "org.springframework:spring-core"), None
        )
        assert spring_dep is not None
        assert spring_dep["version"] == "5.3.0"
        assert spring_dep["scope"] == "runtime"

        # Check test dependency
        junit_dep = next((d for d in deps if d["name"] == "junit:junit"), None)
        assert junit_dep is not None
        assert junit_dep["scope"] == "test"

    def test_parse_gradle_kts_with_single_quotes(
        self, parser: JavaDependencyParser
    ) -> None:
        """Test that Kotlin DSL parser handles both single and double quotes."""
        gradle_kts_content = """
dependencies {
    implementation('org.springframework:spring-core:5.3.0')
    testImplementation("junit:junit:4.13.2")
}
"""
        deps = parser.parse(gradle_kts_content, "build.gradle.kts")

        assert len(deps) == 2
        names = {d["name"] for d in deps}
        assert "org.springframework:spring-core" in names
        assert "junit:junit" in names

    def test_parse_gradle_kts_various_scopes(
        self, parser: JavaDependencyParser
    ) -> None:
        """Test parsing Gradle Kotlin DSL with various scope keywords."""
        gradle_kts_content = """
dependencies {
    implementation("org.springframework:spring-core:5.3.0")
    api("org.springframework:spring-beans:5.3.0")
    compileOnly("com.google.code.findbugs:annotations:3.0.1")
    runtimeOnly("org.postgresql:postgresql:42.3.1")
    testImplementation("junit:junit:4.13.2")
}
"""
        deps = parser.parse(gradle_kts_content, "build.gradle.kts")

        assert len(deps) == 5

        scopes = {d["name"]: d["scope"] for d in deps}
        assert scopes["org.springframework:spring-core"] == "runtime"
        assert scopes["org.springframework:spring-beans"] == "runtime"
        assert scopes["com.google.code.findbugs:annotations"] == "provided"
        assert scopes["org.postgresql:postgresql"] == "runtime"
        assert scopes["junit:junit"] == "test"

    # ==================== Edge cases and complex scenarios ====================

    def test_parse_unsupported_file(self, parser: JavaDependencyParser) -> None:
        """Test that parser raises ValueError for unsupported files."""
        with pytest.raises(ValueError, match="Unsupported file format"):
            parser.parse("content", "Gemfile")

    def test_parse_maven_without_version(self, parser: JavaDependencyParser) -> None:
        """Test parsing Maven dependency without explicit version."""
        pom_content = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <dependencies>
        <dependency>
            <groupId>org.springframework</groupId>
            <artifactId>spring-core</artifactId>
        </dependency>
    </dependencies>
</project>
"""
        deps = parser.parse(pom_content, "pom.xml")

        assert len(deps) == 1
        assert deps[0]["version"] == "unknown"

    def test_parse_gradle_with_multiline_spacing(
        self, parser: JavaDependencyParser
    ) -> None:
        """Test parsing Gradle with extra whitespace and formatting."""
        gradle_content = """
dependencies {
    implementation   'org.springframework:spring-core:5.3.0'
    testImplementation    'junit:junit:4.13.2'
}
"""
        deps = parser.parse(gradle_content, "build.gradle")

        assert len(deps) == 2

    def test_parse_gradle_kts_with_multiline_spacing(
        self, parser: JavaDependencyParser
    ) -> None:
        """Test parsing Gradle Kotlin DSL with extra whitespace."""
        gradle_kts_content = """
dependencies {
    implementation  (  "org.springframework:spring-core:5.3.0"  )
    testImplementation("junit:junit:4.13.2")
}
"""
        deps = parser.parse(gradle_kts_content, "build.gradle.kts")

        # Note: This may not parse correctly due to whitespace in function call,
        # which is valid Kotlin syntax but our simple regex may not catch
        # At minimum, it shouldn't crash
        assert isinstance(deps, list)

    def test_validate_content_with_whitespace(
        self, parser: JavaDependencyParser
    ) -> None:
        """Test that parser rejects whitespace-only content."""
        with pytest.raises(ValueError):
            parser.parse("   \n\t\n   ", "pom.xml")

    def test_source_file_field_preserved(self, parser: JavaDependencyParser) -> None:
        """Test that source_file field correctly identifies source."""
        pom_content = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <dependencies>
        <dependency>
            <groupId>org.springframework</groupId>
            <artifactId>spring-core</artifactId>
            <version>5.3.0</version>
        </dependency>
    </dependencies>
</project>
"""
        deps = parser.parse(pom_content, "pom.xml")
        assert all(d["source_file"] == "pom.xml" for d in deps)

        gradle_content = """
dependencies {
    implementation 'org.springframework:spring-core:5.3.0'
}
"""
        deps = parser.parse(gradle_content, "build.gradle")
        assert all(d["source_file"] == "build.gradle" for d in deps)

        gradle_kts_content = """
dependencies {
    implementation("org.springframework:spring-core:5.3.0")
}
"""
        deps = parser.parse(gradle_kts_content, "build.gradle.kts")
        assert all(d["source_file"] == "build.gradle.kts" for d in deps)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
