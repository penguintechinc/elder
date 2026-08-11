"""Unit tests for the Express.js endpoint parser.

Tests cover:
- File type detection (can_parse method)
- Express route pattern parsing (app.get, router.post, etc.)
- Path parameter normalization (:id to {id})
- Middleware detection
- Handler name extraction
- Chained route parsing
"""

import pytest

from apps.scanner.parsers.endpoint_parser_express import ExpressEndpointParser


class TestExpressEndpointParserCanParse:
    """Test the can_parse method."""

    def test_can_parse_javascript_file(self) -> None:
        """Test that parser recognizes .js files."""
        parser = ExpressEndpointParser()
        assert parser.can_parse("server.js") is True
        assert parser.can_parse("api.js") is True

    def test_can_parse_typescript_file(self) -> None:
        """Test that parser recognizes .ts files."""
        parser = ExpressEndpointParser()
        assert parser.can_parse("server.ts") is True
        assert parser.can_parse("routes.ts") is True

    def test_can_parse_mjs_file(self) -> None:
        """Test that parser recognizes .mjs files."""
        parser = ExpressEndpointParser()
        assert parser.can_parse("app.mjs") is True

    def test_can_parse_jsx_file(self) -> None:
        """Test that parser recognizes .jsx files."""
        parser = ExpressEndpointParser()
        assert parser.can_parse("component.jsx") is True

    def test_can_parse_tsx_file(self) -> None:
        """Test that parser recognizes .tsx files."""
        parser = ExpressEndpointParser()
        assert parser.can_parse("component.tsx") is True

    def test_cannot_parse_other_files(self) -> None:
        """Test that parser rejects non-JS/TS files."""
        parser = ExpressEndpointParser()
        assert parser.can_parse("package.json") is False
        assert parser.can_parse("requirements.txt") is False
        assert parser.can_parse("Dockerfile") is False
        assert parser.can_parse("server.py") is False


class TestExpressEndpointParserMethodRoutes:
    """Test parsing of method-specific routes."""

    def test_parse_simple_get_route(self) -> None:
        """Test parsing a simple GET route."""
        parser = ExpressEndpointParser()
        content = "app.get('/users', getUsersHandler);"

        endpoints = parser.parse(content, "api.js")

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/users"
        assert endpoints[0]["methods"] == ["GET"]
        assert endpoints[0]["framework"] == "express"
        assert endpoints[0]["source_file"] == "api.js"

    def test_parse_post_route(self) -> None:
        """Test parsing a POST route."""
        parser = ExpressEndpointParser()
        content = "app.post('/users', createUser);"

        endpoints = parser.parse(content, "api.js")

        assert len(endpoints) == 1
        assert endpoints[0]["methods"] == ["POST"]

    def test_parse_put_route(self) -> None:
        """Test parsing a PUT route."""
        parser = ExpressEndpointParser()
        content = "app.put('/users/:id', updateUser);"

        endpoints = parser.parse(content, "api.js")

        assert len(endpoints) == 1
        assert endpoints[0]["methods"] == ["PUT"]

    def test_parse_delete_route(self) -> None:
        """Test parsing a DELETE route."""
        parser = ExpressEndpointParser()
        content = "app.delete('/users/:id', deleteUser);"

        endpoints = parser.parse(content, "api.js")

        assert len(endpoints) == 1
        assert endpoints[0]["methods"] == ["DELETE"]

    def test_parse_patch_route(self) -> None:
        """Test parsing a PATCH route."""
        parser = ExpressEndpointParser()
        content = "app.patch('/users/:id', patchUser);"

        endpoints = parser.parse(content, "api.js")

        assert len(endpoints) == 1
        assert endpoints[0]["methods"] == ["PATCH"]

    def test_parse_options_route(self) -> None:
        """Test parsing an OPTIONS route."""
        parser = ExpressEndpointParser()
        content = "app.options('/users', handleOptions);"

        endpoints = parser.parse(content, "api.js")

        assert len(endpoints) == 1
        assert endpoints[0]["methods"] == ["OPTIONS"]

    def test_parse_head_route(self) -> None:
        """Test parsing a HEAD route."""
        parser = ExpressEndpointParser()
        content = "app.head('/users', handleHead);"

        endpoints = parser.parse(content, "api.js")

        assert len(endpoints) == 1
        assert endpoints[0]["methods"] == ["HEAD"]

    def test_parse_route_with_single_quotes(self) -> None:
        """Test parsing route with single quotes."""
        parser = ExpressEndpointParser()
        content = "app.get('/users', handler);"

        endpoints = parser.parse(content, "api.js")

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/users"

    def test_parse_route_with_double_quotes(self) -> None:
        """Test parsing route with double quotes."""
        parser = ExpressEndpointParser()
        content = 'app.get("/users", handler);'

        endpoints = parser.parse(content, "api.js")

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/users"

    def test_parse_multiple_routes_in_file(self) -> None:
        """Test parsing multiple routes from same file."""
        parser = ExpressEndpointParser()
        content = """app.get('/users', getUsers);
app.post('/users', createUser);
app.put('/users/:id', updateUser);
"""

        endpoints = parser.parse(content, "api.js")

        assert len(endpoints) == 3


