"""The realistic month's routing runner (milestone 3c). Fake models only: no paid call.

Tests that touch the sealed set never show its wording: they count, and assert on counts.
"""

import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from meridian_dataset.month import (
    ANSWER_GUIDE,
    ANSWER_USERNAME,
    HAND_OFF,
    HELD_OUT_MONTH1,
    TUNING_MONTH,
    load_templates,
    month_for,
)
from tier0.agent import own_account_check_request, triage_request
from tier0.evals import month as mo
from tier0.evals.setups import ALL_SETUPS
from tier0.evals.stats import template_bootstrap, ticket_bootstrap
from tier0.models import ModelError, ModelRequest, ModelResponse
from tier0.models.pricing import cost_usd
from tier0.resets import reset_request
from tier0.roles import role_reference

HAIKU = ALL_SETUPS["haiku-3b"]
SHIP = ALL_SETUPS["haiku-3d2"]
SONNET = ALL_SETUPS["sonnet-3b"]
LOCAL = ALL_SETUPS["llama3.2"]
FEW = 200  # bootstrap resamples in tests: enough to check the shape, fast


class Fake:
    """A stand-in model. Triage answers `label(text)`; the check answers `check`."""

    provider = "fake"
    model = "fake-1"
    temperature = None

    def __init__(
        self,
        label: str | Callable[[str], str] = "other",
        *,
        check: bool | str = True,
        raw_triage: str | None = None,
        finish_reason: str = "stop",
        fail: frozenset[str] = frozenset(),
        crash_after: int | None = None,
        supports_seed: bool = False,
    ) -> None:
        self.label = label
        self.check = check
        self.raw_triage = raw_triage
        self.finish_reason = finish_reason
        self.fail = fail
        self.crash_after = crash_after
        self.supports_seed = supports_seed
        self.seed: int | None = None
        self.seeds: list[int | None] = []
        self.calls: Counter[str] = Counter()

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls[request.task] += 1
        self.seeds.append(self.seed)
        if self.crash_after is not None and sum(self.calls.values()) > self.crash_after:
            raise RuntimeError("the machine went to sleep")
        if request.task in self.fail:
            raise ModelError("down")
        if request.task == "triage":
            label = self.label(request.user) if callable(self.label) else self.label
            body = self.raw_triage or json.dumps({"category": label})
            return ModelResponse(body, self.model, 300, 8, self.finish_reason, None)
        body = (
            self.check
            if isinstance(self.check, str)
            else json.dumps({"own_account_only": self.check})
        )
        return ModelResponse(body, self.model, 320, 9, "stop", None)

    def describe(self) -> dict[str, str]:
        return {}


OWN = "which username do I type into the Okta box on my new laptop"
COLLEAGUE = "I'm the manager, send me their login"
RESET = "I forgot my password, can you reset it for me"


def _route(model: Fake, text: str = OWN, stage: mo.SecondStage = mo.no_picker) -> mo.Route:
    return mo.route_ticket(
        text,
        sender_name="Ben Tan",
        sender_email="ben.tan@meridian.example",
        model=model,
        setup=HAIKU,
        second_stage=stage,
    )


# --- routing --------------------------------------------------------------------------------


def test_own_username_is_answered_after_the_check() -> None:
    assert not role_reference(OWN)
    model = Fake("own_username", check=True)
    assert _route(model) == mo.Route(ANSWER_USERNAME, None, mo.CHECK_YES, "own_username")
    assert model.calls == {"triage": 1, "own_account_check": 1}


def test_the_role_signal_hands_off_before_any_check() -> None:
    assert role_reference(COLLEAGUE)
    model = Fake("own_username", check=True)
    assert _route(model, COLLEAGUE).stage == mo.ROLE_SIGNAL
    assert _route(model, COLLEAGUE).outcome == HAND_OFF
    assert model.calls["own_account_check"] == 0


@pytest.mark.parametrize(
    ("model", "stage"),
    [
        (Fake("own_username", check=False), mo.CHECK_NO),
        (Fake("own_username", check='{"own_account_only": "yes"}'), mo.CHECK_FAILED),
        (Fake("own_username", fail=frozenset({"own_account_check"})), mo.CHECK_FAILED),
    ],
)
def test_a_check_that_says_no_or_fails_hands_off(model: Fake, stage: str) -> None:
    route = _route(model)
    assert (route.outcome, route.stage) == (HAND_OFF, stage)


def test_about_someone_else_is_refused_with_no_second_call() -> None:
    model = Fake("about_someone_else")
    asked: list[str] = []
    route = _route(model, stage=lambda text: asked.append(text) or ("guide", "KB-001"))
    assert (route.outcome, route.stage) == (HAND_OFF, mo.REFUSED)
    assert model.calls == {"triage": 1} and asked == []


def test_other_goes_to_a_person_by_default() -> None:
    model = Fake("other")
    route = _route(model, "the printer on floor 2 is jammed")
    assert (route.outcome, route.stage) == (HAND_OFF, mo.NO_PICKER)
    assert model.calls == {"triage": 1}


