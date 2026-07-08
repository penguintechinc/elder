"""Unit tests for the Rust Cargo dependency parser.

Tests cover parsing of Cargo.toml and Cargo.lock files, including:
- Simple version specifications
- Complex version specifiers (^, ~, >=, <=, etc.)
- Runtime, dev, and build dependencies
- Git and path dependencies
- Pinned versions in Cargo.lock
- Package URL (PURL) generation
- Version normalization
"""

import pytest

from apps.api.services.sbom.parsers.rust_parser import RustDependencyParser


class TestRustParserCanParse:
    """Test the can_parse method of RustDependencyParser."""

    def test_can_parse_cargo_toml(self) -> None:
        """Test that parser recognizes Cargo.toml files."""
        parser = RustDependencyParser()
        assert parser.can_parse("Cargo.toml") is True

    def test_can_parse_cargo_lock(self) -> None:
        """Test that parser recognizes Cargo.lock files."""
        parser = RustDependencyParser()
        assert parser.can_parse("Cargo.lock") is True

    def test_cannot_parse_other_files(self) -> None:
        """Test that parser rejects non-Cargo files."""
        parser = RustDependencyParser()
        assert parser.can_parse("package.json") is False
        assert parser.can_parse("go.mod") is False
        assert parser.can_parse("Cargo.toml.bak") is False
        assert parser.can_parse("Cargo.lock.old") is False


class TestRustParserSupportedFiles:
    """Test the get_supported_files method of RustDependencyParser."""

    def test_supported_files_returns_list(self) -> None:
        """Test that get_supported_files returns a list."""
        parser = RustDependencyParser()
        supported = parser.get_supported_files()
        assert isinstance(supported, list)
        assert len(supported) == 2

    def test_supported_files_content(self) -> None:
        """Test that get_supported_files includes Cargo.toml and Cargo.lock."""
        parser = RustDependencyParser()
        supported = parser.get_supported_files()
        assert "Cargo.toml" in supported
        assert "Cargo.lock" in supported


