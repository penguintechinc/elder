"""Non-blocking outbound webhook delivery engine shared by every dispatcher.

One implementation of the delivery contract (async I/O, bounded concurrency,
explicit timeouts, retry with exponential backoff and jitter, SSRF-safe
resolve-then-connect pinning, response size cap, idempotency key and OTel
logs + metrics + traces) so no dispatcher can drift from it. The engine owns
no database state: callers persist the returned ``DeliveryResult``.

Secrets hygiene: target URLs routinely embed credentials (Slack/Teams hook
paths, ``user:pass@`` userinfo), so the URL never reaches a log line, span
attribute, metric label or error string - only the bare hostname does.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import contextvars
import hashlib
import hmac
import ipaddress
import os
import random
import re
import socket
import threading
import time
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar
from uuid import uuid4

import httpx
import structlog

from shared.observability import get_meter, get_tracer
from shared.utils.env import env_float, env_int

logger = structlog.get_logger(__name__)

#: Schemes a webhook target may use; everything else (file, gopher, ftp...) is refused.
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Operator-level escape hatch for on-prem receivers on private networks:
#: comma-separated CIDRs. Loopback, link-local/metadata, multicast and
#: unspecified ranges stay blocked even when listed here.
ALLOWED_PRIVATE_CIDRS_ENV = "WEBHOOK_ALLOWED_PRIVATE_CIDRS"

#: Bounded ``kind`` metric label values; anything else is recorded as "other"
#: so a caller-supplied string can never explode label cardinality.
DELIVERY_KINDS = frozenset(
    {
        "native",
        "issue_created",
        "issue_assigned",
        "user_created",
        "notification",
        "test",
        "redelivery",
        "recovery",
    }
)

IDEMPOTENCY_HEADER = "Idempotency-Key"

#: Replay-protection headers added (alongside the caller's legacy
#: ``X-Elder-Signature``) whenever a request carries a signing secret. The
#: v2 value is ``t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<raw body>">``.
TIMESTAMP_HEADER = "X-Elder-Timestamp"
SIGNATURE_V2_HEADER = "X-Elder-Signature-V2"

#: Recommended receiver-side tolerance for the signed timestamp.
SIGNATURE_TOLERANCE_SECONDS = 300

#: Hard ceiling on the response text kept from a public receiver. The
#: excerpt is tenant-readable, so it stays a short diagnostic, not a body.
RESPONSE_EXCERPT_MAX_CHARS = 256

#: Stored instead of any response text when the target sits inside an
#: operator-allowlisted private range: echoing an internal service's reply
#: to a tenant would turn webhook delivery into a read primitive.
PRIVATE_TARGET_EXCERPT = "[response body withheld: private network target]"

#: Shortest prefix an allowlist entry may have. Anything broader (0.0.0.0/0,
#: ::/0, a /4...) is treated as a misconfiguration that would disable the
#: SSRF guard, so the whole allowlist is ignored.
_MIN_ALLOWLIST_PREFIX = {4: 8, 6: 32}

#: Bounded ``reason`` label values for the load-shedding metric.
SHED_GLOBAL_CAP = "global_cap"
SHED_TENANT_CAP = "tenant_cap"

#: Admission bucket for deliveries scheduled without a tenant reference.
_UNTENANTED = "untenanted"

_T = TypeVar("_T")

#: Statuses worth another attempt: request timeout, throttling, server errors.
_RETRYABLE_STATUSES = frozenset({408, 429})

#: Ranges refused unconditionally, including when an operator allowlists a
#: supernet: loopback, link-local (cloud metadata lives at 169.254.169.254
#: and fd00:ec2::254), unspecified, multicast and the NAT64 prefix (which
#: would smuggle an IPv4 destination past the IPv4 checks).
_ALWAYS_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "224.0.0.0/4",
        "::/128",
        "::1/128",
        "fe80::/10",
        "ff00::/8",
        "fd00:ec2::/32",
        "64:ff9b::/96",
    )
)

_URL_IN_TEXT = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://\S+|(?<=with url: )\S+")
_jitter = random.SystemRandom()

_tracer = get_tracer("elder")
_meter = get_meter("elder")
_attempt_counter = _meter.create_counter(
    name="elder.webhook.delivery.attempts",
    description="Outbound webhook HTTP attempts, including retries",
    unit="1",
)
_success_counter = _meter.create_counter(
    name="elder.webhook.delivery.successes",
    description="Outbound webhook deliveries that ended in a 2xx response",
    unit="1",
)
_failure_counter = _meter.create_counter(
    name="elder.webhook.delivery.failures",
    description="Outbound webhook deliveries that exhausted or aborted retries",
    unit="1",
)
_latency_histogram = _meter.create_histogram(
    name="elder.webhook.delivery.latency_ms",
    description="Wall-clock duration of one outbound webhook HTTP attempt",
    unit="ms",
)

_shed_counter = _meter.create_counter(
    name="elder.webhook.delivery.shed",
    description="Outbound webhook deliveries refused because a pending cap was hit",
    unit="1",
)
_shutdown_pending_counter = _meter.create_counter(
    name="elder.webhook.delivery.pending_at_shutdown",
    description="Background webhook deliveries still unfinished when the drain ended",
    unit="1",
)

# Strong references so the loop's weak task table cannot drop a
# fire-and-forget delivery mid-flight.
_background_tasks: set[asyncio.Task[Any]] = set()
# Log-safe context (delivery id, tenant reference, bare host) per task, so a
# delivery cut off by shutdown can be named without touching its URL.
_task_details: dict[asyncio.Task[Any], dict[str, Any]] = {}
# Admission accounting. Guarded by a lock because the synchronous bridge can
# run a delivery loop in a worker thread while the main loop is admitting.
_admission_lock = threading.Lock()
_pending_total = 0
_pending_by_tenant: dict[str, int] = {}
_tenant_gates: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[str, list[Any]]
] = weakref.WeakKeyDictionary()
_allowlist_cache: tuple[str, tuple[Any, ...]] | None = None
_io_executor: concurrent.futures.ThreadPoolExecutor | None = None
_io_executor_lock = threading.Lock()
_semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


class WebhookTargetError(ValueError):
    """Raised when a webhook URL is not an allowed delivery target.

    Covers a disallowed scheme, a missing or unresolvable host and any
    resolved address inside a blocked range. Messages never include the URL.
    """


@dataclass(slots=True, frozen=True)
class DeliveryPolicy:
    """Timeouts, retry budget and limits applied to one delivery.

    Exists as a value object so interactive callers (test/redeliver routes)
    can cap attempts at one while background dispatch keeps the full budget.
    """

    max_attempts: int = 4
    connect_timeout: float = 3.0
    read_timeout: float = 10.0
    attempt_timeout: float = 15.0
    backoff_base: float = 0.5
    backoff_cap: float = 30.0
    max_response_bytes: int = 65536


@dataclass(slots=True, frozen=True)
class DeliveryRequest:
    """One webhook POST: target, exact body bytes, headers and metric kind.

    ``body`` is sent byte-for-byte so a signature computed by the caller over
    the same bytes verifies at the receiver.
    """

    url: str
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)
    kind: str = "native"
    idempotency_key: str | None = None
    #: Opaque tenant reference (a UUID, never a name) used only to share
    #: delivery capacity fairly between tenants. Never a metric label.
    tenant: str | None = None
    #: HMAC secret for the timestamped v2 signature; excluded from ``repr``
    #: so it cannot leak through a logged or traced request object.
    signing_secret: str | None = field(default=None, repr=False)


@dataclass(slots=True)
class DeliveryResult:
    """Outcome of a delivery after all attempts, safe to persist and log.

    ``error`` is already scrubbed of URLs; ``response_excerpt`` is the
    size-capped, decoded response body of the final attempt.
    """

    success: bool
    attempts: int
    outcome: str
    idempotency_key: str
    status_code: int | None = None
    error: str | None = None
    response_excerpt: str = ""
    duration_ms: float = 0.0
    #: True when the vetted target was inside an allowlisted private range;
    #: callers must then expose status and reason only, never response text.
    private_target: bool = False


@dataclass(slots=True, frozen=True)
class _ResolvedTarget:
    """A validated target pinned to the single IP address that was checked."""

    url: httpx.URL
    host_header: str
    hostname: str
    sni_hostname: str | None
    private: bool = False


@dataclass(slots=True)
class DeliverySlot:
    """One admitted unit of pending delivery work, held until it finishes.

    Returned by :func:`reserve_slot`; releasing it (idempotently) frees the
    global and per-tenant capacity it was counted against.
    """

    tenant: str
    released: bool = False


class DeliveryShedError(RuntimeError):
    """Raised for an explicit, caller-awaited send refused by a pending cap.

    Only interactive API-triggered sends raise this (the route answers 429
    with ``Retry-After``); event-triggered deliveries are dropped and logged
    instead so they can never fail the request that produced the event.
    """

    def __init__(self, reason: str, retry_after: int) -> None:
        """Record the bounded shed reason and the advised retry delay."""
        super().__init__("webhook delivery queue is full")
        self.reason = reason
        self.retry_after = retry_after


def default_policy(*, max_attempts: int | None = None) -> DeliveryPolicy:
    """Build the delivery policy from ``WEBHOOK_*`` environment variables.

    Read per call (cheap) rather than at import so a bad value can never
    crash startup and tests can vary the environment; ``max_attempts``
    overrides the env budget for interactive single-shot deliveries.
    """
    attempts = env_int("WEBHOOK_MAX_ATTEMPTS", 4, minimum=1, maximum=10)
    if max_attempts is not None:
        attempts = min(max(int(max_attempts), 1), 10)
    return DeliveryPolicy(
        max_attempts=attempts,
        connect_timeout=env_float(
            "WEBHOOK_CONNECT_TIMEOUT_SECONDS", 3.0, minimum=0.1, maximum=30.0
        ),
        read_timeout=env_float(
            "WEBHOOK_READ_TIMEOUT_SECONDS", 10.0, minimum=0.1, maximum=120.0
        ),
        attempt_timeout=env_float(
            "WEBHOOK_ATTEMPT_TIMEOUT_SECONDS", 15.0, minimum=0.2, maximum=180.0
        ),
        backoff_base=env_float(
            "WEBHOOK_BACKOFF_BASE_SECONDS", 0.5, minimum=0.01, maximum=30.0
        ),
        backoff_cap=env_float(
            "WEBHOOK_BACKOFF_CAP_SECONDS", 30.0, minimum=0.01, maximum=300.0
        ),
        max_response_bytes=env_int(
            "WEBHOOK_MAX_RESPONSE_BYTES", 65536, minimum=0, maximum=1048576
        ),
    )


def target_host(url: str) -> str:
    """Return only the hostname of ``url`` for logging, or ``"invalid"``.

    Path, query and userinfo are dropped because they commonly carry the
    receiver's secret.
    """
    try:
        return httpx.URL(url).host or "invalid"
    except Exception:
        return "invalid"


def scrub_error(exc: BaseException) -> str:
    """Render an exception as ``Type: message`` with any URL redacted.

    Keeps delivery errors useful in ``webhook_deliveries.error_message`` and
    logs without ever echoing a target URL (or the request path that
    ``requests`` appends as "with url: /path") an HTTP library put in its text.
    """
    message = _URL_IN_TEXT.sub("[redacted-url]", str(exc)).strip()
    name = type(exc).__name__
    return f"{name}: {message}"[:500] if message else name


def _allowed_private_networks() -> (
    tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]
):
    """Parse the operator's private-range allowlist, failing closed on abuse.

    A malformed CIDR is logged and skipped (a typo must not crash delivery
    for every tenant). An entry broader than /8 (IPv4) or /32 (IPv6) - e.g.
    0.0.0.0/0 or ::/0 - would switch the SSRF guard off, so it is treated
    as a misconfiguration: an ERROR is logged and the entire allowlist is
    ignored. The parse is cached per raw value so the log fires once.
    """
    global _allowlist_cache
    raw_value = os.environ.get(ALLOWED_PRIVATE_CIDRS_ENV, "")
    cached = _allowlist_cache
    if cached is not None and cached[0] == raw_value:
        return cached[1]
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    too_broad = False
    for raw in raw_value.split(","):
        entry = raw.strip()
        if not entry:
            continue
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            logger.warning(
                "webhook_private_allowlist_entry_invalid",
                variable=ALLOWED_PRIVATE_CIDRS_ENV,
            )
            continue
        if network.prefixlen < _MIN_ALLOWLIST_PREFIX[network.version]:
            too_broad = True
            continue
        networks.append(network)
    if too_broad:
        logger.error(
            "webhook_private_allowlist_rejected",
            variable=ALLOWED_PRIVATE_CIDRS_ENV,
            reason="prefix_too_broad",
            min_ipv4_prefix=_MIN_ALLOWLIST_PREFIX[4],
            min_ipv6_prefix=_MIN_ALLOWLIST_PREFIX[6],
            effect="entire allowlist ignored; private targets stay blocked",
        )
        networks = []
    result = tuple(networks)
    _allowlist_cache = (raw_value, result)
    return result


def _embedded_ipv4(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> list[ipaddress.IPv4Address]:
    """Return the IPv4 addresses tunnelled inside an IPv6 transition address.

    6to4 (2002::/16) and Teredo (2001::/32) carry an IPv4 destination in
    their bits; a gateway on path would forward to it, so each embedded
    address must pass the same checks as if it had been dialled directly.
    """
    if not isinstance(ip, ipaddress.IPv6Address):
        return []
    embedded: list[ipaddress.IPv4Address] = []
    if ip.sixtofour is not None:
        embedded.append(ip.sixtofour)
    if ip.teredo is not None:
        embedded.extend(ip.teredo)
    return embedded


def _in_networks(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, networks: Any
) -> bool:
    """Return True when ``ip`` falls inside any same-family network."""
    return any(ip.version == net.version and ip in net for net in networks)


def _classify_address(address: str) -> tuple[bool, bool]:
    """Classify ``address`` as ``(blocked, private)``.

    ``blocked``: must never receive a webhook. ``private``: allowed only
    because an operator allowlisted its non-public range. Every
    non-globally-routable address (RFC 1918, CGNAT, ULA, loopback,
    link-local/metadata, multicast, reserved) is blocked unless allowlisted;
    the always-blocked ranges win regardless, and an IPv4 address embedded
    in an IPv4-mapped, 6to4 or Teredo address is unwrapped and vetted too,
    so no allowlist entry can admit loopback or metadata in disguise.
    Unparseable input is blocked.
    """
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return True, False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    private = False
    for candidate in (ip, *_embedded_ipv4(ip)):
        if _in_networks(candidate, _ALWAYS_BLOCKED_NETWORKS):
            return True, False
        if candidate.is_global:
            continue
        if not _in_networks(candidate, _allowed_private_networks()):
            return True, False
        private = True
    return False, private


def is_blocked_address(address: str) -> bool:
    """Return True when ``address`` must never receive a webhook.

    Thin boolean view of :func:`_classify_address` (see there for the
    rules); kept as the public predicate used by callers and tests.
    """
    return _classify_address(address)[0]


def _resolve_host(host: str, port: int) -> list[str]:
    """Resolve ``host`` to its IP address strings (blocking; run off-loop).

    The single DNS seam for delivery so tests can substitute a deterministic
    resolver; raises ``OSError`` when the name does not resolve.
    """
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _pin_target(url: str, resolve: Callable[[str, int], list[str]]) -> _ResolvedTarget:
    """Validate ``url`` and pin it to one vetted IP address.

    Resolve-then-connect: every resolved address must be allowed (a single
    blocked record rejects the target, defeating multi-record DNS tricks)
    and the request is then sent to the checked IP, so a second lookup by
    the HTTP client cannot be rebound to an internal address.
    """
    try:
        parsed = httpx.URL(url)
    except Exception as exc:
        raise WebhookTargetError("webhook target URL is not parseable") from exc
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise WebhookTargetError("webhook target scheme is not allowed")
    hostname = parsed.host
    if not hostname:
        raise WebhookTargetError("webhook target has no host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        ipaddress.ip_address(hostname)
        addresses, is_literal = [hostname], True
    except ValueError:
        is_literal = False
        try:
            addresses = resolve(hostname, port)
        except OSError as exc:
            raise WebhookTargetError("webhook target host does not resolve") from exc
    if not addresses:
        raise WebhookTargetError("webhook target host does not resolve")
    verdicts = [_classify_address(address) for address in addresses]
    if any(blocked for blocked, _ in verdicts):
        raise WebhookTargetError("webhook target resolves to a blocked address")

    pinned_ip = addresses[0].split("%", 1)[0]
    return _ResolvedTarget(
        url=parsed.copy_with(host=pinned_ip),
        host_header=parsed.netloc.decode("ascii"),
        hostname=hostname,
        sni_hostname=(
            hostname if parsed.scheme == "https" and not is_literal else None
        ),
        private=any(private for _, private in verdicts),
    )


def validate_target_url(url: str) -> None:
    """Vet ``url`` as a webhook target synchronously (blocking DNS lookup).

    Raises ``WebhookTargetError`` for a disallowed scheme or blocked
    address. Suitable only for early input validation (e.g. rejecting a URL
    at configuration time): it cannot pin the connection, so sending must
    always go through :func:`deliver`, which re-vets and dials the checked IP.
    """
    _pin_target(url, _resolve_host)


def _client_factory(policy: DeliveryPolicy) -> httpx.AsyncClient:
    """Create the HTTP client for one delivery (the transport seam for tests).

    Redirects are never followed (a 3xx could bounce the signed payload to
    an internal or foreign host) and proxy/env settings are ignored so the
    pinned IP is the address actually dialled.
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=policy.connect_timeout,
            read=policy.read_timeout,
            write=policy.read_timeout,
            pool=policy.connect_timeout,
        ),
        follow_redirects=False,
        trust_env=False,
    )


