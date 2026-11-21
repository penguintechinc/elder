# SBOM Dependency Parsers

This directory contains concrete implementations of the `BaseDependencyParser` interface for parsing various package manager dependency files.

## Available Parsers

### Rust (Cargo)
- **Class**: `RustDependencyParser`
- **Module**: `rust_parser.py`
- **Supported Files**:
  - `Cargo.toml` - Manifest with direct dependencies
  - `Cargo.lock` - Lock file with pinned versions
- **Status**: Production Ready
- **Features**:
  - All Cargo version specifiers (^, ~, >=, <=, etc.)
  - Dependency scopes (runtime, dev, build)
  - Git and path dependencies
  - PURL generation
  - Comprehensive test coverage (50+ tests)

### Go
- **Class**: `GoParser`
- **Module**: `go_parser.py`
- **Supported Files**:
  - `go.mod` - Manifest with direct dependencies
  - `go.sum` - Lock file with checksums
- **Status**: Available

### Node.js (npm, yarn, pnpm)
- **Class**: `NodeDependencyParser`
- **Module**: `node_parser.py`
- **Supported Files**:
  - `package.json`
  - `package-lock.json`
  - `yarn.lock`
  - `pnpm-lock.yaml`
- **Status**: Available

### .NET
- **Class**: `DotnetParser`
- **Module**: `dotnet_parser.py`
- **Supported Files**:
  - `.csproj` / `.fsproj` - Project files
  - `packages.config`
- **Status**: Available

### Java/Maven & Gradle
- **Class**: `JavaDependencyParser`
- **Module**: `java_parser.py`
- **Supported Files**:
  - `pom.xml` - Maven
  - `build.gradle` / `build.gradle.kts` - Gradle
- **Status**: Available

### Python
- **Class**: `PythonDependencyParser`
- **Module**: `python_parser.py`
- **Supported Files**:
  - `requirements.txt`
  - `setup.py`
  - `pyproject.toml`
  - `Pipfile`
- **Status**: Available

## Quick Start

### Using a Parser Directly

```python
from apps.api.services.sbom.parsers.rust_parser import RustDependencyParser

parser = RustDependencyParser()

# Check if parser handles the file
if parser.can_parse("Cargo.toml"):
    # Read file content
    with open("Cargo.toml", "r") as f:
        content = f.read()

    # Parse dependencies
    dependencies = parser.parse(content, "Cargo.toml")

    # Use dependencies
    for dep in dependencies:
        print(f"{dep['name']} v{dep['version']}")
```

### Using with SBOM Service

```python
from apps.api.services.sbom.service import SBOMService
from apps.api.services.sbom.parsers.rust_parser import RustDependencyParser

# Create service and register parser
service = SBOMService()
service.register_parser(RustDependencyParser())

# Service finds appropriate parser automatically
with open("Cargo.toml", "r") as f:
    dependencies = service.parse_dependency_file("Cargo.toml", f.read())
```

## Common Output Format

All parsers return dependencies in a standardized format:

```python
{
    "name": str,  # Package name
    "version": str,  # Semantic version
    "purl": str,  # Package URL (pkg:ecosystem/name@version)
    "package_type": str,  # Type (cargo, go, npm, etc.)
    "scope": str,  # Scope (runtime, dev, test, build, etc.)
    "direct": bool,  # Direct vs. transitive dependency
    "source_file": str,  # Source filename
}
```

## Parser Architecture

All parsers inherit from `BaseDependencyParser` and implement:

### Required Methods

- `can_parse(filename: str) -> bool`
  - Check if parser handles the filename

- `get_supported_files() -> List[str]`
  - Return list of supported filenames/patterns

- `parse(content: str, filename: str) -> List[Dict[str, Any]]`
  - Parse file content and return dependencies

### Optional Methods

- `validate_content(content: str) -> bool`
  - Validate file format (inherited with basic implementation)

- `normalize_version(version: str) -> str`
  - Normalize version strings (inherited with basic implementation)

## Rust Parser Documentation

For comprehensive documentation on the Rust parser specifically, see:

- **[RUST_PARSER_GUIDE.md](RUST_PARSER_GUIDE.md)** - Complete usage guide with examples
- **[RUST_PARSER_IMPLEMENTATION.md](RUST_PARSER_IMPLEMENTATION.md)** - Implementation details and architecture
- **[RUST_PARSER_EXAMPLE.py](RUST_PARSER_EXAMPLE.py)** - Runnable examples

## Testing

Each parser includes comprehensive unit tests:

