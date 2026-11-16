"""Proactive posts also stop for the global kill switch on agent actions (ADR-008).

A post to the whole company is an agent action. The runner keeps its own switch
(ADR-020); the global one is checked first, and while it is on a tick sends nothing.
"""

from datetime import date, datetime, time, timedelta

from meridian_dataset import DEFAULT_SEED, generate
from tier0.clock import MockClock
from tier0.controls import Controls, build_controls
from tier0.mocks.directory import MockDirectory
from tier0.mocks.slack import CHANNELS, MockSlack
from tier0.proactive.calendar import BERLIN
from tier0.proactive.runner import RESUME_ACTION, ProactiveRunner


def _berlin(day: date, hour: int = 0) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=BERLIN)


def _runner(start: datetime) -> tuple[ProactiveRunner, MockSlack, MockClock, Controls]:
    world = generate(DEFAULT_SEED)
    clock = MockClock(start)
    slack = MockSlack(world, clock)
    controls = build_controls(clock)
    runner = ProactiveRunner(
        slack=slack,
        directory=MockDirectory(world, clock),
        clock=clock,
        signing_secret=slack.signing_secret,
        domain="meridian.example",
        dry_run=False,
        global_switch=controls.kill_switch,
    )
    return runner, slack, clock, controls


def _resume(runner: ProactiveRunner, slack: MockSlack) -> None:
    press = slack.button_click(
        channel=CHANNELS["it-team"], user="U0ITLEAD", action_id=RESUME_ACTION, value="resume"
    )
    assert runner.handle_interaction(press) == "resumed"


def _run(runner: ProactiveRunner, clock: MockClock, until: datetime) -> None:
    while clock.now() < until:
        runner.tick()
        clock.advance(timedelta(minutes=15))


def test_global_switch_stops_previews_and_posts() -> None:
    # 1 May 2027 is a holiday in several offices: a preview and a post are due before it.
    runner, slack, clock, controls = _runner(_berlin(date(2027, 4, 29)))
    _resume(runner, slack)
    controls.kill_switch.turn_on(by="U0ITLEAD", reason="incident")
    _run(runner, clock, _berlin(date(2027, 5, 1)))
    assert slack.sent_messages == ()
    # Logged once for the whole stop, not on each of the 192 ticks.
    assert [e.action for e in runner.log].count("global_kill_switch_on") == 1


def test_not_even_the_paused_notice_is_sent_while_the_global_switch_is_on() -> None:
    runner, slack, clock, controls = _runner(_berlin(date(2027, 4, 29)))
    controls.kill_switch.turn_on(by="U0ITLEAD", reason="incident")
    assert runner.kill_switch  # the runner's own start-up pause
    _run(runner, clock, _berlin(date(2027, 4, 29), 6))
    assert slack.sent_messages == ()
    # With the global switch off again, the runner's own pause shows as before.
    controls.kill_switch.turn_off(by="U0ITLEAD", reason="over")
    runner.tick()
    (notice,) = slack.sent_messages
    assert notice.channel == CHANNELS["it-team"] and "paused" in notice.text


def test_posts_resume_when_the_global_switch_is_off_again() -> None:
    runner, slack, clock, controls = _runner(_berlin(date(2027, 4, 29)))
    _resume(runner, slack)
    controls.kill_switch.turn_on(by="U0ITLEAD", reason="short drill")
    _run(runner, clock, _berlin(date(2027, 4, 29), 9))
    assert slack.sent_messages == ()
    controls.kill_switch.turn_off(by="U0ITLEAD", reason="drill over")
    _run(runner, clock, _berlin(date(2027, 5, 1)))
    channels = [m.channel for m in slack.sent_messages]
    assert CHANNELS["it-team"] in channels and CHANNELS["general"] in channels
    # A second stop is logged as its own event.
    controls.kill_switch.turn_on(by="U0ITLEAD", reason="again")
    runner.tick()
    assert [e.action for e in runner.log].count("global_kill_switch_on") == 2


def test_without_a_global_switch_the_runner_is_unchanged() -> None:
    world = generate(DEFAULT_SEED)
    clock = MockClock(_berlin(date(2027, 4, 29)))
    slack = MockSlack(world, clock)
    runner = ProactiveRunner(
        slack=slack,
        directory=MockDirectory(world, clock),
        clock=clock,
        signing_secret=slack.signing_secret,
        domain="meridian.example",
        dry_run=False,
    )
    _resume(runner, slack)
    _run(runner, clock, _berlin(date(2027, 5, 1)))
    assert CHANNELS["general"] in [m.channel for m in slack.sent_messages]
    assert "global_kill_switch_on" not in [e.action for e in runner.log]
