"""Dependency file parsers for various package managers.

This module contains concrete implementations of BaseDependencyParser
for different package managers and dependency file formats:

- Rust (Cargo.toml, Cargo.lock) - RustDependencyParser
- Go (go.mod, go.sum) - GoParser
- npm (package.json, package-lock.json, yarn.lock, pnpm-lock.yaml) - NodeDependencyParser
- .NET (csproj, fsproj, packages.config) - DotnetParser
- Java/Maven (pom.xml) - JavaDependencyParser
- Gradle (build.gradle, build.gradle.kts) - JavaDependencyParser
- Python (requirements.txt, setup.py, pyproject.toml, Pipfile) - PythonDependencyParser
- Composer (composer.json, composer.lock) - Coming soon
- And others

Parsers are added incrementally as support for each package manager
is implemented.
"""

# flake8: noqa: E501

from .dotnet_parser import DotnetParser
from .go_parser import GoParser
from .java_parser import JavaDependencyParser
from .node_parser import NodeDependencyParser
from .python_parser import PythonDependencyParser
from .rust_parser import RustDependencyParser
from .sbom_parser import SBOMParser

__all__ = [
    "DotnetParser",
    "GoParser",
    "JavaDependencyParser",
    "NodeDependencyParser",
    "PythonDependencyParser",
    "RustDependencyParser",
    "SBOMParser",
]
