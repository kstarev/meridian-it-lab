"""Milestone 3e: the picker-fix candidates, their code paths and the dev comparison tool.

Fake models only: no paid call. The tool is tested on fake runs of the tuning and dev months.
"""

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from meridian_dataset import knowledge as kn
from meridian_dataset.month import ANSWER_GUIDE, HAND_OFF, TUNING_MONTH, month_for
from tier0.evals import month as mo
from tier0.evals import picker_3e as p3
from tier0.knowledge import prompts as pr
from tier0.knowledge.answer import (
    FIT_FAILED,
    FIT_NO,
    GUIDE,
    NEEDS_A_PERSON,
    NO_REQUEST,
    OFF_FORMAT,
    SCREEN,
    answer_question,
    decide,
    entries_for,
)
from tier0.knowledge.answer import (
    HAND_OFF as KIND_HAND_OFF,
)
from tier0.knowledge.picker import (
    FITS_SCHEMA,
    Pick,
    fit_request,
    parse_fit,
    parse_pick,
    pick_request,
    pick_schema,
)
from tier0.models.base import ModelError, ModelRequest, ModelResponse

FEW = 200
GUIDES = kn.load_runbooks()
ENTRIES = entries_for(GUIDES)
APPROVED = frozenset(e.id for e in ENTRIES)
POINTER = pr.POINTER_GUIDE
OTHER_GUIDE = next(e.id for e in ENTRIES if e.id != POINTER)
VERSIONS_3E = ("KS6A-H", "KS6A-P", "KS6B-H", "KS6B-P", "KS6C-H", "KS6C-P")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- the pins ---------------------------------------------------------------------------------

# A candidate is never edited after its first score.
PINNED = {
    "KS6A-H": "04fdb9942e214d3e8598c0ff1f127cc97eb28eeb51fc6d350d7417f1f1050888",
    "KS6A-P": "660436d583826441786ce17c8d2679edf05d1ee756d59b0a9cb182cedf885b2e",
    "KS6B-H": "880806adac1e5b2067dbb6c8d983288a9c7b815377594355cd03324beb7bb4f0",
    "KS6B-P": "3a98fdcaca4b9dfb78402a387ce346ac584bccf8d106f4ccae23fb0cec78a5a9",
    "KS6C-H": "45906bd1b29e19d18c3ff282c4fb0e68b83d0d09e50998d7b28ef82ff1415cdf",
    "KS6C-P": "45906bd1b29e19d18c3ff282c4fb0e68b83d0d09e50998d7b28ef82ff1415cdf",
    "FIT_CHECK_H": "a327b30149f5031d6546147de02fdac8e012d645bfaf2ec02dcec05543909e83",
    "FIT_CHECK_P": "669de2785900f16c95f8260a9402481b0e045dad14f83a4324550ab85717fc52",
}


def test_the_candidates_are_pinned_by_hash() -> None:
    for version in VERSIONS_3E:
        assert _sha(pr.PICKER_VERSIONS[version]) == PINNED[version]
    assert _sha(pr.FIT_CHECK_H) == PINNED["FIT_CHECK_H"]
    assert _sha(pr.FIT_CHECK_P) == PINNED["FIT_CHECK_P"]
    assert pr.PICKER_VERSIONS["KS6C-H"] == pr.KS5 == pr.PICKER_VERSIONS["KS6C-P"]


def test_the_candidates_are_outside_the_selection() -> None:
    texts = [pr.PICKER_VERSIONS[v] for v in VERSIONS_3E[:4]]
    assert not set(VERSIONS_3E) & {v for c in pr.CANDIDATES.values() for v in c}
    assert not set(VERSIONS_3E) & set(pr.FROZEN.values())
    assert all(t not in pr.FROZEN.values() for t in texts)
    assert pr.FROZEN == {"claude-haiku-5-5": "KH5", "claude-sonnet-5-5": "KS5"}


