"""Test-wide guard: no test can reach the network outside this machine.

The lab uses fake data only and must never touch a real tenant. This fixture
enforces that in code: any outbound connection, UDP send or DNS lookup raises
OutboundNetworkBlocked. Loopback and Unix sockets stay allowed, so in-process
servers and the asyncio event loop keep working.
"""

import ipaddress
import socket

import pytest


class OutboundNetworkBlocked(RuntimeError):
    """Raised when a test tries to reach the network outside this machine."""


_LOCAL_HOSTNAMES = {"", "localhost", "localhost.localdomain"}


def _is_local_host(host: object) -> bool:
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


def _check_address(family: int, address: object) -> None:
    if family not in (socket.AF_INET, socket.AF_INET6):
        return  # Unix sockets and socket pairs never leave the machine.
    host = address[0] if isinstance(address, tuple) else address
    if not _is_local_host(host):
        raise OutboundNetworkBlocked(f"outbound network blocked in tests: {address!r}")


@pytest.fixture(autouse=True)
def _block_outbound_network(monkeypatch: pytest.MonkeyPatch) -> None:
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
        if host is not None and not _is_local_host(host):
            raise OutboundNetworkBlocked(f"outbound network blocked in tests: DNS lookup {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket.socket, "sendto", sendto)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