class TestExpressEndpointParserPathParameters:
    """Test path parameter normalization."""

    def test_normalize_single_param(self) -> None:
        """Test normalizing single path parameter."""
        parser = ExpressEndpointParser()
        content = "app.get('/users/:id', handler);"

        endpoints = parser.parse(content, "api.js")

        assert endpoints[0]["path"] == "/users/{id}"

    def test_normalize_multiple_params(self) -> None:
        """Test normalizing multiple path parameters."""
        parser = ExpressEndpointParser()
        content = "app.get('/users/:userId/posts/:postId', handler);"

        endpoints = parser.parse(content, "api.js")

        assert endpoints[0]["path"] == "/users/{userId}/posts/{postId}"

    def test_normalize_param_with_numbers(self) -> None:
        """Test normalizing parameter names with numbers."""
        parser = ExpressEndpointParser()
        content = "app.get('/api/:v1/users/:id2', handler);"

        endpoints = parser.parse(content, "api.js")

        assert endpoints[0]["path"] == "/api/{v1}/users/{id2}"

    def test_normalize_param_with_underscore(self) -> None:
        """Test normalizing parameter names with underscores."""
        parser = ExpressEndpointParser()
        content = "app.get('/users/:user_id', handler);"

        endpoints = parser.parse(content, "api.js")

        assert endpoints[0]["path"] == "/users/{user_id}"

    def test_path_without_params_unchanged(self) -> None:
        """Test that paths without parameters remain unchanged."""
        parser = ExpressEndpointParser()
        content = "app.get('/api/users', handler);"

        endpoints = parser.parse(content, "api.js")

        assert endpoints[0]["path"] == "/api/users"


class TestExpressEndpointParserRouterRoutes:
    """Test parsing routes with router instances."""

    def test_parse_router_get_route(self) -> None:
        """Test parsing router.get() route."""
        parser = ExpressEndpointParser()
        content = "router.get('/items', getItems);"

        endpoints = parser.parse(content, "routes.js")

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/items"
        assert endpoints[0]["methods"] == ["GET"]

    def test_parse_router_post_route(self) -> None:
        """Test parsing router.post() route."""
        parser = ExpressEndpointParser()
        content = "router.post('/items/:id', createItem);"

        endpoints = parser.parse(content, "routes.js")

        assert len(endpoints) == 1
        assert endpoints[0]["methods"] == ["POST"]

    def test_parse_multiple_router_routes(self) -> None:
        """Test parsing multiple router routes."""
        parser = ExpressEndpointParser()
        content = """router.get('/items', getItems);
router.post('/items', createItem);
router.delete('/items/:id', deleteItem);
"""

        endpoints = parser.parse(content, "routes.js")

        assert len(endpoints) == 3