def test_the_new_texts_are_not_written_against_the_four_tickets() -> None:
    ks5_lines = set(pr.KS5.splitlines())
    new = [
        line
        for v in VERSIONS_3E[:4]
        for line in pr.PICKER_VERSIONS[v].splitlines()
        if line not in ks5_lines
    ]
    new += [pr.FIT_CHECK_H, pr.FIT_CHECK_P]
    text = "\n".join(new).lower()
    for word in ("anyone there", "laptop", "slow", "spreadsheet", "restart", "kb-0"):
        assert word not in text


def test_only_the_p_variants_mention_the_pointer_guide() -> None:
    assert "how to ask IT for help" in pr.PICKER_VERSIONS["KS6A-P"]
    assert "how to ask IT for help" in pr.PICKER_VERSIONS["KS6B-P"]
    assert "how to ask IT for help" not in pr.PICKER_VERSIONS["KS6A-H"]
    assert "how to ask IT for help" not in pr.PICKER_VERSIONS["KS6B-H"]
    assert "how to ask IT for help" in pr.FIT_CHECK_P
    assert "how to ask IT for help" not in pr.FIT_CHECK_H


# --- the picker -------------------------------------------------------------------------------


def _reply(text: str, finish: str = "stop", tokens: tuple[int, int] = (100, 10)) -> ModelResponse:
    return ModelResponse(text, "fake", tokens[0], tokens[1], finish, "r1")


def _asks(asks_for: str, request_type: str, guide: str) -> str:
    return json.dumps({"asks_for": asks_for, "request_type": request_type, "guide": guide})


def test_the_asks_schema_has_the_field_first_and_all_required() -> None:
    schema = pick_schema(ENTRIES, asks=True)
    assert list(schema["properties"]) == ["asks_for", "request_type", "guide"]
    assert schema["properties"]["asks_for"]["enum"] == list(pr.ASKS_FOR)
    assert schema["required"] == ["asks_for", "request_type", "guide"]
    assert schema["additionalProperties"] is False
    plain = pick_schema(ENTRIES)
    assert list(plain["properties"]) == ["request_type", "guide"]
    assert plain["required"] == ["request_type", "guide"]


def test_the_plain_request_is_unchanged_and_asks_has_more_room() -> None:
    plain = pick_request("hello", ENTRIES, pr.KS5)
    assert plain.max_tokens == 64 and plain.json_schema == pick_schema(ENTRIES)
    assert pick_request("hello", ENTRIES, pr.KS5, asks=True).max_tokens == 80


def test_parse_pick_with_asks() -> None:
    ok = parse_pick(_reply(_asks("steps", "how_to", "KB-001")), ENTRIES, asks=True)
    assert ok == Pick("how_to", "KB-001", asks_for="steps")
    none = parse_pick(_reply(_asks("nothing", "other", "none")), ENTRIES, asks=True)
    assert none == Pick("other", None, asks_for="nothing")
    bad = [
        json.dumps({"request_type": "how_to", "guide": "KB-001"}),
        _asks("whatever", "how_to", "KB-001"),
        json.dumps({"asks_for": "steps", "request_type": "how_to", "guide": "KB-001", "extra": 1}),
        _asks("steps", "how_to", "KB-999"),
    ]
    for text in bad:
        assert parse_pick(_reply(text), ENTRIES, asks=True).off_format


def test_parse_pick_without_asks_still_rejects_the_extra_field() -> None:
    text = _asks("steps", "how_to", "KB-001")
    assert parse_pick(_reply(text), ENTRIES).off_format
    plain = parse_pick(_reply('{"request_type": "how_to", "guide": "KB-001"}'), ENTRIES)
    assert plain == Pick("how_to", "KB-001") and plain.asks_for is None


