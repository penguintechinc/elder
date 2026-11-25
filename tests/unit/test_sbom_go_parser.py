"""Unit tests for the Go dependency parser.

Tests cover parsing of go.mod and go.sum files, including:
- Single-line and multi-line require blocks
- Indirect dependency detection
- Direct vs. indirect dependency classification
- Version format handling
- Package URL (PURL) generation
- go.sum hash verification parsing
"""

import pytest

from apps.api.services.sbom.parsers.go_parser import GoParser


class TestGoParserCanParse:
    """Test the can_parse method of GoParser."""

    def test_can_parse_go_mod(self) -> None:
        """Test that parser recognizes go.mod files."""
        parser = GoParser()
        assert parser.can_parse("go.mod") is True

    def test_can_parse_go_sum(self) -> None:
        """Test that parser recognizes go.sum files."""
        parser = GoParser()
        assert parser.can_parse("go.sum") is True

    def test_cannot_parse_other_files(self) -> None:
        """Test that parser rejects non-Go files."""
        parser = GoParser()
        assert parser.can_parse("package.json") is False
        assert parser.can_parse("requirements.txt") is False
        assert parser.can_parse("go.mod.bak") is False
        assert parser.can_parse("go.mod.backup") is False

    def test_can_parse_with_whitespace(self) -> None:
        """Test that parser handles filenames with whitespace."""
        parser = GoParser()
        assert parser.can_parse(" go.mod ") is True
        assert parser.can_parse(" go.sum ") is True


class TestGoParserSupportedFiles:
    """Test the get_supported_files method of GoParser."""

    def test_supported_files_returns_list(self) -> None:
        """Test that get_supported_files returns a list."""
        parser = GoParser()
        supported = parser.get_supported_files()
        assert isinstance(supported, list)
        assert len(supported) == 2

    def test_supported_files_content(self) -> None:
        """Test that get_supported_files includes go.mod and go.sum."""
        parser = GoParser()
        supported = parser.get_supported_files()
        assert "go.mod" in supported
        assert "go.sum" in supported