class TestRustParserParseCargoToml:
    """Test parsing of Cargo.toml files."""

    def test_parse_simple_string_version(self) -> None:
        """Test parsing a simple string version dependency."""
        parser = RustDependencyParser()
        content = """[package]
name = "my-app"
version = "0.1.0"

[dependencies]
serde = "1.0"
"""
        deps = parser.parse(content, "Cargo.toml")

        assert len(deps) == 1
        assert deps[0]["name"] == "serde"
        assert deps[0]["version"] == "1.0"
        assert deps[0]["package_type"] == "cargo"
        assert deps[0]["scope"] == "runtime"
        assert deps[0]["direct"] is True
        assert deps[0]["source_file"] == "Cargo.toml"

    def test_parse_caret_version_spec(self) -> None:
        """Test parsing caret version specifier ^."""
        parser = RustDependencyParser()
        content = """[dependencies]
tokio = "^1.0"
"""
        deps = parser.parse(content, "Cargo.toml")

        assert len(deps) == 1
        assert deps[0]["name"] == "tokio"
        assert deps[0]["version"] == "1.0"

    def test_parse_tilde_version_spec(self) -> None:
        """Test parsing tilde version specifier ~."""
        parser = RustDependencyParser()
        content = """[dependencies]
log = "~0.4"
"""
        deps = parser.parse(content, "Cargo.toml")

        assert len(deps) == 1
        assert deps[0]["name"] == "log"
        assert deps[0]["version"] == "0.4"

    def test_parse_gte_version_spec(self) -> None:
        """Test parsing greater-than-or-equal version specifier."""
        parser = RustDependencyParser()
        content = """[dependencies]
uuid = ">=1.0.0"
"""
        deps = parser.parse(content, "Cargo.toml")

        assert len(deps) == 1
        assert deps[0]["name"] == "uuid"
        assert deps[0]["version"] == "1.0.0"

    def test_parse_exact_version(self) -> None:
        """Test parsing exact version specifier =."""
        parser = RustDependencyParser()
        content = """[dependencies]
regex = "=1.9.1"
"""
        deps = parser.parse(content, "Cargo.toml")

        assert len(deps) == 1
        assert deps[0]["name"] == "regex"
        assert deps[0]["version"] == "1.9.1"

    def test_parse_object_format_dependency(self) -> None:
        """Test parsing dependency with object format (version key)."""
        parser = RustDependencyParser()
        content = """[dependencies]
serde = { version = "1.0", features = ["derive"] }
"""
        deps = parser.parse(content, "Cargo.toml")

        assert len(deps) == 1
        assert deps[0]["name"] == "serde"
        assert deps[0]["version"] == "1.0"
        assert deps[0]["scope"] == "runtime"

    def test_parse_dev_dependencies(self) -> None:
        """Test parsing dev-dependencies section."""
        parser = RustDependencyParser()
        content = """[dependencies]
serde = "1.0"

[dev-dependencies]
mockito = "0.9"
"""
        deps = parser.parse(content, "Cargo.toml")

        assert len(deps) == 2
        dev_dep = next(d for d in deps if d["name"] == "mockito")
        assert dev_dep["scope"] == "dev"
        assert dev_dep["direct"] is True

    def test_parse_build_dependencies(self) -> None:
        """Test parsing build-dependencies section."""
        parser = RustDependencyParser()
        content = """[build-dependencies]
cc = "1.0"
"""
        deps = parser.parse(content, "Cargo.toml")

        assert len(deps) == 1
        assert deps[0]["name"] == "cc"
        assert deps[0]["scope"] == "build"

    def test_parse_multiple_sections(self) -> None:
        """Test parsing all three dependency sections."""
        parser = RustDependencyParser()
        content = """[dependencies]
tokio = "1.0"

[dev-dependencies]
mockito = "0.9"

[build-dependencies]
cc = "1.0"
"""
        deps = parser.parse(content, "Cargo.toml")

        assert len(deps) == 3
        names = {d["name"] for d in deps}
        assert names == {"tokio", "mockito", "cc"}
        scopes = {d["name"]: d["scope"] for d in deps}
        assert scopes == {"tokio": "runtime", "mockito": "dev", "cc": "build"}

    def test_parse_git_dependency(self) -> None:
        """Test parsing git-based dependency."""
        parser = RustDependencyParser()
        content = """[dependencies]
some-crate = { git = "https://github.com/example/crate" }
"""
        deps = parser.parse(content, "Cargo.toml")

        assert len(deps) == 1
        assert deps[0]["name"] == "some-crate"
        assert "git" in deps[0]
        assert deps[0]["git"] == "https://github.com/example/crate"

    def test_parse_git_dependency_with_rev(self) -> None:
        """Test parsing git-based dependency with specific revision."""
        parser = RustDependencyParser()
        content = """[dependencies]
some-crate = { git = "https://github.com/example/crate", rev = "abc123" }
"""
        deps = parser.parse(content, "Cargo.toml")

        assert len(deps) == 1
        assert deps[0]["name"] == "some-crate"
        assert deps[0]["version"] == "abc123"
        assert deps[0]["git"] == "https://github.com/example/crate"

    def test_parse_path_dependency(self) -> None:
        """Test parsing path-based dependency."""
        parser = RustDependencyParser()
        content = """[dependencies]
my-lib = { path = "../my-lib" }
"""
        deps = parser.parse(content, "Cargo.toml")

        assert len(deps) == 1
        assert deps[0]["name"] == "my-lib"
        assert "path" in deps[0]
        assert deps[0]["path"] == "../my-lib"

    def test_parse_purl_generation(self) -> None:
        """Test that PURL is correctly generated."""
        parser = RustDependencyParser()
        content = """[dependencies]
serde = "1.0.130"
"""
        deps = parser.parse(content, "Cargo.toml")

        assert len(deps) == 1
        assert deps[0]["purl"] == "pkg:cargo/serde@1.0.130"

    def test_parse_empty_dependencies(self) -> None:
        """Test parsing file with no dependencies."""
        parser = RustDependencyParser()
        content = """[package]
name = "my-app"
version = "0.1.0"
"""
        deps = parser.parse(content, "Cargo.toml")

        assert len(deps) == 0

    def test_parse_invalid_toml_raises_error(self) -> None:
        """Test that invalid TOML raises ValueError."""
        parser = RustDependencyParser()
        content = """[dependencies
serde = "1.0"  # Missing closing bracket
"""
        with pytest.raises(ValueError, match="Failed to parse TOML"):
            parser.parse(content, "Cargo.toml")

    def test_parse_empty_content_raises_error(self) -> None:
        """Test that empty content raises ValueError."""
        parser = RustDependencyParser()
        with pytest.raises(ValueError, match="Empty or invalid"):
            parser.parse("", "Cargo.toml")


