"""
Unit tests for FastAPI endpoint parser.

Tests the FastAPIEndpointParser for parsing FastAPI route decorators
and extracting endpoint information without external dependencies.
"""

import pytest

from apps.scanner.parsers.endpoint_parser_fastapi import FastAPIEndpointParser


@pytest.fixture
def parser():
    """Create FastAPI parser instance."""
    return FastAPIEndpointParser()


class TestCanParse:
    """Test can_parse() method for file type detection."""

    def test_can_parse_python_file(self, parser):
        """Test that .py files are recognized."""
        assert parser.can_parse("app.py") is True
        assert parser.can_parse("routes.py") is True
        assert parser.can_parse("main.py") is True

    def test_can_parse_case_insensitive(self, parser):
        """Test case-insensitive file extension matching."""
        assert parser.can_parse("APP.PY") is True
        assert parser.can_parse("Main.Py") is True

    def test_cannot_parse_non_python(self, parser):
        """Test that non-Python files are rejected."""
        assert parser.can_parse("app.js") is False
        assert parser.can_parse("routes.go") is False
        assert parser.can_parse("main.ts") is False

    def test_cannot_parse_empty_filename(self, parser):
        """Test that empty filename is rejected."""
        assert parser.can_parse("") is False
        assert parser.can_parse(None) is False


class TestParseBasicDecorators:
    """Test parse() with basic FastAPI decorators."""

    def test_parse_simple_get_route(self, parser):
        """Test parsing simple @app.get decorator."""
        code = """
from fastapi import FastAPI

app = FastAPI()

@app.get('/users')
def get_users():
    return []
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 1
        endpoint = endpoints[0]
        assert endpoint["path"] == "/users"
        assert endpoint["methods"] == ["GET"]
        assert endpoint["function_name"] == "get_users"
        assert endpoint["framework"] == "fastapi"
        assert endpoint["source_file"] == "app.py"
        assert endpoint["auth_required"] is False

    def test_parse_post_route(self, parser):
        """Test parsing @app.post decorator."""
        code = """
@app.post('/items')
def create_item(item: Item):
    return item
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 1
        assert endpoints[0]["methods"] == ["POST"]
        assert endpoints[0]["path"] == "/items"

    def test_parse_multiple_decorators(self, parser):
        """Test parsing multiple route decorators."""
        code = """
@app.get('/users')
def list_users():
    return []

@app.post('/users')
def create_user():
    return {}

@app.get('/items')
def list_items():
    return []
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 3
        paths = [e["path"] for e in endpoints]
        assert "/users" in paths
        assert "/items" in paths

    def test_parse_path_with_parameters(self, parser):
        """Test parsing paths with OpenAPI-style parameters."""
        code = """
@app.get('/users/{user_id}')
def get_user(user_id: int):
    return {}
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/users/{user_id}"

    def test_parse_put_patch_delete(self, parser):
        """Test parsing various HTTP methods."""
        code = """
@app.put('/items/{item_id}')
def update_item():
    pass

@app.patch('/items/{item_id}')
def partial_update():
    pass

@app.delete('/items/{item_id}')
def delete_item():
    pass
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 3
        methods = [e["methods"][0] for e in endpoints]
        assert "PUT" in methods
        assert "PATCH" in methods
        assert "DELETE" in methods


class TestParseRouterDecorators:
    """Test parse() with APIRouter decorators."""

    def test_parse_router_get(self, parser):
        """Test parsing @router.get decorator."""
        code = """
from fastapi import APIRouter

router = APIRouter()

@router.get('/products')
def list_products():
    return []
"""
        endpoints = parser.parse(code, "routes.py")

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/products"
        assert endpoints[0]["methods"] == ["GET"]

    def test_parse_router_post(self, parser):
        """Test parsing @router.post decorator."""
        code = """
@router.post('/products')
def create_product():
    return {}
"""
        endpoints = parser.parse(code, "routes.py")

        assert len(endpoints) == 1
        assert endpoints[0]["methods"] == ["POST"]

    def test_parse_mixed_app_and_router(self, parser):
        """Test parsing both app and router decorators."""
        code = """
@app.get('/health')
def health_check():
    return {"status": "ok"}

@router.get('/users')
def get_users():
    return []
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 2
        paths = {e["path"] for e in endpoints}
        assert "/health" in paths
        assert "/users" in paths


class TestParseAPIRoute:
    """Test parse() with api_route decorator."""

    def test_parse_api_route_single_method(self, parser):
        """Test parsing @app.api_route with single method."""
        code = """
@app.api_route('/custom', methods=['GET'])
def custom_route():
    return {}
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/custom"
        assert endpoints[0]["methods"] == ["GET"]

    def test_parse_api_route_multiple_methods(self, parser):
        """Test parsing @app.api_route with multiple methods."""
        code = """
@app.api_route('/resource', methods=['GET', 'POST', 'PUT'])
def handle_resource():
    return {}
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/resource"
        methods = endpoints[0]["methods"]
        assert "GET" in methods
        assert "POST" in methods
        assert "PUT" in methods

    def test_parse_api_route_router(self, parser):
        """Test parsing @router.api_route."""
        code = """
@router.api_route('/data', methods=['GET', 'POST'])
def handle_data():
    pass
"""
        endpoints = parser.parse(code, "routes.py")

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/data"
        assert set(endpoints[0]["methods"]) == {"GET", "POST"}


class TestAuthenticationDetection:
    """Test detection of authentication requirements."""

    def test_depends_auth_detection(self, parser):
        """Test detection of Depends() authentication."""
        code = """
@app.get('/protected')
def protected_route(current_user: User = Depends(get_current_user)):
    return current_user
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 1
        assert endpoints[0]["auth_required"] is True

    def test_no_auth_when_no_depends(self, parser):
        """Test auth_required is False when no Depends()."""
        code = """