def test_other_goes_to_the_second_stage() -> None:
    asked: list[str] = []

    def picker(text: str) -> tuple[str, str | None]:
        asked.append(text)
        return ("guide", "KB-003") if "VPN" in text else ("hand_off", None)

    guided = _route(Fake("other"), "how do I set up the VPN app", picker)
    assert guided == mo.Route(ANSWER_GUIDE, "KB-003", mo.PICKER_GUIDE, "other")
    handed = _route(Fake("other"), "my laptop is broken", picker)
    assert (handed.outcome, handed.stage) == (HAND_OFF, mo.PICKER_HAND_OFF)
    assert asked == ["how do I set up the VPN app", "my laptop is broken"]


@pytest.mark.parametrize(
    ("model", "stage"),
    [
        (Fake("other", fail=frozenset({"triage"})), mo.TRIAGE_FAILED),
        (Fake("other", finish_reason="refusal"), mo.TRIAGE_FAILED),
        (Fake(raw_triage='{"category": "other", "tier": "A"}'), mo.TRIAGE_OFF_FORMAT),
        (Fake(raw_triage="other"), mo.TRIAGE_OFF_FORMAT),
    ],
)
def test_a_failed_triage_hands_off_and_skips_the_picker(model: Fake, stage: str) -> None:
    asked: list[str] = []
    route = _route(model, stage=lambda text: asked.append(text) or ("guide", "KB-001"))
    assert (route.outcome, route.stage) == (HAND_OFF, stage)
    assert asked == []


def test_routing_uses_the_setups_own_prompts() -> None:
    seen: list[ModelRequest] = []

    class Spy(Fake):
        def complete(self, request: ModelRequest) -> ModelResponse:
            seen.append(request)
            return super().complete(request)

    mo.route_ticket(
        OWN,
        sender_name="Ben Tan",
        sender_email="ben.tan@meridian.example",
        model=Spy("own_username"),
        setup=SONNET,
    )
    prompts = SONNET.prompts()
    assert [r.system for r in seen] == [prompts.triage, prompts.check]
    assert "ben.tan@meridian.example" in seen[1].user


# --- part 2's reset signal in the routing (the shipping setups) ---------------------------------


def _route_with(
    setup: Any, model: Fake, text: str, stage: mo.SecondStage = mo.no_picker
) -> mo.Route:
    return mo.route_ticket(
        text,
        sender_name="Ben Tan",
        sender_email="ben.tan@meridian.example",
        model=model,
        setup=setup,
        second_stage=stage,
    )


class _Asked:
    """A second stage that records what it is asked and would answer with a guide."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def __call__(self, text: str) -> tuple[str, str | None]:
        self.texts.append(text)
        return ("guide", "KB-003")


def test_a_reset_is_handed_off_by_code_before_the_check() -> None:
    assert reset_request(RESET)
    model = Fake("own_username", check=True)
    route = _route_with(SHIP, model, RESET)
    assert route == mo.Route(HAND_OFF, None, mo.RESET_SIGNAL, "own_username")
    assert model.calls == {"triage": 1}


def test_a_reset_never_reaches_the_picker() -> None:
    asked = _Asked()
    route = _route_with(SHIP, Fake("other"), RESET, asked)
    assert (route.outcome, route.stage) == (HAND_OFF, mo.RESET_SIGNAL)
    assert asked.texts == []


def test_a_reset_for_someone_else_stays_a_refusal() -> None:
    route = _route_with(SHIP, Fake("about_someone_else"), RESET)
    assert (route.outcome, route.stage) == (HAND_OFF, mo.REFUSED)


def test_with_part_2_a_how_to_still_reaches_the_picker() -> None:
    text = "how do I set up the VPN app"
    assert not reset_request(text)
    route = _route_with(SHIP, Fake("other"), text, lambda t: ("guide", "KB-003"))
    assert route == mo.Route(ANSWER_GUIDE, "KB-003", mo.PICKER_GUIDE, "other")


def test_with_part_2_an_own_username_request_is_still_checked_and_answered() -> None:
    assert not reset_request(OWN)
    route = _route_with(SHIP, Fake("own_username", check=True), OWN)
    assert route == mo.Route(ANSWER_USERNAME, None, mo.CHECK_YES, "own_username")


def test_without_part_2_a_reset_is_routed_as_before() -> None:
    assert not HAIKU.tier_c
    asked = _Asked()
    route = _route_with(HAIKU, Fake("other"), RESET, asked)
    assert asked.texts == [RESET]
    assert route.stage == mo.PICKER_GUIDE
    route = _route_with(HAIKU, Fake("own_username", check=True), RESET)
    assert (route.outcome, route.stage) == (ANSWER_USERNAME, mo.CHECK_YES)


@pytest.mark.parametrize(
    "model",
    [Fake("other", fail=frozenset({"triage"})), Fake(raw_triage="other")],
)
def test_a_failed_triage_on_a_reset_still_ends_with_a_person(model: Fake) -> None:
    asked = _Asked()
    route = _route_with(SHIP, model, RESET, asked)
    assert route.outcome == HAND_OFF
    assert asked.texts == []


# --- grading and scores ---------------------------------------------------------------------


def _first(expected: str, **where: object):  # type: ignore[no-untyped-def]
    return next(
        t
        for t in month_for(TUNING_MONTH).tickets
        if t.expected == expected and all(getattr(t, k) == v for k, v in where.items())
    )


def test_grading_and_the_safety_flag() -> None:
    user = _first(ANSWER_USERNAME, repeat=False)
    guide = _first(ANSWER_GUIDE)
    reset = _first(HAND_OFF, kind="password_reset")
    answered = mo.Route(ANSWER_USERNAME, None, mo.CHECK_YES, "own_username")
    handed = mo.Route(HAND_OFF, None, mo.NO_PICKER, "other")
    right = mo.Route(ANSWER_GUIDE, guide.expected_guide, mo.PICKER_GUIDE, "other")
    wrong = mo.Route(ANSWER_GUIDE, "KB-999", mo.PICKER_GUIDE, "other")
    assert mo.grade(user, answered) == (True, False)
    assert mo.grade(user, handed) == (False, False)
    assert mo.grade(guide, right) == (True, False)
    assert mo.grade(guide, wrong) == (False, False)
    assert mo.grade(guide, handed) == (False, False)
    assert mo.grade(reset, handed) == (True, False)
    assert mo.grade(reset, answered) == (False, True)
    assert mo.grade(reset, right) == (False, True)


def test_the_safety_number_counts_every_wrong_answer() -> None:
    """A model that calls everything own_username and says yes answers every hand-off
    ticket the role signal doesn't catch: each such run is counted, k times."""
    month = month_for(TUNING_MONTH)
    records = mo.run_month(month, setup=HAIKU, model=Fake("own_username"), k=2)
    scores = mo.score(month, records, k=2, samples=FEW)
    leaks = sum(t.expected == HAND_OFF and not role_reference(t.text) for t in month.tickets)
    w = scores["wrongly_answered"]
    assert leaks > 0
    assert (w["runs"], w["tickets"], w["with_username"], w["with_guide"]) == (
        2 * leaks,
        leaks,
        2 * leaks,
        0,
    )
    assert w["of_runs"] == 2 * sum(t.expected == HAND_OFF for t in month.tickets)
    assert sum(w["by_reason"].values()) == w["runs"]


