"""Engine-backed HTTP recorder shared by the webhook dispatcher tests.

Lives outside ``conftest.py`` so test modules can import it by name.
"""

from __future__ import annotations

from typing import Any

import httpx


class WebhookPostRecorder:
    """Mock-like recorder for tests written against a ``post(url, ...)`` seam.

    Installed as the delivery engine's client factory (use
    ``patch(ENGINE_HTTP, new_callable=WebhookPostRecorder)``), so the real
    engine - SSRF vetting, pinning, signing, retries - runs and only the
    socket is replaced. Each request the engine sends is forwarded to an
    inner ``MagicMock`` as ``post(url, data=<raw body>, headers=<headers>)``,
    which keeps ``return_value``/``side_effect``/``call_args``/``call_count``
    and the ``assert_*`` helpers working on what actually went on the wire.
    """

    def __init__(self) -> None:
        """Start with a 200-OK default response and no recorded calls."""
        from unittest.mock import MagicMock

        post = MagicMock(name="webhook_post")
        post.return_value = MagicMock(status_code=200, text="ok")
        object.__setattr__(self, "_post", post)

    def __getattr__(self, name: str) -> Any:
        """Delegate mock attributes (call_args, assert_called_once...)."""
        return getattr(object.__getattribute__(self, "_post"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Delegate configuration (return_value, side_effect) to the mock."""
        setattr(object.__getattribute__(self, "_post"), name, value)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        """Forward one engine request to the mock and adapt its response."""
        outcome = self._post(
            str(request.url), data=request.content, headers=request.headers
        )
        return httpx.Response(int(outcome.status_code), text=str(outcome.text))

    def __call__(self, policy: Any) -> httpx.AsyncClient:
        """Act as the engine's client factory over the recording transport."""
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self._handle),
            timeout=httpx.Timeout(
                connect=policy.connect_timeout,
                read=policy.read_timeout,
                write=policy.read_timeout,
                pool=policy.connect_timeout,
            ),
            follow_redirects=False,
            trust_env=False,
        )


#: Patch target that swaps the delivery engine's HTTP client factory.
ENGINE_HTTP = "shared.webhooks.delivery._client_factory"