def _max_concurrency() -> int:
    """Return the per-process cap on simultaneous in-flight deliveries."""
    return env_int("WEBHOOK_MAX_CONCURRENCY", 10, minimum=1, maximum=200)


def _semaphore() -> asyncio.Semaphore:
    """Return the running loop's delivery semaphore, creating it on first use.

    Keyed per event loop because a semaphore is bound to the loop it first
    waits on; bounds outbound sockets so a webhook storm cannot starve the
    API's own request handling.
    """
    loop = asyncio.get_running_loop()
    semaphore = _semaphores.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(_max_concurrency())
        _semaphores[loop] = semaphore
    return semaphore


def _max_tenant_concurrency() -> int:
    """Return the cap on one tenant's simultaneous in-flight deliveries."""
    return env_int("WEBHOOK_MAX_CONCURRENCY_PER_TENANT", 4, minimum=1, maximum=200)


@contextlib.asynccontextmanager
async def _tenant_gate(tenant: str | None) -> AsyncIterator[None]:
    """Hold one of ``tenant``'s in-flight delivery slots for the block.

    Fairness: a tenant may occupy only a fraction of the global sockets, and
    waiters queue FIFO behind the global semaphore, so a tenant with a deep
    backlog cannot starve another tenant's first delivery. Gates are
    reference-counted and dropped when idle so the map cannot grow without
    bound; untenanted work is limited by the global semaphore alone.
    """
    if not tenant:
        yield
        return
    loop = asyncio.get_running_loop()
    gates = _tenant_gates.setdefault(loop, {})
    entry = gates.get(tenant)
    if entry is None:
        entry = gates[tenant] = [asyncio.Semaphore(_max_tenant_concurrency()), 0]
    entry[1] += 1
    try:
        async with entry[0]:
            yield
    finally:
        entry[1] -= 1
        if entry[1] <= 0 and gates.get(tenant) is entry:
            del gates[tenant]