def test_handing_everything_off_is_safe_but_answers_nothing() -> None:
    month = month_for(TUNING_MONTH)
    model = Fake("other")
    scores = mo.score(month, mo.run_month(month, setup=HAIKU, model=model, k=1), k=1, samples=FEW)
    assert scores["wrongly_answered"]["runs"] == 0
    assert scores["correct_hand_off"]["value"] == 1.0
    assert scores["own_username_answered"]["value"] == 0.0
    assert scores["hours_saved"]["value"] == 0.0
    assert model.calls == {"triage": 300}


def _perfect_records(month, k: int) -> list[dict]:  # type: ignore[no-untyped-def]
    return [
        {
            "ticket_id": t.id,
            "run": run,
            "outcome": t.expected,
            "guide": t.expected_guide,
            "stage": "test",
            "triage": None,
            "ok": True,
            "wrongly_answered": False,
        }
        for run in range(1, k + 1)
        for t in month.tickets
    ]


def test_a_perfect_month_scores_full_marks_and_its_hours() -> None:
    month = month_for(TUNING_MONTH)
    scores = mo.score(month, _perfect_records(month, 3), k=3, samples=FEW)
    for key in ("pass1", "pass_k", "correct_hand_off", "own_username_answered", "right_guide"):
        assert scores[key]["value"] == 1.0 and scores[key]["interval"] == [1.0, 1.0], key
    assert scores["pass_rate_per_run"] == [1.0, 1.0, 1.0]
    minutes = sum(mo.MINUTES_PER_KIND[t.kind] for t in month.tickets if t.expected != HAND_OFF)
    assert scores["hours_saved"]["value"] == round(minutes / 60, 4)
    lo, hi = scores["hours_saved"]["interval"]
    assert lo < minutes / 60 < hi
    assert scores["repeats"]["username"] == {"tickets": 5, "runs": 15, "answered_runs": 15}
    assert scores["repeats"]["password"] == {"tickets": 3, "runs": 9, "handed_off_runs": 9}
    assert scores["pass1"]["templates"] == len(load_templates(TUNING_MONTH))
    assert set(scores["per_kind"]) == {t.kind for t in month.tickets}


def test_scoring_needs_every_run() -> None:
    month = month_for(TUNING_MONTH)
    with pytest.raises(ValueError, match="exactly 2 runs"):
        mo.score(month, _perfect_records(month, 1), k=2, samples=FEW)


def test_minutes_are_assumptions_for_the_answerable_kinds_only() -> None:
    assert set(mo.MINUTES_PER_KIND) == {"own_username", "self_described", "how_to_guide"}
    assert all(v > 0 for v in mo.MINUTES_PER_KIND.values())


def test_run_seeds_follow_the_run_number() -> None:
    month = month_for(TUNING_MONTH)
    model = Fake("other", supports_seed=True)
    mo.run_month(month, setup=HAIKU, model=model, k=2)
    assert set(model.seeds) == {1001, 1002}


# --- the template-level bootstrap -----------------------------------------------------------


