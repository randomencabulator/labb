"""HTTP requests to addresses a model or a person gave, without reaching into the network labb runs
in (the router's admin page, Ollama, the server's own services, cloud metadata at 169.254.169.254).

Checking a name and then letting the HTTP client look it up again isn't enough: a name can answer a
public address for the check and a private one for the request (DNS rebinding). So `pin` looks the
name up once, refuses it if any of its addresses isn't public, and gives back the request aimed at
that very address, with the name kept for the Host header and for TLS (SNI and the certificate
check). Each redirect goes through `pin` again.

Behind an HTTP proxy (HTTPS_PROXY and the like, which httpx follows), the proxy looks the name up
and connects, so the request can't be pinned: the address is still checked, and the URL is left as
it is.
"""

import ipaddress
import socket
import urllib.request
from urllib.parse import urljoin, urlsplit

import httpx

MAX_REDIRECTS = 5


class Blocked(ValueError):
    """An address that isn't public, or isn't an http(s) URL; the message is for whoever gave it"""


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def pin(url: str, allow_private: bool = False) -> tuple[str, dict, dict]:
    """(the URL to request, headers to add, the request's extensions): aimed at the address the name
    has now, which has to be public unless allow_private"""

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Blocked(f"{url} isn't an http(s) address")
    if allow_private:
        return url, {}, {}
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as e:
        raise Blocked(f"Can't find {host}: {e}") from e
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses or not all(is_public(a) for a in addresses):
        raise Blocked(f"{host} is a private or local address")
    if _proxied(parts.scheme, host):
        return url, {}, {}
    target = str(httpx.URL(url).copy_with(host=addresses[0].split("%")[0]))
    headers = {"Host": parts.netloc.rpartition("@")[2]}
    extensions = {"sni_hostname": host} if parts.scheme == "https" else {}
    return target, headers, extensions


def _proxied(scheme: str, host: str) -> bool:
    return scheme in urllib.request.getproxies() and not urllib.request.proxy_bypass(host)


def get(url: str, *, timeout: float = 20, headers: dict | None = None, max_bytes: int | None = None,
        allow_private: bool = False) -> tuple[httpx.Response, str]:
    """A GET that follows redirects itself, pinning each hop: (the answer, read, and the URL it
    finally came from). Over max_bytes, raises Blocked."""

    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            target, extra, extensions = pin(url, allow_private)
            with client.stream("GET", target, headers={**(headers or {}), **extra}, extensions=extensions) as response:
                if response.is_redirect and "location" in response.headers:
                    url = urljoin(url, response.headers["location"])
                    continue
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if max_bytes is not None and len(body) > max_bytes:
                        raise Blocked(f"{url}: the answer is over {max_bytes // 1_000_000} MB")
                # The body as read, so .text and .json() work after the stream is closed
                response._content = body
                return response, url
    raise Blocked(f"{url}: too many redirects")
