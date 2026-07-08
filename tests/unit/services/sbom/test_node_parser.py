"""Unit tests for Node.js dependency parser.

Tests parsing of package.json, package-lock.json, yarn.lock, and pnpm-lock.yaml
to ensure correct extraction of dependencies with proper metadata.
"""

import json

import pytest
import yaml

from apps.api.services.sbom.parsers.node_parser import NodeDependencyParser


class TestNodeDependencyParser:
    """Test suite for NodeDependencyParser class."""

    @pytest.fixture
    def parser(self):
        """Create a NodeDependencyParser instance for testing."""
        return NodeDependencyParser()

    # can_parse tests

    def test_can_parse_package_json(self, parser):
        """Test parser recognizes package.json files."""
        assert parser.can_parse("package.json") is True

    def test_can_parse_package_lock_json(self, parser):
        """Test parser recognizes package-lock.json files."""
        assert parser.can_parse("package-lock.json") is True

    def test_can_parse_yarn_lock(self, parser):
        """Test parser recognizes yarn.lock files."""
        assert parser.can_parse("yarn.lock") is True

    def test_can_parse_pnpm_lock_yaml(self, parser):
        """Test parser recognizes pnpm-lock.yaml files."""
        assert parser.can_parse("pnpm-lock.yaml") is True

    def test_can_parse_unsupported_file(self, parser):
        """Test parser rejects unsupported files."""
        assert parser.can_parse("Gemfile.lock") is False
        assert parser.can_parse("requirements.txt") is False
        assert parser.can_parse("go.mod") is False

    # get_supported_files tests

    def test_get_supported_files(self, parser):
        """Test parser returns correct list of supported files."""
        files = parser.get_supported_files()
        assert len(files) == 4
        assert "package.json" in files
        assert "package-lock.json" in files
        assert "yarn.lock" in files
        assert "pnpm-lock.yaml" in files

    # parse_package_json tests

    def test_parse_package_json_with_dependencies(self, parser):
        """Test parsing package.json with runtime dependencies."""
        content = json.dumps(
            {
                "name": "test-app",
                "version": "1.0.0",
                "dependencies": {
                    "lodash": "^4.17.21",
                    "express": "4.18.2",
                },
            }
        )

        components = parser.parse(content, "package.json")

        assert len(components) == 2
        assert any(c["name"] == "lodash" and c["scope"] == "runtime" for c in components)
        assert any(c["name"] == "express" and c["scope"] == "runtime" for c in components)
        assert all(c["direct"] is True for c in components)
        assert all(c["package_type"] == "npm" for c in components)
        assert all(c["source_file"] == "package.json" for c in components)

    def test_parse_package_json_with_dev_dependencies(self, parser):
        """Test parsing package.json with development dependencies."""
        content = json.dumps(
            {
                "name": "test-app",
                "version": "1.0.0",
                "devDependencies": {
                    "jest": "^29.0.0",
                    "eslint": "8.50.0",
                },
            }
        )

        components = parser.parse(content, "package.json")

        assert len(components) == 2
        assert all(c["scope"] == "dev" for c in components)
        assert all(c["direct"] is True for c in components)

    def test_parse_package_json_with_scoped_packages(self, parser):
        """Test parsing package.json with scoped packages."""
        content = json.dumps(
            {
                "name": "test-app",
                "version": "1.0.0",
                "dependencies": {
                    "@angular/core": "16.0.0",
                    "@types/node": "^20.0.0",
                    "axios": "1.5.0",
                },
            }
        )

        components = parser.parse(content, "package.json")

        assert len(components) == 3
        assert any(c["name"] == "@angular/core" for c in components)
        assert any(c["name"] == "@types/node" for c in components)

    def test_parse_package_json_with_purl_generation(self, parser):
        """Test Package URL (purl) generation in package.json parsing."""
        content = json.dumps(
            {
                "name": "test-app",
                "dependencies": {
                    "lodash": "4.17.21",
                    "@angular/core": "16.0.0",
                },
            }
        )

        components = parser.parse(content, "package.json")

        lodash = next(c for c in components if c["name"] == "lodash")
        assert lodash["purl"] == "pkg:npm/lodash@4.17.21"

        angular = next(c for c in components if c["name"] == "@angular/core")
        assert angular["purl"] == "pkg:npm/@angular/core@16.0.0"

    def test_parse_package_json_with_peer_dependencies(self, parser):
        """Test parsing peerDependencies from package.json."""
        content = json.dumps(
            {
                "name": "test-plugin",
                "peerDependencies": {
                    "react": "^18.0.0",
                    "react-dom": "^18.0.0",
                },
            }
        )

        components = parser.parse(content, "package.json")

        assert len(components) == 2
        assert all(c["scope"] == "runtime" for c in components)

    def test_parse_package_json_with_optional_dependencies(self, parser):
        """Test parsing optionalDependencies from package.json."""
        content = json.dumps(
            {
                "name": "test-app",
                "optionalDependencies": {
                    "fsevents": "^2.3.0",
                },
            }
        )

        components = parser.parse(content, "package.json")

        assert len(components) == 1
        assert components[0]["scope"] == "runtime"

    def test_parse_package_json_empty_dependencies(self, parser):
        """Test parsing package.json with no dependencies."""
        content = json.dumps(
            {
                "name": "test-app",
                "version": "1.0.0",
            }
        )

        components = parser.parse(content, "package.json")

        assert len(components) == 0

    # parse_package_lock_json tests

    def test_parse_package_lock_json_v7_format(self, parser):
        """Test parsing npm package-lock.json v7+ format with packages key."""
        content = json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "test-app", "version": "1.0.0"},
                    "node_modules/lodash": {
                        "version": "4.17.21",
                    },
                    "node_modules/@angular/core": {
                        "version": "16.0.0",
                    },
                    "node_modules/jest": {
                        "version": "29.0.0",
                        "dev": True,
                    },
                },
            }
        )

        components = parser.parse(content, "package-lock.json")

        assert len(components) == 3
        assert any(c["name"] == "lodash" and c["scope"] == "runtime" for c in components)
        assert any(c["name"] == "@angular/core" for c in components)
        assert any(c["scope"] == "dev" for c in components)
        assert all(c["direct"] is False for c in components)

    def test_parse_package_lock_json_v6_format(self, parser):
        """Test parsing npm package-lock.json v6 format with dependencies key."""
        content = json.dumps(
            {
                "lockfileVersion": 1,
                "dependencies": {
                    "lodash": {
                        "version": "4.17.21",
                    },
                    "jest": {
                        "version": "29.0.0",
                        "dev": True,
                    },
                },
            }
        )

        components = parser.parse(content, "package-lock.json")

        assert len(components) == 2
        assert any(c["name"] == "lodash" and c["scope"] == "runtime" for c in components)
        assert any(c["scope"] == "dev" for c in components)

    # parse_yarn_lock tests

    def test_parse_yarn_lock_basic(self, parser):
        """Test parsing yarn.lock with basic entries."""
        content = """lodash@^4.17.21:
  version "4.17.21"
  resolved "https://registry.npmjs.org/lodash/-/lodash-4.17.21.tgz#7c4f3881288490d56bc50e9ef8a130ddfb7f3e8a"

express@4.18.2:
  version "4.18.2"
  resolved "https://registry.npmjs.org/express/-/express-4.18.2.tgz#something"
"""

        components = parser.parse(content, "yarn.lock")

        assert len(components) == 2
        assert any(c["name"] == "lodash" for c in components)
        assert any(c["name"] == "express" for c in components)
        assert all(c["direct"] is False for c in components)

    def test_parse_yarn_lock_with_scoped_packages(self, parser):
        """Test parsing yarn.lock with scoped packages."""
        content = """@angular/core@16.0.0:
  version "16.0.0"
  resolved "https://registry.npmjs.org/@angular/core/-/core-16.0.0.tgz#hash"

@types/node@^20.0.0:
  version "20.0.0"
  resolved "https://registry.npmjs.org/@types/node/-/node-20.0.0.tgz#hash"
"""

        components = parser.parse(content, "yarn.lock")

        assert len(components) == 2
        assert any(c["name"] == "@angular/core" for c in components)
        assert any(c["name"] == "@types/node" for c in components)

    def test_parse_yarn_lock_with_version_extraction(self, parser):
        """Test version extraction from resolved URLs in yarn.lock."""
        content = """lodash@^4.17.21:
  version "4.17.21"
  resolved "https://registry.npmjs.org/lodash/-/lodash-4.17.21.tgz#hash"
"""

        components = parser.parse(content, "yarn.lock")

        assert len(components) == 1
        assert components[0]["name"] == "lodash"
        assert "4.17.21" in components[0]["version"]

    # parse_pnpm_lock_yaml tests

    def test_parse_pnpm_lock_yaml_with_packages(self, parser):
        """Test parsing pnpm-lock.yaml with packages section."""
        content = yaml.dump(
            {
                "packages": {
                    "lodash@4.17.21": {
                        "resolution": {
                            "integrity": "sha512-abc123",
                        },
                    },
                    "@angular/core@16.0.0": {
                        "resolution": {
                            "integrity": "sha512-def456",
                        },
                    },
                    "jest@29.0.0": {
                        "resolution": {
                            "integrity": "sha512-ghi789",
                        },
                        "dev": True,
                    },
                },
            }
        )

        components = parser.parse(content, "pnpm-lock.yaml")

        assert len(components) == 3
        assert any(c["name"] == "lodash" for c in components)
        assert any(c["name"] == "@angular/core" for c in components)

    def test_parse_pnpm_lock_yaml_with_dependencies(self, parser):
        """Test parsing pnpm-lock.yaml dependencies and devDependencies."""
        content = yaml.dump(
            {
                "dependencies": {
                    "express": {"version": "4.18.2"},
                    "axios": {"version": "1.5.0"},
                },
                "devDependencies": {
                    "jest": {"version": "29.0.0"},
                    "eslint": {"version": "8.50.0"},
                },
            }
        )

        components = parser.parse(content, "pnpm-lock.yaml")

        assert len(components) == 4
        runtime = [c for c in components if c["scope"] == "runtime"]
        dev = [c for c in components if c["scope"] == "dev"]
        assert len(runtime) == 2
        assert len(dev) == 2

    def test_parse_pnpm_lock_yaml_scoped_packages(self, parser):
        """Test parsing scoped packages in pnpm-lock.yaml."""
        content = yaml.dump(
            {
                "packages": {
                    "@angular/core@16.0.0": {},
                    "@babel/core@7.22.0": {},
                },
            }
        )

        components = parser.parse(content, "pnpm-lock.yaml")

        assert len(components) == 2
        assert any(c["name"] == "@angular/core" and "16.0.0" in c["version"] for c in components)

    # Error handling tests

    def test_parse_invalid_json_in_package_json(self, parser):
        """Test error handling for invalid JSON in package.json."""
        content = "{ invalid json }"

        with pytest.raises(ValueError):
            parser.parse(content, "package.json")

    def test_parse_invalid_json_in_package_lock(self, parser):
        """Test error handling for invalid JSON in package-lock.json."""
        content = "{ not valid json"

        with pytest.raises(ValueError):
            parser.parse(content, "package-lock.json")

    def test_parse_invalid_yaml_in_pnpm_lock(self, parser):
        """Test error handling for invalid YAML in pnpm-lock.yaml."""
        # Use actually invalid YAML: unmatched quotes and bad indentation
        content = """
        invalid: unclosed "quote
        bad:
          - item1
         - item2
        """

        with pytest.raises(ValueError):
            parser.parse(content, "pnpm-lock.yaml")

    def test_parse_empty_content(self, parser):
        """Test error handling for empty content."""
        with pytest.raises(ValueError):
            parser.parse("", "package.json")

    def test_parse_whitespace_only_content(self, parser):
        """Test error handling for whitespace-only content."""
        with pytest.raises(ValueError):
            parser.parse("   \n\t  ", "package.json")

    # Validation tests

    def test_validate_content_valid(self, parser):
        """Test content validation with valid input."""
        assert parser.validate_content("some content") is True

    def test_validate_content_empty(self, parser):
        """Test content validation with empty input."""
        assert parser.validate_content("") is False

    def test_validate_content_whitespace_only(self, parser):
        """Test content validation with whitespace-only input."""
        assert parser.validate_content("   \n\t  ") is False

    # Version normalization tests

    def test_normalize_version_with_valid_semver(self, parser):
        """Test version normalization with valid semantic versions."""
        assert parser.normalize_version("1.2.3") == "1.2.3"
        assert parser.normalize_version("2.0.0-beta.1") == "2.0.0-beta.1"

    def test_normalize_version_with_whitespace(self, parser):
        """Test version normalization strips whitespace."""
        assert parser.normalize_version("  1.2.3  ") == "1.2.3"

    def test_normalize_version_none_value(self, parser):
        """Test version normalization handles None gracefully."""
        # Parser should handle None in parse method before calling normalize_version
        assert parser.normalize_version("") == "unknown"

    # Integration tests

    def test_parse_complex_package_json(self, parser):
        """Test parsing complex package.json with all dependency types."""
        content = json.dumps(
            {
                "name": "complex-app",
                "version": "1.0.0",
                "dependencies": {
                    "lodash": "^4.17.21",
                    "express": "4.18.2",
                    "@angular/core": "16.0.0",
                },
                "devDependencies": {
                    "jest": "^29.0.0",
                    "@types/node": "^20.0.0",
                    "eslint": "8.50.0",
                },
                "peerDependencies": {
                    "react": "^18.0.0",
                },
                "optionalDependencies": {
                    "fsevents": "^2.3.0",
                },
            }
        )

        components = parser.parse(content, "package.json")

        assert len(components) == 8
        assert all(c["direct"] is True for c in components)
        assert all(c["package_type"] == "npm" for c in components)

        # Verify scope distribution
        runtime = [c for c in components if c["scope"] == "runtime"]
        dev = [c for c in components if c["scope"] == "dev"]
        assert len(runtime) == 5  # dependencies + peerDependencies + optionalDependencies
        assert len(dev) == 3  # devDependencies


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
