"""The loopback-only guard used by every test and by stub and Ollama eval runs."""

import socket
from pathlib import Path

import pytest

from meridian_dataset import DEFAULT_SEED
from tier0.evals import __main__ as cli
from tier0.netguard import OutboundNetworkBlocked, is_loopback_url, loopback_only

BLOCKED = "outbound network blocked"


def test_blocks_outside_hosts_and_dns() -> None:
    with loopback_only():
        with (
            socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
            pytest.raises(OutboundNetworkBlocked, match=BLOCKED),
        ):
            sock.connect(("203.0.113.10", 443))
        with pytest.raises(OutboundNetworkBlocked, match=BLOCKED):
            socket.getaddrinfo("example.com", 443)


def test_allows_loopback() -> None:
    with loopback_only(), socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(8)
        port = server.getsockname()[1]
        with socket.create_connection(("localhost", port), timeout=1):
            accepted, _ = server.accept()
            accepted.close()


def test_restores_the_previous_socket_functions() -> None:
    before = (socket.socket.connect, socket.socket.sendto, socket.getaddrinfo)
    with loopback_only():
        assert socket.socket.connect is not before[0]
    assert (socket.socket.connect, socket.socket.sendto, socket.getaddrinfo) == before


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("http://localhost:11434", True),
        ("http://127.0.0.1:11434", True),
        ("http://[::1]:11434", True),
        ("http://ollama.example:11434", False),
        ("https://192.168.1.5", False),
        ("not a url", False),
    ],
)
def test_is_loopback_url(url: str, local: bool) -> None:
    assert is_loopback_url(url) is local


def test_eval_refuses_an_ollama_url_off_this_machine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TIER0_OLLAMA_URL", "http://ollama.example:11434")
    args = ["--seed", str(DEFAULT_SEED), "--k", "1", "--model", "ollama", "--out", str(tmp_path)]
    assert cli.main(args) == 2
    assert "must be on this machine" in capsys.readouterr().err
    assert not (tmp_path / "report.md").exists()