def test_the_fit_request_holds_the_message_and_the_guides_line_but_no_steps() -> None:
    guide = next(g for g in GUIDES if g.id == ENTRIES[0].id)
    request = fit_request("my question", ENTRIES[0], pr.FIT_CHECK_H)
    assert request.task == "guide_fit" and request.system == pr.FIT_CHECK_H
    assert request.max_tokens == 16 and request.json_schema == FITS_SCHEMA
    assert "my question" in request.user
    for part in (ENTRIES[0].id, ENTRIES[0].title, ENTRIES[0].summary):
        assert part in request.user
    for step in guide.steps:
        assert step not in request.user


@pytest.mark.parametrize(
    ("text", "finish", "expected"),
    [
        ('{"fits": "yes"}', "stop", True),
        ('{"fits": "no"}', "stop", False),
        ('{"fit": "yes"}', "stop", None),
        ('{"fits": "yes", "why": "x"}', "stop", None),
        ('{"fits": "maybe"}', "stop", None),
        ('{"fits": true}', "stop", None),
        ("yes", "stop", None),
        ('["yes"]', "stop", None),
        ('{"fits": "yes"}', "length", None),
    ],
)
def test_parse_fit_is_strict(text: str, finish: str, expected: bool | None) -> None:
    assert parse_fit(_reply(text, finish)) is expected


# --- the decision and the modes ---------------------------------------------------------------


class Scripted:
    """Replies to guide_pick with `pick` and to guide_fit with `fit`; a ModelError if None."""

    provider = "fake"
    model = "fake-picker"
    temperature = None
    seed = None
    supports_seed = False

    def __init__(self, pick: str | None, fit: str | None = None) -> None:
        self.pick, self.fit = pick, fit
        self.requests: list[ModelRequest] = []

    def describe(self) -> dict[str, str]:
        return {}

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        text = self.pick if request.task == "guide_pick" else self.fit
        if text is None:
            raise ModelError("down")
        return _reply(text, tokens=(100, 10) if request.task == "guide_pick" else (40, 2))


def _ask(model: Scripted, text: str = "a question", **kw: Any):
    return answer_question(text, model=model, guides=GUIDES, system="s", **kw)


def test_asks_steps_and_a_guide_posts_the_guide() -> None:
    model = Scripted(_asks("steps", "how_to", OTHER_GUIDE))
    out = _ask(model, mode="asks")
    assert (out.kind, out.guide, out.why) == (GUIDE, OTHER_GUIDE, None)
    assert model.requests[0].max_tokens == 80


def test_asks_person_is_handed_off() -> None:
    out = _ask(Scripted(_asks("person", "how_to", OTHER_GUIDE)), mode="asks")
    assert (out.kind, out.guide, out.why) == (KIND_HAND_OFF, None, NEEDS_A_PERSON)


def test_asks_nothing_gets_only_the_pointer_guide_and_only_when_it_is_set() -> None:
    pointer = Scripted(_asks("nothing", "how_to", POINTER))
    assert _ask(pointer, mode="asks").why == NO_REQUEST
    out = _ask(pointer, mode="asks", pointer=POINTER)
    assert (out.kind, out.guide, out.why) == (GUIDE, POINTER, None)
    other = Scripted(_asks("nothing", "how_to", OTHER_GUIDE))
    out = _ask(other, mode="asks", pointer=POINTER)
    assert (out.kind, out.guide, out.why) == (KIND_HAND_OFF, None, NO_REQUEST)
    none = Scripted(_asks("nothing", "other", "none"))
    assert _ask(none, mode="asks", pointer=POINTER).why == NO_REQUEST


def test_the_pointer_must_be_approved() -> None:
    pick = Pick("other", POINTER, asks_for=pr.ASKS_NOTHING)
    assert decide(pick, frozenset(), None, pointer=POINTER)[2] == NO_REQUEST
    assert decide(pick, frozenset({POINTER}), None, pointer=POINTER)[0] == GUIDE


