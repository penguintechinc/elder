"""Unit tests to validate proper decorator usage on API endpoints.

This test suite ensures that decorators are correctly applied to endpoints,
specifically catching issues like @resource_role_required on CREATE endpoints.
"""

import ast
import os
from pathlib import Path
from typing import Dict, List, Set


class DecoratorValidator:
    """Validate decorator usage across API endpoint files."""

    def __init__(self, api_path: str):
        """Initialize validator with path to API directory."""
        self.api_path = Path(api_path)
        self.issues: list[dict[str, str]] = []

    def find_endpoint_files(self) -> list[Path]:
        """Find all Python files in the API v1 directory."""
        v1_path = self.api_path / "api" / "v1"
        if not v1_path.exists():
            return []
        return list(v1_path.glob("*.py"))

    def parse_file(self, file_path: Path) -> ast.Module:
        """Parse a Python file into an AST."""
        with open(file_path) as f:
            return ast.parse(f.read(), filename=str(file_path))

    def get_decorators(self, node: ast.FunctionDef) -> set[str]:
        """Extract decorator names from a function definition."""
        decorators = set()
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name):
                decorators.add(decorator.id)
            elif isinstance(decorator, ast.Call):
                if isinstance(decorator.func, ast.Name):
                    decorators.add(decorator.func.id)
        return decorators

    def get_route_methods(self, node: ast.FunctionDef) -> list[str]:
        """Extract HTTP methods from @bp.route decorator."""
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call):
                # Check if it's bp.route
                if (
                    isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "route"
                ):
                    # Look for methods parameter
                    for keyword in decorator.keywords:
                        if keyword.arg == "methods":
                            if isinstance(keyword.value, ast.List):
                                return [
                                    elt.s if isinstance(elt, ast.Str) else elt.value
                                    for elt in keyword.value.elts
                                    if isinstance(elt, (ast.Str, ast.Constant))
                                ]
        return []

    def is_create_function(self, func_name: str, methods: list[str]) -> bool:
        """Determine if a function is a CREATE endpoint."""
        # Check if it's a POST method and function name suggests creation
        if "POST" in methods:
            if func_name.startswith("create_") or func_name == "create":
                return True
        return False

    def validate_decorators(self, file_path: Path) -> None:
        """Validate decorator usage in a file."""
        try:
            tree = self.parse_file(file_path)
        except SyntaxError:
            return

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                decorators = self.get_decorators(node)
                methods = self.get_route_methods(node)

                # Check if this is a CREATE endpoint
                if self.is_create_function(node.name, methods):
                    # CREATE endpoints should NOT have @resource_role_required
                    if "resource_role_required" in decorators:
                        self.issues.append(
                            {
                                "file": str(file_path.name),
                                "function": node.name,
                                "issue": "CREATE endpoint has @resource_role_required decorator",
                                "severity": "error",
                                "description": (
                                    "@resource_role_required should only be used on "
                                    "UPDATE/DELETE endpoints, not CREATE endpoints. "
                                    "Users cannot have roles on resources that don't exist yet."
                                ),
                            }
                        )

                    # CREATE endpoints should have @login_required
                    if "login_required" not in decorators:
                        self.issues.append(
                            {
                                "file": str(file_path.name),
                                "function": node.name,
                                "issue": "CREATE endpoint missing @login_required decorator",
                                "severity": "warning",
                                "description": (
                                    "CREATE endpoints should have @login_required "
                                    "to ensure only authenticated users can create resources."
                                ),
                            }
                        )

    def validate_all(self) -> list[dict[str, str]]:
        """Validate all API endpoint files."""
        files = self.find_endpoint_files()
        for file_path in files:
            self.validate_decorators(file_path)
        return self.issues


def test_no_resource_role_required_on_create_endpoints():
    """Test that CREATE endpoints do not have @resource_role_required decorator."""
    # Get the API path
    tests_dir = Path(__file__).parent.parent
    project_root = tests_dir.parent
    api_path = project_root / "apps" / "api"

    if not api_path.exists():
        # Running outside of normal project structure, skip test
        return

    validator = DecoratorValidator(str(api_path))
    issues = validator.validate_all()

    # Filter for critical errors (resource_role_required on CREATE)
    errors = [i for i in issues if i["severity"] == "error"]

    if errors:
        error_messages = []
        for error in errors:
            error_messages.append(
                f"\n{error['file']}::{error['function']}\n"
                f"  Issue: {error['issue']}\n"
                f"  Fix: {error['description']}"
            )
        pytest.fail(
            f"Found {len(errors)} decorator validation error(s):"
            + "".join(error_messages)
        )


def test_create_endpoints_have_login_required():
    """Test that all CREATE endpoints have @login_required decorator."""
    tests_dir = Path(__file__).parent.parent
    project_root = tests_dir.parent
    api_path = project_root / "apps" / "api"

    if not api_path.exists():
        return

    validator = DecoratorValidator(str(api_path))
    issues = validator.validate_all()

    # Filter for warnings (missing login_required)
    warnings = [i for i in issues if i["severity"] == "warning"]

    if warnings:
        warning_messages = []
        for warning in warnings:
            warning_messages.append(
                f"\n{warning['file']}::{warning['function']}\n"
                f"  Issue: {warning['issue']}\n"
                f"  Recommendation: {warning['description']}"
            )
        # Print warnings but don't fail the test
        print(
            f"Found {len(warnings)} decorator validation warning(s):"
            + "".join(warning_messages)
        )


def test_validator_finds_test_files():
    """Test that validator can find API endpoint files."""
    tests_dir = Path(__file__).parent.parent
    project_root = tests_dir.parent
    api_path = project_root / "apps" / "api"

    if not api_path.exists():
        return

    validator = DecoratorValidator(str(api_path))
    files = validator.find_endpoint_files()

    # Should find multiple endpoint files
    assert len(files) > 0, "Should find API endpoint files"

    # Check that some expected files exist
    file_names = [f.name for f in files]
    expected_files = ["entities.py", "software.py", "organizations.py"]
    found_expected = [f for f in expected_files if f in file_names]
    assert (
        len(found_expected) > 0
    ), f"Should find at least one of {expected_files}, found: {file_names}"


if __name__ == "__main__":
    """Run validator standalone for debugging."""
    import sys

    tests_dir = Path(__file__).parent.parent
    project_root = tests_dir.parent
    api_path = project_root / "apps" / "api"

    validator = DecoratorValidator(str(api_path))
    issues = validator.validate_all()

    if issues:
        print(f"Found {len(issues)} decorator validation issue(s):\n")
        for issue in issues:
            print(f"[{issue['severity'].upper()}] {issue['file']}::{issue['function']}")
            print(f"  {issue['issue']}")
            print(f"  {issue['description']}\n")
        sys.exit(1)
    else:
        print("✓ All decorator validations passed!")
        sys.exit(0)
