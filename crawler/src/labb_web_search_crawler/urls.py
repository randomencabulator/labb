"""URLs: accepting "example.com" as well as full URLs, and refusing private addresses.

A model decides what gets fetched, so the crawler must not become a way into the network it
runs in (the router's admin page, the labb server, cloud metadata at 169.254.169.254).
Every host is resolved and checked before it's fetched, redirects and browser requests too,
unless ALLOW_PRIVATE is set (for a crawler that's meant to read an intranet).
"""

import ipaddress
import os
import socket
import time
import urllib.request
from functools import lru_cache
from urllib.parse import urlsplit, urlunsplit

import httpx

ALLOW_PRIVATE = os.getenv("ALLOW_PRIVATE", "").lower() in ("1", "true", "yes")
# How long a host's addresses are trusted
RESOLVE_SECONDS = 300


class BadUrl(ValueError):
    """Not a URL the crawler reads"""


class Blocked(ValueError):
    """A URL on a private or local address"""


def normalize(raw: str) -> str:
    """An http(s) URL without its fragment; "example.com/a" becomes "https://example.com/a" """

    text = raw.strip()
    if not text:
        raise BadUrl("No URL")
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https"):
        raise BadUrl(f"Only http and https URLs can be read, not {parts.scheme}")
    try:
        parts.port
    except ValueError:
        raise BadUrl(f"Not a URL: {raw}")
    if not parts.hostname:
        raise BadUrl(f"Not a URL: {raw}")
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))


def _private(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


@lru_cache(maxsize=1024)
def _addresses(host: str, bucket: int) -> tuple[str, ...]:
    # `bucket` changes every RESOLVE_SECONDS, so a cached answer expires
    return tuple(sorted({info[4][0] for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}))


def resolve(host: str) -> tuple[str, ...]:
    return _addresses(host, int(time.time() // RESOLVE_SECONDS))


def check(url: str):
    """Raise Blocked if the URL's host is (or resolves to) a private, local or reserved address"""

    if ALLOW_PRIVATE:
        return
    host = (urlsplit(url).hostname or "").strip("[]")
    if not host:
        raise BadUrl(f"Not a URL: {url}")
    try:
        addresses = (host,) if _is_ip(host) else resolve(host)
    except OSError:
        raise BadUrl(f"Can't find the site {host}")
    if not addresses or any(_private(a) for a in addresses):
        raise Blocked(f"{host} is a private or local address, which the crawler doesn't read")


def pin(url: str) -> tuple[str, dict, dict]:
    """check, and the request aimed at the address that was checked: (URL, headers to add, the
    request's extensions), so the name can't answer another address when it's fetched (DNS
    rebinding). The Host header and TLS (SNI, the certificate) keep the name. Unchanged with
    ALLOW_PRIVATE, or behind a proxy (which looks the name up itself)."""

    check(url)
    parts = urlsplit(url)
    host = (parts.hostname or "").strip("[]")
    if ALLOW_PRIVATE or _is_ip(host) or (parts.scheme in urllib.request.getproxies() and not urllib.request.proxy_bypass(host)):
        return url, {}, {}
    address = resolve(host)[0].split("%")[0]
    target = str(httpx.URL(url).copy_with(host=address))
    extensions = {"sni_hostname": host} if parts.scheme == "https" else {}
    return target, {"Host": parts.netloc.rpartition("@")[2]}, extensions


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False