def test_the_screen_and_off_format_win_first_in_asks_mode() -> None:
    nothing = Pick("how_to", POINTER, asks_for=pr.ASKS_NOTHING)
    assert decide(nothing, APPROVED, "reset", pointer=POINTER)[2] == SCREEN
    off = Pick("other", None, off_format=True, asks_for=pr.ASKS_NOTHING)
    assert decide(off, APPROVED, None, pointer=POINTER)[2] == OFF_FORMAT
    out = _ask(Scripted("not json"), mode="asks", pointer=POINTER)
    assert out.why == OFF_FORMAT


def test_fit_yes_posts_the_guide_after_exactly_two_calls() -> None:
    model = Scripted('{"request_type": "how_to", "guide": "KB-001"}', '{"fits": "yes"}')
    out = _ask(model, mode="fit", fit_system=pr.FIT_CHECK_H)
    assert (out.kind, out.guide, out.fit) == (GUIDE, "KB-001", True)
    assert [r.task for r in model.requests] == ["guide_pick", "guide_fit"]
    assert (out.input_tokens, out.output_tokens) == (140, 12)
    assert model.requests[0].max_tokens == 64


def test_fit_no_and_a_failed_check_hand_off() -> None:
    pick = '{"request_type": "how_to", "guide": "KB-001"}'
    no = _ask(Scripted(pick, '{"fits": "no"}'), mode="fit", fit_system="f")
    assert (no.kind, no.why, no.fit) == (KIND_HAND_OFF, FIT_NO, False)
    bad = _ask(Scripted(pick, "banana"), mode="fit", fit_system="f")
    assert (bad.kind, bad.why, bad.fit) == (KIND_HAND_OFF, FIT_FAILED, None)
    down = _ask(Scripted(pick, None), mode="fit", fit_system="f")
    assert (down.kind, down.why, down.fit) == (KIND_HAND_OFF, FIT_FAILED, None)
    assert down.input_tokens == 100


def test_a_hand_off_pick_makes_no_second_call() -> None:
    model = Scripted('{"request_type": "other", "guide": "none"}', '{"fits": "yes"}')
    out = _ask(model, mode="fit", fit_system="f")
    assert out.kind == KIND_HAND_OFF and [r.task for r in model.requests] == ["guide_pick"]
    assert (out.input_tokens, out.output_tokens) == (100, 10)


def test_the_modes_are_checked() -> None:
    model = Scripted("x")
    with pytest.raises(ValueError, match="unknown picker mode"):
        _ask(model, mode="bogus")
    with pytest.raises(ValueError, match="fit_system"):
        _ask(model, mode="fit")
    assert not model.requests


def test_plain_mode_sends_exactly_the_old_request() -> None:
    model = Scripted('{"request_type": "how_to", "guide": "KB-001"}')
    out = _ask(model, "hello there", pointer=POINTER, fit_system="ignored")
    assert out.kind == GUIDE and out.fit is None
    [sent] = model.requests
    expected = pick_request("hello there", ENTRIES, "s")
    assert sent == expected and sent.max_tokens == 64
    assert list(sent.json_schema["properties"]) == ["request_type", "guide"]


# --- the month runner's wiring ----------------------------------------------------------------


class MonthFake:
    """Triages everything "other"; the picker replies by what the request asks for."""

    provider = "fake"
    model = "fake-1"
    temperature = None
    supports_seed = False

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self.seed: int | None = None

    def describe(self) -> dict[str, str]:
        return {}

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls[request.task] += 1
        if request.task == "triage":
            return _reply(json.dumps({"category": "other"}), tokens=(300, 8))
        if request.task == "guide_fit":
            return _reply('{"fits": "yes"}', tokens=(50, 2))
        if "asks_for" in request.json_schema["properties"]:
            return _reply(_asks("steps", "how_to", "KB-001"), tokens=(2_700, 20))
        return _reply('{"request_type": "how_to", "guide": "KB-001"}', tokens=(2_700, 20))


def _setup() -> mo.Setup:
    return mo.ALL_SETUPS["llama3.2"]


