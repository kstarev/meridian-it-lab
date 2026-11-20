"""The month runner counts, per task, the replies that didn't finish normally: cut short by the
reply limit (finish "length") and any other finish but "stop" (a refusal). It only counts: each
such reply is routed exactly as before, and every one of them here is a hand-off.

Fake models and the tuning month only: no paid call, and no test here loads the sealed month.
"""

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from meridian_dataset.month import HAND_OFF, TUNING_MONTH
from tier0.agent import own_account_check_request, triage_request
from tier0.evals import month as mo
from tier0.evals.setups import ALL_SETUPS, SONNET
from tier0.knowledge.gate import gate_request
from tier0.knowledge.picker import NO_GUIDE
from tier0.models import ModelRequest, ModelResponse

FEW = 200  # bootstrap resamples in tests: enough to check the shape, fast
SHIP = ALL_SETUPS["haiku-3d2"]
PICKER_PROMPT = "KS6A-H"  # month3's frozen picker prompt; plain mode, one guide_pick call
OWN = "which username do I type into the Okta box on my new laptop"
HOW_TO = "how do I connect my work laptop to the office wifi in Lisbon"
LINE = 'Replies cut short by the reply limit (finish "length"): '

#: Each task's reply when a test doesn't set one: all finish "stop" and are on the schema.
CLEAN: dict[str, tuple[str, str]] = {
    "triage": (json.dumps({"category": "other"}), "stop"),
    "own_account_check": (json.dumps({"own_account_only": True}), "stop"),
    "howto_gate": (json.dumps({"plain_how_to": True}), "stop"),
    "guide_pick": (json.dumps({"request_type": "how_to", "guide": NO_GUIDE}), "stop"),
}


class Fake:
    """A stand-in model: each task gets its own (text, finish reason)."""

    provider = "fake"
    temperature = None
    supports_seed = False

    def __init__(self, model: str = "fake-1", **replies: tuple[str, str]) -> None:
        self.model = model
        self.seed: int | None = None
        self.replies = CLEAN | replies
        self.calls: Counter[str] = Counter()

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls[request.task] += 1
        text, finish = self.replies[request.task]
        return ModelResponse(text, self.model, 300, 8, finish, None)

    def describe(self) -> dict[str, str]:
        return {}


# --- the adapter ------------------------------------------------------------------------------


def test_the_adapter_counts_each_unfinished_reply_on_its_own_task() -> None:
    inner = Fake(triage=("", "length"), own_account_check=("", "refusal"))
    counted = mo.CountedModel(inner)  # type: ignore[arg-type]
    for _ in range(2):
        counted.complete(triage_request("hello"))
    counted.complete(own_account_check_request(OWN, sender_name="Ben", sender_email="b@x.example"))
    counted.complete(gate_request(HOW_TO, "rules"))
    assert counted.calls == {"triage": 2, "own_account_check": 1, "howto_gate": 1}
    assert counted.cut_short == {"triage": 2}
    assert counted.not_stop == {"own_account_check": 1}
    # A raw provider value is "another finish" too.
    odd = mo.CountedModel(Fake(howto_gate=("", "pause_turn")))  # type: ignore[arg-type]
    odd.complete(gate_request(HOW_TO, "rules"))
    assert (odd.cut_short, odd.not_stop) == (Counter(), Counter({"howto_gate": 1}))


def test_the_adapter_hands_back_the_reply_it_was_given() -> None:
    inner = Fake(triage=("", "length"))
    reply = mo.CountedModel(inner).complete(triage_request("hello"))  # type: ignore[arg-type]
    assert (reply.text, reply.finish_reason) == ("", "length")


# --- routing is unchanged ---------------------------------------------------------------------


def _route(model: Any, text: str) -> mo.Route:
    """One message through the runner's routing, with the gate and month3's picker on `model`."""
    picker = mo.knowledge_picker(SHIP, model, PICKER_PROMPT)
    return mo.route_ticket(
        text,
        sender_name="Ben Tan",
        sender_email="ben.tan@meridian.example",
        model=model,
        setup=SHIP,
        second_stage=picker.stage,
        gate=mo.gate_for("G3"),
    )


#: The task whose reply doesn't finish, the message that reaches it, and where it ends up.
CASES = [
    ("triage", HOW_TO, {"length": mo.TRIAGE_OFF_FORMAT, "refusal": mo.TRIAGE_FAILED}),
    ("own_account_check", OWN, {"length": mo.CHECK_FAILED, "refusal": mo.CHECK_FAILED}),
    ("howto_gate", HOW_TO, {"length": mo.GATE_FAILED, "refusal": mo.GATE_FAILED}),
    ("guide_pick", HOW_TO, {"length": mo.PICKER_HAND_OFF, "refusal": mo.PICKER_HAND_OFF}),
]


