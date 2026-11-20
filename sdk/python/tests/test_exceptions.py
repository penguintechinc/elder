"""Unit tests for elder_sdk.exceptions."""

from __future__ import annotations

import httpx
import pytest

from elder_sdk.exceptions import (
    ElderAuthError,
    ElderCatalogError,
    ElderError,
    ElderNotFoundError,
    ElderRateLimitError,
    ElderServerError,
    ElderTransportError,
    ElderValidationError,
    error_from_response,
    raise_for_status,
)


def _make_response(
    status_code: int,
    *,
    json_body: object = None,
    text: str | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    req = httpx.Request("GET", "https://elder.example.com/api/v1/test")
    if json_body is not None:
        return httpx.Response(status_code, json=json_body, headers=headers, request=req)
    return httpx.Response(status_code, text=text or "", headers=headers, request=req)


def test_elder_error_formatting_and_repr() -> None:
    err = ElderError("boom", status_code=418, error_code="TEAPOT", request_id="req-1")
    assert str(err) == "[418] boom"
    assert "TEAPOT" in repr(err)
    assert err.request_id == "req-1"

    plain = ElderError("plain")
    assert str(plain) == "plain"

    assert issubclass(ElderTransportError, ElderError)
    assert issubclass(ElderCatalogError, ElderError)


@pytest.mark.parametrize(
    ("status_code", "expected_cls"),
    [
        (400, ElderValidationError),
        (422, ElderValidationError),
        (401, ElderAuthError),
        (403, ElderAuthError),
        (404, ElderNotFoundError),
        (429, ElderRateLimitError),
        (500, ElderServerError),
        (502, ElderServerError),
        (503, ElderServerError),
        (504, ElderServerError),
        (409, ElderError),
    ],
)
def test_error_from_response_status_mapping(
    status_code: int, expected_cls: type[ElderError]
) -> None:
    resp = _make_response(
        status_code,
        json_body={
            "message": "failed",
            "errorCode": "E_FAIL",
            "details": {"field": "name"},
        },
        headers={"X-Request-Id": "rid-99"},
    )
    err = error_from_response(resp)
    assert type(err) is expected_cls
    assert err.status_code == status_code
    assert err.message == "failed"
    assert err.error_code == "E_FAIL"
    assert err.details == {"field": "name"}
    assert err.request_id == "rid-99"


def test_rate_limit_retry_after_parsing() -> None:
    resp_valid = _make_response(
        429, json_body={"error": "slow down"}, headers={"Retry-After": "12.5"}
    )
    err_valid = error_from_response(resp_valid)
    assert isinstance(err_valid, ElderRateLimitError)
    assert err_valid.retry_after == 12.5

    resp_bad = _make_response(
        429, json_body={"error": "slow down"}, headers={"Retry-After": "later"}
    )
    err_bad = error_from_response(resp_bad)
    assert isinstance(err_bad, ElderRateLimitError)
    assert err_bad.retry_after is None

    resp_neg = _make_response(
        429, json_body={"error": "slow down"}, headers={"Retry-After": "-3"}
    )
    err_neg = error_from_response(resp_neg)
    assert isinstance(err_neg, ElderRateLimitError)
    assert err_neg.retry_after is None


def test_error_extraction_non_json_and_list_payloads() -> None:
    resp_text = _make_response(502, text="Bad Gateway Upstream")
    err_text = error_from_response(resp_text)
    assert isinstance(err_text, ElderServerError)
    assert err_text.message == "Bad Gateway Upstream"
    assert err_text.details == "Bad Gateway Upstream"

    resp_empty = _make_response(503, text="")
    err_empty = error_from_response(resp_empty)
    assert err_empty.message == "Service Unavailable"
    assert err_empty.details is None

    resp_list = _make_response(400, json_body=["bad1", "bad2"])
    err_list = error_from_response(resp_list)
    assert isinstance(err_list, ElderValidationError)
    assert err_list.details == ["bad1", "bad2"]


def test_raise_for_status_behavior() -> None:
    ok = _make_response(200, json_body={"ok": True})
    raise_for_status(ok)

    not_found = _make_response(404, json_body={"error": "missing"})
    with pytest.raises(ElderNotFoundError, match="missing"):
        raise_for_status(not_found)
