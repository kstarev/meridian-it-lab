"""Part 2's selection rule, on saved scores and a fake model only (no paid call)."""

import hashlib
import json
from collections import Counter

import pytest

from tier0.evals import m3_selection
from tier0.evals import m3d2_selection as sel
from tier0.models import StubModel


def test_part_2s_scorer_cases() -> None:
    cases = sel.instances_3d2()
    assert len(cases) == 67
    assert Counter(c.group for c in cases) == {
        "everyday": 6 + 3 + 16,
        "colleague": 16,
        "several": 12,
        "reset": 14,
    }
    assert {c.triage_expected for c in cases if c.group in ("several", "reset")} == {"other"}
    assert not any("{" in c.text for c in cases)


def test_a_reset_is_right_when_triage_doesnt_say_own_username() -> None:
    cases = [c for c in sel.instances_3d2() if c.group == "reset"]
    result = m3_selection.score("stub", "T1", k=1, model=StubModel(), cases=cases)
    # The keyword stub reads the three that ask for a username too as own_username.
    assert result["total"]["reset"] == 14 and result["correct"]["reset"] == 11


def _save(tmp_path, model: str, version: str, set_name: str, correct: dict, total: dict) -> None:  # type: ignore[no-untyped-def]
    text = sel._text(version)
    (tmp_path / f"{model}__{version}__{set_name}.json").write_text(
        json.dumps(
            {
                "k": 3,
                "prompt_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "correct": correct,
                "total": total,
            }
        )
    )


def _scores(
    tmp_path, version: str, *, everyday: int, colleague: int, several: int, reset: int
) -> None:  # type: ignore[no-untyped-def]
    m = "m"
    _save(
        tmp_path,
        m,
        version,
        "all",
        {"everyday": everyday, "colleague": colleague},
        {"everyday": 219, "colleague": 174},
    )
    _save(tmp_path, m, version, "several", {"several": 36}, {"several": 36})
    _save(
        tmp_path,
        m,
        version,
        "part2",
        {"everyday": 75, "colleague": 48, "several": several, "reset": reset},
        {"everyday": 75, "colleague": 48, "several": 36, "reset": 42},
    )


def test_the_rule_keeps_the_current_prompt_unless_the_new_one_wins(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(sel, "RESULTS", tmp_path)
    _scores(tmp_path, "T1", everyday=219, colleague=100, several=0, reset=30)
    _scores(tmp_path, "HT8", everyday=205, colleague=157, several=36, reset=42)
    _scores(tmp_path, "HT7", everyday=219, colleague=160, several=30, reset=42)
    # HT7 has more everyday answers but fails R25's floor, so HT8 stays.
    assert sel.pick("m", "triage", "HT8", "HT7")["pick"] == "HT8"
    _scores(tmp_path, "HT6", everyday=210, colleague=159, several=36, reset=42)
    assert sel.pick("m", "triage", "HT8", "HT6")["pick"] == "HT6"


def test_the_reset_floor_counts(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(sel, "RESULTS", tmp_path)
    _scores(tmp_path, "T1", everyday=219, colleague=100, several=0, reset=30)
    _scores(tmp_path, "HT8", everyday=205, colleague=157, several=36, reset=42)
    _scores(tmp_path, "HT6", everyday=219, colleague=170, several=36, reset=41)
    assert sel.pick("m", "triage", "HT8", "HT6")["pick"] == "HT8"


def test_no_candidate_clears_the_floors_and_the_current_prompt_stays(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(sel, "RESULTS", tmp_path)
    _scores(tmp_path, "T1", everyday=219, colleague=100, several=0, reset=30)
    _scores(tmp_path, "HT8", everyday=205, colleague=157, several=35, reset=42)
    result = sel.pick("m", "triage", "HT8", None)
    assert result["pick"] == "HT8" and "no candidate" in result["why"]


def test_a_stale_saved_score_is_refused(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(sel, "RESULTS", tmp_path)
    _scores(tmp_path, "T1", everyday=219, colleague=100, several=0, reset=30)
    path = tmp_path / "m__T1__all.json"
    data = json.loads(path.read_text())
    data["prompt_sha256"] = "0" * 64
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="stale"):
        sel.load("m", "T1")
