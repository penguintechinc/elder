"""SSRF guard shared by outbound-HTTP stream nodes.

Workflow authors supply arbitrary URLs to nodes like http_request and
webhook_out. Before any request is issued, guard_ssrf() rejects non-http(s)
schemes and any host that resolves to an internal/reserved address (loopback,
private, link-local, multicast, reserved, CGNAT, ipv4-mapped) — blocking the
cloud metadata endpoint 169.254.169.254 and internal services. Callers must
also disable auto-redirects so a 3xx cannot bounce past this pre-flight check.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# RFC 6598 carrier-grade NAT space is NOT flagged by ipaddress.is_private.
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")


def is_blocked_ip(ip_str: str) -> bool:
    """True if an IP is loopback/private/link-local/reserved/multicast/CGNAT.

    Blocks the whole internal/reserved space, including the cloud metadata
    endpoint 169.254.169.254 (link-local) and IPv4-mapped IPv6 forms.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable -> block (fail closed)
    if getattr(ip, "ipv4_mapped", None):
        return is_blocked_ip(str(ip.ipv4_mapped))
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip in _CGNAT_NET
    )


def guard_ssrf(url: str) -> None:
    """Reject SSRF-prone URLs before any request is issued.

    Rejects non-http(s) schemes and any host that resolves to an
    internal/reserved address. Raises ValueError on a blocked URL.

    NOTE: this validates at resolve time; a determined attacker could still
    attempt DNS-rebinding between this check and the connect. Callers disable
    auto-redirects so redirect-based SSRF is not possible. A future hardening
    pass can pin the connection to the validated IP.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"Blocked URL scheme: {parsed.scheme!r} (only http/https allowed)"
        )
    host = parsed.hostname
    if not host:
        raise ValueError("URL has no host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve host {host!r}: {exc}")
    for info in infos:
        ip_str = info[4][0]
        if is_blocked_ip(ip_str):
            raise ValueError(
                f"Blocked request to internal/reserved address {ip_str} (host {host!r})"
            )
