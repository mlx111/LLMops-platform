import ipaddress
import os
import socket
from urllib.parse import urlparse

from requests.adapters import HTTPAdapter


def _target_allowlist() -> set[str]:
    """Hostnames explicitly trusted as evaluation targets (comma-separated env var).

    Lets the platform reach internal/self-hosted agents (e.g. host.docker.internal)
    without weakening SSRF protection for arbitrary external URLs.
    """
    raw = os.getenv("LLMOPS_TARGET_ALLOWLIST", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _is_allowed_host(host: str) -> bool:
    return host.strip().lower() in _target_allowlist()


def validate_target_url(url: str | None) -> None:
    """Validate a target URL and reject localhost/internal destinations.

    Hosts listed in LLMOPS_TARGET_ALLOWLIST bypass the internal-IP block so the
    platform can evaluate trusted self-hosted agents on the internal network.
    """
    if not url:
        return

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: '{parsed.scheme}'")

    host = parsed.hostname or ""
    if _is_allowed_host(host):
        return

    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        raise ValueError("Localhost URLs are not allowed")

    _validate_host(host)


def _validate_host(host: str) -> None:
    try:
        ip = ipaddress.ip_address(host)
        _raise_for_internal_ip(ip, "Target URL points to a private/internal IP")
        return
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return

    resolved_any = False
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        resolved_host = sockaddr[0]
        try:
            ip = ipaddress.ip_address(resolved_host)
        except ValueError:
            continue
        resolved_any = True
        _raise_for_internal_ip(ip, "Target URL resolved to a private/internal IP")

    if not resolved_any:
        return


def _raise_for_internal_ip(ip: ipaddress._BaseAddress, message: str) -> None:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise ValueError(message)


def _resolve_one(host: str) -> str:
    """Resolve a hostname to a single IP address (preferring IPv4)."""
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    except (socket.gaierror, IndexError):
        try:
            return socket.getaddrinfo(host, None, socket.AF_INET6, socket.SOCK_STREAM)[0][4][0]
        except (socket.gaierror, IndexError):
            raise ValueError(f"Cannot resolve hostname: {host}")


def resolve_and_validate(url: str) -> tuple[str, str]:
    """Resolve a URL's hostname, validate that all resolved IPs are
    non-internal, and return (resolved_ip, original_hostname).

    Raises ValueError if the hostname resolves to an internal/private IP
    or cannot be resolved.

    This pins the hostname to a specific IP to prevent DNS rebinding attacks.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""

    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        raise ValueError("Localhost URLs are not allowed")

    addrinfos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    resolved_any = False
    for info in addrinfos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        candidate = sockaddr[0]
        try:
            ip = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        resolved_any = True
        _raise_for_internal_ip(
            ip, f"Blocked request to internal/private IP: {candidate}"
        )

    if not resolved_any:
        raise ValueError(f"Could not resolve {host} to a valid IP")

    return _resolve_one(host), host


class ValidatingHTTPAdapter(HTTPAdapter):
    """A requests TransportAdapter that resolves DNS and validates the resolved
    IP before establishing a TCP connection, preventing DNS rebinding /
    TOCTOU attacks on SSRF validation.

    After validation, the hostname in the URL is replaced with the resolved IP
    and the original hostname is set via the Host header, so the connection
    goes to the validated IP only.
    """

    def send(self, request, **kwargs):
        host = urlparse(request.url).hostname or ""
        if _is_allowed_host(host):
            # Trusted internal target: connect to the hostname directly without
            # DNS-rebinding IP pinning (the hostname may be a Docker alias).
            return super().send(request, **kwargs)
        resolved_ip, original_host = resolve_and_validate(request.url)
        request.headers["Host"] = original_host
        request.url = request.url.replace(f"://{original_host}", f"://{resolved_ip}", 1)
        return super().send(request, **kwargs)
