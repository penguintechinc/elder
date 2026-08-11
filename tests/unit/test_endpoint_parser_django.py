"""Unit tests for Django endpoint parser.

Tests parsing of Django URL patterns including path(), re_path(), url(),
and DRF router registrations with various configurations and edge cases.
"""

import pytest

from apps.scanner.parsers.endpoint_parser_django import DjangoEndpointParser


class TestDjangoEndpointParser:
    """Test suite for DjangoEndpointParser."""

    @pytest.fixture
    def parser(self) -> DjangoEndpointParser:
        """Create a DjangoEndpointParser instance for testing."""
        return DjangoEndpointParser()

    # ==================== can_parse() tests ====================

    def test_can_parse_python_file(self, parser: DjangoEndpointParser) -> None:
        """Test that parser recognizes Python files."""
        assert parser.can_parse("urls.py") is True

    def test_can_parse_any_py_file(self, parser: DjangoEndpointParser) -> None:
        """Test that parser recognizes all .py files."""
        assert parser.can_parse("views.py") is True
        assert parser.can_parse("api.py") is True
        assert parser.can_parse("admin.py") is True

    def test_cannot_parse_non_python_files(self, parser: DjangoEndpointParser) -> None:
        """Test that parser rejects non-Python files."""
        assert parser.can_parse("urls.txt") is False
        assert parser.can_parse("requirements.txt") is False
        assert parser.can_parse("settings.yaml") is False
        assert parser.can_parse("models.js") is False

    # ==================== parse() with path() patterns ====================

    def test_parse_simple_path_pattern(self, parser: DjangoEndpointParser) -> None:
        """Test parsing simple path() pattern."""
        content = "path('api/users/', views.user_list)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        endpoint = endpoints[0]
        assert endpoint["path"] == "/api/users/"
        assert endpoint["view_name"] == "views.user_list"
        assert endpoint["framework"] == "django"
        assert endpoint["source_file"] == "urls.py"
        assert endpoint["url_name"] is None

    def test_parse_path_with_name(self, parser: DjangoEndpointParser) -> None:
        """Test parsing path() pattern with named URL."""
        content = "path('api/posts/', views.post_list, name='post_list')"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        endpoint = endpoints[0]
        assert endpoint["path"] == "/api/posts/"
        assert endpoint["url_name"] == "post_list"

    def test_parse_path_with_int_converter(self, parser: DjangoEndpointParser) -> None:
        """Test parsing path() with integer converter."""
        content = "path('users/<int:pk>/', views.user_detail)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        endpoint = endpoints[0]
        assert endpoint["path"] == "/users/{pk}/"

    def test_parse_path_with_slug_converter(self, parser: DjangoEndpointParser) -> None:
        """Test parsing path() with slug converter."""
        content = "path('posts/<slug:post_slug>/', views.post_detail)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        endpoint = endpoints[0]
        assert endpoint["path"] == "/posts/{post_slug}/"

    def test_parse_path_with_uuid_converter(self, parser: DjangoEndpointParser) -> None:
        """Test parsing path() with UUID converter."""
        content = "path('items/<uuid:item_id>/', views.item_detail)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        endpoint = endpoints[0]
        assert endpoint["path"] == "/items/{item_id}/"

    def test_parse_path_method_inference_list(
        self, parser: DjangoEndpointParser
    ) -> None:
        """Test HTTP method inference from list view name."""
        content = "path('api/users/', views.user_list)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        assert endpoints[0]["methods"] == ["GET"]

    def test_parse_path_method_inference_create(
        self, parser: DjangoEndpointParser
    ) -> None:
        """Test HTTP method inference from create view name."""
        content = "path('api/users/', views.create_user)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        assert endpoints[0]["methods"] == ["POST"]

    def test_parse_path_method_inference_detail(
        self, parser: DjangoEndpointParser
    ) -> None:
        """Test HTTP method inference from detail view name."""
        content = "path('api/users/<int:pk>/', views.user_detail)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        assert endpoints[0]["methods"] == ["GET"]

    def test_parse_path_method_inference_update(
        self, parser: DjangoEndpointParser
    ) -> None:
        """Test HTTP method inference from update view name."""
        content = "path('api/users/<int:pk>/', views.update_user)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        assert endpoints[0]["methods"] == ["PUT", "PATCH"]

    def test_parse_path_method_inference_delete(
        self, parser: DjangoEndpointParser
    ) -> None:
        """Test HTTP method inference from delete view name."""
        content = "path('api/users/<int:pk>/', views.delete_user)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        assert endpoints[0]["methods"] == ["DELETE"]

    # ==================== parse() with re_path() patterns ====================

    def test_parse_simple_re_path(self, parser: DjangoEndpointParser) -> None:
        """Test parsing simple re_path() pattern."""
        content = "re_path(r'^api/users/$', views.user_list)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        endpoint = endpoints[0]
        assert endpoint["path"] == "/api/users/"
        assert endpoint["view_name"] == "views.user_list"
        assert endpoint["framework"] == "django"

    def test_parse_re_path_with_named_group(self, parser: DjangoEndpointParser) -> None:
        """Test parsing re_path() with named capture group."""
        content = "re_path(r'^users/(?P<pk>\\d+)/$', views.user_detail)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        endpoint = endpoints[0]
        assert "{pk}" in endpoint["path"]

    def test_parse_re_path_with_name(self, parser: DjangoEndpointParser) -> None:
        """Test parsing re_path() with named URL."""
        content = "re_path(r'^api/posts/$', views.post_list, name='post_list')"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        assert endpoints[0]["url_name"] == "post_list"

    def test_parse_re_path_removes_anchors(self, parser: DjangoEndpointParser) -> None:
        """Test that re_path parser removes regex anchors."""
        content = "re_path(r'^api/items/$', views.item_list)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        endpoint = endpoints[0]
        assert not endpoint["path"].startswith("^")
        assert not endpoint["path"].endswith("$")

    # ==================== parse() with legacy url() patterns ====================

    def test_parse_legacy_url_pattern(self, parser: DjangoEndpointParser) -> None:
        """Test parsing legacy url() pattern."""
        content = "url(r'^api/users/$', views.user_list)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        endpoint = endpoints[0]
        assert endpoint["path"] == "/api/users/"
        assert endpoint["view_name"] == "views.user_list"
        assert endpoint["framework"] == "django"

    def test_parse_legacy_url_with_parameter(
        self, parser: DjangoEndpointParser
    ) -> None:
        """Test parsing legacy url() with regex parameter."""
        content = "url(r'^users/(?P<user_id>\\d+)/$', views.user_detail)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        endpoint = endpoints[0]
        assert "{user_id}" in endpoint["path"]

    def test_parse_legacy_url_with_name(self, parser: DjangoEndpointParser) -> None:
        """Test parsing legacy url() with named URL."""
        content = "url(r'^api/data/$', views.get_data, name='data')"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        assert endpoints[0]["url_name"] == "data"

    # ==================== parse() with DRF router patterns ====================

    def test_parse_drf_router_registration(self, parser: DjangoEndpointParser) -> None:
        """Test parsing DRF router.register() call."""
        content = "router.register('users', UserViewSet)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        endpoint = endpoints[0]
        assert endpoint["path"] == "/users/"
        assert endpoint["view_name"] == "UserViewSet"
        assert endpoint["framework"] == "django"

    def test_parse_drf_router_all_crud_methods(
        self, parser: DjangoEndpointParser
    ) -> None:
        """Test that DRF ViewSets get all CRUD methods."""
        content = "router.register('posts', PostViewSet)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        endpoint = endpoints[0]
        expected_methods = ["GET", "POST", "PUT", "PATCH", "DELETE"]
        assert sorted(endpoint["methods"]) == sorted(expected_methods)

    def test_parse_drf_router_with_prefix(self, parser: DjangoEndpointParser) -> None:
        """Test parsing DRF router with custom prefix."""
        content = "router.register('api/v1/comments', CommentViewSet)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        endpoint = endpoints[0]
        assert endpoint["path"] == "/api/v1/comments/"

    def test_parse_drf_router_with_raw_string(
        self, parser: DjangoEndpointParser
    ) -> None:
        """Test parsing DRF router with raw string prefix."""
        content = "router.register(r'items', ItemViewSet)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        endpoint = endpoints[0]
        assert endpoint["path"] == "/items/"

    # ==================== Line number tracking ====================

    def test_parse_line_number_tracking(self, parser: DjangoEndpointParser) -> None:
        """Test that line numbers are correctly tracked."""
        content = """# This is line 1
# This is line 2
path('api/users/', views.user_list)
# This is line 4
path('api/posts/', views.post_list)
"""
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 2
        assert endpoints[0]["line_number"] == 3
        assert endpoints[1]["line_number"] == 5

    # ==================== Multiple patterns in one file ====================

    def test_parse_multiple_patterns_mixed(self, parser: DjangoEndpointParser) -> None:
        """Test parsing multiple URL patterns of different types."""
        content = """
path('api/users/', views.user_list)
re_path(r'^api/posts/$', views.post_list)
url(r'^api/comments/$', views.comment_list)
router.register('items', ItemViewSet)
"""
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 4

        # Verify all endpoints are present
        paths = [ep["path"] for ep in endpoints]
        assert "/api/users/" in paths
        assert "/api/posts/" in paths
        assert "/api/comments/" in paths
        assert "/items/" in paths

    def test_parse_empty_content(self, parser: DjangoEndpointParser) -> None:
        """Test parsing empty content returns empty list."""
        content = ""
        endpoints = parser.parse(content, "urls.py")

        assert endpoints == []

    def test_parse_no_url_patterns(self, parser: DjangoEndpointParser) -> None:
        """Test parsing content with no URL patterns returns empty list."""
        content = """
# This is just comments
import views
some_variable = 'test'
"""
        endpoints = parser.parse(content, "urls.py")

        assert endpoints == []

    # ==================== Path parameter normalization ====================

    def test_normalize_path_leading_slash(self, parser: DjangoEndpointParser) -> None:
        """Test that path normalization adds leading slash."""
        content = "path('users/', views.user_list)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        assert endpoints[0]["path"].startswith("/")

    def test_normalize_path_parameter_conversion(
        self, parser: DjangoEndpointParser
    ) -> None:
        """Test that path converter parameters are normalized."""
        test_cases = [
            ("path('users/<int:id>/', views.detail)", "/users/{id}/"),
            ("path('posts/<str:slug>/', views.detail)", "/posts/{slug}/"),
            ("path('items/<uuid:uuid>/', views.detail)", "/items/{uuid}/"),
            (
                "path('articles/<slug:article_slug>/', views.detail)",
                "/articles/{article_slug}/",
            ),
        ]

        for content, expected_path in test_cases:
            endpoints = parser.parse(content, "urls.py")
            assert len(endpoints) == 1
            assert endpoints[0]["path"] == expected_path

    def test_normalize_regex_path_removes_anchors(
        self, parser: DjangoEndpointParser
    ) -> None:
        """Test that regex anchors are removed from paths."""
        content = "re_path(r'^api/v1/data/$', views.data)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        path = endpoints[0]["path"]
        assert not path.startswith("^")
        assert not path.endswith("$")

    def test_normalize_regex_named_groups(self, parser: DjangoEndpointParser) -> None:
        """Test that regex named groups are converted to parameters."""
        content = "re_path(r'^users/(?P<user_id>\\d+)/$', views.detail)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        endpoint = endpoints[0]
        assert "{user_id}" in endpoint["path"]

    def test_normalize_regex_digit_pattern(self, parser: DjangoEndpointParser) -> None:
        """Test that \\d+ regex patterns are normalized to {id}."""
        content = "re_path(r'^items/(\\d+)/$', views.detail)"
        endpoints = parser.parse(content, "urls.py")

        assert len(endpoints) == 1
        endpoint = endpoints[0]
        # Should contain some parameter placeholder
        assert "{" in endpoint["path"]

    # ==================== Framework field verification ====================

    def test_framework_field_all_patterns(self, parser: DjangoEndpointParser) -> None:
        """Test that framework field is set to 'django' for all patterns."""
        content = """
path('api/users/', views.user_list)
re_path(r'^api/posts/$', views.post_list)
url(r'^api/comments/$', views.comment_list)
router.register('items', ItemViewSet)
"""
        endpoints = parser.parse(content, "urls.py")

        assert all(ep["framework"] == "django" for ep in endpoints)

    # ==================== Source file field verification ====================

    def test_source_file_field_preserved(self, parser: DjangoEndpointParser) -> None:
        """Test that source_file field correctly identifies the source."""
        content = "path('api/users/', views.user_list)"

        endpoints1 = parser.parse(content, "urls.py")
        assert endpoints1[0]["source_file"] == "urls.py"

        endpoints2 = parser.parse(content, "api/urls.py")
        assert endpoints2[0]["source_file"] == "api/urls.py"

        endpoints3 = parser.parse(content, "v1_urls.py")
        assert endpoints3[0]["source_file"] == "v1_urls.py"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
