"""Unit tests for the Flask endpoint parser.

Tests cover parsing of Flask route decorators and Flask-RESTful resources
to extract API endpoint information including paths, HTTP methods, and
authentication requirements.
"""

import pytest

from apps.scanner.parsers.endpoint_parser_flask import FlaskEndpointParser


class TestFlaskParserCanParse:
    """Test the can_parse method of FlaskEndpointParser."""

    def test_can_parse_py_files(self) -> None:
        """Test that parser recognizes .py files."""
        parser = FlaskEndpointParser()
        assert parser.can_parse("app.py") is True
        assert parser.can_parse("routes.py") is True
        assert parser.can_parse("api.py") is True

    def test_can_parse_case_insensitive(self) -> None:
        """Test that parser handles case-insensitive filenames."""
        parser = FlaskEndpointParser()
        assert parser.can_parse("APP.PY") is True
        assert parser.can_parse("Routes.Py") is True

    def test_cannot_parse_non_python_files(self) -> None:
        """Test that parser rejects non-Python files."""
        parser = FlaskEndpointParser()
        assert parser.can_parse("app.js") is False
        assert parser.can_parse("routes.go") is False
        assert parser.can_parse("api.json") is False
        assert parser.can_parse("README.md") is False

    def test_cannot_parse_empty_filename(self) -> None:
        """Test that parser rejects empty filenames."""
        parser = FlaskEndpointParser()
        assert parser.can_parse("") is False
        assert parser.can_parse(None) is False


class TestFlaskParserGetSupportedFiles:
    """Test the get_supported_files method of FlaskEndpointParser."""

    def test_supported_files_returns_list(self) -> None:
        """Test that get_supported_files returns a list."""
        parser = FlaskEndpointParser()
        supported = parser.get_supported_files()
        assert isinstance(supported, list)
        assert len(supported) > 0

    def test_supported_files_contains_py_pattern(self) -> None:
        """Test that supported files includes *.py pattern."""
        parser = FlaskEndpointParser()
        supported = parser.get_supported_files()
        assert "*.py" in supported


class TestFlaskParserSimpleRoutes:
    """Test parsing of simple Flask routes."""

    def test_parse_simple_app_route(self) -> None:
        """Test parsing of simple @app.route() decorator."""
        parser = FlaskEndpointParser()
        # Mock validate_content to return True
        parser.validate_content = lambda x: True

        code = """
@app.route('/users')
def get_users():
    return []
"""
        endpoints = parser.parse(code, "app.py")
        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/users"
        assert endpoints[0]["methods"] == ["GET"]
        assert endpoints[0]["function_name"] == "get_users"
        assert endpoints[0]["framework"] == "flask"
        assert endpoints[0]["source_file"] == "app.py"

    def test_parse_app_route_with_methods(self) -> None:
        """Test parsing @app.route with explicit methods."""
        parser = FlaskEndpointParser()
        parser.validate_content = lambda x: True

        code = """
@app.route('/users', methods=['GET', 'POST'])
def users_handler():
    return {}
"""
        endpoints = parser.parse(code, "app.py")
        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/users"
        assert set(endpoints[0]["methods"]) == {"GET", "POST"}
        assert endpoints[0]["function_name"] == "users_handler"

    def test_parse_blueprint_route(self) -> None:
        """Test parsing @bp.route() decorator."""
        parser = FlaskEndpointParser()
        parser.validate_content = lambda x: True

        code = """
@bp.route('/posts')
def list_posts():
    return []
"""
        endpoints = parser.parse(code, "routes.py")
        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/posts"
        assert endpoints[0]["methods"] == ["GET"]


