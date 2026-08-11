# Rust Cargo Dependency Parser Guide

## Overview

The `RustDependencyParser` is a dependency file parser for Rust projects that extracts package information from Cargo.toml and Cargo.lock files. It implements the `BaseDependencyParser` interface and supports various version specification formats and dependency scopes.

## Supported Files

- **Cargo.toml** - Manifest file with direct dependency declarations
- **Cargo.lock** - Lock file with pinned versions of all dependencies

## Features

### File Recognition

The parser automatically detects and handles:
- Cargo.toml files (direct dependencies)
- Cargo.lock files (pinned versions)

### Dependency Scopes

The parser distinguishes between three dependency scopes:

1. **runtime** - Regular dependencies from `[dependencies]` section
2. **dev** - Development dependencies from `[dev-dependencies]` section
3. **build** - Build script dependencies from `[build-dependencies]` section

### Version Specifier Support

The parser handles various Cargo version specification formats:

```toml
# Simple version strings
serde = "1.0"                          # Exact version
tokio = "1.25.0"                       # Semantic version

# Caret (compatible) versions
axum = "^0.6"                          # >= 0.6, < 1.0

# Tilde (approximately) versions
tower = "~0.4"                         # >= 0.4, < 0.5

# Comparison operators
tracing = ">=0.1.30"                   # Minimum version
regex = ">0.5"
uuid = "<=1.1"
log = "<0.5"

# Exact match
exact = "=1.2.3"
```

### Special Dependency Types

#### Git Dependencies

```toml
[dependencies]
some-crate = { git = "https://github.com/example/crate" }
some-crate = { git = "https://github.com/example/crate", rev = "abc123" }
some-crate = { git = "https://github.com/example/crate", branch = "main" }
some-crate = { git = "https://github.com/example/crate", tag = "v1.0.0" }
```

The parser extracts the git URL and revision/branch/tag information.

#### Path Dependencies

```toml
[dependencies]
my-lib = { path = "../my-lib" }
```

The parser captures the local path reference.

## Output Format

Each dependency is returned as a dictionary with the following fields:

```python
{
    "name": str,  # Crate name (e.g., "serde")
    "version": str,  # Semantic version (e.g., "1.0.130")
    "purl": str,  # Package URL (e.g., "pkg:cargo/serde@1.0.130")
    "package_type": str,  # Always "cargo"
    "scope": str,  # "runtime", "dev", or "build"
    "direct": bool,  # True for Cargo.toml, False for Cargo.lock
    "source_file": str,  # "Cargo.toml" or "Cargo.lock"
    # Optional fields for special dependency types:
    "git": str,  # Git URL (if git dependency)
    "path": str,  # Local path (if path dependency)
}
```

## Usage Examples

### Basic Usage

```python
from apps.api.services.sbom.parsers.rust_parser import RustDependencyParser

parser = RustDependencyParser()

# Check if parser can handle a file
if parser.can_parse("Cargo.toml"):
    # Parse the file content
    with open("Cargo.toml", "r") as f:
        content = f.read()

    dependencies = parser.parse(content, "Cargo.toml")

    for dep in dependencies:
        print(f"Found: {dep['name']} v{dep['version']}")
```

### Integration with SBOM Service

```python
from apps.api.services.sbom.service import SBOMService
from apps.api.services.sbom.parsers.rust_parser import RustDependencyParser

# Create service and register parser
service = SBOMService()
service.register_parser(RustDependencyParser())

# Parse a file
with open("Cargo.toml", "r") as f:
    content = f.read()

dependencies = service.parse_dependency_file("Cargo.toml", content)
```

### Filtering by Scope

```python
# Get only runtime dependencies
runtime_deps = [d for d in dependencies if d["scope"] == "runtime"]

# Get only dev dependencies
dev_deps = [d for d in dependencies if d["scope"] == "dev"]

# Get only build dependencies
build_deps = [d for d in dependencies if d["scope"] == "build"]
```

### Working with Cargo.lock

```python
# Parse pinned versions from lock file
with open("Cargo.lock", "r") as f:
    lock_content = f.read()

locked_deps = parser.parse(lock_content, "Cargo.lock")

# All Cargo.lock dependencies have direct=False
for dep in locked_deps:
    assert dep["direct"] is False
```

### Package URL (PURL) Generation

```python
# Each dependency includes a standardized Package URL
dep = dependencies[0]
print(dep["purl"])  # Output: pkg:cargo/serde@1.0.130

# PURLs can be used for vulnerability tracking and SBOM generation
```

## Version Normalization

