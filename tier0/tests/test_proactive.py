"""Proactive posts (ADR-020): the pure scheduler, the runner's limits, the preview command.

Everything runs on the mock clock and mock Slack; nothing is real.
"""

import ast
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest

from meridian_dataset import DEFAULT_SEED, generate
from tier0.clock import MockClock
from tier0.guard import check_reply
from tier0.mocks.directory import MockDirectory
from tier0.mocks.slack import BOT_USER_ID, CHANNELS, MockSlack
from tier0.proactive import calendar, preview, schedule, templates
from tier0.proactive.calendar import BERLIN, COUNTRY_TZ, HolidayBlock, tz
from tier0.proactive.runner import CANCEL_ACTION, ProactiveRunner
from tier0.proactive.schedule import Config, State, holiday_schedule, plan, pulse_blocker

ALL = tuple(sorted(COUNTRY_TZ))
CONFIG = Config()
BANNED = (
    "I'd be happy to help",
    "Certainly!",
    "Great question",
    "I hope this helps",
    "Is there anything else I can help you with",
    "As an AI",
    "I apologize for any inconvenience",
    "don't hesitate to reach out",
)


def _posts(year: int, countries: tuple[str, ...] = ALL) -> tuple[schedule.Post, ...]:
    return holiday_schedule(year, countries, CONFIG)


def _with(posts: tuple[schedule.Post, ...], country: str, day: date) -> schedule.Post:
    return next(p for p in posts if (country, day) in {(c, d) for c, d, _ in p.holidays})


def _is_night(hour: int) -> bool:
    return hour >= CONFIG.night[0] or hour < CONFIG.night[1]


def _deadline(post: schedule.Post) -> datetime:
    return min(datetime.combine(d, time(0), tzinfo=tz(c)) for c, d, _ in post.holidays)


# --- holiday notes ---------------------------------------------------------------------


def test_the_working_day_before_at_0900_local() -> None:
    # Epiphany (Wed 6 Jan 2027) in Spain alone: Tuesday 09:00 Madrid.
    post = _with(_posts(2027, ("ES",)), "ES", date(2027, 1, 6))
    assert post.send_at.astimezone(tz("ES")) == datetime(2027, 1, 5, 9, tzinfo=tz("ES"))
    assert post.countries == ("ES",) and post.channel == "general"


def test_consecutive_holidays_share_one_note() -> None:
    # Germany at Easter: Good Friday and Easter Monday, joined by the weekend.
    posts = _posts(2027, ("DE",))
    good_friday = _with(posts, "DE", date(2027, 3, 26))
    assert good_friday is _with(posts, "DE", date(2027, 3, 29))
    assert good_friday.send_at.astimezone(BERLIN).date() == date(2027, 3, 25)
    assert "Friday 26 March and Monday 29 March" in good_friday.text


def test_1_may_2027_is_one_note_at_a_daytime_hour_for_most_and_night_for_nobody() -> None:
    posts = _posts(2027)
    note = _with(posts, "DE", date(2027, 5, 1))
    labour_day = {"BR", "DE", "ES", "MX", "PL", "SG"}
    assert labour_day <= set(note.countries)
    for country in labour_day:
        assert note is _with(posts, country, date(2027, 5, 1))
    # 1 May 2027 is a Saturday: the note goes out on Friday 30 April, before anyone is off.
    assert note.berlin_day == date(2027, 4, 30)
    assert note.send_at < _deadline(note)
    hours = {c: note.send_at.astimezone(tz(c)).hour for c in note.countries}
    assert not any(_is_night(h) for h in hours.values()), hours
    daytime = sum(8 <= h < 18 for h in hours.values())
    assert daytime > len(hours) / 2, hours
    # No other hour that day does better.
    for step in range(24):
        moment = datetime.combine(date(2027, 4, 30), time(0), tzinfo=BERLIN) + timedelta(hours=step)
        if moment >= _deadline(note):
            break
        local = [moment.astimezone(tz(c)).hour for c in note.countries]
        if not any(_is_night(h) for h in local):
            assert sum(8 <= h < 18 for h in local) <= daytime
    assert note.send_at == datetime(2027, 4, 30, 13, tzinfo=UTC)
    # Poland is also off on Monday 3 May, when IT works; Germany's day is only a Saturday.
    assert "IT is working as usual" in note.text
    assert "Wochenendbereitschaft" in note.text