class TestExpressEndpointParserUseRoutes:
    """Test parsing app.use() middleware routes."""

    def test_parse_use_route(self) -> None:
        """Test parsing app.use() middleware."""
        parser = ExpressEndpointParser()
        content = "app.use('/api', apiRouter);"

        endpoints = parser.parse(content, "server.js")

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/api"
        assert endpoints[0]["methods"] == ["ALL"]
        assert endpoints[0]["handler_name"] == "middleware"
        assert endpoints[0]["has_middleware"] is True

    def test_parse_router_use_route(self) -> None:
        """Test parsing router.use() middleware."""
        parser = ExpressEndpointParser()
        content = "router.use('/admin', adminRouter);"

        endpoints = parser.parse(content, "routes.js")

        assert len(endpoints) == 1
        assert endpoints[0]["methods"] == ["ALL"]

    def test_parse_use_with_path_params(self) -> None:
        """Test parsing app.use() with path parameters."""
        parser = ExpressEndpointParser()
        content = "app.use('/api/:version', versionedRouter);"

        endpoints = parser.parse(content, "server.js")

        assert endpoints[0]["path"] == "/api/{version}"
        assert endpoints[0]["methods"] == ["ALL"]


class TestExpressEndpointParserChainedRoutes:
    """Test parsing chained route definitions."""

    def test_parse_chained_get_post(self) -> None:
        """Test parsing chained .get().post() routes."""
        parser = ExpressEndpointParser()
        content = "app.route('/users').get(getUsers).post(createUser);"

        endpoints = parser.parse(content, "api.js")

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/users"
        assert set(endpoints[0]["methods"]) == {"GET", "POST"}
        assert endpoints[0]["handler_name"] == "chained_handlers"

    def test_parse_chained_multiple_methods(self) -> None:
        """Test parsing chained route with multiple methods."""
        parser = ExpressEndpointParser()
        content = (
            "app.route('/items/:id').get(getItem).put(updateItem).delete(deleteItem);"
        )

        endpoints = parser.parse(content, "api.js")

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/items/{id}"
        assert set(endpoints[0]["methods"]) == {"GET", "PUT", "DELETE"}

    def test_parse_router_chained_route(self) -> None:
        """Test parsing router.route() chained methods."""
        parser = ExpressEndpointParser()
        content = "router.route('/posts').get(getPosts).post(createPost);"

        endpoints = parser.parse(content, "routes.js")

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/posts"
        assert set(endpoints[0]["methods"]) == {"GET", "POST"}

    def test_parse_chained_with_whitespace(self) -> None:
        """Test parsing chained route with extra whitespace."""
        parser = ExpressEndpointParser()
        content = """app.route('/users')
            .get(getUsers)
            .post(createUser)
            .put(updateUsers);"""

        endpoints = parser.parse(content, "api.js")

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/users"
        assert set(endpoints[0]["methods"]) == {"GET", "POST", "PUT"}


class TestExpressEndpointParserMiddlewareDetection:
    """Test middleware detection in routes."""

    def test_detect_middleware_in_route(self) -> None:
        """Test detection of middleware functions."""
        parser = ExpressEndpointParser()
        content = "app.get('/users', authenticateUser, getUsersHandler);"

        endpoints = parser.parse(content, "api.js")

        assert endpoints[0]["has_middleware"] is True

    def test_no_middleware_detected(self) -> None:
        """Test when no middleware is present."""
        parser = ExpressEndpointParser()
        content = "app.get('/users', getUsers);"

        endpoints = parser.parse(content, "api.js")

        assert endpoints[0]["has_middleware"] is False

    def test_detect_multiple_middleware(self) -> None:
        """Test detection of multiple middleware functions."""
        parser = ExpressEndpointParser()
        content = "app.post('/data', validate, authenticate, authorize, handler);"

        endpoints = parser.parse(content, "api.js")

        assert endpoints[0]["has_middleware"] is True


class TestExpressEndpointParserHandlerNames:
    """Test handler name extraction."""

    def test_extract_simple_handler_name(self) -> None:
        """Test extracting simple handler function name."""
        parser = ExpressEndpointParser()
        content = "app.get('/users', getUsers);"

        endpoints = parser.parse(content, "api.js")

        assert endpoints[0]["handler_name"] == "getUsers"

    def test_extract_handler_with_middleware(self) -> None:
        """Test extracting handler name when middleware present."""
        parser = ExpressEndpointParser()
        content = "app.get('/users', auth, getUsers);"

        endpoints = parser.parse(content, "api.js")

        assert endpoints[0]["handler_name"] == "getUsers"

    def test_extract_arrow_function_handler(self) -> None:
        """Test extracting arrow function handler name."""
        parser = ExpressEndpointParser()
        content = "app.get('/users', (req, res) => res.json([]));"

        endpoints = parser.parse(content, "api.js")

        # Arrow functions without names should have None
        assert endpoints[0]["handler_name"] is None

    def test_extract_inline_function_handler(self) -> None:
        """Test extracting inline function handler."""
        parser = ExpressEndpointParser()
        content = "app.get('/users', function(req, res) { res.json([]); });"

        endpoints = parser.parse(content, "api.js")

        assert endpoints[0]["handler_name"] is None