class TestFlaskParserMethodShortcuts:
    """Test parsing of Flask 2.0+ method shortcuts."""

    def test_parse_app_get_shortcut(self) -> None:
        """Test parsing @app.get() shortcut."""
        parser = FlaskEndpointParser()
        parser.validate_content = lambda x: True

        code = """
@app.get('/items')
def get_items():
    return []
"""
        endpoints = parser.parse(code, "app.py")
        assert len(endpoints) == 1
        assert endpoints[0]["methods"] == ["GET"]

    def test_parse_app_post_shortcut(self) -> None:
        """Test parsing @app.post() shortcut."""
        parser = FlaskEndpointParser()
        parser.validate_content = lambda x: True

        code = """
@app.post('/items')
def create_item():
    return {}
"""
        endpoints = parser.parse(code, "app.py")
        assert len(endpoints) == 1
        assert endpoints[0]["methods"] == ["POST"]

    def test_parse_app_put_shortcut(self) -> None:
        """Test parsing @app.put() shortcut."""
        parser = FlaskEndpointParser()
        parser.validate_content = lambda x: True

        code = """
@app.put('/items/<int:id>')
def update_item(id):
    return {}
"""
        endpoints = parser.parse(code, "app.py")
        assert len(endpoints) == 1
        assert endpoints[0]["methods"] == ["PUT"]
        assert endpoints[0]["path"] == "/items/{id}"

    def test_parse_app_delete_shortcut(self) -> None:
        """Test parsing @app.delete() shortcut."""
        parser = FlaskEndpointParser()
        parser.validate_content = lambda x: True

        code = """
@app.delete('/items/<int:id>')
def delete_item(id):
    return {}
"""
        endpoints = parser.parse(code, "app.py")
        assert len(endpoints) == 1
        assert endpoints[0]["methods"] == ["DELETE"]


class TestFlaskParserPathNormalization:
    """Test the _normalize_path method."""

    def test_normalize_path_int_param(self) -> None:
        """Test normalization of <int:id> parameters."""
        parser = FlaskEndpointParser()
        normalized = parser._normalize_path("/users/<int:id>")
        assert normalized == "/users/{id}"

    def test_normalize_path_string_param(self) -> None:
        """Test normalization of <string:name> parameters."""
        parser = FlaskEndpointParser()
        normalized = parser._normalize_path("/posts/<string:slug>")
        assert normalized == "/posts/{slug}"

    def test_normalize_path_bare_param(self) -> None:
        """Test normalization of bare <id> parameters."""
        parser = FlaskEndpointParser()
        normalized = parser._normalize_path("/items/<id>")
        assert normalized == "/items/{id}"

    def test_normalize_path_multiple_params(self) -> None:
        """Test normalization of multiple parameters."""
        parser = FlaskEndpointParser()
        normalized = parser._normalize_path("/users/<int:user_id>/posts/<int:post_id>")
        assert normalized == "/users/{user_id}/posts/{post_id}"

    def test_normalize_path_uuid_param(self) -> None:
        """Test normalization of UUID parameters."""
        parser = FlaskEndpointParser()
        normalized = parser._normalize_path("/resources/<uuid:resource_id>")
        assert normalized == "/resources/{resource_id}"

    def test_normalize_path_no_params(self) -> None:
        """Test normalization of paths without parameters."""
        parser = FlaskEndpointParser()
        normalized = parser._normalize_path("/users/list")
        assert normalized == "/users/list"


class TestFlaskParserAuthentication:
    """Test authentication decorator detection."""

    def test_auth_required_with_login_required(self) -> None:
        """Test detection of @login_required decorator."""
        parser = FlaskEndpointParser()
        parser.validate_content = lambda x: True

        code = """
@login_required
@app.route('/protected')
def protected_route():
    return {}
"""
        endpoints = parser.parse(code, "app.py")
        assert len(endpoints) == 1
        assert endpoints[0]["auth_required"] is True

    def test_auth_required_with_jwt_required(self) -> None:
        """Test detection of @jwt_required decorator."""
        parser = FlaskEndpointParser()
        parser.validate_content = lambda x: True

        code = """
@jwt_required()
@app.route('/api/data')
def get_data():
    return {}
"""
        endpoints = parser.parse(code, "app.py")
        assert len(endpoints) == 1
        assert endpoints[0]["auth_required"] is True

    def test_no_auth_required(self) -> None:
        """Test that routes without auth are marked as public."""
        parser = FlaskEndpointParser()
        parser.validate_content = lambda x: True

        code = """
@app.route('/public')
def public_route():
    return {}
"""
        endpoints = parser.parse(code, "app.py")
        assert len(endpoints) == 1
        assert endpoints[0]["auth_required"] is False