```bash
# Run all tests
pytest tests/unit/test_sbom_*.py -v

# Run Rust parser tests specifically
pytest tests/unit/test_sbom_rust_parser.py -v

# Run specific test class
pytest tests/unit/test_sbom_rust_parser.py::TestRustParserParseCargoToml -v
```

### Rust Parser Test Coverage

- 50+ test methods
- 9 test classes
- Coverage areas:
  - File recognition
  - All Cargo version specifiers
  - Dependency scopes (runtime, dev, build)
  - Special dependency types (git, path)
  - Cargo.lock parsing
  - PURL generation
  - Version normalization
  - Error handling
  - Real-world scenarios

## Adding a New Parser

To add support for a new package manager:

1. **Create parser module** in this directory:
   ```python
   from apps.api.services.sbom.base import BaseDependencyParser


   class NewLanguageParser(BaseDependencyParser):
       def can_parse(self, filename: str) -> bool:
           # Check if filename matches supported patterns
           pass

       def get_supported_files(self) -> List[str]:
           # Return list of supported filenames
           pass

       def parse(self, content: str, filename: str) -> List[Dict[str, Any]]:
           # Parse content and return dependencies
           pass
   ```

2. **Add to __init__.py**:
   ```python
   from .new_parser import NewLanguageParser

   __all__ = [..., "NewLanguageParser"]
   ```

3. **Create comprehensive tests** in `tests/unit/test_sbom_new_parser.py`

4. **Document the parser** in a README or guide file

5. **Register with SBOM service**:
   ```python
   service.register_parser(NewLanguageParser())
   ```

## File Structure

```
parsers/
├── README.md                          # This file
├── base.py                            # Base class (in parent dir)
├── __init__.py                        # Package exports
├── rust_parser.py                     # Rust Cargo parser
├── go_parser.py                       # Go parser
├── node_parser.py                     # Node.js parser
├── dotnet_parser.py                   # .NET parser
├── java_parser.py                     # Java/Maven/Gradle parser
├── python_parser.py                   # Python parser
├── RUST_PARSER_GUIDE.md               # Rust parser usage guide
├── RUST_PARSER_IMPLEMENTATION.md      # Rust parser details
└── RUST_PARSER_EXAMPLE.py             # Rust parser examples
```

## Testing Approach

All parsers follow consistent testing patterns:

1. **File Recognition Tests** - Can parser identify correct files?
2. **Supported Files Tests** - Does get_supported_files() return correct list?
3. **Parsing Tests** - Does parser correctly extract dependencies?
4. **Format Tests** - Are output fields in correct format?
5. **Special Cases** - Git, path, optional dependencies, etc.
6. **Normalization Tests** - Version normalization working?
7. **Error Handling Tests** - Proper error handling for invalid input?
8. **Integration Tests** - Real-world scenarios working?

## PURL (Package URL) Format

All parsers generate Package URLs for compatibility with SBOM standards:

- Format: `pkg:{ecosystem}/{name}@{version}`
- Examples:
  - `pkg:cargo/serde@1.0.130`
  - `pkg:go/github.com/gin-gonic/gin@v1.9.1`
  - `pkg:npm/express@4.18.2`
  - `pkg:nuget/NewtonSoft.Json@13.0.1`

See [PURL Specification](https://github.com/package-url/purl-spec) for details.

## Performance

All parsers optimize for typical real-world use cases:

- **Single-file parsing**: Milliseconds
- **Large manifest files**: Linear time complexity
- **Memory efficiency**: Stream where possible, cache structure minimally
- **Error recovery**: Continue parsing despite individual failures

## Known Limitations

- **Workspace/Monorepo**: Parsers handle individual manifest files
- **Transitive Resolution**: Cargo.lock provides pinned versions but not full tree
- **Feature/Conditional Dependencies**: Parsed but metadata not always captured
- **Platform-specific**: Dependencies with platform conditions noted but not filtered

## Future Enhancements

- Workspace/monorepo support
- Dependency tree resolution
- Vulnerability integration
- License compliance checking
- Advanced filtering and analysis
- Performance optimizations for large codebases

## Support & Documentation

- **Rust Parser**: See [RUST_PARSER_GUIDE.md](RUST_PARSER_GUIDE.md)
- **Base Class**: See [../base.py](../base.py)
- **SBOM Service**: See [../service.py](../service.py)
- **Tests**: See [../../../tests/unit/](../../../tests/unit/)

## License

Limited AGPL3 with preamble for fair use, as per Elder project standards.
