"""Streams HTTP SSRF guard tests (pure unit, no network).

Tests _is_blocked_ip() and _guard_ssrf() functions with monkeypatching
for DNS resolution validation.

regression: streams-ssrf-protection-phase4b
"""

import socket
from unittest.mock import patch

import pytest

from apps.worker.streams.nodes.actions.http_request import _guard_ssrf, _is_blocked_ip


class TestIsBlockedIp:
    """Test _is_blocked_ip IP blocking logic."""

    def test_loopback_ipv4_blocked(self):
        """Loopback address 127.0.0.1 is blocked."""
        assert _is_blocked_ip("127.0.0.1") is True

    def test_private_ipv4_blocked(self):
        """Private addresses 10.x.x.x are blocked."""
        assert _is_blocked_ip("10.0.0.5") is True
        assert _is_blocked_ip("10.255.255.255") is True

    def test_private_ipv4_192_blocked(self):
        """Private addresses 192.168.x.x are blocked."""
        assert _is_blocked_ip("192.168.1.10") is True
        assert _is_blocked_ip("192.168.255.255") is True

    def test_cloud_metadata_endpoint_blocked(self):
        """Cloud metadata endpoint 169.254.169.254 (link-local) is blocked."""
        assert _is_blocked_ip("169.254.169.254") is True

    def test_cgnat_blocked(self):
        """CGNAT range 100.64.0.0/10 is blocked."""
        assert _is_blocked_ip("100.64.0.1") is True
        assert _is_blocked_ip("100.127.255.255") is True

    def test_private_ipv4_172_blocked(self):
        """Private addresses 172.16.x.x to 172.31.x.x are blocked."""
        assert _is_blocked_ip("172.16.0.1") is True
        assert _is_blocked_ip("172.31.255.255") is True

    def test_loopback_ipv6_blocked(self):
        """IPv6 loopback ::1 is blocked."""
        assert _is_blocked_ip("::1") is True

    def test_private_ipv6_blocked(self):
        """IPv6 private addresses are blocked."""
        assert _is_blocked_ip("fd00::1") is True
        assert _is_blocked_ip("fc00::1") is True

    def test_link_local_ipv6_blocked(self):
        """IPv6 link-local fe80:: is blocked."""
        assert _is_blocked_ip("fe80::1") is True

    def test_multicast_ipv4_blocked(self):
        """Multicast addresses 224.0.0.0/4 are blocked."""
        assert _is_blocked_ip("224.0.0.1") is True
        assert _is_blocked_ip("239.255.255.255") is True

    def test_reserved_ipv4_blocked(self):
        """Reserved addresses are blocked."""
        assert _is_blocked_ip("0.0.0.0") is True  # unspecified
        assert _is_blocked_ip("255.255.255.255") is True  # broadcast

    def test_public_ipv4_allowed(self):
        """Public addresses are allowed."""
        assert _is_blocked_ip("8.8.8.8") is False
        assert _is_blocked_ip("1.1.1.1") is False

    def test_public_ipv6_allowed(self):
        """Public IPv6 addresses are allowed."""
        assert _is_blocked_ip("2001:4860:4860::8888") is False

    def test_unparseable_ip_blocked(self):
        """Unparseable IP strings are blocked (fail-closed)."""
        assert _is_blocked_ip("not-an-ip") is True
        assert _is_blocked_ip("") is True