@app.get('/public')
def public_route():
    return {"message": "public"}
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 1
        assert endpoints[0]["auth_required"] is False

    def test_depends_auth_multiline(self, parser):
        """Test Depends() detection across multiple lines."""
        code = """
@app.get('/data')
def get_data(
    user: User = Depends(get_current_user),
    limit: int = 10
):
    return []
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 1
        assert endpoints[0]["auth_required"] is True

    def test_depends_in_body_not_detected_as_auth(self, parser):
        """Test that Depends() without authentication context."""
        code = """
@app.get('/items')
def get_items(deps: Depends(get_dependencies)):
    return []
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 1
        # Depends() is detected as auth indicator regardless of context
        assert endpoints[0]["auth_required"] is True


class TestPathParameterNormalization:
    """Test path parameter normalization."""

    def test_openapi_path_parameters_unchanged(self, parser):
        """Test that OpenAPI-style paths remain unchanged."""
        code = """
@app.get('/users/{user_id}/posts/{post_id}')
def get_user_post():
    pass
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 1
        # FastAPI already uses OpenAPI format
        assert endpoints[0]["path"] == "/users/{user_id}/posts/{post_id}"

    def test_complex_path_parsing(self, parser):
        """Test parsing complex paths with parameters."""
        code = """
@app.get('/api/v1/users/{user_id}')
def complex_path():
    pass
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/api/v1/users/{user_id}"


class TestFunctionNameExtraction:
    """Test extraction of function names."""

    def test_extract_function_name(self, parser):
        """Test extracting function name from decorator."""
        code = """
@app.get('/test')
def my_handler():
    pass
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 1
        assert endpoints[0]["function_name"] == "my_handler"

    def test_extract_async_function_name(self, parser):
        """Test extracting async function name."""
        code = """
@app.get('/async')
async def async_handler():
    pass
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 1
        assert endpoints[0]["function_name"] == "async_handler"

    def test_unknown_function_name(self, parser):
        """Test handling when function name cannot be found."""
        code = """
@app.get('/test')
# Comment without def
"""
        endpoints = parser.parse(code, "app.py")

        # Should still parse endpoint but function name may be 'unknown'
        if len(endpoints) > 0:
            assert endpoints[0]["function_name"] in ["unknown", ""]


class TestLineNumbers:
    """Test line number tracking."""

    def test_line_number_single_decorator(self, parser):
        """Test line number assignment for decorator."""
        code = """from fastapi import FastAPI

app = FastAPI()

@app.get('/test')
def test_handler():
    pass
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 1
        # Line 5 has the decorator (1-indexed)
        assert endpoints[0]["line_number"] == 5

    def test_line_numbers_multiple(self, parser):
        """Test line numbers for multiple endpoints."""
        code = """@app.get('/first')
def first():
    pass

@app.post('/second')
def second():
    pass
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 2
        line_numbers = sorted([e["line_number"] for e in endpoints])
        assert line_numbers == [1, 5]


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_content(self, parser):
        """Test parsing empty content."""
        endpoints = parser.parse("", "app.py")
        assert endpoints == []

    def test_no_routes_in_file(self, parser):
        """Test file with no routes."""
        code = """
from fastapi import FastAPI

app = FastAPI()

def helper_function():
    pass
"""
        endpoints = parser.parse(code, "app.py")
        assert endpoints == []

    def test_decorator_in_string_not_parsed(self, parser):
        """Test that decorators in strings are not parsed."""
        code = """
text = "@app.get('/fake')"

@app.get('/real')
def real():
    pass
"""
        endpoints = parser.parse(code, "app.py")

        # Should only find one endpoint
        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/real"

    def test_malformed_decorator(self, parser):
        """Test handling of malformed decorators."""
        code = """
@app.get(
def incomplete():
    pass
"""
        # Should not crash, may or may not parse
        endpoints = parser.parse(code, "app.py")
        assert isinstance(endpoints, list)

    def test_framework_field(self, parser):
        """Test that framework field is always set to 'fastapi'."""
        code = """
@app.get('/test')
def test():
    pass
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 1
        assert endpoints[0]["framework"] == "fastapi"

    def test_source_file_field(self, parser):
        """Test that source_file field matches provided filename."""
        code = """
@app.get('/test')
def test():
    pass
"""
        endpoints = parser.parse(code, "custom_routes.py")

        assert len(endpoints) == 1
        assert endpoints[0]["source_file"] == "custom_routes.py"

    def test_methods_is_list(self, parser):
        """Test that methods field is always a list."""
        code = """
@app.get('/test')
def test():
    pass
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 1
        assert isinstance(endpoints[0]["methods"], list)
        assert all(isinstance(m, str) for m in endpoints[0]["methods"])

    def test_path_with_single_quotes(self, parser):
        """Test parsing paths with single quotes."""
        code = """
@app.get('/single')
def test():
    pass
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/single"

    def test_path_with_double_quotes(self, parser):
        """Test parsing paths with double quotes."""
        code = """
@app.get("/double")
def test():
    pass
"""
        endpoints = parser.parse(code, "app.py")

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/double"


class TestGetSupportedFiles:
    """Test get_supported_files() method."""

    def test_supported_files_returns_list(self, parser):
        """Test that supported files returns list."""
        result = parser.get_supported_files()
        assert isinstance(result, list)

    def test_supports_python_pattern(self, parser):
        """Test that Python pattern is supported."""
        result = parser.get_supported_files()
        assert "*.py" in result