class TestExpressEndpointParserComments:
    """Test comment handling."""

    def test_skip_comment_lines(self) -> None:
        """Test that comment lines are skipped."""
        parser = ExpressEndpointParser()
        content = """// app.get('/ignored', handler);
app.get('/users', getUsers);
"""

        endpoints = parser.parse(content, "api.js")

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/users"

    def test_skip_multi_line_comment_markers(self) -> None:
        """Test that multi-line comment markers are skipped."""
        parser = ExpressEndpointParser()
        content = """* This is a comment
app.get('/users', getUsers);
"""

        endpoints = parser.parse(content, "api.js")

        assert len(endpoints) == 1


class TestExpressEndpointParserLineNumbers:
    """Test line number tracking."""

    def test_track_line_numbers(self) -> None:
        """Test that line numbers are correctly recorded."""
        parser = ExpressEndpointParser()
        content = """app.get('/first', handler1);
app.post('/second', handler2);
app.put('/third', handler3);
"""

        endpoints = parser.parse(content, "api.js")

        assert endpoints[0]["line_number"] == 1
        assert endpoints[1]["line_number"] == 2
        assert endpoints[2]["line_number"] == 3

    def test_line_number_with_blank_lines(self) -> None:
        """Test line numbers with blank lines in content."""
        parser = ExpressEndpointParser()
        content = """app.get('/first', handler);

app.post('/second', handler);
"""

        endpoints = parser.parse(content, "api.js")

        assert endpoints[0]["line_number"] == 1
        assert endpoints[1]["line_number"] == 3


class TestExpressEndpointParserIntegration:
    """Integration tests combining multiple features."""

    def test_parse_realistic_express_file(self) -> None:
        """Test parsing a realistic Express.js file."""
        parser = ExpressEndpointParser()
        content = """const express = require('express');
const app = express();

// Middleware
app.use(express.json());
app.use('/api', apiRouter);

// Routes
app.get('/health', healthCheck);
app.get('/users', authenticateUser, getUsers);
app.post('/users', validateInput, createUser);
app.get('/users/:id', getUserById);

app.route('/posts/:postId')
    .get(getPost)
    .put(updatePost)
    .delete(deletePost);

app.listen(3000);
"""

        endpoints = parser.parse(content, "server.js")

        # Should find all routes including middleware
        assert len(endpoints) > 0

        # Check that paths are normalized
        user_route = next(e for e in endpoints if "{id}" in e.get("path", ""))
        assert user_route["path"] == "/users/{id}"

    def test_parse_typescript_express_file(self) -> None:
        """Test parsing TypeScript Express file."""
        parser = ExpressEndpointParser()
        content = """import express, { Request, Response } from 'express';

const app = express();

app.get('/api/items', (req: Request, res: Response) => {
    res.json([]);
});

app.post('/api/items/:itemId', createItem);
"""

        endpoints = parser.parse(content, "server.ts")

        assert len(endpoints) >= 2
        assert any(e["path"] == "/api/items" for e in endpoints)
        assert any(e["path"] == "/api/items/{itemId}" for e in endpoints)

    def test_endpoint_framework_field(self) -> None:
        """Test that framework field is always set to 'express'."""
        parser = ExpressEndpointParser()
        content = """app.get('/users', getUsers);
app.post('/items', createItem);
app.use('/admin', adminRouter);
app.route('/posts').get(getPosts).post(createPost);
"""

        endpoints = parser.parse(content, "server.js")

        for endpoint in endpoints:
            assert endpoint["framework"] == "express"

    def test_endpoint_source_file_field(self) -> None:
        """Test that source_file field is correctly set."""
        parser = ExpressEndpointParser()
        content = "app.get('/users', getUsers);"

        endpoints = parser.parse(content, "routes/users.ts")

        assert all(e["source_file"] == "routes/users.ts" for e in endpoints)