class TestGuardSsrf:
    """Test _guard_ssrf URL validation."""

    def test_http_scheme_allowed(self):
        """HTTP scheme is allowed."""
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("8.8.8.8", 80),
                )
            ]
            # Should not raise
            _guard_ssrf("http://example.com")

    def test_https_scheme_allowed(self):
        """HTTPS scheme is allowed."""
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("8.8.8.8", 443),
                )
            ]
            # Should not raise
            _guard_ssrf("https://example.com")

    def test_file_scheme_blocked(self):
        """file:// scheme is blocked."""
        with pytest.raises(ValueError, match="Blocked URL scheme"):
            _guard_ssrf("file:///etc/passwd")

    def test_ftp_scheme_blocked(self):
        """ftp:// scheme is blocked."""
        with pytest.raises(ValueError, match="Blocked URL scheme"):
            _guard_ssrf("ftp://ftp.example.com")

    def test_data_scheme_blocked(self):
        """data: scheme is blocked."""
        with pytest.raises(ValueError, match="Blocked URL scheme"):
            _guard_ssrf("data:text/html,<html></html>")

    def test_gopher_scheme_blocked(self):
        """gopher:// scheme is blocked."""
        with pytest.raises(ValueError, match="Blocked URL scheme"):
            _guard_ssrf("gopher://example.com")

    def test_no_host_blocked(self):
        """URL without host is blocked."""
        with pytest.raises(ValueError, match="URL has no host"):
            _guard_ssrf("http://")

    def test_dns_resolution_to_localhost_blocked(self):
        """Host resolving to 127.0.0.1 is blocked."""
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            # Mock getaddrinfo to return loopback
            mock_getaddrinfo.return_value = [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("127.0.0.1", 80),
                )
            ]

            with pytest.raises(
                ValueError, match="Blocked request to internal/reserved address"
            ):
                _guard_ssrf("http://localhost")

    def test_dns_resolution_to_private_ip_blocked(self):
        """Host resolving to 10.x.x.x is blocked."""
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("10.0.0.1", 80),
                )
            ]

            with pytest.raises(
                ValueError, match="Blocked request to internal/reserved address"
            ):
                _guard_ssrf("http://internal.local")

    def test_dns_resolution_to_cloud_metadata_blocked(self):
        """Host resolving to 169.254.169.254 (cloud metadata) is blocked."""
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("169.254.169.254", 80),
                )
            ]

            with pytest.raises(
                ValueError, match="Blocked request to internal/reserved address"
            ):
                _guard_ssrf("http://169.254.169.254")

    def test_dns_resolution_to_cgnat_blocked(self):
        """Host resolving to CGNAT 100.64.0.0/10 is blocked."""
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("100.64.0.1", 80),
                )
            ]

            with pytest.raises(
                ValueError, match="Blocked request to internal/reserved address"
            ):
                _guard_ssrf("http://cgnat.example.com")

    def test_dns_resolution_to_public_ip_allowed(self):
        """Host resolving to 8.8.8.8 is allowed."""
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("8.8.8.8", 80),
                )
            ]
            # Should not raise
            _guard_ssrf("http://google-dns.example.com")

    def test_dns_resolution_to_public_ipv6_allowed(self):
        """Host resolving to public IPv6 is allowed."""
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (
                    socket.AF_INET6,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("2001:4860:4860::8888", 443),
                )
            ]
            # Should not raise
            _guard_ssrf("https://ipv6.example.com")

    def test_dns_failure_raises_error(self):
        """DNS resolution failure raises error."""
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.side_effect = socket.gaierror("Name or service not known")

            with pytest.raises(ValueError, match="Cannot resolve host"):
                _guard_ssrf("http://nonexistent.invalid")

    def test_multiple_dns_results_all_checked(self):
        """Multiple DNS results: all must be safe."""
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            # First result is safe, second is blocked (common with dual-stack)
            mock_getaddrinfo.return_value = [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("8.8.8.8", 80),
                ),
                (
                    socket.AF_INET6,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("127.0.0.1", 80),
                ),
            ]

            # Should fail because one result is blocked
            with pytest.raises(
                ValueError, match="Blocked request to internal/reserved address"
            ):
                _guard_ssrf("http://example.com")

    def test_http_url_default_port_80(self):
        """HTTP URLs default to port 80 if not specified."""
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("8.8.8.8", 80),
                )
            ]
            _guard_ssrf("http://example.com")
            # Verify getaddrinfo was called with port 80
            args = mock_getaddrinfo.call_args
            assert args[0][1] == 80

    def test_https_url_default_port_443(self):
        """HTTPS URLs default to port 443 if not specified."""
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("8.8.8.8", 443),
                )
            ]
            _guard_ssrf("https://example.com")
            # Verify getaddrinfo was called with port 443
            args = mock_getaddrinfo.call_args
            assert args[0][1] == 443

    def test_explicit_port_used(self):
        """Explicit ports in URL are used."""
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("8.8.8.8", 8080),
                )
            ]
            _guard_ssrf("http://example.com:8080")
            # Verify getaddrinfo was called with port 8080
            args = mock_getaddrinfo.call_args
            assert args[0][1] == 8080

    def test_url_with_path_and_query(self):
        """URL with path and query is parsed correctly."""
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("8.8.8.8", 80),
                )
            ]
            # Should parse host correctly
            _guard_ssrf("http://example.com/path?query=value")
            # Verify getaddrinfo was called with correct host
            args = mock_getaddrinfo.call_args
            assert args[0][0] == "example.com"
