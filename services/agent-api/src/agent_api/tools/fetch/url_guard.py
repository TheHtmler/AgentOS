from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

from agent_api.tools.fetch.types import FetchProviderError

_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata",
    }
)

_MAX_REDIRECTS = 5
_MAX_RESPONSE_BYTES = 2_000_000


class UnsafeUrlError(ValueError):
    """URL failed SSRF policy checks."""


def assert_public_http_url(url: str) -> str:
    """Validate scheme/host and resolve DNS to reject private targets."""

    normalized = url.strip()
    if not normalized:
        raise UnsafeUrlError("url must not be blank")

    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrlError("only http and https URLs are allowed")

    if not parsed.hostname:
        raise UnsafeUrlError("url must include a hostname")

    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in _BLOCKED_HOSTS or hostname.endswith(".localhost"):
        raise UnsafeUrlError("hostname is not allowed")

    if parsed.username or parsed.password:
        raise UnsafeUrlError("urls with embedded credentials are not allowed")

    _assert_hostname_resolves_public(hostname)
    return normalized


def _assert_hostname_resolves_public(hostname: str) -> None:
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None

    if literal is not None:
        if not _is_public_ip(literal):
            raise UnsafeUrlError("ip address is not publicly routable")
        return

    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"unable to resolve hostname: {hostname}") from exc

    if not infos:
        raise UnsafeUrlError(f"unable to resolve hostname: {hostname}")

    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip_text = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            continue
        if not _is_public_ip(ip):
            raise UnsafeUrlError("hostname resolves to a non-public address")


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def get_public_url(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: float,
    max_bytes: int = _MAX_RESPONSE_BYTES,
) -> httpx.Response:
    """GET a URL with redirect re-validation and response size capping."""

    current = assert_public_http_url(url)

    for _ in range(_MAX_REDIRECTS + 1):
        try:
            response = await client.get(
                current,
                timeout=timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise FetchProviderError(
                "request timed out",
                provider="local",
                recoverable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise FetchProviderError(
                f"transport error: {exc}",
                provider="local",
                recoverable=True,
            ) from exc

        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                raise FetchProviderError(
                    "redirect missing location",
                    provider="local",
                    recoverable=True,
                )
            current = assert_public_http_url(urljoin(current, location))
            continue

        if response.status_code >= 400:
            raise FetchProviderError(
                f"HTTP {response.status_code}",
                provider="local",
                recoverable=True,
            )

        if len(response.content) > max_bytes:
            raise FetchProviderError(
                "response body exceeds size limit",
                provider="local",
                recoverable=True,
            )

        return response

    raise FetchProviderError(
        "too many redirects",
        provider="local",
        recoverable=True,
    )
