"""The same-day count: the definition, its agreement with the ladder, and the CLI."""

import json
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from meridian_dataset.month import month_for
from tier0.clock import MockClock
from tier0.evals import month_same_day as sd
from tier0.evals.month import RESET_SIGNAL, TUNING_MONTH
from tier0.evals.month import main as month_main
from tier0.identity.binding import BoundIdentity
from tier0.mocks.calendar import MockCalendar
from tier0.mocks.device_assurance import MockDeviceAssurance
from tier0.mocks.security_events import MockSecurityEvents
from tier0.verification.context import SAME_DAY_REPEAT, ContextChecks

LIMIT = 3
WINDOW = timedelta(hours=24)
T0 = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)


def _count(times: list[datetime], sender: str = "a") -> sd.Count:
    return sd.count_senders([(sender, t) for t in times], limit=LIMIT, window=WINDOW)


def test_four_requests_inside_24_hours_are_over() -> None:
    count = _count([T0 + i * HOUR for i in range(4)])
    assert (count.over_limit, count.most) == (1, 4)


def test_a_fourth_request_exactly_24_hours_after_the_first_is_not_over() -> None:
    count = _count([T0, T0 + HOUR, T0 + 2 * HOUR, T0 + WINDOW])
    assert (count.over_limit, count.most) == (0, 3)


def test_four_requests_eight_hours_apart_are_not_over() -> None:
    count = _count([T0 + i * 8 * HOUR for i in range(4)])
    assert (count.over_limit, count.most) == (0, 3)


def test_only_the_sender_who_goes_over_is_counted() -> None:
    sent = [("a", T0 + i * HOUR) for i in range(4)] + [("b", T0), ("b", T0 + HOUR)]
    count = sd.count_senders(sent, limit=LIMIT, window=WINDOW)
    assert (count.requests, count.senders, count.over_limit, count.most) == (6, 2, 1, 4)


def test_no_requests_count_as_zero() -> None:
    assert _count([]) == sd.Count(0, 0, 0, 0)


class _Ladder:
    def __init__(self) -> None:
        self.clock = MockClock(T0)
        self.checks = ContextChecks(
            clock=self.clock,
            calendar=MockCalendar(self.clock),
            devices=MockDeviceAssurance(self.clock),
            security_events=MockSecurityEvents(self.clock),
        )
        self.identity = BoundIdentity(
            directory_id="00u-test",
            email="a.person@meridian.example",
            slack_user_id="U1",
            slack_team_id="T1",
        )

    def repeats(self, times: list[datetime]) -> bool:
        found = False
        for at in sorted(times):
            if at > self.clock.now():
                self.clock.advance(at - self.clock.now())
            result = self.checks.check(self.identity, action="own_username")
            found = found or SAME_DAY_REPEAT in result.findings
        return found


def _hand_made() -> list[list[datetime]]:
    return [
        [T0],
        [T0 + i * HOUR for i in range(4)],
        [T0, T0 + HOUR, T0 + 2 * HOUR, T0 + WINDOW],
        [T0 + i * 8 * HOUR for i in range(4)],
        [T0 + i * 8 * HOUR for i in range(7)],
        [T0, T0, T0, T0],
    ]


def _random_lists() -> list[list[datetime]]:
    rng = random.Random(7)  # noqa: S311  # a test seed, not a secret
    return [
        [T0 + timedelta(minutes=rng.randint(0, 72 * 60)) for _ in range(rng.randint(1, 7))]
        for _ in range(200)
    ]


def test_the_count_agrees_with_the_ladders_own_same_day_check() -> None:
    for times in _hand_made() + _random_lists():
        over = _count(times).over_limit == 1
        assert over == _Ladder().repeats(times), sorted(times)


def test_the_tuning_month_has_no_sender_over_the_limit() -> None:
    month = month_for(TUNING_MONTH)
    counts = sd.key_counts(month, limit=LIMIT, window=WINDOW)
    assert list(counts) == ["own_username_requests", "resets", "pooled"]
    for count in counts.values():
        assert count.over_limit == 0 and count.most <= 3 and count.requests > 0
    assert counts["pooled"].requests == (
        counts["own_username_requests"].requests + counts["resets"].requests
    )


def test_a_month_that_does_go_over_is_counted() -> None:
    month = month_for(TUNING_MONTH)
    chosen = [t for t in month.tickets if t.kind in ("password_reset", "mfa_reset")][:4]
    start = datetime.fromisoformat(chosen[0].sent_at)
    changed = {
        t.id: replace(t, persona_id=chosen[0].persona_id, sent_at=(start + i * HOUR).isoformat())
        for i, t in enumerate(chosen)
    }
    busy = replace(month, tickets=tuple(changed.get(t.id, t) for t in month.tickets))
    counts = sd.key_counts(busy, limit=LIMIT, window=WINDOW)
    assert counts["resets"].over_limit == 1
    assert counts["pooled"].over_limit == 1
    assert counts["own_username_requests"].over_limit == 0


class _Fake:
    provider = "fake"
    model = "fake-1"
    temperature = None

    def complete(self, request: Any) -> Any:
        from tier0.models import ModelResponse

        body = json.dumps({"category": "other"})
        return ModelResponse(body, self.model, 300, 8, "stop", None)

    def describe(self) -> dict[str, str]:
        return {}


@pytest.fixture(scope="module")
def result_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("month")
    argv = ["run", "--set", TUNING_MONTH, "--setup", "haiku-3d2", "--k", "2", "--out", str(out)]
    code = month_main(argv, make_model=lambda: _Fake(), samples=50)  # type: ignore[arg-type, return-value]
    assert code == 0
    return next(out.glob("*.json"))


def test_reset_signal_counts_from_a_real_result_file(result_file: Path) -> None:
    result = json.loads(result_file.read_text(encoding="utf-8"))
    month = month_for(TUNING_MONTH)
    counts = sd.reset_signal_counts(month, result, limit=LIMIT, window=WINDOW)
    assert sorted(counts) == [1, 2]
    for run, count in counts.items():
        fired = sum(
            1
            for t in result["tickets"]
            for r in t["runs"]
            if r["run"] == run and r["stage"] == RESET_SIGNAL
        )
        assert count.requests == fired and fired > 0
        assert count.over_limit == 0


def test_a_result_of_another_month_is_refused(result_file: Path) -> None:
    result = json.loads(result_file.read_text(encoding="utf-8"))
    month = month_for(TUNING_MONTH)
    for key, value in (("month_sha256", "0" * 64), ("set", "another_month")):
        other = json.loads(json.dumps(result))
        other["config"][key] = value
        with pytest.raises(ValueError):
            sd.reset_signal_counts(month, other, limit=LIMIT, window=WINDOW)


@pytest.mark.usefixtures("sealed_month1")
def test_the_cli_refuses_the_sealed_set_before_building_any_month(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("a month was built")

    monkeypatch.setattr(sd, "month_for", boom)
    assert sd.main(["--set", "held_out_month1"]) == 2
    assert capsys.readouterr().err.startswith("refused:")


def test_the_cli_prints_numbers_only(result_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert sd.main(["--set", "tuning_month", "--results", str(result_file)]) == 0
    out = capsys.readouterr().out
    assert "The reset signal, haiku-3d2 (k = 2):" in out and "run 2:" in out
    assert "@" not in out and "MON-" not in out
    for t in month_for(TUNING_MONTH).tickets:
        if len(t.text) >= 12:
            assert t.text not in out


def test_the_settings_are_the_ladders_own() -> None:
    assert sd.ladder_settings() == (3, timedelta(hours=24))