class TestFlaskParserRestfulResources:
    """Test parsing of Flask-RESTful resources."""

    def test_parse_add_resource(self) -> None:
        """Test parsing api.add_resource() calls."""
        parser = FlaskEndpointParser()
        parser.validate_content = lambda x: True

        code = """
api.add_resource(UserResource, '/users')
"""
        endpoints = parser.parse(code, "api.py")
        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/users"
        assert endpoints[0]["function_name"] == "UserResource"
        assert set(endpoints[0]["methods"]) == {"GET", "POST", "PUT", "PATCH", "DELETE"}

    def test_parse_add_resource_with_path_params(self) -> None:
        """Test parsing api.add_resource() with path parameters."""
        parser = FlaskEndpointParser()
        parser.validate_content = lambda x: True

        code = """
api.add_resource(UserResource, '/users/<int:id>')
"""
        endpoints = parser.parse(code, "api.py")
        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/users/{id}"


class TestFlaskParserMultipleEndpoints:
    """Test parsing multiple endpoints in one file."""

    def test_parse_multiple_routes(self) -> None:
        """Test parsing multiple routes in a single file."""
        parser = FlaskEndpointParser()
        parser.validate_content = lambda x: True

        code = """
@app.route('/users')
def get_users():
    return []

@app.route('/posts', methods=['GET', 'POST'])
def posts_handler():
    return {}

@app.delete('/items/<int:id>')
def delete_item(id):
    return {}
"""
        endpoints = parser.parse(code, "app.py")
        assert len(endpoints) == 3
        assert endpoints[0]["path"] == "/users"
        assert endpoints[1]["path"] == "/posts"
        assert endpoints[2]["path"] == "/items/{id}"


class TestFlaskParserEdgeCases:
    """Test edge cases and error handling."""

    def test_parse_empty_content(self) -> None:
        """Test parsing empty content."""
        parser = FlaskEndpointParser()
        parser.validate_content = lambda x: False

        endpoints = parser.parse("", "app.py")
        assert endpoints == []

    def test_parse_code_without_routes(self) -> None:
        """Test parsing Python file without routes."""
        parser = FlaskEndpointParser()
        parser.validate_content = lambda x: True

        code = """
def helper_function():
    return "not a route"
"""
        endpoints = parser.parse(code, "helpers.py")
        assert len(endpoints) == 0

    def test_function_name_not_found(self) -> None:
        """Test handling when function name cannot be found."""
        parser = FlaskEndpointParser()
        parser.validate_content = lambda x: True

        code = """
@app.route('/orphan')
"""
        endpoints = parser.parse(code, "app.py")
        assert len(endpoints) == 1
        assert endpoints[0]["function_name"] == "unknown"

    def test_line_numbers_correct(self) -> None:
        """Test that line numbers are correctly reported."""
        parser = FlaskEndpointParser()
        parser.validate_content = lambda x: True

        code = """
@app.route('/first')
def first():
    return {}

@app.route('/second')
def second():
    return {}
"""
        endpoints = parser.parse(code, "app.py")
        assert len(endpoints) == 2
        assert endpoints[0]["line_number"] == 2
        assert endpoints[1]["line_number"] == 6


class TestFlaskParserMetadata:
    """Test endpoint metadata."""

    def test_endpoint_contains_all_fields(self) -> None:
        """Test that endpoints contain all required fields."""
        parser = FlaskEndpointParser()
        parser.validate_content = lambda x: True

        code = """
@app.route('/test')
def test_route():
    return {}
"""
        endpoints = parser.parse(code, "test.py")
        assert len(endpoints) == 1
        endpoint = endpoints[0]

        required_fields = [
            "path",
            "methods",
            "function_name",
            "line_number",
            "framework",
            "source_file",
            "auth_required",
        ]
        for field in required_fields:
            assert field in endpoint

    def test_framework_always_flask(self) -> None:
        """Test that framework field is always 'flask'."""
        parser = FlaskEndpointParser()
        parser.validate_content = lambda x: True

        code = """
@app.route('/test')
def test():
    return {}
"""
        endpoints = parser.parse(code, "app.py")
        assert all(e["framework"] == "flask" for e in endpoints)

    def test_source_file_matches_input(self) -> None:
        """Test that source_file field matches input filename."""
        parser = FlaskEndpointParser()
        parser.validate_content = lambda x: True

        code = """
@app.route('/test')
def test():
    return {}
"""
        filename = "custom_routes.py"
        endpoints = parser.parse(code, filename)
        assert all(e["source_file"] == filename for e in endpoints)
