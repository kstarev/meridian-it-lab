"""Keep a run on this machine: block anything that would leave it.

The lab uses fake data only and must never touch a real tenant. Tests run inside
this guard (root conftest.py), and so does every eval run with the stub or a local
Ollama model. While `loopback_only()` is active, any outbound connection, UDP send
or DNS lookup for a host other than this machine raises OutboundNetworkBlocked.
Loopback and Unix sockets stay allowed, so a local Ollama server, in-process
servers and the asyncio event loop keep working.
"""

import ipaddress
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlsplit


class OutboundNetworkBlocked(RuntimeError):
    """Raised when code tries to reach the network outside this machine."""


_LOCAL_HOSTNAMES = {"", "localhost", "localhost.localdomain"}


def is_local_host(host: object) -> bool:
    """True for localhost names and loopback addresses (127.0.0.0/8, ::1)."""
    if isinstance(host, bytes):
        host = host.decode()
    name = str(host).strip("[]").lower()
    if name in _LOCAL_HOSTNAMES:
        return True
    try:
        # Drop an IPv6 zone suffix such as "fe80::1%en0" before parsing.
        return ipaddress.ip_address(name.split("%")[0]).is_loopback
    except ValueError:
        return False


def is_loopback_url(url: str) -> bool:
    """True when the URL's host is this machine, e.g. http://localhost:11434."""
    host = urlsplit(url).hostname
    return host is not None and is_local_host(host)


def _check_address(family: int, address: object) -> None:
    if family not in (socket.AF_INET, socket.AF_INET6):
        return  # Unix sockets and socket pairs never leave the machine.
    host = address[0] if isinstance(address, tuple) else address
    if not is_local_host(host):
        raise OutboundNetworkBlocked(f"outbound network blocked: {address!r}")


@contextmanager
def loopback_only() -> Iterator[None]:
    """Allow only this machine for the duration of the block, then restore."""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_sendto = socket.socket.sendto
    real_getaddrinfo = socket.getaddrinfo

    def connect(self, address):
        _check_address(self.family, address)
        return real_connect(self, address)

    def connect_ex(self, address):
        _check_address(self.family, address)
        return real_connect_ex(self, address)

    def sendto(self, data, *args):
        # sendto(data, address) or sendto(data, flags, address)
        _check_address(self.family, args[-1])
        return real_sendto(self, data, *args)

    def getaddrinfo(host, *args, **kwargs):
        if host is not None and not is_local_host(host):
            raise OutboundNetworkBlocked(f"outbound network blocked: DNS lookup {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = sendto
    socket.getaddrinfo = getaddrinfo
    try:
        yield
    finally:
        socket.socket.connect = real_connect
        socket.socket.connect_ex = real_connect_ex
        socket.socket.sendto = real_sendto
        socket.getaddrinfo = real_getaddrinfo