class TestRustParserParseCargoLock:
    """Test parsing of Cargo.lock files."""

    def test_parse_simple_cargo_lock(self) -> None:
        """Test parsing a simple Cargo.lock file."""
        parser = RustDependencyParser()
        content = """version = 3

[[package]]
name = "serde"
version = "1.0.130"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""
        deps = parser.parse(content, "Cargo.lock")

        assert len(deps) == 1
        assert deps[0]["name"] == "serde"
        assert deps[0]["version"] == "1.0.130"
        assert deps[0]["scope"] == "runtime"
        assert deps[0]["direct"] is False
        assert deps[0]["source_file"] == "Cargo.lock"

    def test_parse_multiple_packages_cargo_lock(self) -> None:
        """Test parsing multiple packages in Cargo.lock."""
        parser = RustDependencyParser()
        content = """version = 3

[[package]]
name = "serde"
version = "1.0.130"
source = "registry+https://github.com/rust-lang/crates.io-index"

[[package]]
name = "tokio"
version = "1.25.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""
        deps = parser.parse(content, "Cargo.lock")

        assert len(deps) == 2
        names = {d["name"] for d in deps}
        assert names == {"serde", "tokio"}

    def test_parse_cargo_lock_purl_generation(self) -> None:
        """Test PURL generation for Cargo.lock entries."""
        parser = RustDependencyParser()
        content = """version = 3

[[package]]
name = "uuid"
version = "1.1.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""
        deps = parser.parse(content, "Cargo.lock")

        assert len(deps) == 1
        assert deps[0]["purl"] == "pkg:cargo/uuid@1.1.0"

    def test_parse_cargo_lock_skips_root_package(self) -> None:
        """Test that root package without source is skipped."""
        parser = RustDependencyParser()
        content = """version = 3

[[package]]
name = "my-app"
version = "0.1.0"

[[package]]
name = "serde"
version = "1.0.130"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""
        deps = parser.parse(content, "Cargo.lock")

        # Root package (my-app) without source should be skipped
        assert len(deps) == 1
        assert deps[0]["name"] == "serde"

    def test_parse_cargo_lock_missing_name(self) -> None:
        """Test that packages without name are skipped."""
        parser = RustDependencyParser()
        content = """version = 3

[[package]]
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""
        deps = parser.parse(content, "Cargo.lock")

        assert len(deps) == 0

    def test_parse_cargo_lock_missing_version(self) -> None:
        """Test that packages without version are skipped."""
        parser = RustDependencyParser()
        content = """version = 3

[[package]]
name = "serde"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""
        deps = parser.parse(content, "Cargo.lock")

        assert len(deps) == 0

    def test_parse_empty_cargo_lock(self) -> None:
        """Test parsing Cargo.lock with no packages section."""
        parser = RustDependencyParser()
        content = """version = 3
"""
        deps = parser.parse(content, "Cargo.lock")

        assert len(deps) == 0


