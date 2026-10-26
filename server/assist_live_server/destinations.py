"""Check destination addresses before a connection can send content."""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from typing import Literal

from aiohttp.abc import AbstractResolver, ResolveResult
from yarl import URL

type Destination = Literal["local", "cloud"]
type Resolver = Callable[[str, int], Awaitable[tuple[str, ...]]]

# These are standard policy ranges, not deployment addresses. Integer octets
# follow the public-tree checker's convention for private network definitions.
_LOCAL_NETWORKS = (
    ipaddress.IPv4Network((10 << 24, 8)),  # RFC 1918 private class A.
    ipaddress.IPv4Network(((172 << 24) | (16 << 16), 12)),  # RFC 1918 private class B.
    ipaddress.IPv4Network(((192 << 24) | (168 << 16), 16)),  # RFC 1918 private class C.
    ipaddress.IPv4Network(((169 << 24) | (254 << 16), 16)),  # RFC 3927 link-local.
    ipaddress.IPv4Network((127 << 24, 8)),  # RFC 1122 loopback.
    ipaddress.IPv6Network((0xFC00 << 112, 7)),  # RFC 4193 unique-local.
    ipaddress.IPv6Network((0xFE80 << 112, 10)),  # RFC 4291 link-local.
    ipaddress.IPv6Network((1, 128)),  # RFC 4291 loopback.
)


def classify_address(address: str) -> Destination:
    ip = ipaddress.ip_address(address)
    return "local" if any(ip in network for network in _LOCAL_NETWORKS) else "cloud"


def require_destination(destination: str, local_only: bool) -> None:
    if destination not in {"local", "cloud"} or (local_only and destination != "local"):
        raise ValueError("destination refused by local only policy")


def speaker_url(value: str) -> URL:
    url = URL(value)
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.user is not None
        or url.query_string
        or url.fragment
        or "%" in url.host
    ):
        raise ValueError("invalid speaker URL")
    _ = url.port
    return url


async def resolve_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        return (str(ipaddress.ip_address(host)),)
    except ValueError:
        pass
    rows = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    )
    return tuple(dict.fromkeys(str(row[4][0]) for row in rows))


async def checked_addresses(
    url: URL, *, local_only: bool, resolver: Resolver = resolve_addresses
) -> tuple[tuple[str, ...], Destination]:
    assert url.host is not None and url.port is not None
    async with asyncio.timeout(5):
        addresses = await resolver(url.host, url.port)
    if not addresses:
        raise ValueError("speaker destination has no addresses")
    destinations = [classify_address(address) for address in addresses]
    destination: Destination = "cloud" if "cloud" in destinations else "local"
    require_destination(destination, local_only)
    return addresses, destination


class PinnedResolver(AbstractResolver):
    """Use only checked addresses and keep the original TLS and HTTP names."""

    def __init__(self, host: str, addresses: tuple[str, ...]) -> None:
        self.host = host
        self.addresses = addresses

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[ResolveResult]:
        if host != self.host:
            raise ValueError("unexpected destination host")
        return [
            ResolveResult(
                hostname=host,
                host=address,
                port=port,
                family=socket.AF_INET6 if ":" in address else socket.AF_INET,
                proto=socket.IPPROTO_TCP,
                flags=socket.AI_NUMERICHOST,
            )
            for address in self.addresses
        ]

    async def close(self) -> None:
        pass