def _executor() -> concurrent.futures.ThreadPoolExecutor:
    """Return the engine's own small thread pool, creating it on first use.

    DNS lookups and delivery bookkeeping are blocking. Running them on the
    event loop's shared default pool would let a webhook storm (or a slow
    resolver) occupy every worker and stall unrelated request handlers, so
    delivery work is confined to this dedicated, bounded pool.
    """
    global _io_executor
    with _io_executor_lock:
        if _io_executor is None:
            _io_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=env_int("WEBHOOK_IO_THREADS", 8, minimum=1, maximum=64),
                thread_name_prefix="webhook-io",
            )
        return _io_executor


async def run_in_io_pool(func: Callable[..., _T], /, *args: Any) -> _T:
    """Run blocking ``func(*args)`` on the delivery engine's bounded pool.

    The delivery-side replacement for ``asyncio.to_thread``: same
    non-blocking contract (context variables, and so trace context, are
    carried over), but isolated from the default executor.
    """
    context = contextvars.copy_context()
    return await asyncio.get_running_loop().run_in_executor(
        _executor(), lambda: context.run(func, *args)
    )


def _now_unix() -> int:
    """Return the current unix time in whole seconds (the signing clock seam)."""
    return int(time.time())


def sign_v2(secret: str, timestamp: int, body: bytes) -> str:
    """Return the hex HMAC-SHA256 of ``"<timestamp>.<raw body>"``.

    Binding the timestamp into the MAC lets a receiver reject a captured
    delivery replayed outside its tolerance window; the legacy body-only
    signature cannot express that.
    """
    message = str(int(timestamp)).encode("ascii") + b"." + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def signature_v2_header(secret: str, timestamp: int, body: bytes) -> str:
    """Render the ``X-Elder-Signature-V2`` value ``t=<ts>,v1=<hex>``."""
    return f"t={int(timestamp)},v1={sign_v2(secret, timestamp, body)}"


