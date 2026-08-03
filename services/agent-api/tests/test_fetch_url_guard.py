import pytest

from agent_api.tools.fetch.url_guard import UnsafeUrlError, assert_public_http_url


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