def test_never_at_night_for_anyone_if_some_hour_avoids_it() -> None:
    for year in (2026, 2027, 2028):
        for post in _posts(year):
            if post.year_end or not post.holidays:
                continue
            chosen = sum(_is_night(post.send_at.astimezone(tz(c)).hour) for c in post.countries)
            if chosen == 0:
                continue
            start = datetime.combine(post.berlin_day, time(0), tzinfo=BERLIN)
            for step in range(24):
                moment = start + timedelta(hours=step)
                if moment >= _deadline(post):
                    break
                assert any(_is_night(moment.astimezone(tz(c)).hour) for c in post.countries), (
                    post.post_id
                )


def test_every_note_goes_out_before_anyone_listed_is_off() -> None:
    for post in _posts(2027):
        if post.holidays:
            assert post.send_at < _deadline(post), post.post_id


def test_at_most_one_post_per_berlin_day() -> None:
    for year in (2026, 2027, 2028):
        days = [p.berlin_day for p in _posts(year)]
        assert len(days) == len(set(days))


def test_one_year_end_note_for_everyone_replaces_the_country_notes() -> None:
    posts = _posts(2027)
    (year_end,) = [p for p in posts if p.year_end]
    assert year_end.countries == ALL
    assert year_end.berlin_day == date(2027, 12, 23)
    for p in posts:
        if not p.year_end:
            assert not any(date(2027, 12, 24) <= d <= date(2028, 1, 1) for _, d, _ in p.holidays)
    assert "IT runs reduced cover from Friday 24 December to Saturday 1 January" in year_end.text
    # The UK's substitute Monday (3 Jan 2028) belongs to the break, not to its own note.
    assert not any(p.berlin_day == date(2027, 12, 31) for p in posts)


def test_berlin_state_holidays_only_change_the_coverage_line() -> None:
    # 8 March 2027 is a holiday in Berlin, not in Germany as a whole.
    assert calendar.it_off(date(2027, 3, 8))
    assert not any(d == date(2027, 3, 8) for p in _posts(2027) for _, d, _ in p.holidays)
    block = HolidayBlock("ES", date(2027, 3, 8), date(2027, 3, 8), ((date(2027, 3, 8), "x"),))
    assert block.coverage() == "it_off"
    assert HolidayBlock("ES", date(2027, 3, 9), date(2027, 3, 9), ()).coverage() == "it_open"


def test_notes_name_the_country_not_the_holiday() -> None:
    for post in _posts(2027):
        for _, _, name in post.holidays:
            assert name not in post.text, (post.post_id, name)
        if not post.year_end:
            assert any(templates.COUNTRY_NAMES["en"][c] in post.text for c in post.countries)
        assert "happy holidays" in post.text.lower(), post.text


def test_every_headline_says_happy_holidays_in_its_language() -> None:
    greeting = {
        "en": "happy holidays",
        "de": "frohe feiertage",
        "es": "felices fiestas",
        "fr": "bonnes fêtes",
    }
    for language, options in templates.HEADLINES.items():
        assert all(greeting[language] in o.lower() for o in options), language


def test_english_plus_the_reviewed_local_language_only() -> None:
    posts = _posts(2027)
    de = _with(posts, "DE", date(2027, 10, 3))
    english, german = de.text.split("\n\n---\n\n")
    assert "Happy holidays" in english and "Frohe Feiertage" in german
    assert "Germany" in english and "Deutschland" in german
    # Brazil, Poland, the Netherlands and Japan: English only, no unreviewed text.
    for country, day in (
        ("BR", date(2027, 11, 15)),
        ("PL", date(2027, 11, 11)),
        ("NL", date(2027, 4, 27)),
        ("JP", date(2027, 11, 3)),
    ):
        post = _with(posts, country, day)
        if post.countries == (country,):
            assert "---" not in post.text, (country, post.text)


@pytest.mark.parametrize("language", ["pt", "pl", "nl", "ja", "en"])
def test_unreviewed_languages_cant_be_turned_on(language: str) -> None:
    with pytest.raises(ValueError, match="reviewed"):
        Config(local_languages=frozenset({"de", language}))