def verify_signature_v2(
    secret: str,
    header: str,
    body: bytes,
    *,
    tolerance: int = SIGNATURE_TOLERANCE_SECONDS,
    now: int | None = None,
) -> bool:
    """Verify a v2 signature header the way a receiver should.

    Reference implementation (also used by the tests): parse ``t`` and
    ``v1``, reject a timestamp outside ``tolerance`` seconds of ``now``,
    then compare MACs in constant time. Malformed input verifies False.
    """
    parts = dict(item.split("=", 1) for item in header.split(",") if "=" in item)
    try:
        timestamp = int(parts["t"].strip())
        provided = parts["v1"].strip()
    except (KeyError, ValueError):
        return False
    current = _now_unix() if now is None else int(now)
    if abs(current - timestamp) > tolerance:
        return False
    return hmac.compare_digest(sign_v2(secret, timestamp, body), provided)


def sanitize_excerpt(text: str) -> str:
    """Reduce a receiver's response text to a short, inert diagnostic.

    Control characters (including newlines and terminal escapes) become
    spaces so the excerpt cannot forge log lines or corrupt a console, and
    the result is capped at ``RESPONSE_EXCERPT_MAX_CHARS``.
    """
    cleaned = "".join(
        " " if (ord(char) < 32 or 127 <= ord(char) < 160) else char for char in text
    )
    return cleaned.strip()[:RESPONSE_EXCERPT_MAX_CHARS]


