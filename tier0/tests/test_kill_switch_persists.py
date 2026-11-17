"""The kill switch survives a restart (ADR-043, R6). Fake data and mocks only."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import pytest

from meridian_dataset import World
from tier0.audit import AuditLog
from tier0.clock import MockClock, format_time, parse_time
from tier0.controls import (
    ACTIONS_STOPPED,
    ACTIONS_STOPPED_MESSAGE,
    KillSwitch,
    KillSwitchFile,
    KillSwitchNotSaved,
    build_controls,
)
from tier0.evals.ladder import identity_of
from tier0.mcp_server.client import McpToolError, open_session
from tier0.runtime import offline_runtime

START = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> MockClock:
    return MockClock(START)


def _switch(clock: MockClock, path: Path) -> tuple[KillSwitch, AuditLog]:
    log = AuditLog(clock)
    return KillSwitch(clock, log, state=KillSwitchFile(path)), log


def test_on_survives_a_restart_with_since_and_an_audit_entry(clock: MockClock, tmp_path: Path):
    path = tmp_path / "state" / "switch.json"
    first, _ = _switch(clock, path)
    first.turn_on(by="U0ITLEAD", reason="agent is looping")
    turned_on_at = clock.now()
    clock.advance(timedelta(hours=2))
    second, log = _switch(clock, path)
    assert second.is_on
    assert second.since == turned_on_at
    (entry,) = log.events("kill_switch.restored")
    assert entry.actor == "system"
    assert entry.details["on"] is True
    assert entry.details["readable"] is True
    saved = json.loads(path.read_text())
    assert saved["on"] is True and saved["by"] == "U0ITLEAD"
    assert saved["since"] == format_time(turned_on_at)


def test_on_then_off_restarts_off(clock: MockClock, tmp_path: Path):
    path = tmp_path / "switch.json"
    first, _ = _switch(clock, path)
    first.turn_on(by="a", reason="drill")
    first.turn_off(by="a", reason="all clear")
    assert not first.is_on and first.since is None
    second, log = _switch(clock, path)
    assert not second.is_on and second.since is None
    assert log.entries == ()


def test_no_file_starts_off_and_audits_nothing(clock: MockClock, tmp_path: Path):
    switch, log = _switch(clock, tmp_path / "missing" / "switch.json")
    assert not switch.is_on
    assert log.entries == ()


def _state(**changes: object) -> str:
    base = {"on": False, "since": None, "by": "a", "reason": "r", "at": "2026-11-16T09:00:00.000Z"}
    return json.dumps({**base, **changes})


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "",
        "[]",
        _state(on="yes"),
        _state(since=5),
        _state(on=True, since="yesterday"),
        _state(on=True, since=None),
        _state(by=1),
        _state(at="never"),
        '{"on": false}',
    ],
)
def test_a_bad_file_fails_closed(clock: MockClock, tmp_path: Path, content: str):
    path = tmp_path / "switch.json"
    path.write_text(content)
    switch, log = _switch(clock, path)
    assert switch.is_on
    assert switch.since == clock.now()
    (entry,) = log.events("kill_switch.restored")
    assert entry.details["readable"] is False
    assert "could not be read" in (entry.reason or "")


def test_an_unreadable_path_fails_closed(clock: MockClock, tmp_path: Path):
    path = tmp_path / "switch.json"
    path.mkdir()  # a directory where the file should be
    switch, log = _switch(clock, path)
    assert switch.is_on
    assert len(log.events("kill_switch.restored")) == 1


def test_a_person_can_turn_a_failed_closed_switch_off(clock: MockClock, tmp_path: Path):
    path = tmp_path / "switch.json"
    path.write_text("garbage")
    switch, _ = _switch(clock, path)
    switch.turn_off(by="a", reason="checked, state was junk")
    assert not switch.is_on
    again, _ = _switch(clock, path)
    assert not again.is_on


def _fail_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)


def test_turn_off_that_cannot_be_saved_stays_on(
    clock: MockClock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "switch.json"
    switch, log = _switch(clock, path)
    switch.turn_on(by="a", reason="drill")
    since = switch.since
    _fail_replace(monkeypatch)
    with pytest.raises(KillSwitchNotSaved):
        switch.turn_off(by="b", reason="all clear")
    assert switch.is_on and switch.since == since
    (entry,) = log.events("kill_switch.not_saved")
    assert entry.details["wanted_on"] is False and entry.details["now_on"] is True
    assert log.events("kill_switch.off") == ()
    monkeypatch.undo()
    restarted, _ = _switch(clock, path)
    assert restarted.is_on
    assert list(tmp_path.glob("*.tmp")) == []


def test_turn_on_that_cannot_be_saved_is_on_in_memory(
    clock: MockClock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    switch, log = _switch(clock, tmp_path / "switch.json")
    _fail_replace(monkeypatch)
    with pytest.raises(KillSwitchNotSaved):
        switch.turn_on(by="a", reason="drill")
    assert switch.is_on and switch.since == clock.now()
    assert len(log.events("kill_switch.on")) == 1
    assert len(log.events("kill_switch.not_saved")) == 1
    assert list(tmp_path.iterdir()) == []  # the temp file is cleaned up


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs permission checks")
def test_a_read_only_directory_is_a_failed_save(clock: MockClock, tmp_path: Path):
    folder = tmp_path / "ro"
    folder.mkdir()
    switch, _ = _switch(clock, folder / "switch.json")
    folder.chmod(0o500)
    try:
        with pytest.raises(KillSwitchNotSaved):
            switch.turn_on(by="a", reason="drill")
        assert switch.is_on
    finally:
        folder.chmod(0o700)


def test_the_file_is_written_atomically_and_leaves_no_temp_file(clock: MockClock, tmp_path: Path):
    path = tmp_path / "nested" / "switch.json"
    switch, _ = _switch(clock, path)
    switch.turn_on(by="a", reason="one")
    switch.turn_off(by="a", reason="two")
    assert [p.name for p in path.parent.iterdir()] == ["switch.json"]
    saved = json.loads(path.read_text())
    assert saved["on"] is False and saved["since"] is None
    assert parse_time(saved["at"]) == clock.now()


def test_without_a_state_file_nothing_changes(clock: MockClock, tmp_path: Path):
    log = AuditLog(clock)
    switch = KillSwitch(clock, log)
    assert not switch.is_on
    switch.turn_on(by="a", reason="drill")
    assert switch.is_on and switch.since == clock.now()
    switch.turn_off(by="a", reason="done")
    assert not switch.is_on
    assert [e.event for e in log.entries] == ["kill_switch.on", "kill_switch.off"]
    assert list(tmp_path.iterdir()) == []
    assert not build_controls(clock).kill_switch.is_on


def test_build_controls_passes_the_file_through(clock: MockClock, tmp_path: Path):
    path = tmp_path / "switch.json"
    build_controls(clock, kill_switch_file=path).kill_switch.turn_on(by="a", reason="drill")
    assert build_controls(clock, kill_switch_file=path).kill_switch.is_on


def test_a_second_runtime_on_the_same_file_refuses_tool_calls(world: World, tmp_path: Path):
    path = tmp_path / "switch.json"
    person = next(p for p in world.people if p.status == "active" and p.slack_active)

    async def main() -> tuple[bool, McpToolError]:
        with anyio.fail_after(30):
            async with offline_runtime(world, kill_switch_file=path) as first:
                first.controls.kill_switch.turn_on(by="U0ITLEAD", reason="drill")
            async with offline_runtime(world, kill_switch_file=path) as second:
                token = second.issuer.mint(identity_of(world, person), ticket_id="R6-1").token
                async with open_session(second.mcp_app, token) as session:
                    with pytest.raises(McpToolError) as info:
                        await session.call_tool("get_my_username", None)
                return second.controls.kill_switch.is_on, info.value

    is_on, error = anyio.run(main)
    assert is_on
    assert (error.code, error.message) == (ACTIONS_STOPPED, ACTIONS_STOPPED_MESSAGE)


def test_a_runtime_without_the_file_starts_off(world: World, tmp_path: Path):
    async def main() -> bool:
        async with offline_runtime(world) as rt:
            return rt.controls.kill_switch.is_on

    assert anyio.run(main) is False