def test_post_text_is_clean() -> None:
    emoji = re.compile("[\U0001f000-\U0001faff☀-➿]")
    texts = [p.text for p in _posts(2027)]
    texts += [
        templates.pulse_text(lang, f"pulse-{n}")
        for lang in templates.REVIEWED_LANGUAGES
        for n in range(9)
    ]
    for text in texts:
        for phrase in BANNED:
            assert phrase.lower() not in text.lower(), (phrase, text)
        assert all(e in templates.ALLOWED_EMOJI for e in emoji.findall(text)), text
        assert check_reply(text, requester_email=None, domain="meridian.example").allowed, text


# --- pulse check ---------------------------------------------------------------------


def _berlin(day: date, hour: int = 10) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=BERLIN).astimezone(UTC)


def test_pulse_needs_3_quiet_working_days_and_tue_to_thu() -> None:
    # Last human message on Wed 2 June 2027; quiet Thu, Fri, Mon -> Tue 8 June is due.
    last = _berlin(date(2027, 6, 2), 15)
    state = State()
    assert pulse_blocker(date(2027, 6, 7), last, state, CONFIG, frozenset()) == "not_tue_to_thu"
    assert pulse_blocker(date(2027, 6, 8), last, state, CONFIG, frozenset()) is None
    # Quiet Thu and Fri only: not yet.
    assert (
        pulse_blocker(date(2027, 6, 8), _berlin(date(2027, 6, 3), 9), state, CONFIG, frozenset())
        == "not_quiet"
    )
    assert pulse_blocker(date(2027, 6, 11), last, state, CONFIG, frozenset()) == "not_tue_to_thu"


def test_holidays_dont_count_as_working_days() -> None:
    # Quiet across Easter in Berlin: Good Friday and Easter Monday don't count.
    last = _berlin(date(2027, 3, 24), 15)
    assert schedule.quiet_working_days(last, date(2027, 3, 30)) == 1
    assert pulse_blocker(date(2027, 3, 31), last, State(), CONFIG, frozenset()) == "not_quiet"


def test_pulse_once_per_streak_and_at_most_every_two_weeks() -> None:
    last = _berlin(date(2027, 6, 2), 15)
    fired = State(last_pulse_at=_berlin(date(2027, 6, 8)))
    assert (
        pulse_blocker(date(2027, 6, 9), last, fired, CONFIG, frozenset())
        == "already_sent_this_streak"
    )
    # Someone posts on 9 June, then quiet again: still too soon on 15-16 June.
    again = _berlin(date(2027, 6, 9), 11)
    assert pulse_blocker(date(2027, 6, 16), again, fired, CONFIG, frozenset()) == "too_soon"
    assert pulse_blocker(date(2027, 6, 22), again, fired, CONFIG, frozenset()) is None


def test_pulse_skips_holiday_note_days_incidents_and_reduced_coverage() -> None:
    last = _berlin(date(2027, 6, 2), 15)
    day = date(2027, 6, 8)
    assert pulse_blocker(day, last, State(), CONFIG, frozenset({day})) == "holiday_note_day"
    assert pulse_blocker(day, last, State(incident=True), CONFIG, frozenset()) == "incident"
    reduced = Config(reduced_coverage_dates=frozenset({day}))
    assert pulse_blocker(day, last, State(), reduced, frozenset()) == "reduced_coverage"
    # The year-end break is reduced coverage; so is a Berlin holiday.
    assert (
        pulse_blocker(date(2027, 12, 28), _berlin(date(2027, 12, 1)), State(), CONFIG, frozenset())
        == "reduced_coverage"
    )


def test_plan_is_pure_and_repeats() -> None:
    now = _berlin(date(2027, 4, 29), 15)
    one = plan(now, ALL, None, State(), CONFIG)
    two = plan(now, ALL, None, State(), CONFIG)
    assert one == two
    assert [p.post_id for p in one.previews] == ["holiday-2027-04-30"]


def test_metric_counts_tickets_within_24h_of_a_pulse() -> None:
    pulse = _berlin(date(2027, 6, 8))
    tickets = [pulse + timedelta(hours=h) for h in (-1, 1, 23, 25)]
    assert schedule.tickets_within_24h([pulse], tickets) == 2


# --- the runner: mock clock, mock Slack ---------------------------------------------


