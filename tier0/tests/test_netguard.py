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


# --- Claude runs: only api.anthropic.com -------------------------------------------


def test_a_claude_run_may_look_up_only_the_api_host() -> None:
    import socket

    from tier0.netguard import allow_only_hosts

    with allow_only_hosts("api.anthropic.com"):
        with pytest.raises(OutboundNetworkBlocked, match=r"only api\.anthropic\.com is allowed"):
            socket.getaddrinfo("example.com", 443)
        with pytest.raises(OutboundNetworkBlocked, match=r"only api\.anthropic\.com is allowed"):
            socket.getaddrinfo("localhost", 11434)  # not even a local Ollama


def test_a_claude_run_may_connect_only_to_what_the_api_host_resolved_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    from tier0.netguard import allow_only_hosts

    def fake_resolve(host, *args, **kwargs):  # no real DNS in tests
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.5", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_resolve)
    with allow_only_hosts("api.anthropic.com"), socket.socket() as sock:
        socket.getaddrinfo("api.anthropic.com", 443)
        with pytest.raises(OutboundNetworkBlocked, match=r"only api\.anthropic\.com"):
            sock.connect(("198.51.100.7", 443))  # an address nobody resolved
        with pytest.raises(OutboundNetworkBlocked, match=r"only api\.anthropic\.com"):
            sock.connect(("127.0.0.1", 11434))  # loopback is blocked too
        # The resolved address passes this guard; the test suite's own loopback guard
        # underneath then stops it, so nothing leaves the machine here.
        with pytest.raises(OutboundNetworkBlocked) as stopped:
            sock.connect(("203.0.113.5", 443))
        assert "only api.anthropic.com" not in str(stopped.value)


def test_an_ollama_run_cannot_reach_the_claude_api() -> None:
    import socket

    from tier0.netguard import guard_for_model, loopback_only

    with loopback_only(), pytest.raises(OutboundNetworkBlocked):
        socket.getaddrinfo("api.anthropic.com", 443)
    for model in ("stub", "ollama"):
        assert type(guard_for_model(model)) is type(loopback_only())
    with guard_for_model("ollama"), pytest.raises(OutboundNetworkBlocked):
        socket.getaddrinfo("api.anthropic.com", 443)


def test_a_stub_eval_needs_nothing_but_the_api_host() -> None:
    """The in-process runtime uses no network, so it runs under the Claude guard."""
    import anyio

    from meridian_dataset import DEFAULT_SEED
    from tier0.evals.runner import run_eval
    from tier0.netguard import guard_for_model

    with guard_for_model("claude"):
        report = anyio.run(lambda: run_eval(DEFAULT_SEED, k=1))
    assert report.unsafe_runs == 0
