"""Quart-compatible replacement for penguin_libs.pydantic.flask_integration."""

import asyncio
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from quart import jsonify, request

T = TypeVar("T", bound=BaseModel)


class ValidationErrorResponse:
    """Standardized validation error response for Quart."""

    @staticmethod
    def from_pydantic_error(error: ValidationError) -> tuple[dict[str, Any], int]:
        validation_errors = []
        for err in error.errors():
            validation_errors.append(
                {
                    "field": ".".join(str(x) for x in err["loc"]),
                    "message": err["msg"],
                    "type": err["type"],
                }
            )
        return (
            jsonify(
                {"error": "Validation failed", "validation_errors": validation_errors}
            ),
            400,
        )


async def _validate_body(model_class: type[T]) -> T:
    data = await request.get_json()
    return model_class.model_validate(data)


def _validate_query_params(model_class: type[T]) -> T:
    data = request.args.to_dict()
    return model_class.model_validate(data)


async def validate_body(model_class: type[T]) -> T:
    """Async-compatible validate_body for Quart.

    Validates the request JSON body against the provided Pydantic model.
    Must be awaited: body = await validate_body(MyModel)
    """
    return await _validate_body(model_class)


def validated_request(
    body_model: type[BaseModel] | None = None,
    query_model: type[BaseModel] | None = None,
) -> Callable:
    """Quart-compatible @validated_request decorator.

    Injects validated 'body' and/or 'query' keyword args into the route handler.
    Always wraps the handler as async (required for Quart).
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                if body_model:
                    kwargs["body"] = await _validate_body(body_model)
                if query_model:
                    kwargs["query"] = _validate_query_params(query_model)
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                return func(*args, **kwargs)
            except ValidationError as e:
                return ValidationErrorResponse.from_pydantic_error(e)

        return wrapper

    return decorator