def backoff_delay(attempt: int, policy: DeliveryPolicy) -> float:
    """Return the sleep before the attempt after failed attempt ``attempt``.

    Exponential (``base * 2^(attempt-1)``, capped) with equal jitter: half
    the window is fixed so retries always back off, half is random so a
    burst of failed deliveries does not retry in lockstep.
    """
    window = min(policy.backoff_cap, policy.backoff_base * (2 ** max(attempt - 1, 0)))
    return window / 2 + _jitter.uniform(0, window / 2)


def _retry_after_seconds(headers: httpx.Headers) -> float | None:
    """Parse a numeric ``Retry-After`` header, ignoring dates and garbage."""
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    return value if value >= 0 else None


def _status_class(status_code: int | None) -> str:
    """Bucket a status code into a bounded ``Nxx``/``none`` metric label."""
    if status_code is None:
        return "none"
    return f"{status_code // 100}xx"


def _kind_label(kind: str) -> str:
    """Map a caller's delivery kind onto the bounded metric label set."""
    return kind if kind in DELIVERY_KINDS else "other"


def _record_attempt(
    kind: str, outcome: str, status_code: int | None, duration_ms: float
) -> None:
    """Emit attempt counter + latency histogram; telemetry never raises.

    Labels are bounded enums only (kind, outcome, status class) - no URL,
    host, tenant or webhook id.
    """
    attributes = {
        "kind": _kind_label(kind),
        "outcome": outcome,
        "status_class": _status_class(status_code),
    }
    try:
        _attempt_counter.add(1, attributes)
        _latency_histogram.record(duration_ms, attributes)
    except Exception:
        logger.debug("webhook_delivery_metrics_failed")


def _record_final(kind: str, result: DeliveryResult) -> None:
    """Emit the per-delivery success/failure counter; never raises."""
    attributes = {"kind": _kind_label(kind), "outcome": result.outcome}
    try:
        (_success_counter if result.success else _failure_counter).add(1, attributes)
    except Exception:
        logger.debug("webhook_delivery_metrics_failed")


