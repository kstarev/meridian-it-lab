"""The test-wide network guard in conftest.py blocks anything that leaves the machine.

Addresses come from the documentation ranges (RFC 5737, RFC 3849), which are
never routed, so even a broken guard can't reach a real host.
"""

import socket

import pytest

BLOCKED = "outbound network blocked"


def test_tcp_connect_to_outside_host_is_blocked():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(RuntimeError, match=BLOCKED):
            sock.connect(("203.0.113.10", 443))
        with pytest.raises(RuntimeError, match=BLOCKED):
            sock.connect_ex(("203.0.113.10", 443))


def test_ipv6_connect_to_outside_host_is_blocked():
    with (
        socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock,
        pytest.raises(RuntimeError, match=BLOCKED),
    ):
        sock.connect(("2001:db8::10", 443, 0, 0))


def test_udp_send_to_outside_host_is_blocked():
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock,
        pytest.raises(RuntimeError, match=BLOCKED),
    ):
        sock.sendto(b"x", ("203.0.113.10", 53))


def test_dns_lookup_is_blocked():
    with pytest.raises(RuntimeError, match=BLOCKED):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(RuntimeError, match=BLOCKED):
        socket.create_connection(("example.com", 443), timeout=1)


def test_loopback_stays_allowed():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(8)
        port = server.getsockname()[1]
        for host in ("127.0.0.1", "localhost"):
            with socket.create_connection((host, port), timeout=1):
                # Accept each connection so the listen backlog never fills up.
                accepted, _ = server.accept()
                accepted.close()