def test_the_picker_stage_follows_the_mode() -> None:
    model = MonthFake()
    asks = mo.knowledge_picker(_setup(), model, "KS6B-H")  # type: ignore[arg-type]
    assert (asks.mode, asks.pointer) == ("asks", None)
    assert asks.stage("how do I do a thing?") == (mo.GUIDE, "KB-001")
    assert model.calls["guide_fit"] == 0
    fit = mo.knowledge_picker(_setup(), model, "KS6C-P")  # type: ignore[arg-type]
    assert (fit.mode, fit.pointer) == ("fit", POINTER)
    assert fit.stage("how do I do a thing?") == (mo.GUIDE, "KB-001")
    assert model.calls["guide_fit"] == 1


def test_the_picker_hashes() -> None:
    model = MonthFake()

    def pick(version: str) -> mo.Picker:
        return mo.knowledge_picker(_setup(), model, version)  # type: ignore[arg-type]

    assert pick("KS5").prompt_sha256 == _sha(pr.KS5)
    assert pick("KS6A-P").prompt_sha256 == PINNED["KS6A-P"]
    whole = {"system": pr.KS5, "mode": "fit", "fit": pr.FIT_CHECK_H, "pointer": None}
    expected = hashlib.sha256(json.dumps(whole, sort_keys=True).encode()).hexdigest()
    assert pick("KS6C-H").prompt_sha256 == expected
    assert pick("KS6C-H").prompt_sha256 != pick("KS6C-P").prompt_sha256
    assert pick("KS6B-H").prompt_sha256 != pick("KS6B-P").prompt_sha256


def _month_run(tmp: Path, *extra: str) -> tuple[int, dict[str, Any], MonthFake]:
    model = MonthFake()
    argv = [
        "run", "--set", TUNING_MONTH, "--setup", "llama3.2", "--k", "1", "--picker", "knowledge",
        *extra, "--out", str(tmp),
    ]  # fmt: skip
    code = mo.main(argv, make_model=lambda: model, samples=FEW)  # type: ignore[arg-type, return-value]
    [path] = tmp.glob("*.json")
    return code, json.loads(path.read_text(encoding="utf-8")), model


def test_a_fit_run_counts_its_second_call_and_records_the_mode(tmp_path: Path) -> None:
    _, result, model = _month_run(tmp_path, "--picker-prompt", "KS6C-H")
    c = result["config"]
    assert c["picker_mode"] == "fit" and "picker_pointer" not in c
    assert c["picker_prompt"] == "KS6C-H"
    assert c["calls"]["guide_fit"] == model.calls["guide_fit"] > 0
    assert c["calls"]["guide_pick"] == model.calls["guide_pick"]


def test_a_pointer_variant_records_the_pointer(tmp_path: Path) -> None:
    _, result, _ = _month_run(tmp_path, "--picker-prompt", "KS6B-P")
    assert result["config"]["picker_mode"] == "asks"
    assert result["config"]["picker_pointer"] == POINTER


def test_a_plain_run_writes_neither_key(tmp_path: Path) -> None:
    _, result, _ = _month_run(tmp_path, "--picker-prompt", "KS5")
    assert "picker_mode" not in result["config"] and "picker_pointer" not in result["config"]
    assert result["config"]["picker_prompt_sha256"] == _sha(pr.KS5)
    assert "guide_fit" not in result["config"]["calls"]


def test_the_pointer_only_plain_variant_records_the_pointer_not_a_mode(tmp_path: Path) -> None:
    _, result, _ = _month_run(tmp_path, "--picker-prompt", "KS6A-P")
    assert "picker_mode" not in result["config"]
    assert result["config"]["picker_pointer"] == POINTER
    assert result["config"]["picker_prompt_sha256"] == PINNED["KS6A-P"]


# --- the comparison tool ----------------------------------------------------------------------

DEV = "held_out_month1"