The parser normalizes various version specifier formats to semantic versions:

```python
parser = RustDependencyParser()

# All these normalize to "1.0"
parser._normalize_cargo_version("1.0")  # "1.0"
parser._normalize_cargo_version("^1.0")  # "1.0"
parser._normalize_cargo_version("~1.0")  # "1.0"
parser._normalize_cargo_version(">=1.0")  # "1.0"
parser._normalize_cargo_version(">1.0")  # "1.0"
```

## Error Handling

The parser raises `ValueError` for invalid input:

```python
parser = RustDependencyParser()

try:
    # Invalid TOML
    parser.parse("invalid [toml", "Cargo.toml")
except ValueError as e:
    print(f"Parse error: {e}")

try:
    # Empty content
    parser.parse("", "Cargo.toml")
except ValueError as e:
    print(f"Validation error: {e}")
```

## Real-World Example

Here's a complete example parsing a realistic Cargo.toml:

```python
from apps.api.services.sbom.parsers.rust_parser import RustDependencyParser

content = """
[package]
name = "server"
version = "0.1.0"
edition = "2021"

[dependencies]
tokio = { version = "1.25", features = ["full"] }
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
axum = "^0.6"
tower = "~0.4"

[dev-dependencies]
mockito = "0.9"

[build-dependencies]
cc = "1.0"
"""

parser = RustDependencyParser()
deps = parser.parse(content, "Cargo.toml")

# Results:
# - 5 runtime dependencies (tokio, serde, serde_json, axum, tower)
# - 1 dev dependency (mockito)
# - 1 build dependency (cc)

# Filter by scope
runtime = [d for d in deps if d["scope"] == "runtime"]
dev = [d for d in deps if d["scope"] == "dev"]
build = [d for d in deps if d["scope"] == "build"]

print(f"Runtime: {len(runtime)}, Dev: {len(dev)}, Build: {len(build)}")
# Output: Runtime: 5, Dev: 1, Build: 1
```

## Testing

Comprehensive unit tests are available in `tests/unit/test_sbom_rust_parser.py`:

```bash
# Run all Rust parser tests
pytest tests/unit/test_sbom_rust_parser.py -v

# Run specific test class
pytest tests/unit/test_sbom_rust_parser.py::TestRustParserParseCargoToml -v

# Run specific test
pytest tests/unit/test_sbom_rust_parser.py::TestRustParserParseCargoToml::test_parse_simple_string_version -v
```

### Test Coverage

The test suite covers:

- File recognition (can_parse)
- Supported files listing
- Simple version specifications
- Complex version specifiers (^, ~, >=, <=, etc.)
- All three dependency scopes (runtime, dev, build)
- Git and path dependencies
- Cargo.lock parsing
- PURL generation
- Version normalization
- Error handling for invalid TOML
- Real-world integration scenarios

## Implementation Details

### TOML Parsing

The parser uses Python's built-in `tomllib` module (Python 3.11+) for TOML parsing. This ensures:

- Fast, safe parsing without external dependencies
- Full TOML 1.0 specification support
- Proper error handling with descriptive messages

### Direct vs. Indirect Dependencies

- **Direct** (`direct=True`): Dependencies explicitly declared in Cargo.toml
- **Indirect** (`direct=False`): Transitive dependencies pinned in Cargo.lock

This distinction is useful for:
- License compliance analysis (often only direct deps matter)
- Vulnerability scanning
- Dependency tree analysis

## Limitations and Considerations

1. **Workspace dependencies**: Currently parses each Cargo.toml independently. Workspace-level dependencies may need additional handling.

2. **Feature resolution**: Version specifiers with feature dependencies are parsed but feature information is not currently tracked in the output.

3. **Platform-specific dependencies**: Dependencies specified with `cfg()` markers are parsed but platform information is not tracked.

4. **Dependency conditions**: Optional dependencies and conditional features are parsed but conditions are not explicitly tracked.

## Integration with SBOM Service

The parser is registered with the SBOM service for automatic dependency file detection:

```python
from apps.api.services.sbom.service import SBOMService
from apps.api.services.sbom.parsers.rust_parser import RustDependencyParser

service = SBOMService()
service.register_parser(RustDependencyParser())

# Service automatically routes to correct parser
deps = service.parse_dependency_file("Cargo.toml", content)
```

## See Also

- [BaseDependencyParser](../base.py) - Base class interface
- [SBOM Service](../service.py) - Main SBOM service
- [Cargo Documentation](https://doc.rust-lang.org/cargo/)
- [Package URL (PURL) Specification](https://github.com/package-url/purl-spec)
