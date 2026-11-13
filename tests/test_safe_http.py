"""safe_http: requests to addresses that were given, aimed at the address that was checked."""

import socket

import httpx
import pytest

from labb import safe_http


@pytest.fixture(autouse=True)
def no_proxy(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


def answers(monkeypatch, *addresses_per_lookup):
    """getaddrinfo answering each lookup with the next list of addresses"""
    lookups = list(addresses_per_lookup)

    def getaddrinfo(host, port, *args, **kwargs):
        found = lookups.pop(0) if len(lookups) > 1 else lookups[0]
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in found]

    monkeypatch.setattr(safe_http.socket, "getaddrinfo", getaddrinfo)


def test_private_and_local_addresses_are_refused(monkeypatch):
    for address in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "::ffff:10.0.0.1", "fd00::1", "100.64.0.1"):
        answers(monkeypatch, [address])
        with pytest.raises(safe_http.Blocked, match="private or local"):
            safe_http.pin("http://example.com/")
    # One private address among public ones is enough
    answers(monkeypatch, ["93.184.215.14", "10.0.0.5"])
    with pytest.raises(safe_http.Blocked):
        safe_http.pin("https://example.com/")
    for url in ("ftp://example.com/", "file:///etc/passwd", "http:///nohost"):
        with pytest.raises(safe_http.Blocked):
            safe_http.pin(url)


def test_the_request_goes_to_the_address_that_was_checked(monkeypatch):
    # A name that answers a public address first and a private one after (DNS rebinding)
    answers(monkeypatch, ["93.184.215.14"], ["127.0.0.1"])
    target, headers, extensions = safe_http.pin("https://example.com:8443/a?b=1")
    assert target == "https://93.184.215.14:8443/a?b=1"
    assert headers == {"Host": "example.com:8443"} and extensions == {"sni_hostname": "example.com"}
    answers(monkeypatch, ["2606:2800:21f:cb07:6820:80da:af6b:8b2c"])
    target, headers, extensions = safe_http.pin("http://user:pw@example.com/x")
    assert target.startswith("http://user:pw@[2606:2800:21f:cb07:6820:80da:af6b:8b2c]/x") and headers == {"Host": "example.com"}
    assert extensions == {}


def test_allowing_private_addresses_leaves_the_url_as_it_is(monkeypatch):
    answers(monkeypatch, ["10.0.0.5"])
    assert safe_http.pin("http://nas.lan/x", allow_private=True) == ("http://nas.lan/x", {}, {})


def test_get_checks_every_redirect(monkeypatch):
    answers(monkeypatch, ["93.184.215.14"])
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers["host"]))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "http://inside.example/secret"})
        return httpx.Response(200, text="fine")

    real_client = httpx.Client
    monkeypatch.setattr(safe_http.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    response, url = safe_http.get("http://example.com/plain")
    assert response.text == "fine" and url == "http://example.com/plain"
    assert seen[-1] == ("http://93.184.215.14/plain", "example.com")

    def lookup(host, port, *args, **kwargs):
        address = "10.1.2.3" if host == "inside.example" else "93.184.215.14"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    monkeypatch.setattr(safe_http.socket, "getaddrinfo", lookup)
    with pytest.raises(safe_http.Blocked, match="inside.example"):
        safe_http.get("http://example.com/start")


def test_behind_a_proxy_the_address_is_checked_and_the_url_kept(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.lan:3128")
    answers(monkeypatch, ["93.184.215.14"])
    assert safe_http.pin("https://example.com/a") == ("https://example.com/a", {}, {})
    answers(monkeypatch, ["10.0.0.5"])
    with pytest.raises(safe_http.Blocked):
        safe_http.pin("https://example.com/a")