def _month(name: str):
    return month_for(name, seed=1130 if name == TUNING_MONTH else None)


def _seed(name: str) -> int:
    return _month(name).seed


def _outcome(ticket, answer_chat: bool, right_guides: bool = True) -> tuple[str, str | None]:
    if ticket.kind == "chitchat" and answer_chat:
        return "answer_guide", POINTER
    if ticket.expected == ANSWER_GUIDE and right_guides:
        return "answer_guide", ticket.expected_guide
    return "hand_off", None


def _runs_file(
    tmp: Path, version: str, set_name: str, *, answer_chat: bool = False, **over: Any
) -> Path:
    month = _month(set_name)
    config: dict[str, Any] = {
        "set": set_name,
        "setup": p3.ARM,
        "picker_prompt": version,
        "k": 3,
        "seed": month.seed,
        "month_sha256": "0" * 64,
        "picker_prompt_sha256": "1" * 64,
        "picker_model": "claude-sonnet-5-5",
        "prompt_cache": True,
        "failed_calls": {},
        "calls": {"guide_pick": 10, "triage": 10},
        "cost_usd": 0.5,
    }
    config |= over
    tickets = []
    for t in month.tickets:
        outcome, guide = _outcome(t, answer_chat)
        runs = [
            {"run": n, "outcome": outcome, "guide": guide, "stage": "picker", "triage": "other"}
            for n in range(1, config["k"] + 1)
        ]
        tickets.append({"ticket_id": t.id, "runs": runs, "text": t.text})
    path = tmp / f"{version}-{set_name}.json"
    path.write_text(json.dumps({"config": config, "tickets": tickets}), encoding="utf-8")
    return path


def test_option_month_changes_only_the_small_talk_tickets() -> None:
    month = _month(TUNING_MONTH)
    assert p3.option_month(month, "H") is month
    changed = p3.option_month(month, "P")
    chat = [t for t in month.tickets if t.kind == "chitchat"]
    assert chat
    for old, new in zip(month.tickets, changed.tickets, strict=True):
        if old.kind == "chitchat":
            assert (new.expected, new.expected_guide, new.reason) == (ANSWER_GUIDE, POINTER, None)
            assert old.expected == HAND_OFF
            assert new.text == old.text and new.id == old.id
        else:
            assert new == old


def test_regrade_reproduces_a_runs_own_grades() -> None:
    month = _month(TUNING_MONTH)
    records = []
    for i, t in enumerate(month.tickets):
        outcome, guide = _outcome(t, answer_chat=i % 2 == 0, right_guides=i % 3 != 0)
        if i % 5 == 0:
            outcome, guide = "answer_username", None
        ok, wrongly = mo.grade(t, mo.Route(outcome, guide, "s", "other"))
        records.append(
            {"ticket_id": t.id, "run": 1, "outcome": outcome, "guide": guide, "stage": "s",
             "triage": "other", "ok": ok, "wrongly_answered": wrongly}
        )  # fmt: skip
    again = p3.regrade(
        month,
        [{k: v for k, v in r.items() if k not in ("ok", "wrongly_answered")} for r in records],
    )
    assert again == records


def test_under_option_p_a_pointer_answer_is_right_and_a_hand_off_is_not() -> None:
    month = _month(TUNING_MONTH)
    chat = next(t for t in month.tickets if t.kind == "chitchat")

    def grade(option: str, outcome: str, guide: str | None) -> tuple[bool, bool]:
        row = {"ticket_id": chat.id, "run": 1, "outcome": outcome, "guide": guide}
        [r] = p3.regrade(p3.option_month(month, option), [row])
        return r["ok"], r["wrongly_answered"]

    assert grade("P", "answer_guide", POINTER) == (True, False)
    assert grade("P", "hand_off", None) == (False, False)
    assert grade("H", "answer_guide", POINTER) == (False, True)
    assert grade("H", "hand_off", None) == (True, False)