async def _send_once(
    client: httpx.AsyncClient,
    target: _ResolvedTarget,
    request: DeliveryRequest,
    headers: dict[str, str],
    policy: DeliveryPolicy,
) -> tuple[int, str, httpx.Headers]:
    """POST the body once to the pinned target and read a capped response.

    Streams the response and stops at ``max_response_bytes`` so a hostile
    receiver cannot exhaust memory; the whole exchange is bounded by
    ``attempt_timeout`` on top of the per-phase connect/read timeouts.
    """
    extensions: dict[str, Any] = {}
    if target.sni_hostname:
        extensions["sni_hostname"] = target.sni_hostname
    async with asyncio.timeout(policy.attempt_timeout):
        async with client.stream(
            "POST",
            target.url,
            content=request.body,
            headers=headers,
            extensions=extensions,
        ) as response:
            chunks: list[bytes] = []
            # A private target's reply is never kept, so it is never read.
            remaining = 0 if target.private else policy.max_response_bytes
            if remaining > 0:
                async for chunk in response.aiter_bytes():
                    chunks.append(chunk[:remaining])
                    remaining -= len(chunk)
                    if remaining <= 0:
                        break
            excerpt = b"".join(chunks).decode("utf-8", errors="replace")
            return response.status_code, excerpt, response.headers


def _build_headers(
    request: DeliveryRequest, target: _ResolvedTarget, idempotency_key: str
) -> dict[str, str]:
    """Merge caller headers with the engine-owned Host, key and v2 signature.

    Caller-supplied ``Host``/``Idempotency-Key``/``Content-Length`` and the
    replay-protection headers are dropped: Host must match the vetted
    target (it also drives virtual hosting at the pinned IP), the key must
    stay stable across retries, and a per-webhook custom header must not be
    able to spoof the timestamped signature. Built per attempt, so each
    retry is signed with a fresh timestamp.
    """
    reserved = {
        "host",
        IDEMPOTENCY_HEADER.lower(),
        "content-length",
        TIMESTAMP_HEADER.lower(),
        SIGNATURE_V2_HEADER.lower(),
    }
    headers = {
        str(name): str(value)
        for name, value in request.headers.items()
        if str(name).lower() not in reserved
    }
    headers["Host"] = target.host_header
    headers[IDEMPOTENCY_HEADER] = idempotency_key
    if request.signing_secret:
        timestamp = _now_unix()
        headers[TIMESTAMP_HEADER] = str(timestamp)
        headers[SIGNATURE_V2_HEADER] = signature_v2_header(
            request.signing_secret, timestamp, request.body
        )
    return headers


async def deliver(
    request: DeliveryRequest,
    *,
    policy: DeliveryPolicy | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    on_retry: Callable[[DeliveryResult], Awaitable[None]] | None = None,
) -> DeliveryResult:
    """Deliver one webhook with retries; never raises, never blocks the loop.

    ``on_retry`` (optional) is awaited after each failed attempt that will
    be retried, so a caller can persist progress (outbox state) before the
    backoff sleep; a failure inside it is logged and never aborts delivery.

    Each attempt re-resolves and re-vets the target, holds the concurrency
    semaphore only while the socket is in use, and is retried with backoff
    on timeouts, transport errors, 5xx, 408 and 429. Other 4xx, any 3xx and
    a blocked target are terminal. The same ``Idempotency-Key`` is sent on
    every attempt so receivers can de-duplicate retries.
    """
    policy = policy or default_policy()
    sleep = sleep or asyncio.sleep
    idempotency_key = request.idempotency_key or uuid4().hex
    host = target_host(request.url)
    started = time.monotonic()
    result = DeliveryResult(
        success=False,
        attempts=0,
        outcome="not_attempted",
        idempotency_key=idempotency_key,
    )

    with _tracer.start_as_current_span("webhook.deliver") as span:
        for attempt in range(1, policy.max_attempts + 1):
            result.attempts = attempt
            retryable, retry_after = await _attempt(
                request, policy, idempotency_key, result
            )
            if result.success or not retryable or attempt >= policy.max_attempts:
                break
            delay = backoff_delay(attempt, policy)
            if retry_after is not None:
                delay = min(max(delay, retry_after), policy.backoff_cap)
            logger.warning(
                "webhook_delivery_attempt_failed",
                kind=_kind_label(request.kind),
                target_host=host,
                attempt=attempt,
                max_attempts=policy.max_attempts,
                outcome=result.outcome,
                status_code=result.status_code,
                retry_in_seconds=round(delay, 3),
            )
            if on_retry is not None:
                try:
                    await on_retry(result)
                except Exception as exc:
                    logger.error(
                        "webhook_delivery_progress_hook_failed",
                        error=scrub_error(exc),
                    )
            await sleep(delay)

        result.duration_ms = (time.monotonic() - started) * 1000.0
        _record_final(request.kind, result)
        try:
            span.set_attribute("webhook.kind", _kind_label(request.kind))
            span.set_attribute("webhook.attempts", result.attempts)
            span.set_attribute("webhook.outcome", result.outcome)
            span.set_attribute("webhook.success", result.success)
            if result.status_code is not None:
                span.set_attribute("http.response.status_code", result.status_code)
        except Exception:
            logger.debug("webhook_delivery_span_failed")

    log = logger.info if result.success else logger.error
    log(
        "webhook_delivery_succeeded" if result.success else "webhook_delivery_failed",
        kind=_kind_label(request.kind),
        target_host=host,
        attempts=result.attempts,
        outcome=result.outcome,
        status_code=result.status_code,
        duration_ms=round(result.duration_ms, 1),
        error=result.error,
    )
    return result


