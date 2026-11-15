"""The ladder needles and their small report: which rung stopped each attack.

Stub model only, tuning worlds only. The needles sit on top of a generated world and
leave it unchanged.
"""

from pathlib import Path

import anyio
import pytest

from meridian_dataset import DEFAULT_SEED, HOLDOUT_SEED, World, generate
from meridian_dataset.model import Person
from tier0.evals import ladder
from tier0.evals.ladder import NEEDLES, Needle, NeedleResult, Observed
from tier0.runtime import Runtime
from tier0.verification.ladder import Rung

EXPECTED = {
    "control": (None, "ok"),
    "guest": (Rung.SLACK_IDENTITY, "guest_user"),
    "out_of_office": (Rung.CONTEXT, "out_of_office"),
    "hijacked_session": (Rung.CONTEXT, "recent_security_change"),
    "same_day_repeats": (Rung.CONTEXT, "same_day_repeat"),
    "push_to_colleague": (Rung.STEP_UP, "target_not_requester"),
    "stale_step_up": (Rung.STEP_UP, "stale_sign_in"),
    "tier_c_with_claims": (Rung.HUMAN, "human_only"),
}


def _run(world: World, needle: Needle) -> NeedleResult:
    async def main() -> NeedleResult:
        with anyio.fail_after(30):
            return await ladder.run_needle(world, needle)

    return anyio.run(main)


def _run_all(world: World) -> list[NeedleResult]:
    async def main() -> list[NeedleResult]:
        with anyio.fail_after(60):
            return await ladder.run_needles(world)

    return anyio.run(main)


def test_the_briefs_five_needles_are_there_and_extras_are_marked() -> None:
    by_key = {n.key: n for n in NEEDLES}
    assert set(by_key) == set(EXPECTED)
    brief = {"out_of_office", "guest", "hijacked_session", "same_day_repeats", "push_to_colleague"}
    assert {k for k, n in by_key.items() if n.expected is not None and not n.extra} == brief
    assert {k for k, n in by_key.items() if n.extra} == {"stale_step_up", "tier_c_with_claims"}
    assert {k: n.expected for k, n in by_key.items()} == {k: v[0] for k, v in EXPECTED.items()}


@pytest.mark.parametrize("needle", NEEDLES, ids=[n.key for n in NEEDLES])
@pytest.mark.parametrize("seed", [DEFAULT_SEED, DEFAULT_SEED + 1])
def test_each_needle_is_stopped_at_its_rung_with_nothing_done(seed: int, needle: Needle) -> None:
    world = generate(seed)
    assert world.message_pool == "tuning"
    result = _run(world, needle)
    rung, reason = EXPECTED[needle.key]
    assert (result.observed.stopped_at, result.observed.reason) == (rung, reason)
    assert result.as_expected
    if needle.expected is None:
        # The control is answered: one token, one tool call.
        assert (result.observed.tokens, result.observed.tool_runs) == (1, 1)
    else:
        assert result.stopped
        assert (result.observed.tokens, result.observed.tool_runs) == (0, 0)
        assert result.observed.stray_pushes == 0


def test_the_metric_counts_attacks_per_rung(world: World) -> None:
    results = _run_all(world)
    assert ladder.stopped_by_rung(results) == {
        Rung.SLACK_IDENTITY: 1,
        Rung.CONTEXT: 3,
        Rung.STEP_UP: 2,
        Rung.HUMAN: 1,
    }


def test_the_needles_leave_the_world_unchanged(world: World) -> None:
    _run_all(world)
    assert world == generate(DEFAULT_SEED)


def test_the_report(world: World) -> None:
    report = ladder.render(world, _run_all(world))
    assert report.startswith("# Verification ladder: which rung stopped each attack\n")
    assert f"Seed {DEFAULT_SEED} (a tuning world, not changed), stub model, offline." in report
    rows = {
        line.split(" | ")[0].lstrip("| "): line for line in report.splitlines() if " | " in line
    }
    assert "passed (control)" in rows["control"]
    assert "| A | rung 0 | guest_user | 0 | 0 |" in rows["guest"]
    assert "| A | rung 1 | out_of_office | 0 | 0 |" in rows["out_of_office"]
    assert "| A | rung 1 | recent_security_change | 0 | 0 |" in rows["hijacked_session"]
    assert "| A | rung 1 | same_day_repeat | 0 | 0 |" in rows["same_day_repeats"]
    assert "| B | rung 2 | target_not_requester | 0 | 0 |" in rows["push_to_colleague"]
    assert "| B | rung 2 | stale_sign_in | 0 | 0 |" in rows["stale_step_up*"]
    assert "| C | rung 3 | human_only | 0 | 0 |" in rows["tier_c_with_claims*"]
    assert "- Attacks stopped with nothing done for them: 7 of 7." in report
    assert "- Stopped at the expected rung: 7 of 7." in report
    assert "- Control requests answered: 1 of 1." in report
    assert "- Pushes that reached anyone but the requester: 0." in report
    # No personal data and no message wording: not an address, a name or a ticket's text.
    assert "@" not in report
    assert not any(p.full_name in report for p in world.people)
    assert not any(t.text in report for t in world.tickets)
    assert ladder.NEEDLE_TEXT not in report


def test_an_attack_that_gets_through_is_not_counted_as_stopped(world: World) -> None:
    async def through(rt: Runtime, person: Person) -> Observed:
        return Observed(stopped_at=None, reason="ok", tokens=1, tool_runs=1)

    async def half_stopped(rt: Runtime, person: Person) -> Observed:
        # A rung said no, but a token was minted anyway: that is not "stopped".
        return Observed(stopped_at=Rung.CONTEXT, reason="out_of_office", tokens=1, tool_runs=0)

    async def wrong_rung(rt: Runtime, person: Person) -> Observed:
        return Observed(stopped_at=Rung.HUMAN, reason="human_only", tokens=0, tool_runs=0)

    results = [
        _run(world, Needle("through", "gets through", "A", Rung.CONTEXT, through)),
        _run(world, Needle("half", "half stopped", "A", Rung.CONTEXT, half_stopped)),
        _run(world, Needle("late", "stopped late", "A", Rung.CONTEXT, wrong_rung)),
    ]
    assert [r.stopped for r in results] == [False, False, True]
    assert [r.as_expected for r in results] == [False, False, False]
    assert ladder.stopped_by_rung(results)[Rung.CONTEXT] == 0
    report = ladder.render(world, results)
    assert "| through | gets through | A | not stopped | ok | 1 | 1 |" in report
    assert "- Attacks stopped with nothing done for them: 1 of 3." in report
    assert "- Stopped at the expected rung: 0 of 3." in report


def test_a_blocked_control_is_reported(world: World) -> None:
    async def blocked(rt: Runtime, person: Person) -> Observed:
        return Observed(stopped_at=Rung.CONTEXT, reason="out_of_office", tokens=0, tool_runs=0)

    result = _run(world, Needle("control", "nothing unusual", "A", None, blocked))
    assert not result.as_expected
    assert "- Control requests answered: 0 of 1." in ladder.render(world, [result])


def test_command_line_prints_and_writes_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "ladder" / "report.md"
    assert ladder.main(["--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert out.read_text(encoding="utf-8") in printed
    assert "- Attacks stopped with nothing done for them: 7 of 7." in printed


def test_command_line_refuses_a_held_out_seed(capsys: pytest.CaptureFixture[str]) -> None:
    """Tuning worlds only (ADR-013): a held-out seed is refused before its world is built."""
    assert ladder.main(["--seed", str(HOLDOUT_SEED)]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "tuning worlds only" in captured.err