def _collect(tmp: Path, **over: Any) -> dict[str, Any]:
    runs = {}
    for version in ("KS6A-H", "KS6A-P"):
        for set_name in p3.SETS:
            runs[f"{version}/{set_name}"] = _runs_file(
                tmp, version, set_name, answer_chat=version.endswith("P"), **over
            )
    return p3.collect(runs)


def test_collect_keeps_only_ids_enums_and_numbers(tmp_path: Path) -> None:
    data = _collect(tmp_path)
    assert [(r["version"], r["set"]) for r in data["runs"]] == [
        ("KS6A-H", DEV),
        ("KS6A-P", DEV),
        ("KS6A-H", TUNING_MONTH),
        ("KS6A-P", TUNING_MONTH),
    ]
    first = data["runs"][0]
    assert (first["candidate"], first["variant"], first["calls"]) == ("KS6A", "H", 20)
    assert first["records"] == sorted(first["records"], key=lambda r: (r[0], r[1]))
    dump = json.dumps(data)
    for name in p3.SETS:
        for t in _month(name).tickets[:40]:
            assert t.text not in dump and t.sender_email not in dump
    for run in data["runs"]:
        for row in run["records"]:
            for v in row:
                assert v is None or isinstance(v, int) or len(v) <= 40


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"failed_calls": {"guide_pick": 1}}, "failed"),
        ({"set": "tuning_month_x"}, "set"),
        ({"setup": "sonnet-3d2"}, "setup"),
        ({"k": 1}, "k is"),
        ({"prompt_cache": False}, "prompt cache"),
        ({"picker_prompt": "KS5"}, "picker prompt"),
    ],
)
def test_collect_refuses(tmp_path: Path, over: dict[str, Any], reason: str) -> None:
    path = _runs_file(tmp_path, "KS6A-H", TUNING_MONTH, **over)
    with pytest.raises(ValueError, match=reason) as e:
        p3.collect({"KS6A-H/tuning_month": path})
    assert "KS6A-H/tuning_month" in str(e.value)


def test_collect_refuses_other_sets_and_unknown_versions(tmp_path: Path) -> None:
    path = _runs_file(tmp_path, "KS6A-H", TUNING_MONTH)
    with pytest.raises(ValueError, match="set"):
        p3.collect({"KS6A-H/held_out_month2": path})
    with pytest.raises(ValueError, match="KS7"):
        p3.collect({"KS7-H/tuning_month": path})


def test_the_baseline_may_have_k_1_and_no_cache(tmp_path: Path) -> None:
    path = _runs_file(tmp_path, "KS5", TUNING_MONTH, k=1, prompt_cache=False)
    data = p3.collect({"KS5/tuning_month": path})
    assert data["runs"][0]["variant"] is None and data["runs"][0]["candidate"] == "KS5"


def test_overlaps() -> None:
    assert p3.overlaps([0.1, 0.5], [0.4, 0.9])
    assert p3.overlaps([0.1, 0.5], [0.5, 0.9])
    assert p3.overlaps([0.1, 0.9], [0.4, 0.5])
    assert not p3.overlaps([0.1, 0.3], [0.4, 0.9])


def _fake_data(versions: list[str]) -> dict[str, Any]:
    runs = [
        {"version": v, "set": s, "variant": v[-1], "candidate": v[:-2], "k": 3, "seed": 1,
         "cost_usd": 1.0, "calls": 10, "records": []}
        for v in versions
        for s in p3.SETS
    ]  # fmt: skip
    return {"sets": list(p3.SETS), "arm": p3.ARM, "k": 3, "runs": runs}


def _patch_rule(
    monkeypatch: pytest.MonkeyPatch, rates: dict[str, tuple[float, float, float]], wrong: set[str]
) -> None:
    def pooled(data: Any, version: str, option: str, **kw: Any) -> dict[str, Any]:
        value, lo, hi = rates[version.rsplit("-", 1)[0]]
        return {"right_guide": {"value": value, "interval": [lo, hi]}}

    def scores(run: dict[str, Any], option: str, samples: int) -> dict[str, Any]:
        return {"wrongly_answered": {"runs": int(run["candidate"] in wrong)}}

    monkeypatch.setattr(p3, "pooled", pooled)
    monkeypatch.setattr(p3, "_run_scores", scores)


