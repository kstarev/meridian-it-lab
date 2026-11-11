"""Keep a run on this machine, or on one named API host: block anything else.

The lab uses fake data only and must never touch a real tenant. Tests run inside
this guard (root conftest.py), and so does every eval run with the stub or a local
Ollama model. While `loopback_only()` is active, any outbound connection, UDP send
or DNS lookup for a host other than this machine raises OutboundNetworkBlocked.
Loopback and Unix sockets stay allowed, so a local Ollama server, in-process
servers and the asyncio event loop keep working.

A Claude eval run uses `allow_only_hosts("api.anthropic.com")` instead: DNS lookups for
any other name, and connections to any address that lookup didn't return (loopback
included), raise OutboundNetworkBlocked. `guard_for_model()` picks the right guard.
"""

import ipaddress
import socket
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
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


ANTHROPIC_API_HOST = "api.anthropic.com"


@contextmanager
def allow_only_hosts(*hosts: str) -> Iterator[None]:
    """Allow only DNS lookups of `hosts` and connections to the addresses they resolve
    to. Everything else, this machine included, is blocked."""
    allowed_names = {h.lower() for h in hosts}
    allowed_ips: set[str] = set()
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_sendto = socket.socket.sendto
    real_getaddrinfo = socket.getaddrinfo
    only = ", ".join(sorted(allowed_names))

    def check(family: int, address: object) -> None:
        if family not in (socket.AF_INET, socket.AF_INET6):
            return  # Unix sockets and socket pairs never leave the machine.
        host = address[0] if isinstance(address, tuple) else address
        if str(host) not in allowed_ips:
            raise OutboundNetworkBlocked(
                f"outbound network blocked: {address!r} (only {only} is allowed)"
            )

    def connect(self, address):
        check(self.family, address)
        return real_connect(self, address)

    def connect_ex(self, address):
        check(self.family, address)
        return real_connect_ex(self, address)

    def sendto(self, data, *args):
        check(self.family, args[-1])
        return real_sendto(self, data, *args)

    def getaddrinfo(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else str(host or "")
        if name.lower() not in allowed_names:
            raise OutboundNetworkBlocked(
                f"outbound network blocked: DNS lookup {host!r} (only {only} is allowed)"
            )
        results = real_getaddrinfo(host, *args, **kwargs)
        for _family, _type, _proto, _canon, sockaddr in results:
            allowed_ips.add(str(sockaddr[0]))
        return results

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


def guard_for_model(model: str) -> AbstractContextManager[None]:
    """The network guard for an eval run: Claude may reach only its API host; the stub
    and Ollama stay on this machine."""
    if model == "claude":
        return allow_only_hosts(ANTHROPIC_API_HOST)
    return loopback_only()