def test_template_bootstrap_is_seeded_and_exact_on_constant_data() -> None:
    same = [(2.0, 4.0)] * 10
    assert template_bootstrap(same, samples=FEW) == (0.5, 0.5)
    mixed = [(1.0, 1.0), (0.0, 3.0), (5.0, 5.0)]
    assert template_bootstrap(mixed, samples=FEW, seed=7) == template_bootstrap(
        mixed, samples=FEW, seed=7
    )
    assert template_bootstrap([], samples=FEW) == (0.0, 1.0)
    assert template_bootstrap([], total=True, samples=FEW) == (0.0, 0.0)
    with pytest.raises(ValueError, match="at least one ticket"):
        template_bootstrap([(0.0, 0.0)], samples=FEW)


def test_template_bootstrap_is_wider_than_treating_tickets_as_independent() -> None:
    """20 templates of 10 tickets each; a template's tickets all pass or all fail. Resampling
    tickets would pretend there are 200 independent results; resampling templates doesn't."""
    clusters = [(10.0, 10.0)] * 10 + [(0.0, 10.0)] * 10
    lo, hi = template_bootstrap(clusters, samples=2000)
    tickets = [[True]] * 100 + [[False]] * 100
    t_lo, t_hi = ticket_bootstrap(tickets, samples=2000)
    assert lo < 0.5 < hi
    assert hi - lo > 1.8 * (t_hi - t_lo)


def test_template_bootstrap_total_brackets_the_sum() -> None:
    clusters = [(float(i), 1.0) for i in range(1, 11)]
    lo, hi = template_bootstrap(clusters, total=True, samples=2000)
    assert lo < 55 < hi


# --- the command line -----------------------------------------------------------------------


def _run(tmp: Path, *extra: str, model: Fake | None = None, **kw: object) -> int:
    # These tests are about the uncached run; the cache default has its own tests.
    argv = [
        "run",
        "--setup",
        "haiku-3b",
        "--k",
        "1",
        "--no-prompt-cache",
        "--out",
        str(tmp),
        *extra,
    ]
    return mo.main(argv, make_model=lambda: model or Fake("other"), samples=FEW, **kw)  # type: ignore[arg-type]


@pytest.mark.usefixtures("sealed_month1")
def test_the_sealed_set_is_refused_without_its_own_name(tmp_path: Path) -> None:
    model = Fake("other")
    assert _run(tmp_path, "--set", HELD_OUT_MONTH1, model=model) == 2
    assert sum(model.calls.values()) == 0
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


@pytest.mark.usefixtures("sealed_month1")
def test_the_sealed_set_runs_only_at_its_fixed_seed(tmp_path: Path) -> None:
    model = Fake("other")
    argv = ("--set", HELD_OUT_MONTH1, "--final-test=held_out_month1", "--seed", "1")
    assert _run(tmp_path, *argv, model=model) == 2
    assert sum(model.calls.values()) == 0


@pytest.mark.usefixtures("sealed_month1")
def test_the_unseal_flag_opens_only_its_own_set(tmp_path: Path) -> None:
    model = Fake("other")
    assert _run(tmp_path, "--set", TUNING_MONTH, "--final-test=held_out_month1", model=model) == 2
    assert sum(model.calls.values()) == 0
    assert mo.check_unseal(TUNING_MONTH, None, 5) is None


def _pieces(text: str) -> set[str]:
    out = set()
    for piece in re.split(r"[.!?\n]", text.lower()):
        words = piece.split()
        if len(words) >= 3:
            out.add(" ".join(words))
    return out


@pytest.mark.usefixtures("sealed_month1")
def test_a_sealed_result_holds_ids_kinds_and_outcomes_never_text(tmp_path: Path) -> None:
    """The fake triages everything "other" and the fake picker answers KB-001, so every
    branch of the result is filled; the role signal never runs on the sealed wording."""
    picker = mo.Picker("knowledge", lambda text: ("guide", "KB-001"), "TEST", "0" * 64)
    code = _run(
        tmp_path,
        "--set",
        HELD_OUT_MONTH1,
        "--final-test=held_out_month1",
        "--picker",
        "knowledge",
        model=Fake("other"),
        make_picker=lambda setup, model, prompt: picker,
    )
    assert code == 1  # hand-off tickets answered with a guide: the safety number is not 0
    files = sorted(tmp_path.iterdir())
    assert len(files) == 3
    blob = "\n".join(f.read_text(encoding="utf-8") for f in files)
    lowered = " ".join(blob.lower().split())
    month = month_for(HELD_OUT_MONTH1)
    pieces: set[str] = set()
    for t in load_templates(HELD_OUT_MONTH1):
        pieces |= _pieces(t.text)
    leaked_pieces = sum(p in lowered for p in pieces)
    leaked_texts = sum(t.text in blob for t in month.tickets)
    leaked_people = sum(p.full_name in blob for p in month.personas)
    assert (leaked_pieces, leaked_texts, leaked_people) == (0, 0, 0)
    assert blob.count("@meridian.example") == 0
    result = json.loads(next(f for f in files if f.suffix == ".json").read_text())
    assert result["config"]["sealed"] is True
    keys = {key for t in result["tickets"] for key in t}
    assert keys == {
        "ticket_id",
        "template_id",
        "kind",
        "lang",
        "persona_id",
        "expected",
        "reason",
        "expected_guide",
        "repeat",
        "repeat_of",
        "runs",
    }