def _runner(
    start: datetime, *, dry_run: bool = False
) -> tuple[ProactiveRunner, MockSlack, MockClock]:
    world = generate(DEFAULT_SEED)
    clock = MockClock(start)
    slack = MockSlack(world, clock)
    runner = ProactiveRunner(
        slack=slack,
        directory=MockDirectory(world, clock),
        clock=clock,
        signing_secret=slack.signing_secret,
        domain="meridian.example",
        dry_run=dry_run,
    )
    return runner, slack, clock


def _run(runner: ProactiveRunner, clock: MockClock, until: datetime) -> None:
    while clock.now() < until:
        runner.tick()
        clock.advance(timedelta(minutes=15))


def _human(slack: MockSlack, world_user: str = "U0PERSON01", text: str = "printer?") -> None:
    slack.post_as_person(CHANNELS["it-help"], world_user, text)


def test_dry_run_is_the_default_and_sends_nothing() -> None:
    world = generate(DEFAULT_SEED)
    clock = MockClock(_berlin(date(2027, 4, 29), 0))
    slack = MockSlack(world, clock)
    runner = ProactiveRunner(
        slack=slack,
        directory=MockDirectory(world, clock),
        clock=clock,
        signing_secret=slack.signing_secret,
        domain="meridian.example",
    )
    assert runner.dry_run
    _run(runner, clock, _berlin(date(2027, 5, 1), 0))
    assert slack.sent_messages == ()
    actions = [e.action for e in runner.log]
    assert "would_preview" in actions and "would_post" in actions


def test_no_late_preview_for_a_post_that_cant_go_out() -> None:
    # The Thursday note's preview was due Wed 08:00 UTC; starting later, it is skipped.
    runner, slack, clock = _runner(_berlin(date(2027, 4, 29), 0))
    _human(slack)
    _run(runner, clock, _berlin(date(2027, 4, 29), 12))
    assert slack.sent_messages == ()


def test_preview_24h_before_with_a_cancel_button_then_the_post() -> None:
    runner, slack, clock = _runner(_berlin(date(2027, 4, 29), 0))
    _human(slack)  # keep #it-help busy, so no pulse check
    _run(runner, clock, _berlin(date(2027, 5, 1), 0))
    preview_msg, post_msg = slack.sent_messages
    assert preview_msg.channel == CHANNELS["it-team"]
    button = preview_msg.blocks[1]["elements"][0]
    assert button["action_id"] == CANCEL_ACTION and button["value"] == "holiday-2027-04-30"
    assert post_msg.channel == CHANNELS["general"]
    assert float(post_msg.ts) - float(preview_msg.ts) >= 23 * 3600
    assert post_msg.text in preview_msg.text  # IT saw the exact text


def test_cancel_from_it_team_stops_the_post() -> None:
    runner, slack, clock = _runner(_berlin(date(2027, 4, 29), 0))
    _human(slack)
    _run(runner, clock, _berlin(date(2027, 4, 29), 16))
    assert [m.channel for m in slack.sent_messages] == [CHANNELS["it-team"]]
    click = slack.button_click(
        channel=CHANNELS["it-team"],
        user="U0ITPERSON",
        action_id=CANCEL_ACTION,
        value="holiday-2027-04-30",
    )
    assert runner.handle_interaction(click) == "cancelled"
    _run(runner, clock, _berlin(date(2027, 5, 1), 0))
    assert [m.channel for m in slack.sent_messages] == [CHANNELS["it-team"]]
    assert any(e.action == "cancelled" and e.detail == "U0ITPERSON" for e in runner.log)


def test_cancel_from_elsewhere_or_forged_is_refused() -> None:
    runner, slack, _clock = _runner(_berlin(date(2027, 4, 29), 16))
    other = slack.button_click(
        channel=CHANNELS["general"],
        user="U0SOMEONE",
        action_id=CANCEL_ACTION,
        value="holiday-2027-04-30",
    )
    assert runner.handle_interaction(other).startswith("refused")
    forged = slack.button_click(
        channel=CHANNELS["it-team"],
        user="U0SOMEONE",
        action_id=CANCEL_ACTION,
        value="holiday-2027-04-30",
    )
    forged = type(forged)(headers=forged.headers, body=forged.body + b"x")
    assert runner.handle_interaction(forged).startswith("rejected")
    assert runner.state.cancelled == frozenset()