class TestRustParserVersionNormalization:
    """Test version normalization methods."""

    def test_normalize_cargo_version_simple(self) -> None:
        """Test normalizing simple version strings."""
        parser = RustDependencyParser()
        assert parser._normalize_cargo_version("1.0") == "1.0"
        assert parser._normalize_cargo_version("1.0.0") == "1.0.0"
        assert parser._normalize_cargo_version("2.5.3") == "2.5.3"

    def test_normalize_cargo_version_caret(self) -> None:
        """Test normalizing caret version specifiers."""
        parser = RustDependencyParser()
        assert parser._normalize_cargo_version("^1.0") == "1.0"
        assert parser._normalize_cargo_version("^1.0.0") == "1.0.0"

    def test_normalize_cargo_version_tilde(self) -> None:
        """Test normalizing tilde version specifiers."""
        parser = RustDependencyParser()
        assert parser._normalize_cargo_version("~1.0") == "1.0"
        assert parser._normalize_cargo_version("~0.4.2") == "0.4.2"

    def test_normalize_cargo_version_comparison_operators(self) -> None:
        """Test normalizing comparison operator version specs."""
        parser = RustDependencyParser()
        assert parser._normalize_cargo_version(">=1.0") == "1.0"
        assert parser._normalize_cargo_version(">1.0") == "1.0"
        assert parser._normalize_cargo_version("<=2.0") == "2.0"
        assert parser._normalize_cargo_version("<2.0") == "2.0"
        assert parser._normalize_cargo_version("=1.5") == "1.5"

    def test_normalize_cargo_version_with_whitespace(self) -> None:
        """Test normalizing versions with extra whitespace."""
        parser = RustDependencyParser()
        assert parser._normalize_cargo_version("  ^1.0  ") == "1.0"
        assert parser._normalize_cargo_version("  >= 1.0  ") == "1.0"

    def test_normalize_version_empty_string(self) -> None:
        """Test normalizing empty version strings."""
        parser = RustDependencyParser()
        assert parser.normalize_version("") == "unknown"
        assert parser.normalize_version("   ") == "unknown"

    def test_normalize_version_none_like(self) -> None:
        """Test normalizing None-like strings."""
        parser = RustDependencyParser()
        assert parser.normalize_version(None) == "unknown"


class TestRustParserIntegration:
    """Integration tests for real-world scenarios."""

    def test_parse_real_cargo_toml(self) -> None:
        """Test parsing a realistic Cargo.toml file."""
        parser = RustDependencyParser()
        content = """[package]
name = "server"
version = "0.1.0"
edition = "2021"

[dependencies]
tokio = { version = "1.25", features = ["full"] }
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
axum = "^0.6"
tower = "~0.4"
tracing = ">=0.1.30"

[dev-dependencies]
mockito = "0.9"
tokio = { version = "1.25", features = ["test-util"] }

[build-dependencies]
cc = "1.0"
"""
        deps = parser.parse(content, "Cargo.toml")

        # 6 runtime + 2 dev (tokio appears in both, kept per-scope) + 1 build = 9 total
        assert len(deps) == 9

        # Verify each dependency
        dep_map = {d["name"]: d for d in deps}
        assert "tokio" in dep_map
        assert "serde" in dep_map
        assert "serde_json" in dep_map
        assert "axum" in dep_map
        assert "tower" in dep_map
        assert "tracing" in dep_map
        assert "mockito" in dep_map
        assert "cc" in dep_map

        # Verify scopes
        assert dep_map["cc"]["scope"] == "build"
        assert dep_map["mockito"]["scope"] == "dev"
        assert dep_map["serde"]["scope"] == "runtime"

    def test_package_type_always_cargo(self) -> None:
        """Test that package_type is always 'cargo'."""
        parser = RustDependencyParser()
        content = """[dependencies]
dep1 = "1.0"

[dev-dependencies]
dep2 = "2.0"

[build-dependencies]
dep3 = "3.0"
"""
        deps = parser.parse(content, "Cargo.toml")

        for dep in deps:
            assert dep["package_type"] == "cargo"

    def test_source_file_tracking(self) -> None:
        """Test that source_file is properly set."""
        parser = RustDependencyParser()
        toml_content = "[dependencies]\nserde = '1.0'"
        lock_content = "version = 3\n\n[[package]]\nname = 'tokio'\nversion = '1.0'\nsource = 'registry'"

        toml_deps = parser.parse(toml_content, "Cargo.toml")
        lock_deps = parser.parse(lock_content, "Cargo.lock")

        for dep in toml_deps:
            assert dep["source_file"] == "Cargo.toml"
            assert dep["direct"] is True

        for dep in lock_deps:
            assert dep["source_file"] == "Cargo.lock"
            assert dep["direct"] is False
