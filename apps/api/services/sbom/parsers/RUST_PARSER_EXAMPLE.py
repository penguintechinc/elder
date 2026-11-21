"""Example usage of the RustDependencyParser.

This file demonstrates how to use the RustDependencyParser to parse
Cargo.toml and Cargo.lock files in various scenarios.

Run this file to see example output:
    python3 apps/api/services/sbom/parsers/RUST_PARSER_EXAMPLE.py
"""

# flake8: noqa: E501

from apps.api.services.sbom.parsers.rust_parser import RustDependencyParser


def example_simple_cargo_toml() -> None:
    """Example: Parse a simple Cargo.toml file."""
    print("=" * 70)
    print("EXAMPLE 1: Simple Cargo.toml Parsing")
    print("=" * 70)

    parser = RustDependencyParser()

    # Simple Cargo.toml content
    content = """[package]
name = "hello-world"
version = "0.1.0"
edition = "2021"

[dependencies]
serde = "1.0"
tokio = "1.25"
"""

    print("\nInput Cargo.toml:")
    print(content)

    dependencies = parser.parse(content, "Cargo.toml")

    print("\nParsed Dependencies:")
    for dep in dependencies:
        print(f"  - {dep['name']} v{dep['version']} ({dep['scope']})")
        print(f"    PURL: {dep['purl']}")


def example_complex_cargo_toml() -> None:
    """Example: Parse Cargo.toml with version specifiers."""
    print("\n" + "=" * 70)
    print("EXAMPLE 2: Version Specifiers")
    print("=" * 70)

    parser = RustDependencyParser()

    content = """[package]
name = "advanced-app"
version = "1.0.0"

[dependencies]
serde = "1.0.130"
tokio = { version = "1.25", features = ["full"] }
axum = "^0.6"
tower = "~0.4"
uuid = ">=1.0"

[dev-dependencies]
mockito = "0.9"

[build-dependencies]
cc = "1.0"
"""

    print("\nInput Cargo.toml:")
    print(content)

    dependencies = parser.parse(content, "Cargo.toml")

    print("\nParsed Dependencies by Scope:")
    scopes = {}
    for dep in dependencies:
        scope = dep["scope"]
        if scope not in scopes:
            scopes[scope] = []
        scopes[scope].append(dep)

    for scope in ["runtime", "dev", "build"]:
        if scope in scopes:
            print(f"\n  {scope.upper()} Dependencies ({len(scopes[scope])}):")
            for dep in scopes[scope]:
                print(f"    - {dep['name']} v{dep['version']}")
                print(f"      PURL: {dep['purl']}")


def example_special_dependencies() -> None:
    """Example: Parse special dependency types."""
    print("\n" + "=" * 70)
    print("EXAMPLE 3: Git and Path Dependencies")
    print("=" * 70)

    parser = RustDependencyParser()

    content = """[dependencies]
# Regular dependency
serde = "1.0"

# Git dependency with revision
my-crate = { git = "https://github.com/example/my-crate", rev = "abc123" }

# Local path dependency
local-lib = { path = "../local-lib" }
"""

    print("\nInput Cargo.toml:")
    print(content)

    dependencies = parser.parse(content, "Cargo.toml")

    print("\nParsed Dependencies:")
    for dep in dependencies:
        print(f"\n  {dep['name']}:")
        print(f"    Version: {dep['version']}")
        print(f"    PURL: {dep['purl']}")
        if "git" in dep:
            print(f"    Git URL: {dep['git']}")
        if "path" in dep:
            print(f"    Path: {dep['path']}")


def example_cargo_lock() -> None:
    """Example: Parse Cargo.lock file."""
    print("\n" + "=" * 70)
    print("EXAMPLE 4: Cargo.lock Parsing")
    print("=" * 70)

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

[[package]]
name = "uuid"
version = "1.1.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""

    print("\nInput Cargo.lock:")
    print(content)

    dependencies = parser.parse(content, "Cargo.lock")

    print("\nParsed Pinned Dependencies:")
    print(f"Total packages: {len(dependencies)}")
    for dep in dependencies:
        print(f"\n  {dep['name']}:")
        print(f"    Pinned Version: {dep['version']}")
        print(f"    Direct: {dep['direct']}")
        print(f"    PURL: {dep['purl']}")


def example_version_normalization() -> None:
    """Example: Version normalization."""
    print("\n" + "=" * 70)
    print("EXAMPLE 5: Version Normalization")
    print("=" * 70)

    parser = RustDependencyParser()

    versions = [
        "1.0",
        "^1.0",
        "~1.0",
        ">=1.0",
        ">1.0",
        "<=2.0",
        "<2.0",
        "=1.0",
        "1.0.0",
        "^1.5.3",
    ]

    print("\nVersion Specifier Normalization:")
    print(f"{'Original':<20} {'Normalized':<20}")
    print("-" * 40)
    for version in versions:
        normalized = parser._normalize_cargo_version(version)
        print(f"{version:<20} {normalized:<20}")


def example_with_sbom_service() -> None:
    """Example: Integration with SBOM service."""
    print("\n" + "=" * 70)
    print("EXAMPLE 6: SBOM Service Integration")
    print("=" * 70)

    from apps.api.services.sbom.service import SBOMService

    # Create service and register parser
    service = SBOMService()
    service.register_parser(RustDependencyParser())

    # Parse a file
    content = """[dependencies]
tokio = "1.25"
serde = { version = "1.0", features = ["derive"] }

[dev-dependencies]
mockito = "0.9"
"""

    print("\nUsing SBOM Service to parse Cargo.toml:")
    print(content)

    # Service automatically routes to correct parser
    dependencies = service.parse_dependency_file("Cargo.toml", content)

    print(f"\nService found parser and extracted {len(dependencies)} dependencies:")
    for dep in dependencies:
        print(f"  - {dep['name']} v{dep['version']} (scope: {dep['scope']})")


def example_filtering() -> None:
    """Example: Filter dependencies by criteria."""
    print("\n" + "=" * 70)
    print("EXAMPLE 7: Filtering Dependencies")
    print("=" * 70)

    parser = RustDependencyParser()

    content = """[dependencies]
tokio = "1.25"
serde = "1.0"
serde_json = "1.0"

[dev-dependencies]
mockito = "0.9"

[build-dependencies]
cc = "1.0"
"""

    dependencies = parser.parse(content, "Cargo.toml")

    # Filter by scope
    runtime_deps = [d for d in dependencies if d["scope"] == "runtime"]
    dev_deps = [d for d in dependencies if d["scope"] == "dev"]
    build_deps = [d for d in dependencies if d["scope"] == "build"]

    print("\nFiltered Dependencies:")
    print(f"  Runtime ({len(runtime_deps)}): {[d['name'] for d in runtime_deps]}")
    print(f"  Dev ({len(dev_deps)}): {[d['name'] for d in dev_deps]}")
    print(f"  Build ({len(build_deps)}): {[d['name'] for d in build_deps]}")

    # Filter by direct
    direct_deps = [d for d in dependencies if d["direct"]]
    print(f"\n  Direct ({len(direct_deps)}): {[d['name'] for d in direct_deps]}")


if __name__ == "__main__":
    """Run all examples."""
    print("\n" + "=" * 70)
    print("RUST CARGO DEPENDENCY PARSER - USAGE EXAMPLES")
    print("=" * 70)

    example_simple_cargo_toml()
    example_complex_cargo_toml()
    example_special_dependencies()
    example_cargo_lock()
    example_version_normalization()
    example_filtering()
    example_with_sbom_service()

    print("\n" + "=" * 70)
    print("All examples completed successfully!")
    print("=" * 70 + "\n")