@pytest.mark.parametrize("finish", ["length", "refusal"])
@pytest.mark.parametrize(("task", "text", "stages"), CASES)
def test_an_unfinished_reply_is_routed_as_before_and_hands_off(
    task: str, text: str, stages: dict[str, str], finish: str
) -> None:
    replies = {task: ("", finish)}
    if task == "own_account_check":
        replies["triage"] = (json.dumps({"category": "own_username"}), "stop")
    bare = _route(Fake(**replies), text)
    counted = mo.CountedModel(Fake(**replies))  # type: ignore[arg-type]
    assert _route(counted, text) == bare
    assert (bare.outcome, bare.stage) == (HAND_OFF, stages[finish])
    assert counted.calls[task] == 1
    want = Counter({task: 1})
    assert (counted.cut_short, counted.not_stop) == (
        (want, Counter()) if finish == "length" else (Counter(), want)
    )


# --- the run: the result file and its summary -------------------------------------------------


def _run(tmp: Path, triage: Fake, picker: Fake) -> tuple[dict[str, Any], str]:
    """A month3-shaped run on the tuning month: the arm, its gate G3, and the picker on its own
    (Sonnet) adapter, at k = 1."""
    argv = ["run", "--set", TUNING_MONTH, "--setup", SHIP.name, "--k", "1", "--picker"]
    argv += ["knowledge", "--picker-prompt", PICKER_PROMPT, "--gate", "G3", "--no-prompt-cache"]
    code = mo.main(
        [*argv, "--out", str(tmp)],
        make_model=lambda: triage,  # type: ignore[arg-type, return-value]
        make_picker_model=lambda: picker,  # type: ignore[arg-type, return-value]
        samples=FEW,
    )
    assert code == 0  # nothing out of scope was answered
    [path] = tmp.glob("*.json")
    result = json.loads(path.read_text(encoding="utf-8"))
    return result, path.with_suffix(".md").read_text(encoding="utf-8")


def _routes(result: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [
        (t["ticket_id"], r["outcome"], r["stage"]) for t in result["tickets"] for r in t["runs"]
    ]


def test_a_clean_run_reports_zero_for_every_task(tmp_path: Path) -> None:
    result, summary = _run(tmp_path, Fake(), Fake(SONNET))
    c = result["config"]
    assert set(c["calls"]) == {"triage", "howto_gate", "guide_pick"}
    assert c["cut_short"] == c["not_stop"] == {task: 0 for task in c["calls"]}
    assert list(c)[list(c).index("calls") + 1 :][:2] == ["cut_short", "not_stop"]
    assert (
        f"- {LINE}guide_pick 0, howto_gate 0, triage 0. Other replies that did not finish with "
        '"stop" (a refusal, say): guide_pick 0, howto_gate 0, triage 0.'
    ) in summary.splitlines()


def test_a_cut_short_picker_is_counted_and_still_hands_off(tmp_path: Path) -> None:
    clean, _ = _run(tmp_path / "clean", Fake(), Fake(SONNET))
    picker = Fake(SONNET, guide_pick=("", "length"))
    result, summary = _run(tmp_path / "cut", Fake(), picker)
    c = result["config"]
    picks = picker.calls["guide_pick"]
    assert picks > 0 and c["calls"]["guide_pick"] == picks
    assert c["cut_short"] == {"guide_pick": picks, "howto_gate": 0, "triage": 0}
    assert c["not_stop"] == {"guide_pick": 0, "howto_gate": 0, "triage": 0}
    # The same outcome on every ticket as a picker that says "no guide": a hand-off.
    assert _routes(result) == _routes(clean)
    assert {o for _, o, _ in _routes(result)} == {HAND_OFF}
    assert f"{LINE}guide_pick {picks:,}, howto_gate 0, triage 0." in summary


def test_a_refusing_gate_is_counted_apart_and_hands_off(tmp_path: Path) -> None:
    triage = Fake(howto_gate=("", "refusal"))
    picker = Fake(SONNET)
    result, summary = _run(tmp_path, triage, picker)
    c = result["config"]
    gates = triage.calls["howto_gate"]
    assert gates > 0 and picker.calls["guide_pick"] == 0
    assert c["not_stop"] == {"howto_gate": gates, "triage": 0}
    assert c["cut_short"] == {"howto_gate": 0, "triage": 0}
    assert mo.GATE_FAILED in {s for _, _, s in _routes(result)}
    assert {o for _, o, _ in _routes(result)} == {HAND_OFF}
    assert f'"stop" (a refusal, say): howto_gate {gates:,}, triage 0.' in summary


def test_an_older_result_without_the_counts_renders_without_the_line(tmp_path: Path) -> None:
    result, _ = _run(tmp_path, Fake(), Fake(SONNET))
    old = json.loads(json.dumps(result))
    del old["config"]["cut_short"], old["config"]["not_stop"]
    text = mo.render(old, "cmd")
    assert LINE not in text
    lines = mo.render(result, "cmd").splitlines()
    assert text.splitlines() == [x for x in lines if not x.startswith(f"- {LINE}")]