class TestGoParserParseGoMod:
    """Test parsing of go.mod files."""

    def test_parse_simple_require_single_line(self) -> None:
        """Test parsing a simple single-line require."""
        parser = GoParser()
        content = (
            "module github.com/example/app\n\nrequire github.com/gin-gonic/gin v1.9.1"
        )

        deps = parser.parse(content, "go.mod")

        assert len(deps) == 1
        assert deps[0]["name"] == "github.com/gin-gonic/gin"
        assert deps[0]["version"] == "v1.9.1"
        assert deps[0]["package_type"] == "go"
        assert deps[0]["direct"] is True

    def test_parse_multiple_requires(self) -> None:
        """Test parsing multiple require statements."""
        parser = GoParser()
        content = """module github.com/example/app

require github.com/gin-gonic/gin v1.9.1
require github.com/stretchr/testify v1.8.4
require github.com/google/uuid v1.3.0
"""
        deps = parser.parse(content, "go.mod")

        assert len(deps) == 3
        assert deps[0]["name"] == "github.com/gin-gonic/gin"
        assert deps[1]["name"] == "github.com/stretchr/testify"
        assert deps[2]["name"] == "github.com/google/uuid"

    def test_parse_require_block(self) -> None:
        """Test parsing multi-line require block."""
        parser = GoParser()
        content = """module github.com/example/app

require (
    github.com/gin-gonic/gin v1.9.1
    github.com/stretchr/testify v1.8.4
    github.com/google/uuid v1.3.0
)
"""
        deps = parser.parse(content, "go.mod")

        assert len(deps) == 3
        assert deps[0]["name"] == "github.com/gin-gonic/gin"
        assert deps[1]["name"] == "github.com/stretchr/testify"
        assert deps[2]["name"] == "github.com/google/uuid"

    def test_parse_indirect_dependency(self) -> None:
        """Test detection of indirect dependencies."""
        parser = GoParser()
        content = """module github.com/example/app

require github.com/gin-gonic/gin v1.9.1
require github.com/stretchr/testify v1.8.4 // indirect
"""
        deps = parser.parse(content, "go.mod")

        assert len(deps) == 2
        assert deps[0]["direct"] is True
        assert deps[1]["direct"] is False

    def test_parse_indirect_in_block(self) -> None:
        """Test detection of indirect dependencies in require block."""
        parser = GoParser()
        content = """module github.com/example/app

require (
    github.com/gin-gonic/gin v1.9.1
    github.com/stretchr/testify v1.8.4 // indirect
    github.com/google/uuid v1.3.0 // indirect
)
"""
        deps = parser.parse(content, "go.mod")

        assert len(deps) == 3
        assert deps[0]["direct"] is True
        assert deps[1]["direct"] is False
        assert deps[2]["direct"] is False

    def test_parse_purl_generation(self) -> None:
        """Test Package URL (PURL) generation."""
        parser = GoParser()
        content = (
            "module github.com/example/app\n\nrequire github.com/gin-gonic/gin v1.9.1"
        )

        deps = parser.parse(content, "go.mod")

        assert deps[0]["purl"] == "pkg:golang/github.com/gin-gonic/gin@v1.9.1"

    def test_parse_scope_is_runtime(self) -> None:
        """Test that all Go dependencies have runtime scope."""
        parser = GoParser()
        content = """module github.com/example/app

require (
    github.com/gin-gonic/gin v1.9.1
    github.com/stretchr/testify v1.8.4
)
"""
        deps = parser.parse(content, "go.mod")

        for dep in deps:
            assert dep["scope"] == "runtime"

    def test_parse_source_file_recorded(self) -> None:
        """Test that source filename is recorded."""
        parser = GoParser()
        content = (
            "module github.com/example/app\n\nrequire github.com/gin-gonic/gin v1.9.1"
        )

        deps = parser.parse(content, "go.mod")

        assert deps[0]["source_file"] == "go.mod"

    def test_parse_version_with_prerelease(self) -> None:
        """Test parsing version with pre-release tags."""
        parser = GoParser()
        content = """module github.com/example/app

require (
    github.com/gin-gonic/gin v1.9.0-beta.1
    github.com/lib/pq v1.10.9-rc.1
)
"""
        deps = parser.parse(content, "go.mod")

        assert deps[0]["version"] == "v1.9.0-beta.1"
        assert deps[1]["version"] == "v1.10.9-rc.1"

    def test_parse_version_with_timestamp_hash(self) -> None:
        """Test parsing pseudo-version with timestamp and hash."""
        parser = GoParser()
        content = "module github.com/example/app\n\nrequire github.com/some/package v0.0.0-20240101120000-abcdef123456"

        deps = parser.parse(content, "go.mod")

        assert len(deps) == 1
        assert deps[0]["version"] == "v0.0.0-20240101120000-abcdef123456"

    def test_parse_empty_require_block_skipped(self) -> None:
        """Test that empty lines in require block are skipped."""
        parser = GoParser()
        content = """module github.com/example/app

require (

    github.com/gin-gonic/gin v1.9.1

    github.com/stretchr/testify v1.8.4

)
"""
        deps = parser.parse(content, "go.mod")

        assert len(deps) == 2

    def test_parse_replace_directives_ignored(self) -> None:
        """Test that replace directives are parsed but not included in output."""
        parser = GoParser()
        content = """module github.com/example/app

require github.com/gin-gonic/gin v1.9.1

replace github.com/some/package v1.0.0 => github.com/fork/package v1.0.1
"""
        deps = parser.parse(content, "go.mod")

        # Only the require should be in output
        assert len(deps) == 1
        assert deps[0]["name"] == "github.com/gin-gonic/gin"

    def test_parse_exclude_directives_ignored(self) -> None:
        """Test that exclude directives are parsed but not included in output."""
        parser = GoParser()
        content = """module github.com/example/app

require github.com/gin-gonic/gin v1.9.1

exclude github.com/bad/package v1.0.0
"""
        deps = parser.parse(content, "go.mod")

        # Only the require should be in output
        assert len(deps) == 1
        assert deps[0]["name"] == "github.com/gin-gonic/gin"

    def test_parse_invalid_empty_content_raises_error(self) -> None:
        """Test that empty content raises ValueError."""
        parser = GoParser()
        with pytest.raises(ValueError, match="Invalid or empty"):
            parser.parse("", "go.mod")

    def test_parse_whitespace_only_content_raises_error(self) -> None:
        """Test that whitespace-only content raises ValueError."""
        parser = GoParser()
        with pytest.raises(ValueError, match="Invalid or empty"):
            parser.parse("   \n\n   ", "go.mod")

    def test_parse_unsupported_filename_raises_error(self) -> None:
        """Test that unsupported filename raises ValueError."""
        parser = GoParser()
        with pytest.raises(ValueError, match="Unsupported file"):
            parser.parse("module github.com/example/app", "package.json")


