"""
Standardized API response utilities for Elder API.

This module provides consistent response formatting across all API endpoints,
ensuring uniform error handling and success responses.
"""

# flake8: noqa: E501

from typing import Any, Optional, Tuple

from quart import jsonify


class ApiResponse:
    """Standardized API response helpers for consistent response formatting."""

    @staticmethod
    def error(message: str, status_code: int = 400, **kwargs) -> tuple[Any, int]:
        """
        Generate a standard error response.

        Args:
            message: Error message to return
            status_code: HTTP status code (default: 400)
            **kwargs: Additional fields to include in response

        Returns:
            Tuple of (jsonified response, status_code)

        Example:
            return ApiResponse.error("Invalid input", 400)
        """
        response_data = {"error": message}
        response_data.update(kwargs)
        return jsonify(response_data), status_code

    @staticmethod
    def validation_error(field: str, message: str = "is required") -> tuple[Any, int]:
        """
        Generate a validation error response.

        Args:
            field: Name of the field that failed validation
            message: Validation error message (default: "is required")

        Returns:
            Tuple of (jsonified response, 400)

        Example:
            return ApiResponse.validation_error("name", "must be at least 3 characters")
        """
        return jsonify({"error": f"{field} {message}", "field": field}), 400

    @staticmethod
    def not_found(
        resource_type: str = "Resource", resource_id: Any | None = None
    ) -> tuple[Any, int]:
        """
        Generate a not found error response.

        Args:
            resource_type: Type of resource not found (e.g., "Organization", "Entity")
            resource_id: Optional ID of the resource that wasn't found

        Returns:
            Tuple of (jsonified response, 404)

        Example:
            return ApiResponse.not_found("Organization", 123)
        """
        if resource_id is not None:
            message = f"{resource_type} with id {resource_id} not found"
        else:
            message = f"{resource_type} not found"
        return jsonify({"error": message}), 404

    @staticmethod
    def forbidden(message: str = "Access denied") -> tuple[Any, int]:
        """
        Generate a forbidden access response.

        Args:
            message: Forbidden message (default: "Access denied")

        Returns:
            Tuple of (jsonified response, 403)

        Example:
            return ApiResponse.forbidden("You don't have permission to modify this resource")
        """
        return jsonify({"error": message}), 403

    @staticmethod
    def unauthorized(message: str = "Authentication required") -> tuple[Any, int]:
        """
        Generate an unauthorized response.

        Args:
            message: Unauthorized message (default: "Authentication required")

        Returns:
            Tuple of (jsonified response, 401)

        Example:
            return ApiResponse.unauthorized("Invalid token")
        """
        return jsonify({"error": message}), 401

    @staticmethod
    def success(data: Any, status_code: int = 200) -> tuple[Any, int]:
        """
        Generate a success response.

        Args:
            data: Data to return (dict, list, or primitive)
            status_code: HTTP status code (default: 200)

        Returns:
            Tuple of (jsonified response, status_code)

        Example:
            return ApiResponse.success({"id": 1, "name": "Test"}, 201)
        """
        return jsonify(data), status_code

    @staticmethod
    def created(data: Any) -> tuple[Any, int]:
        """
        Generate a resource created response.

        Args:
            data: Created resource data

        Returns:
            Tuple of (jsonified response, 201)

        Example:
            return ApiResponse.created({"id": 1, "name": "New Organization"})
        """
        return jsonify(data), 201

    @staticmethod
    def no_content() -> tuple[str, int]:
        """
        Generate a no content response (typically for successful deletes).

        Returns:
            Tuple of (empty string, 204)

        Example:
            return ApiResponse.no_content()
        """
        return "", 204

    @staticmethod
    def bad_request(message: str = "Bad request") -> tuple[Any, int]:
        """
        Generate a bad request response.

        Args:
            message: Bad request message

        Returns:
            Tuple of (jsonified response, 400)

        Example:
            return ApiResponse.bad_request("Request body must be JSON")
        """
        return jsonify({"error": message}), 400

    @staticmethod
    def conflict(message: str) -> tuple[Any, int]:
        """
        Generate a conflict response (e.g., duplicate entry).

        Args:
            message: Conflict message

        Returns:
            Tuple of (jsonified response, 409)

        Example:
            return ApiResponse.conflict("An organization with this name already exists")
        """
        return jsonify({"error": message}), 409

    @staticmethod
    def internal_error(message: str = "Internal server error") -> tuple[Any, int]:
        """
        Generate an internal server error response.

        Args:
            message: Error message (default: "Internal server error")

        Returns:
            Tuple of (jsonified response, 500)

        Example:
            return ApiResponse.internal_error("Database connection failed")
        """
        return jsonify({"error": message}), 500