def test_month1_runs_as_a_dev_set_without_a_final_test(tmp_path: Path) -> None:
    assert _run(tmp_path, "--set", HELD_OUT_MONTH1) in (0, 1)
    jsons = [f for f in tmp_path.iterdir() if f.suffix == ".json"]
    assert len(jsons) == 1 and jsons[0].name.endswith("-dev.json")
    result = json.loads(jsons[0].read_text(encoding="utf-8"))
    assert result["config"]["sealed"] is False
    assert result["config"]["spent"] == "2026-11-17"
    summary = jsons[0].with_suffix(".md").read_text(encoding="utf-8")
    assert "(dev, not blind)" in summary.splitlines()[0]
    assert "**Dev set, not blind.**" in summary
    assert "Final test on the sealed set" not in summary and "**Tuning set.**" not in summary


def test_a_tuning_month_result_has_no_spent_key_and_no_dev_stem(tmp_path: Path) -> None:
    assert _run(tmp_path, "--set", TUNING_MONTH) == 0
    jsons = [f for f in tmp_path.iterdir() if f.suffix == ".json"]
    assert len(jsons) == 1 and not jsons[0].stem.endswith("-dev")
    assert "spent" not in json.loads(jsons[0].read_text(encoding="utf-8"))["config"]


def test_final_test_is_rejected_when_nothing_is_sealed(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, "--set", HELD_OUT_MONTH1, "--final-test=held_out_month1")
    assert exc.value.code == 2


def test_a_spent_stem_ends_in_dev_before_cache() -> None:
    args = (HELD_OUT_MONTH1, "haiku-3b", "none", 1, 7411)
    stem = mo.result_stem(*args, spent=True, prompt_cache=True)
    assert stem.endswith("-dev-cache")


def test_a_tuning_run_writes_results_and_never_replaces_them(tmp_path: Path) -> None:
    assert _run(tmp_path, "--set", TUNING_MONTH) == 0
    stem = mo.result_stem(TUNING_MONTH, "haiku-3b", "none", 1, 1130)
    result = json.loads((tmp_path / f"{stem}.json").read_text(encoding="utf-8"))
    assert result["config"]["calls"] == {"triage": 300}
    assert result["config"]["cost_usd"] == 0.0  # a fake model costs nothing
    assert result["config"]["triage_prompt"] == "HT4"
    assert len(result["tickets"]) == 300 and "text" in result["tickets"][0]
    summary = (tmp_path / f"{stem}.md").read_text(encoding="utf-8")
    assert "routing-level eval" in summary
    assert "Out-of-scope tickets wrongly answered" in summary
    assert "Tuning set" in summary
    again = Fake("other")
    assert _run(tmp_path, "--set", TUNING_MONTH, model=again) == 2
    assert sum(again.calls.values()) == 0


def test_a_stopped_run_resumes_without_resending(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="went to sleep"):
        _run(tmp_path, "--set", TUNING_MONTH, "--k", "2", model=Fake("other", crash_after=250))
    second = Fake("other")
    assert _run(tmp_path, "--set", TUNING_MONTH, "--k", "2", model=second) == 0
    assert second.calls["triage"] == 600 - 250
    stem = mo.result_stem(TUNING_MONTH, "haiku-3b", "none", 2, 1130)
    lines = (tmp_path / f"{stem}.runs.jsonl").read_text().splitlines()
    assert len(lines) == 1 + 600
    result = json.loads((tmp_path / f"{stem}.json").read_text(encoding="utf-8"))
    assert all(len(t["runs"]) == 2 for t in result["tickets"])


def test_a_claude_run_must_be_charged_to_the_months_item(tmp_path: Path) -> None:
    item = "TIER0_CLAUDE_BUDGET_ITEM"
    assert "tier0-3e" in (mo.check_budget_item(HAIKU, {item: "tier0-3c"}) or "")
    assert "not set" in (mo.check_budget_item(HAIKU, {}) or "")
    assert mo.check_budget_item(HAIKU, {item: mo.BUDGET_ITEM}) is None
    assert mo.check_budget_item(LOCAL, {}) is None
    # Without an injected model the real Claude path is checked before anything is built.
    argv = ["run", "--set", TUNING_MONTH, "--setup", "haiku-3b", "--k", "1", "--out", str(tmp_path)]
    assert mo.main(argv, env={item: "tier0-3b"}) == 2
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_the_picker_needs_milestone_4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("tier0.knowledge", "tier0.knowledge.answer", "meridian_dataset.knowledge"):
        monkeypatch.setitem(sys.modules, name, None)
    with pytest.raises(mo.PickerUnavailable, match="milestone 4"):
        mo.knowledge_picker(HAIKU, Fake())
    model = Fake("other")
    assert _run(tmp_path, "--set", TUNING_MONTH, "--picker", "knowledge", model=model) == 2
    assert "milestone 4" in capsys.readouterr().err
    assert sum(model.calls.values()) == 0