class TestGoParserParseGoSum:
    """Test parsing of go.sum files."""

    def test_parse_go_sum_simple_entry(self) -> None:
        """Test parsing a simple go.sum entry."""
        parser = GoParser()
        content = "github.com/gin-gonic/gin v1.9.1 h1:SHw5L5ZWnOTpHxzKgwl=\n"

        deps = parser.parse(content, "go.sum")

        assert len(deps) == 1
        assert deps[0]["name"] == "github.com/gin-gonic/gin"
        assert deps[0]["version"] == "v1.9.1"
        assert deps[0]["hash"] == "h1:SHw5L5ZWnOTpHxzKgwl="

    def test_parse_go_sum_multiple_entries(self) -> None:
        """Test parsing multiple go.sum entries."""
        parser = GoParser()
        content = """github.com/gin-gonic/gin v1.9.1 h1:SHw5L5ZWnOTpHxzKgwl=
github.com/stretchr/testify v1.8.4 h1:Eo8i7xY5u+3A0qRgEWYkKXrBaKFxwvVFjSxPeC/pSo=
github.com/google/uuid v1.3.0 h1:c5/5+VAXxRAqWr+Nj8=
"""
        deps = parser.parse(content, "go.sum")

        assert len(deps) == 3
        assert deps[0]["name"] == "github.com/gin-gonic/gin"
        assert deps[1]["name"] == "github.com/stretchr/testify"
        assert deps[2]["name"] == "github.com/google/uuid"

    def test_parse_go_sum_purl_generation(self) -> None:
        """Test Package URL generation for go.sum entries."""
        parser = GoParser()
        content = "github.com/gin-gonic/gin v1.9.1 h1:SHw5L5ZWnOTpHxzKgwl=\n"

        deps = parser.parse(content, "go.sum")

        assert deps[0]["purl"] == "pkg:golang/github.com/gin-gonic/gin@v1.9.1"

    def test_parse_go_sum_source_file_recorded(self) -> None:
        """Test that source filename is recorded for go.sum entries."""
        parser = GoParser()
        content = "github.com/gin-gonic/gin v1.9.1 h1:SHw5L5ZWnOTpHxzKgwl=\n"

        deps = parser.parse(content, "go.sum")

        assert deps[0]["source_file"] == "go.sum"

    def test_parse_go_sum_duplicate_entries_skipped(self) -> None:
        """Test that duplicate module@version entries are skipped."""
        parser = GoParser()
        # go.sum can have multiple hashes for the same module version
        content = """github.com/gin-gonic/gin v1.9.1 h1:SHw5L5ZWnOTpHxzKgwl=
github.com/gin-gonic/gin v1.9.1 h1:OtherHashValue=
github.com/stretchr/testify v1.8.4 h1:Eo8i7xY5u+3A0qRgEWYkKXrBaKFxwvVFjSxPeC/pSo=
"""
        deps = parser.parse(content, "go.sum")

        # Should only have 2 entries (duplicate skipped)
        assert len(deps) == 2
        assert deps[0]["name"] == "github.com/gin-gonic/gin"
        assert deps[1]["name"] == "github.com/stretchr/testify"

    def test_parse_go_sum_indirect_always_false(self) -> None:
        """Test that go.sum entries are marked as indirect."""
        parser = GoParser()
        content = "github.com/gin-gonic/gin v1.9.1 h1:SHw5L5ZWnOTpHxzKgwl=\n"

        deps = parser.parse(content, "go.sum")

        assert deps[0]["direct"] is False

    def test_parse_go_sum_pseudo_version(self) -> None:
        """Test parsing pseudo-versions in go.sum."""
        parser = GoParser()
        content = (
            "github.com/some/package v0.0.0-20240101120000-abcdef123456 h1:hash=\n"
        )

        deps = parser.parse(content, "go.sum")

        assert len(deps) == 1
        assert deps[0]["version"] == "v0.0.0-20240101120000-abcdef123456"


class TestGoParserIntegration:
    """Integration tests for GoParser."""

    def test_parse_real_world_go_mod(self) -> None:
        """Test parsing a realistic go.mod file."""
        parser = GoParser()
        content = """module github.com/example/myapp

go 1.23

require (
    github.com/gin-gonic/gin v1.9.1
    github.com/lib/pq v1.10.9
)

require github.com/google/uuid v1.3.0 // indirect

replace github.com/some/package => github.com/fork/package v1.0.0
"""
        deps = parser.parse(content, "go.mod")

        # Should have 3 requires (replace is not included)
        assert len(deps) == 3

        # Check direct dependencies
        direct_deps = [d for d in deps if d["direct"]]
        assert len(direct_deps) == 2

        # Check indirect dependencies
        indirect_deps = [d for d in deps if not d["direct"]]
        assert len(indirect_deps) == 1

    def test_parse_real_world_go_sum(self) -> None:
        """Test parsing a realistic go.sum file."""
        parser = GoParser()
        content = """github.com/gin-gonic/gin v1.9.1 h1:SHw5L5ZWnOTpHxzKgwl=
github.com/gin-gonic/gin v1.9.1/go.mod h1:AbcDeFgQRKT3uM4=
github.com/lib/pq v1.10.9 h1:EqnZf1oMVESDkLsrCN=
github.com/lib/pq v1.10.9/go.mod h1:W7NEvErKLjQR=
"""
        deps = parser.parse(content, "go.sum")

        # Should only have 2 unique module@version combinations
        # (duplicates with /go.mod are separate entries in go.sum but we only track unique)
        assert len(deps) == 2

    def test_parse_dependency_dict_structure(self) -> None:
        """Test that parsed dependencies have all required fields."""
        parser = GoParser()
        content = (
            "module github.com/example/app\n\nrequire github.com/gin-gonic/gin v1.9.1"
        )

        deps = parser.parse(content, "go.mod")

        required_fields = [
            "name",
            "version",
            "purl",
            "package_type",
            "scope",
            "direct",
            "source_file",
        ]
        for dep in deps:
            for field in required_fields:
                assert field in dep, f"Missing field: {field}"