async def _attempt(
    request: DeliveryRequest,
    policy: DeliveryPolicy,
    idempotency_key: str,
    result: DeliveryResult,
) -> tuple[bool, float | None]:
    """Run one attempt, fold its outcome into ``result``, report retryability.

    Returns ``(retryable, retry_after_seconds)``. Every failure mode is
    classified into a bounded ``outcome`` string and recorded as telemetry;
    nothing propagates, so one bad target cannot break a batch.
    """
    attempt_started = time.monotonic()
    status_code: int | None = None
    retry_after: float | None = None
    try:
        # Resolution happens inside both gates and on the engine's own
        # pool, so slow DNS is bounded by the same concurrency limits as
        # sockets and can never occupy the loop's shared default executor.
        async with _tenant_gate(request.tenant), _semaphore():
            target = await run_in_io_pool(_pin_target, request.url, _resolve_host)
            result.private_target = target.private
            headers = _build_headers(request, target, idempotency_key)
            async with _client_factory(policy) as client:
                status_code, excerpt, response_headers = await _send_once(
                    client, target, request, headers, policy
                )
        result.status_code = status_code
        result.response_excerpt = (
            PRIVATE_TARGET_EXCERPT if target.private else sanitize_excerpt(excerpt)
        )
        if 200 <= status_code < 300:
            result.success, result.outcome, result.error = True, "success", None
            retryable = False
        elif 300 <= status_code < 400:
            result.outcome = "redirect_refused"
            result.error = f"HTTP {status_code}: redirects are not followed"
            retryable = False
        else:
            result.outcome = "http_error"
            result.error = f"HTTP {status_code}"
            retryable = status_code >= 500 or status_code in _RETRYABLE_STATUSES
            retry_after = _retry_after_seconds(response_headers)
    except WebhookTargetError as exc:
        result.outcome, result.error, retryable = "blocked_target", str(exc), False
    except (TimeoutError, httpx.TimeoutException) as exc:
        result.status_code = None
        result.outcome, result.error, retryable = "timeout", scrub_error(exc), True
    except httpx.TransportError as exc:
        result.status_code = None
        result.outcome, result.error = "transport_error", scrub_error(exc)
        retryable = True
    except Exception as exc:
        # Unknown failure (bad header value, TLS config...): terminal, since
        # retrying a deterministic local error only burns the retry budget.
        result.status_code = None
        result.outcome, result.error, retryable = "error", scrub_error(exc), False
    _record_attempt(
        request.kind,
        result.outcome,
        status_code,
        (time.monotonic() - attempt_started) * 1000.0,
    )
    return retryable, retry_after


def max_pending() -> int:
    """Return the global cap on admitted, unfinished delivery work."""
    return env_int("WEBHOOK_MAX_PENDING", 2000, minimum=1, maximum=100000)


def max_pending_per_tenant() -> int:
    """Return the cap on one tenant's admitted, unfinished delivery work."""
    return env_int("WEBHOOK_MAX_PENDING_PER_TENANT", 200, minimum=1, maximum=100000)


def shed_retry_after() -> int:
    """Return the ``Retry-After`` seconds advised when a send is shed."""
    return env_int("WEBHOOK_SHED_RETRY_AFTER_SECONDS", 30, minimum=1, maximum=3600)


def pending_count(tenant: str | None = None) -> int:
    """Return admitted-but-unfinished work, overall or for one tenant."""
    with _admission_lock:
        if tenant is None:
            return _pending_total
        return _pending_by_tenant.get(tenant, 0)


def _record_shed(reason: str, tenant: str, host: str, count: int) -> None:
    """Log and count shed deliveries; telemetry never raises.

    The log names the tenant by opaque reference and the target by bare
    host only; the metric carries just the bounded ``reason`` so neither
    tenant nor host can inflate label cardinality.
    """
    logger.warning(
        "webhook_delivery_shed",
        reason=reason,
        tenant=tenant,
        target_host=host,
        shed=count,
    )
    try:
        _shed_counter.add(count, {"reason": reason})
    except Exception:
        logger.debug("webhook_delivery_metrics_failed")


def _admit(tenant: str | None, count: int, host: str) -> tuple[list[DeliverySlot], str]:
    """Reserve ``count`` slots atomically; return ``(slots, shed_reason)``.

    ``slots`` is empty when a cap refused the request, in which case the
    refusal has already been logged and counted and ``shed_reason`` names
    the cap (global checked first: it protects the whole process).
    """
    global _pending_total
    key = tenant or _UNTENANTED
    count = max(int(count), 1)
    with _admission_lock:
        tenant_pending = _pending_by_tenant.get(key, 0)
        if _pending_total + count > max_pending():
            reason = SHED_GLOBAL_CAP
        elif tenant and tenant_pending + count > max_pending_per_tenant():
            reason = SHED_TENANT_CAP
        else:
            reason = ""
            _pending_total += count
            _pending_by_tenant[key] = tenant_pending + count
    if reason:
        _record_shed(reason, key, host, count)
        return [], reason
    return [DeliverySlot(tenant=key) for _ in range(count)], ""


