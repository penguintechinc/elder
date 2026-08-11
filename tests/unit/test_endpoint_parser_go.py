"""Unit tests for Go HTTP endpoint parser."""

import unittest

from apps.scanner.parsers.endpoint_parser_go import GoEndpointParser


class TestGoEndpointParser(unittest.TestCase):
    """Test cases for GoEndpointParser class."""

    def setUp(self):
        """Set up test fixtures."""
        self.parser = GoEndpointParser()

    def test_can_parse_go_file(self):
        """Test can_parse() returns True for .go files."""
        self.assertTrue(self.parser.can_parse("handler.go"))
        self.assertTrue(self.parser.can_parse("routes.go"))
        self.assertTrue(self.parser.can_parse("main.go"))

    def test_can_parse_non_go_file(self):
        """Test can_parse() returns False for non-.go files."""
        self.assertFalse(self.parser.can_parse("handler.py"))
        self.assertFalse(self.parser.can_parse("routes.ts"))
        self.assertFalse(self.parser.can_parse("main.js"))
        self.assertFalse(self.parser.can_parse("config.yaml"))

    def test_parse_gin_routes(self):
        """Test parsing Gin framework routes."""
        content = """
package main

func main() {
    r := gin.Default()
    r.GET("/api/users", GetUsers)
    r.POST("/api/users", CreateUser)
    r.PUT("/api/users/:id", UpdateUser)
    r.DELETE("/api/users/:id", DeleteUser)
}
"""
        endpoints = self.parser.parse(content, "main.go")
        self.assertEqual(len(endpoints), 4)

        # Check GET endpoint
        get_endpoint = endpoints[0]
        self.assertEqual(get_endpoint["path"], "/api/users")
        self.assertEqual(get_endpoint["methods"], ["GET"])
        self.assertEqual(get_endpoint["handler_name"], "GetUsers")
        self.assertEqual(get_endpoint["framework"], "gin")
        self.assertEqual(get_endpoint["source_file"], "main.go")

        # Check PUT with path parameter
        put_endpoint = endpoints[2]
        self.assertEqual(put_endpoint["path"], "/api/users/{id}")
        self.assertEqual(put_endpoint["methods"], ["PUT"])
        self.assertEqual(put_endpoint["handler_name"], "UpdateUser")

    def test_parse_gin_any_method(self):
        """Test parsing Gin Any() method routes."""
        content = """
r.Any("/api/webhook", WebhookHandler)
"""
        endpoints = self.parser.parse(content, "routes.go")
        self.assertEqual(len(endpoints), 1)

        endpoint = endpoints[0]
        self.assertEqual(endpoint["path"], "/api/webhook")
        self.assertEqual(len(endpoint["methods"]), 7)
        self.assertIn("GET", endpoint["methods"])
        self.assertIn("POST", endpoint["methods"])
        self.assertIn("DELETE", endpoint["methods"])

    def test_parse_chi_routes(self):
        """Test parsing Chi framework routes."""
        content = """
package main

func main() {
    r := chi.NewRouter()
    r.Get("/items", ListItems)
    r.Post("/items", CreateItem)
    r.Put("/items/{itemID}", UpdateItem)
    r.Delete("/items/{itemID}", DeleteItem)
}
"""
        endpoints = self.parser.parse(content, "main.go")
        self.assertEqual(len(endpoints), 4)

        # Check GET endpoint
        get_endpoint = endpoints[0]
        self.assertEqual(get_endpoint["path"], "/items")
        self.assertEqual(get_endpoint["methods"], ["GET"])
        self.assertEqual(get_endpoint["handler_name"], "ListItems")
        self.assertEqual(get_endpoint["framework"], "chi")

        # Check PUT with path parameter
        put_endpoint = endpoints[2]
        self.assertEqual(put_endpoint["path"], "/items/{itemID}")
        self.assertEqual(put_endpoint["methods"], ["PUT"])

    def test_parse_gorilla_routes(self):
        """Test parsing Gorilla Mux routes."""
        content = """
package main

func main() {
    r := mux.NewRouter()
    r.HandleFunc("/api/products", ListProducts).Methods("GET")
    r.HandleFunc("/api/products", CreateProduct).Methods("POST")
    r.HandleFunc("/api/products/{id}", GetProduct).Methods("GET")
    r.HandleFunc("/api/products/{id}", UpdateProduct).Methods("PUT")
}
"""
        endpoints = self.parser.parse(content, "routes.go")
        self.assertEqual(len(endpoints), 4)

        # Check GET endpoint with methods
        get_endpoint = endpoints[0]
        self.assertEqual(get_endpoint["path"], "/api/products")
        self.assertEqual(get_endpoint["methods"], ["GET"])
        self.assertEqual(get_endpoint["framework"], "gorilla")

        # Check PUT endpoint
        put_endpoint = endpoints[3]
        self.assertEqual(put_endpoint["path"], "/api/products/{id}")
        self.assertEqual(put_endpoint["methods"], ["PUT"])

    def test_parse_gorilla_no_methods(self):
        """Test Gorilla route without explicit methods defaults to all methods."""
        content = """
r.HandleFunc("/api/catch-all", CatchAllHandler)
"""
        endpoints = self.parser.parse(content, "routes.go")
        self.assertEqual(len(endpoints), 1)

        endpoint = endpoints[0]
        self.assertEqual(endpoint["path"], "/api/catch-all")
        self.assertEqual(len(endpoint["methods"]), 7)

    def test_parse_nethttp_routes(self):
        """Test parsing net/http standard library routes."""
        content = """
package main

func main() {
    http.HandleFunc("/home", HomeHandler)
    http.HandleFunc("/about", AboutHandler)
    http.HandleFunc("/api/status", StatusHandler)
}
"""
        endpoints = self.parser.parse(content, "main.go")
        self.assertEqual(len(endpoints), 3)

        # Check endpoint
        endpoint = endpoints[0]
        self.assertEqual(endpoint["path"], "/home")
        self.assertEqual(endpoint["handler_name"], "HomeHandler")
        self.assertEqual(endpoint["framework"], "net/http")
        self.assertEqual(len(endpoint["methods"]), 7)

    def test_normalize_path_params_gin_format(self):
        """Test path parameter normalization for Gin format."""
        content = """
r.GET("/api/users/:id/posts/:postID", GetUserPost)
"""
        endpoints = self.parser.parse(content, "main.go")
        self.assertEqual(len(endpoints), 1)

        endpoint = endpoints[0]
        self.assertEqual(endpoint["path"], "/api/users/{id}/posts/{postID}")

    def test_normalize_path_params_chi_format(self):
        """Test path parameter normalization for Chi format."""
        content = """
r.Get("/users/:userID/settings/:setting", GetUserSetting)
"""
        endpoints = self.parser.parse(content, "main.go")
        self.assertEqual(len(endpoints), 1)

        endpoint = endpoints[0]
        self.assertEqual(endpoint["path"], "/users/{userID}/settings/{setting}")

    def test_normalize_path_params_gorilla_format(self):
        """Test Gorilla format paths remain unchanged."""
        content = """
r.HandleFunc("/products/{id}", GetProduct).Methods("GET")
"""
        endpoints = self.parser.parse(content, "main.go")
        self.assertEqual(len(endpoints), 1)

        endpoint = endpoints[0]
        self.assertEqual(endpoint["path"], "/products/{id}")

    def test_line_number_calculation(self):
        """Test line number calculation for endpoints."""
        content = """package main

func setupRoutes() {
    r := gin.Default()
    r.GET("/health", Health)
    r.POST("/api/users", CreateUser)
}
"""
        endpoints = self.parser.parse(content, "routes.go")
        self.assertEqual(len(endpoints), 2)

        # First endpoint on line 5
        self.assertEqual(endpoints[0]["line_number"], 5)
        # Second endpoint on line 6
        self.assertEqual(endpoints[1]["line_number"], 6)

    def test_extract_middleware(self):
        """Test middleware extraction."""
        content = """
func setupRoutes(r *gin.Engine) {
    r.Use(AuthMiddleware)
    r.Use(LoggingMiddleware)
    r.Use(CORSMiddleware)
    r.GET("/api/users", GetUsers)
}
"""
        endpoints = self.parser.parse(content, "main.go")
        self.assertEqual(len(endpoints), 1)

        endpoint = endpoints[0]
        self.assertEqual(len(endpoint["middleware"]), 3)
        self.assertIn("AuthMiddleware", endpoint["middleware"])
        self.assertIn("LoggingMiddleware", endpoint["middleware"])
        self.assertIn("CORSMiddleware", endpoint["middleware"])

    def test_multiple_frameworks_mixed(self):
        """Test parsing when multiple frameworks are mixed."""
        content = """
func setupRoutes() {
    ginRouter := gin.Default()
    ginRouter.GET("/gin-route", GinHandler)

    chiRouter := chi.NewRouter()
    chiRouter.Post("/chi-route", ChiHandler)

    http.HandleFunc("/http-route", HttpHandler)
}
"""
        endpoints = self.parser.parse(content, "mixed.go")
        self.assertEqual(len(endpoints), 3)

        frameworks = [ep["framework"] for ep in endpoints]
        self.assertIn("gin", frameworks)
        self.assertIn("chi", frameworks)
        self.assertIn("net/http", frameworks)

    def test_empty_content(self):
        """Test parsing empty content returns empty list."""
        endpoints = self.parser.parse("", "main.go")
        self.assertEqual(len(endpoints), 0)

    def test_endpoint_with_quoted_paths(self):
        """Test endpoints with single and double quoted paths."""
        content = """
r.GET("/double-quoted", Handler1)
r.GET('/single-quoted', Handler2)
"""
        endpoints = self.parser.parse(content, "main.go")
        self.assertEqual(len(endpoints), 2)

        self.assertEqual(endpoints[0]["path"], "/double-quoted")
        self.assertEqual(endpoints[1]["path"], "/single-quoted")

    def test_source_file_field(self):
        """Test source_file field is correctly set."""
        content = 'r.GET("/test", TestHandler)'
        endpoints = self.parser.parse(content, "handler.go")

        self.assertEqual(len(endpoints), 1)
        self.assertEqual(endpoints[0]["source_file"], "handler.go")

    def test_handler_name_extraction(self):
        """Test handler name extraction."""
        content = """
r.GET("/users", GetAllUsers)
r.GET("/users/:id", GetUserByID)
r.POST("/users", CreateNewUser)
"""
        endpoints = self.parser.parse(content, "users.go")

        self.assertEqual(endpoints[0]["handler_name"], "GetAllUsers")
        self.assertEqual(endpoints[1]["handler_name"], "GetUserByID")
        self.assertEqual(endpoints[2]["handler_name"], "CreateNewUser")


if __name__ == "__main__":
    unittest.main()
