"""Bounded in-flight admission control for hosted ``hub-mcp``.

Hosted mode only checks credential presence, so credential-keyed limits alone
can be bypassed with random tokens. This module layers a global cap, a bounded
per-caller table and a per-source-address cap, all fail-closed and O(in-flight).
"""

from __future__ import annotations

import ipaddress
import logging
import os
from collections.abc import Iterable, Mapping

_Network = ipaddress.IPv4Network | ipaddress.IPv6Network

_log = logging.getLogger(__name__)
UNKNOWN_BUCKET = "unknown"
DEFAULT_IPV6_PREFIX_LEN = 64


def ipv6_prefix_len() -> int:
    """Read ``MCP_IPV6_PREFIX_LEN`` (valid 32..128); invalid values warn and use 64."""
    raw = (os.getenv("MCP_IPV6_PREFIX_LEN") or "").strip()
    if not raw:
        return DEFAULT_IPV6_PREFIX_LEN
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if not 32 <= value <= 128:
        _log.warning(
            "invalid MCP_IPV6_PREFIX_LEN %r (want 32..128); using %d",
            raw,
            DEFAULT_IPV6_PREFIX_LEN,
        )
        return DEFAULT_IPV6_PREFIX_LEN
    return value


def normalize_ip_key(addr: str | None) -> str:
    """Map an address to its per-IP limiter key, failing closed to a shared bucket.

    IPv4 is used as-is, IPv4-mapped IPv6 collapses to its IPv4 form and other
    IPv6 collapses to its network prefix so rotating addresses inside one
    prefix share a counter. Missing or unparseable input shares ``unknown``.
    """
    text = (addr or "").strip().split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return UNKNOWN_BUCKET
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/{ipv6_prefix_len()}", strict=False))
    return str(ip)


class ConcurrencyLimiter:
    """Track in-flight requests per caller and per source address under hard caps.

    Entries exist only while they have in-flight requests, so tables can never
    exceed the global cap; ``max_tracked_callers`` is an additional hard bound
    and a full table of busy entries rejects new keys instead of evicting.
    """

    def __init__(self) -> None:
        """Create empty counters (single event loop; check+increment is atomic)."""
        self.total = 0
        self.callers: dict[str, int] = {}
        self.ips: dict[str, int] = {}

    def try_acquire(
        self,
        caller_key: str,
        ip_key: str,
        *,
        max_total: int,
        max_per_caller: int,
        max_per_ip: int,
        max_tracked_callers: int,
    ) -> str | None:
        """Admit a request or return the refusal reason (``total``/``tracked``/``ip``/``caller``)."""
        if self.total >= max_total:
            return "total"
        if self.ips.get(ip_key, 0) >= max_per_ip:
            return "ip"
        if caller_key not in self.callers and len(self.callers) >= max_tracked_callers:
            return "tracked"
        if self.callers.get(caller_key, 0) >= max_per_caller:
            return "caller"
        self.total += 1
        self.ips[ip_key] = self.ips.get(ip_key, 0) + 1
        self.callers[caller_key] = self.callers.get(caller_key, 0) + 1
        return None

    def release(self, caller_key: str, ip_key: str) -> None:
        """Release a previously admitted request and drop entries that hit zero."""
        self.total = max(0, self.total - 1)
        for table, key in ((self.callers, caller_key), (self.ips, ip_key)):
            remaining = table.get(key, 1) - 1
            if remaining <= 0:
                table.pop(key, None)
            else:
                table[key] = remaining


def parse_trusted_proxies(raw: str | None) -> list[_Network]:
    """Parse a comma list of CIDRs/addresses, silently skipping invalid items."""
    nets: list[_Network] = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return nets


def _in(addr: str, nets: Iterable[_Network]) -> bool:
    """Return True when ``addr`` parses and falls in any network."""
    try:
        ip = ipaddress.ip_address(addr.strip())
    except ValueError:
        return False
    return any(ip in n for n in nets)


def client_ip(remote_addr: str | None, headers: Mapping[str, str]) -> str:
    """Resolve the normalised per-IP limiter key; trust X-Forwarded-For only from a trusted peer.

    With ``MCP_TRUSTED_PROXIES`` unset (default) the socket peer is always used.
    When the peer is trusted, the right-most untrusted hop is used. The chosen
    address is normalised by :func:`normalize_ip_key` (IPv6 grouped by prefix).
    """
    peer = remote_addr or UNKNOWN_BUCKET
    nets = parse_trusted_proxies(os.getenv("MCP_TRUSTED_PROXIES"))
    if not nets or not _in(peer, nets):
        return normalize_ip_key(peer)
    xff = headers.get("X-Forwarded-For", "")
    hops = [h.strip() for h in xff.split(",") if h.strip()]
    for hop in reversed(hops):
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            return normalize_ip_key(peer)
        if not _in(hop, nets):
            return normalize_ip_key(hop)
    return normalize_ip_key(hops[0] if hops else peer)