def test_recommend_only_one_eligible(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_rule(monkeypatch, {"KS6A": (0.5, 0.4, 0.6), "KS6B": (0.9, 0.8, 1.0)}, {"KS6A"})
    rec = p3.recommend(_fake_data(["KS6A-H", "KS6B-H"]), "H")
    assert rec["eligible"] == ["KS6B"] and rec["recommended"] == "KS6B"
    assert rec["why"] == "only one eligible" and rec["not_run"] == ["KS6C"]


def test_recommend_overlap_picks_the_simplest(monkeypatch: pytest.MonkeyPatch) -> None:
    rates = {"KS6A": (0.70, 0.5, 0.8), "KS6B": (0.75, 0.6, 0.8), "KS6C": (0.90, 0.85, 0.95)}
    _patch_rule(monkeypatch, rates, set())
    rec = p3.recommend(_fake_data(["KS6A-H", "KS6B-H", "KS6C-H"]), "H")
    assert rec["recommended"] == "KS6C"  # the best is apart from both
    assert rec["why"] == "highest right-guide rate, apart from the others"
    rates["KS6C"] = (0.80, 0.7, 0.95)
    rec = p3.recommend(_fake_data(["KS6A-H", "KS6B-H", "KS6C-H"]), "H")
    assert rec["recommended"] == "KS6A" and rec["why"] == "intervals overlap: the simplest"


def test_recommend_apart_takes_the_highest(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_rule(monkeypatch, {"KS6A": (0.4, 0.3, 0.5), "KS6B": (0.9, 0.8, 1.0)}, set())
    rec = p3.recommend(_fake_data(["KS6A-P", "KS6B-P"]), "P")
    assert rec["recommended"] == "KS6B" and rec["eligible"] == ["KS6A", "KS6B"]


def test_recommend_none_eligible_and_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_rule(monkeypatch, {"KS6A": (0.4, 0.3, 0.5)}, {"KS6A"})
    rec = p3.recommend(_fake_data(["KS6A-H"]), "H")
    assert rec == {
        "option": "H",
        "eligible": [],
        "not_run": ["KS6B", "KS6C"],
        "recommended": None,
        "why": "none eligible",
    }
    # a variant run on one set only is not run
    data = _fake_data(["KS6A-H"])
    data["runs"] = data["runs"][:1]
    assert p3.recommend(data, "H")["not_run"] == ["KS6A", "KS6B", "KS6C"]


def test_the_report_is_deterministic_and_names_what_was_run(tmp_path: Path) -> None:
    runs = {}
    for version, chat in (("KS5", False), ("KS6A-H", False), ("KS6B-P", True)):
        for set_name in p3.SETS:
            extra = {"k": 1, "prompt_cache": False} if version == "KS5" else {}
            runs[f"{version}/{set_name}"] = _runs_file(
                tmp_path, version, set_name, answer_chat=chat, **extra
            )
    data = p3.collect(runs)
    first = p3.render(data, samples=FEW)
    assert first == p3.render(data, samples=FEW) and first.endswith("\n")
    assert not first.endswith("\n\n")
    for name in ("KS5", "KS6A-H", "KS6B-P"):
        assert name in first
    assert "KS6C" in first  # named in the rule's not-run list
    assert "do not edit by hand" in first and "not blind" in first
    assert "Recommended: KS6B" in first or "Recommended: none" in first
    assert "Total:" in first and "all overlap" in first
    out = tmp_path / "data.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    md = tmp_path / "r.md"
    assert p3.main(["report", "--data", str(out), "--out", str(md)]) == 0