def test_kill_switch_stops_previews_and_posts_and_is_logged() -> None:
    runner, slack, clock = _runner(_berlin(date(2027, 4, 29), 0))
    runner.set_kill_switch(True, by="U0ITLEAD")
    _run(runner, clock, _berlin(date(2027, 5, 1), 0))
    assert slack.sent_messages == ()
    assert runner.log[0].action == "kill_switch_on" and runner.log[0].detail == "U0ITLEAD"


def test_a_second_post_on_the_same_day_is_refused() -> None:
    runner, slack, clock = _runner(_berlin(date(2027, 4, 29), 0))
    _human(slack)
    _run(runner, clock, _berlin(date(2027, 5, 1), 0))
    pulse = schedule.pulse_post(date(2027, 4, 30), CONFIG)
    runner._post(pulse)
    assert runner.log[-1].action == "refused_second_post_today"
    assert len([m for m in slack.sent_messages if m.channel != CHANNELS["it-team"]]) == 1


def test_a_post_the_guard_blocks_is_not_sent_and_it_team_is_told(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leaky = {
        lang: tuple("Ask kenji.sato@meridian.example. " + v for v in vs)
        for lang, vs in templates.HEADLINES.items()
    }
    monkeypatch.setattr(templates, "HEADLINES", leaky)
    holiday_schedule.cache_clear()
    try:
        runner, slack, clock = _runner(_berlin(date(2027, 4, 29), 0))
        _human(slack)
        _run(runner, clock, _berlin(date(2027, 5, 1), 0))
    finally:
        holiday_schedule.cache_clear()
    assert all("kenji" not in m.text for m in slack.sent_messages)
    assert [m.channel for m in slack.sent_messages] == [CHANNELS["it-team"]]
    assert "output guard" in slack.sent_messages[0].text
    assert any(e.action == "blocked" for e in runner.log)


def test_pulse_check_end_to_end_and_tier0s_own_messages_dont_count() -> None:
    # A person posts on Wed 2 June 2027; tier0's own pulse later doesn't restart the count.
    runner, slack, clock = _runner(_berlin(date(2027, 6, 2), 15))
    _human(slack)
    _run(runner, clock, _berlin(date(2027, 6, 10), 12))
    pulses = [m for m in slack.sent_messages if m.channel == CHANNELS["it-help"]]
    assert len(pulses) == 1
    sent_at = datetime.fromtimestamp(float(pulses[0].ts), tz=UTC).astimezone(BERLIN)
    assert (sent_at.date(), sent_at.hour) == (date(2027, 6, 8), 10)
    previews = [m for m in slack.sent_messages if m.channel == CHANNELS["it-team"]]
    assert any("stays quiet" in m.text for m in previews)
    # Its own message is in the history, but the next working days stay "quiet" with no
    # second pulse in this streak.
    history = slack.conversations_history(channel=CHANNELS["it-help"])["messages"]
    assert history[0]["user"] == BOT_USER_ID and "bot_id" in history[0]
    assert runner.last_human_message() < datetime.fromtimestamp(float(pulses[0].ts), tz=UTC)


def test_a_human_message_after_the_preview_cancels_nothing_but_skips_the_pulse() -> None:
    runner, slack, clock = _runner(_berlin(date(2027, 6, 2), 15))
    _human(slack)
    _run(runner, clock, _berlin(date(2027, 6, 7), 12))  # the preview went out Mon 10:00
    assert any("stays quiet" in m.text for m in slack.sent_messages)
    _human(slack, text="actually, my VPN is slow")
    _run(runner, clock, _berlin(date(2027, 6, 9), 12))
    assert not [m for m in slack.sent_messages if m.channel == CHANNELS["it-help"]]


def test_proactive_code_never_reaches_a_model() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "tier0" / "proactive"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(("tier0.models", "tier0.agent")), path
            if isinstance(node, ast.Import):
                assert not any(
                    a.name.startswith(("tier0.models", "anthropic")) for a in node.names
                ), path
    assert "model" not in ProactiveRunner.__init__.__code__.co_varnames


# --- the preview command -------------------------------------------------------------


def test_holidays_preview_prints_the_year(capsys: pytest.CaptureFixture[str]) -> None:
    assert preview.main(["--year", "2027", "--countries", "all"]) == 0
    out = capsys.readouterr().out
    assert "Fri 2027-04-30 13:00 UTC (15:00 Berlin)" in out
    assert "dry run, nothing is sent" in out
    assert "at most 1 per day" in out
    assert preview.main(["--year", "2027", "--countries", "XX"]) == 2