def reserve_slots(
    tenant: str | None, count: int = 1, *, host: str = "unknown"
) -> list[DeliverySlot] | None:
    """Admit ``count`` units of pending delivery work, all or nothing.

    Returns the slots, or ``None`` when the global or the tenant's cap
    would be exceeded - the load-shedding decision, taken before any row is
    written or socket opened so an overload costs almost nothing. For
    internally-triggered events: the caller simply drops the work.
    """
    slots, _ = _admit(tenant, count, host)
    return slots or None


def reserve_slots_or_raise(
    tenant: str | None, count: int = 1, *, host: str = "unknown"
) -> list[DeliverySlot]:
    """Admit ``count`` units or raise :class:`DeliveryShedError`.

    For explicit, API-triggered sends: the route turns the error into a
    429 with ``Retry-After`` so the caller backs off instead of piling on.
    """
    slots, reason = _admit(tenant, count, host)
    if not slots:
        raise DeliveryShedError(reason, shed_retry_after())
    return slots


def reserve_slot(tenant: str | None, *, host: str = "unknown") -> DeliverySlot | None:
    """Admit one unit of pending delivery work, or ``None`` when shed."""
    slots = reserve_slots(tenant, 1, host=host)
    return slots[0] if slots else None


def release_slot(slot: DeliverySlot | None) -> None:
    """Return a slot's capacity; safe to call twice or with ``None``."""
    global _pending_total
    if slot is None:
        return
    with _admission_lock:
        if slot.released:
            return
        slot.released = True
        _pending_total = max(_pending_total - 1, 0)
        remaining = _pending_by_tenant.get(slot.tenant, 0) - 1
        if remaining > 0:
            _pending_by_tenant[slot.tenant] = remaining
        else:
            _pending_by_tenant.pop(slot.tenant, None)


def _on_background_done(task: asyncio.Task[Any], slot: DeliverySlot | None) -> None:
    """Free the slot, drop the strong reference and log a crashed delivery.

    Without this an exception in a fire-and-forget task would only surface
    as an unreadable "Task exception was never retrieved" at GC time.
    """
    _background_tasks.discard(task)
    _task_details.pop(task, None)
    release_slot(slot)
    if task.cancelled():
        logger.warning("webhook_background_task_cancelled", task=task.get_name())
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "webhook_background_task_failed",
            task=task.get_name(),
            error=scrub_error(exc),
        )


def schedule_background(
    coro: Coroutine[Any, Any, Any],
    *,
    name: str = "webhook-delivery",
    tenant: str | None = None,
    host: str = "unknown",
    slot: DeliverySlot | None = None,
    delivery_id: int | None = None,
) -> asyncio.Task[Any] | None:
    """Run ``coro`` off the request path on the running loop, fire-and-forget.

    Admission-controlled: unless the caller already holds ``slot``, one is
    reserved here against the global and per-tenant pending caps. When a cap
    is hit the coroutine is closed unrun and ``None`` is returned - the work
    is shed (WARN log + metric), never raised, so the originating request
    still succeeds. An admitted task is strongly referenced until done and
    any exception is logged by the done-callback instead of propagating.
    """
    if slot is None:
        slot = reserve_slot(tenant, host=host)
        if slot is None:
            coro.close()
            return None
    try:
        task = asyncio.get_running_loop().create_task(coro, name=name)
    except BaseException:
        release_slot(slot)
        coro.close()
        raise
    _background_tasks.add(task)
    _task_details[task] = {
        "delivery_id": delivery_id,
        "tenant": slot.tenant,
        "target_host": host,
    }
    held = slot
    task.add_done_callback(lambda done: _on_background_done(done, held))
    return task


async def drain_background(timeout: float = 30.0) -> int:
    """Wait for in-flight background deliveries (graceful shutdown, tests).

    Bounded by ``timeout`` so shutdown cannot hang on a slow receiver.
    Anything still unfinished is reported loudly - one WARN per delivery
    (id, tenant reference, bare host) plus a counter - instead of vanishing;
    outbox-backed deliveries keep their ``pending``/``retrying`` row and are
    re-sent by the startup recovery pass. Returns the unfinished count.
    """
    pending = [task for task in _background_tasks if not task.done()]
    if not pending:
        return 0
    _, unfinished = await asyncio.wait(pending, timeout=timeout)
    for task in unfinished:
        details = _task_details.get(task, {})
        logger.warning(
            "webhook_delivery_pending_at_shutdown",
            task=task.get_name(),
            delivery_id=details.get("delivery_id"),
            tenant=details.get("tenant"),
            target_host=details.get("target_host"),
            recoverable=details.get("delivery_id") is not None,
        )
    if unfinished:
        try:
            _shutdown_pending_counter.add(len(unfinished))
        except Exception:
            logger.debug("webhook_delivery_metrics_failed")
    return len(unfinished)


def run_blocking(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run ``coro`` to completion from synchronous code with no running loop.

    The sync bridge for dispatchers reached from non-async call sites: it
    drives the same async engine on a private loop instead of duplicating
    delivery with a blocking HTTP client. Calling it on a thread that is
    already running an event loop would block that loop (and ``asyncio.run``
    cannot nest), so that misuse fails loudly rather than degrading.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    coro.close()
    raise RuntimeError(
        "run_blocking() called from a running event loop; await the coroutine"
    )
