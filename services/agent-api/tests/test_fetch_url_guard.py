from unittest.mock import patch

import pytest

from agent_api.tools.fetch.url_guard import (
    UnsafeUrlError,
    assert_public_http_url,
    assert_remote_fetch_url,
)


def test_assert_public_http_url_accepts_example() -> None:
    assert assert_public_http_url("https://example.com/path") == "https://example.com/path"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/admin",
        "http://192.168.1.10/",
        "http://10.0.0.5/",
        "file:///etc/passwd",
        "ftp://example.com/a",
        "",
    ],
)
def test_assert_public_http_url_rejects_unsafe(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        assert_public_http_url(url)


def test_remote_fetch_allows_hostname_even_if_local_dns_is_poisoned() -> None:
    with patch(
        "agent_api.tools.fetch.url_guard.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("127.0.0.1", 0))],
    ):
        assert assert_remote_fetch_url("https://github.com/TheHtmler/AgentOS") == (
            "https://github.com/TheHtmler/AgentOS"
        )
        with pytest.raises(UnsafeUrlError, match="non-public"):
            assert_public_http_url("https://github.com/TheHtmler/AgentOS")


def test_remote_fetch_still_blocks_literal_private_ip() -> None:
    with pytest.raises(UnsafeUrlError):
        assert_remote_fetch_url("http://127.0.0.1/secret")
