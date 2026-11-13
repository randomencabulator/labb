"""URLs the browser may go to: http(s) only and, unless it's run on someone's own machine, no
private addresses. A model decides where the browser goes, so it must not become a way into the
network it runs in (the router's admin page, the labb server, cloud metadata)."""

import ipaddress
import socket
import time
from functools import lru_cache
from urllib.parse import urlsplit

# How long a host's addresses are trusted
RESOLVE_SECONDS = 300


class Refused(ValueError):
    """A URL the browser doesn't go to; the message is for the model"""


def normalize(raw: str) -> str:
    """A URL to open: "example.com/a" becomes "https://example.com/a" """

    text = raw.strip()
    if not text:
        raise Refused("Give the page's URL")
    if text == "about:blank":
        return text
    if "://" not in text and not text.startswith(("http:", "https:")):
        if text.split(":", 1)[0].lower() in ("file", "chrome", "javascript", "data", "view-source", "about", "blob"):
            raise Refused("Only http and https addresses can be opened")
        text = f"{'http' if _is_local_host(text) else 'https'}://{text}"
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Refused("Only http and https addresses can be opened")
    return text


def _is_local_host(text: str) -> bool:
    """Whether an address typed without a scheme is on this machine or network, as a dev server
    prints it ("0.0.0.0:3000", "[::1]:5173"): those speak plain http"""

    try:
        host = (urlsplit(f"//{text}").hostname or "").lower()
    except ValueError:
        return False
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


def _private(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


@lru_cache(maxsize=1024)
def _addresses(host: str, bucket: int) -> tuple[str, ...]:
    # `bucket` changes every RESOLVE_SECONDS, so a cached answer expires
    return tuple(sorted({info[4][0] for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}))


def is_private(url: str) -> bool:
    """Whether the URL's host is, or resolves to, a private, local or reserved address"""

    host = (urlsplit(url).hostname or "").strip("[]")
    if not host:
        return True
    try:
        ipaddress.ip_address(host)
        addresses = (host,)
    except ValueError:
        try:
            addresses = _addresses(host, int(time.time() // RESOLVE_SECONDS))
        except OSError:
            # The browser will say it can't find the site; a host that doesn't resolve reaches nothing
            return False
    return not addresses or any(_private(a) for a in addresses)


def site(url: str) -> str:
    """The site a URL is on, for asking about and remembering: its host without www."""

    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")