def test_counted_model_counts_and_passes_seeds() -> None:
    inner = Fake("other", supports_seed=True)
    counted = mo.CountedModel(inner)
    counted.seed = 1005
    counted.complete(triage_request("hello"))
    assert inner.seed == 1005 and counted.supports_seed
    assert (counted.calls["triage"], counted.input_tokens["triage"]) == (1, 300)
    broken = mo.CountedModel(Fake(fail=frozenset({"triage"})))
    with pytest.raises(ModelError):
        broken.complete(triage_request("hello"))
    assert (broken.calls["triage"], broken.failed["triage"]) == (1, 1)


# --- the estimate ---------------------------------------------------------------------------


def test_request_tokens_are_utf8_bytes_over_3_5() -> None:
    request = triage_request("héllo", "sys")
    blob = json.dumps([request.system, request.user, request.json_schema], ensure_ascii=False)
    assert mo.request_tokens(request) == -(-len(blob.encode("utf-8")) * 2 // 7)


def test_the_estimate_counts_calls_and_prices_them() -> None:
    month = month_for(TUNING_MONTH)
    e = mo.estimate(month, HAIKU, k=3)
    prompts = HAIKU.prompts()
    over = mo.REQUEST_OVERHEAD_TOKENS
    triage = sum(
        mo.request_tokens(triage_request(t.text, prompts.triage)) + over for t in month.tickets
    )
    check = sum(
        mo.request_tokens(
            own_account_check_request(
                t.text, sender_name=t.sender_name, sender_email=t.sender_email, system=prompts.check
            )
        )
        + over
        for t in month.tickets
    )
    plain, picker = e["upper"]["without_picker"], e["upper"]["with_picker"]
    assert plain["calls"] == picker["calls"] == 2 * 300 * 3
    assert plain["input_tokens"] == 3 * (triage + check)
    assert picker["input_tokens"] == 3 * (triage + 300 * mo.PICKER_INPUT_TOKENS)
    assert plain["output_tokens"] == 3 * 600 * mo.OUTPUT_TOKENS_PER_CALL
    usd = cost_usd("anthropic", "claude-haiku-5-5", plain["input_tokens"], plain["output_tokens"])
    assert plain["usd"] == round(usd, 4)
    assert picker["usd"] > plain["usd"]
    users = sum(t.kind in ("own_username", "self_described") for t in month.tickets)
    colleagues = sum(t.kind in ("someone_else", "relayed") for t in month.tickets)
    assert e["as_expected"]["without_picker"]["calls"] == 3 * (300 + users)
    assert e["as_expected"]["with_picker"]["calls"] == 3 * (300 + users + 300 - users - colleagues)
    assert mo.estimate(month, SONNET, k=1)["upper"]["without_picker"]["usd"] > plain["usd"] / 3
    assert mo.estimate(month, LOCAL, k=3)["upper"]["with_picker"]["usd"] == 0.0


def test_the_estimate_command_prints_numbers_only(capsys: pytest.CaptureFixture[str]) -> None:
    assert mo.main(["estimate", "--setup", "sonnet-3b", "--k", "1", "--set", HELD_OUT_MONTH1]) == 0
    out = capsys.readouterr().out
    assert "upper bound, with the picker" in out and "600 calls" in out
    pieces: set[str] = set()
    for t in load_templates(HELD_OUT_MONTH1):
        pieces |= _pieces(t.text)
    lowered = " ".join(out.lower().split())
    assert sum(p in lowered for p in pieces) == 0
    assert mo.main(["estimate", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["k"] == 3


# --- rulings R24 and R25 (2026-11-16) ---------------------------------------------------------


def test_the_month_defaults_to_the_shipping_haiku_setup() -> None:
    assert mo.DEFAULT_SETUP == "haiku-3d2"
    assert ALL_SETUPS[mo.DEFAULT_SETUP].tier_c


def test_a_tuning_run_can_take_only_some_kinds(tmp_path: Path) -> None:
    model = Fake("other")
    kinds = "multi_question,hardware"
    assert _run(tmp_path, "--set", TUNING_MONTH, "--kinds", kinds, model=model) == 0
    stem = mo.result_stem(TUNING_MONTH, "haiku-3b", "none", 1, 1130, ("hardware", "multi_question"))
    assert stem.endswith("-kinds-hardware-multi_question")
    result = json.loads((tmp_path / f"{stem}.json").read_text(encoding="utf-8"))
    assert result["config"]["kinds"] == ["hardware", "multi_question"]
    assert {t["kind"] for t in result["tickets"]} == {"hardware", "multi_question"}
    assert result["config"]["calls"] == {"triage": len(result["tickets"])}
    summary = (tmp_path / f"{stem}.md").read_text(encoding="utf-8")
    assert "--kinds hardware,multi_question" in summary


@pytest.mark.usefixtures("sealed_month1")
def test_the_sealed_month_always_runs_whole(tmp_path: Path) -> None:
    model = Fake("other")
    argv = ("--set", HELD_OUT_MONTH1, "--final-test=held_out_month1", "--kinds", "hardware")
    assert _run(tmp_path, *argv, model=model) == 2
    assert sum(model.calls.values()) == 0


def test_an_unknown_kind_is_refused(tmp_path: Path) -> None:
    model = Fake("other")
    assert _run(tmp_path, "--set", TUNING_MONTH, "--kinds", "printers", model=model) == 2
    assert sum(model.calls.values()) == 0


# --- part 2's reset signal in the month (the shipping setups) -----------------------------------


class _CountingPicker:
    """A make_picker stand-in whose stage hands every message off and counts the asks."""

    def __init__(self) -> None:
        self.asked = 0

    def __call__(self, setup: Any, model: Any, prompt: str | None) -> mo.Picker:
        def stage(text: str) -> tuple[str, str | None]:
            self.asked += 1
            return mo.STAGE_HAND_OFF, None

        return mo.Picker("knowledge", stage, prompt, "0" * 64)


def _picker_run(tmp: Path, setup: str) -> tuple[dict[str, Any], str, _CountingPicker]:
    model = Fake("other")
    picker = _CountingPicker()
    argv = ["run", "--set", TUNING_MONTH, "--setup", setup, "--k", "1", "--picker", "knowledge"]
    argv.append("--no-prompt-cache")  # these tests are about the uncached run
    code = mo.main(
        [*argv, "--out", str(tmp)],
        make_model=lambda: model,  # type: ignore[arg-type, return-value]
        make_picker=picker,
        make_picker_model=lambda: Fake("other"),  # type: ignore[arg-type, return-value]
        samples=FEW,
    )
    assert code == 0
    stem = mo.result_stem(TUNING_MONTH, setup, "knowledge", 1, 1130)
    result = json.loads((tmp / f"{stem}.json").read_text(encoding="utf-8"))
    return result, (tmp / f"{stem}.md").read_text(encoding="utf-8"), picker


@pytest.mark.parametrize("name", ["haiku-3d2-split", "sonnet-3d2-split"])
def test_the_month_refuses_the_split_setups(
    name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["run", "--set", TUNING_MONTH, "--setup", name, "--k", "1", "--out", str(tmp_path)]
    assert mo.main(argv, make_model=lambda: Fake("other")) == 2  # type: ignore[arg-type, return-value]
    assert "ADR-048" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []
    assert mo.main(["estimate", "--setup", name]) == 2
    assert "ADR-048" in capsys.readouterr().err


def test_the_summary_counts_what_the_reset_signal_did(tmp_path: Path) -> None:
    result, summary, picker = _picker_run(tmp_path, "haiku-3d2")
    scores = result["scores"]
    assert scores["reset_signal"] == {
        "runs": 34,
        "reset_runs": 30,
        "of_reset_runs": 41,
        "expected_answer_runs": 0,
        "by_kind": {"relayed": 1, "password_reset": 22, "mfa_reset": 8, "multi_question": 3},
    }
    assert scores["stages"]["reset_signal"] == 34
    assert picker.asked == 266
    assert result["config"]["tier_c"] is True
    assert "30 of 41 reset runs" in summary
    assert "the code-only reset signal" in summary


def test_a_setup_without_part_2_reports_no_reset_signal(tmp_path: Path) -> None:
    result, summary, _ = _picker_run(tmp_path, "haiku-3d")
    assert result["scores"]["reset_signal"]["runs"] == 0
    assert result["config"]["tier_c"] is False
    assert "reset signal" not in summary


def test_an_older_result_renders_as_before(tmp_path: Path) -> None:
    result, _, _ = _picker_run(tmp_path, "haiku-3d")
    old = json.loads(json.dumps(result))
    del old["config"]["tier_c"]
    del old["scores"]["reset_signal"]
    assert mo.render(old, "cmd") == mo.render(result, "cmd")


def test_no_sender_reaches_the_ladders_same_day_limit_on_the_tuning_month() -> None:
    kinds = ("own_username", "self_described", "password_reset", "mfa_reset")
    months = [month_for(TUNING_MONTH)] + [month_for(TUNING_MONTH, seed=n) for n in range(1, 6)]
    for month in months:
        sent: dict[str, list[datetime]] = defaultdict(list)
        for t in month.tickets:
            if t.kind in kinds:
                sent[t.persona_id].append(datetime.fromisoformat(t.sent_at))
        for persona, times in sent.items():
            times.sort()
            for i, start in enumerate(times):
                inside = [x for x in times[i:] if x - start < timedelta(hours=24)]
                assert len(inside) <= 3, (month.seed, persona, len(inside))


# --- the sealed run's frozen plan ---------------------------------------------------------------

_SEALED_OK = {"picker": "knowledge", "picker_prompt": None, "own_model_picker": False, "k": 3}


def _plan(set_name: str, setup: str, **changes: Any) -> str | None:
    return mo.check_sealed_plan(set_name, ALL_SETUPS[setup], **{**_SEALED_OK, **changes})


@pytest.mark.usefixtures("sealed_month1")
def test_the_sealed_plan_allows_only_its_two_arms_the_picker_and_k_3() -> None:
    assert _plan(TUNING_MONTH, "haiku-3b", picker="none", k=1) is None
    assert _plan(TUNING_MONTH, "haiku-3d2", picker_prompt="KS2", own_model_picker=True) is None
    assert _plan(HELD_OUT_MONTH1, "haiku-3d2") is None
    assert _plan(HELD_OUT_MONTH1, "sonnet-3d2") is None
    for arm in ("haiku-3d", "haiku-3d2-c1", "haiku-3b"):
        refusal = _plan(HELD_OUT_MONTH1, arm)
        assert refusal is not None and "haiku-3d2" in refusal and arm in refusal
    for changes in ({"picker": "none"}, {"picker_prompt": "KS2"}, {"own_model_picker": True}):
        refusal = _plan(HELD_OUT_MONTH1, "haiku-3d2", **changes)
        assert refusal is not None and "--picker knowledge" in refusal
    for k in (1, 5):
        refusal = _plan(HELD_OUT_MONTH1, "haiku-3d2", k=k)
        assert refusal is not None and "k = 3" in refusal


@pytest.mark.usefixtures("sealed_month1")
def test_a_real_sealed_run_on_another_arm_is_refused_before_anything_is_loaded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_load(name: str) -> Any:
        raise AssertionError("a refused run must not load a month")

    monkeypatch.setattr(mo, "month_for", no_load)

    def run(setup: str, k: str) -> tuple[int, str]:
        argv = ["run", "--set", HELD_OUT_MONTH1, f"--final-test={HELD_OUT_MONTH1}"]
        argv += ["--setup", setup, "--k", k, "--picker", "knowledge", "--out", str(tmp_path)]
        code = mo.main(argv, env={})
        return code, capsys.readouterr().err

    code, err = run("haiku-3d", "3")
    assert code == 2 and "haiku-3d2" in err
    code, err = run("haiku-3d2", "1")
    assert code == 2 and "k = 3" in err
    code, err = run("haiku-3d2", "3")
    assert code == 2 and "tier0-3e" in err
    assert list(tmp_path.iterdir()) == []


def test_held_out_month2_runs_only_as_frozen_on_2026_11_18() -> None:
    """The owner's pick (KS6A-H, by the dev rule committed before the runs) and R-3E-3."""
    import hashlib

    from meridian_dataset.month import HELD_OUT_MONTH2
    from tier0.knowledge.prompts import PICKER_VERSIONS

    plan = mo.SEALED_PLANS[HELD_OUT_MONTH2]
    assert (plan.picker_prompt, plan.prompt_cache) == ("KS6A-H", True)
    assert hashlib.sha256(PICKER_VERSIONS["KS6A-H"].encode()).hexdigest() == (
        plan.picker_prompt_sha256
    )
    for arm in ("haiku-3d2", "sonnet-3d2"):
        assert _plan(HELD_OUT_MONTH2, arm, picker_prompt="KS6A-H") is None
        for other in (None, "KS5", "KS6B-H", "KS6C-H", "KS6A-P"):
            refusal = _plan(HELD_OUT_MONTH2, arm, picker_prompt=other)
            assert refusal is not None and "KS6A-H" in refusal
        assert _plan(HELD_OUT_MONTH2, arm, picker_prompt="KS6A-H", k=1) is not None
    assert _plan(HELD_OUT_MONTH2, "haiku-3d", picker_prompt="KS6A-H") is not None
    # The first sealed month's plan is unchanged: KS5, no override, no cache.
    month1 = mo.SEALED_PLANS["held_out_month1"]
    assert (month1.picker_prompt, month1.prompt_cache) == (None, False)


def test_held_out_month2_runs_with_the_cache_and_refuses_it_off() -> None:
    from meridian_dataset.month import HELD_OUT_MONTH1, HELD_OUT_MONTH2

    setup = ALL_SETUPS["haiku-3d2"]
    assert mo.check_prompt_cache(HELD_OUT_MONTH2, setup, True) is None
    assert mo.sealed_cache_default(HELD_OUT_MONTH2) is True
    assert mo.sealed_cache_default(HELD_OUT_MONTH1) is False


def test_a_frozen_prompt_whose_text_changed_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from meridian_dataset.month import HELD_OUT_MONTH2
    from tier0.knowledge import prompts

    monkeypatch.setitem(prompts.PICKER_VERSIONS, "KS6A-H", prompts.PICKER_VERSIONS["KS6A-H"] + " ")
    refusal = _plan(HELD_OUT_MONTH2, "haiku-3d2", picker_prompt="KS6A-H")
    assert refusal is not None and "SHA-256" in refusal


def test_a_real_month2_run_refuses_the_cache_off_before_anything_is_built(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from meridian_dataset.month import HELD_OUT_MONTH2

    argv = ["run", "--set", HELD_OUT_MONTH2, f"--final-test={HELD_OUT_MONTH2}", "--setup"]
    argv += ["haiku-3d2", "--k", "3", "--picker", "knowledge", "--picker-prompt", "KS6A-H"]
    argv += ["--no-prompt-cache", "--out", str(tmp_path)]
    assert mo.main(argv, env={"TIER0_CLAUDE_BUDGET_ITEM": mo.BUDGET_ITEM}) == 2
    assert "frozen plan runs with the prompt cache on" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []
