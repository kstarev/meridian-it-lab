"""The hand-written sentences about the sealed tests after held-out v3, checked against their
sources: held-out v5's summary JSON, held-out v6's generated report (it has no JSON sibling),
the sealed month's two result files and the guide screen fix's plan.

They are in the README ("What's next" and the follow-up bullets) and in docs/how-it-works.md
(the how-to test's bullets, the held-out v6 and sealed-month sections, and two limits). Each
test rebuilds a sentence from its source and requires it word for word, so a failure is stated
with its numbers and can't drift from them.
"""

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
DOC = ROOT / "docs/how-it-works.md"
V5 = ROOT / "docs/evals/2026-11-16-m3d-v5-final.json"
V6_REPORT = ROOT / "docs/evals/2026-11-17-m3d2-v6-final.md"
MONTH = "docs/evals/2026-11-17-m3c-sealed-month-{arm}.json"
GUIDE_FIX_PLAN = ROOT / "docs/plans/milestone-4-guide-screen-fix.md"
DEFAULTS = ("haiku-3d2", "sonnet-3d2")


def _readme_says(text: str) -> None:
    assert text in README.read_text(), f"README no longer says, word for word: {text!r}"


def _doc_says(text: str) -> None:
    assert text in DOC.read_text(), f"docs/how-it-works.md no longer says, word for word: {text!r}"


@cache
def _v6() -> str:
    return V6_REPORT.read_text()


def _v6_unsafe() -> dict[str, int]:
    """Unsafe runs per arm, from the report's pass-rule table."""
    rows = re.findall(
        r"^\| (\S+) \([^)]*\) \| (\d+) \| \d+ \| (\d+) \| (?:pass|\*\*fail\*\*) \|",
        _v6(),
        re.MULTILINE,
    )
    assert len(rows) == 5, rows
    for arm, _, exposed in rows:
        assert exposed == "0", arm  # no data exposed in any arm
    return {arm: int(n) for arm, n, _ in rows}


def _v6_relayed_unnamed_stopped() -> dict[str, tuple[int, int]]:
    """Relayed requests that name nobody, runs stopped per arm (the colleague block's row)."""
    (header,) = re.findall(r"^\| Measure \|(.*)\|$", _v6(), re.MULTILINE)
    arms = [re.sub(r" \(.*", "", c.strip()) for c in header.split("|")]
    block = _v6().split("| Colleague runs stopped |", 1)[1]
    row = re.search(r"^\|\s+relayed_unnamed \|(.*)\|$", block, re.MULTILINE)  # NBSP indent
    assert row
    cells = [re.match(r"(\d+)/(\d+)", c.strip()) for c in row.group(1).split("|")]
    return {
        arm: (int(m.group(1)), int(m.group(2))) for arm, m in zip(arms, cells, strict=True) if m
    }


@cache
def _month(arm: str) -> dict[str, Any]:
    return json.loads((ROOT / MONTH.format(arm=arm)).read_text())


def _month_facts() -> dict[str, Any]:
    haiku, sonnet = (_month(a) for a in DEFAULTS)
    for m in (haiku, sonnet):
        assert m["config"]["sealed"] and m["config"]["set"] == "held_out_month1"
        assert (m["config"]["picker_model"], m["config"]["picker_prompt"]) == (
            "claude-sonnet-5-5",
            "KS5",
        )
        wrong = m["scores"]["wrongly_answered"]
        assert wrong["runs"] > 0  # the pass rule is 0 over all runs: it failed
        assert wrong["with_guide"] == wrong["runs"] and wrong["with_username"] == 0
    (tickets,) = {m["scores"]["tickets"] for m in (haiku, sonnet)}
    (k,) = {m["scores"]["k"] for m in (haiku, sonnet)}
    (of_runs,) = {m["scores"]["wrongly_answered"]["of_runs"] for m in (haiku, sonnet)}
    (missed,) = {m["scores"]["wrongly_answered"]["tickets"] for m in (haiku, sonnet)}
    return {
        "tickets": tickets,
        "k": k,
        "of_runs": of_runs,
        "missed_tickets": missed,
        "runs": [m["scores"]["wrongly_answered"]["runs"] for m in (haiku, sonnet)],
        "hand_off": [round(m["scores"]["correct_hand_off"]["value"], 2) for m in (haiku, sonnet)],
    }


def _haiku_caught_by_triage() -> tuple[tuple[int, int], tuple[int, int]]:
    """Held-out v5: colleague runs caught by triage itself, with HT4 (3b) and with T1 (3a)."""
    arms = json.loads(V5.read_text())["arms"]
    return tuple(arms["haiku-3b"]["caught_by_triage"]), tuple(arms["haiku"]["caught_by_triage"])


# The README.


def test_readme_the_sealed_month_failed_its_safety_rule() -> None:
    f = _month_facts()
    haiku, sonnet = f["runs"]
    _readme_says(
        f"Then a realistic month of {f['tickets']} requests (milestone 3c), sealed until its one "
        "run on 17 November, failed its safety rule on both Claude setups: the Sonnet guide "
        "picker they share answered requests meant for a person with a guide, in "
        f"{haiku} of {f['of_runs']} runs of such requests on one setup and {sonnet} of "
        f"{f['of_runs']} on the other. No username was given out. The picker's fix will be "
        "tested on a new locked-away month."
    )


def test_readme_the_guide_checks_fix_is_built_and_not_yet_tested() -> None:
    status = GUIDE_FIX_PLAN.read_text().split("\n")[2]
    assert "Built on 2026-11-17" in status and "neither written nor sealed" in status
    _readme_says(
        "A fix for the guide check is built from practice data and not yet tested on a "
        "locked-away set."
    )
    assert "A fix for the guide check is planned." not in README.read_text()


def test_readme_the_stricter_check_is_haikus_default_and_was_measured_again() -> None:
    stopped = _v6_relayed_unnamed_stopped()
    hc7, c1 = stopped["haiku-3d2"], stopped["haiku-3d2-c1"]
    assert hc7[0] == hc7[1] and c1[0] < c1[1] and hc7[1] == c1[1]
    _readme_says(
        "That stricter check is now Haiku's default. A second locked-away set, held-out v6, "
        f"measured it again on 17 November: it stopped {hc7[0]} of {hc7[1]} runs of such "
        f"requests, and the old check {c1[0]} of {c1[1]}."
    )
    _readme_says(
        "That check is now Haiku's default; on held-out v6 it stopped "
        f"{hc7[0]} of {hc7[1]} runs of such requests, and the old check {c1[0]} of {c1[1]}"
    )


# docs/how-it-works.md.


def test_doc_the_guide_screens_fix_is_built_and_not_yet_tested() -> None:
    _doc_says(
        "The guide screen's fix was built on 2026-11-17 from practice (tuning) data only, and is "
        "not yet tested on a locked-away set"
    )
    _doc_says("That set, `held_out_g2`, is not written yet.")
    assert "planned, not built" not in DOC.read_text()


def test_doc_haikus_check_prompt_measured_again_on_v6() -> None:
    stopped = _v6_relayed_unnamed_stopped()
    hc7, c1 = stopped["haiku-3d2"], stopped["haiku-3d2-c1"]
    sentence = (
        "with the same triage prompt, HC7 stopped {} of {} runs of relayed requests that name "
        "nobody, and C1 {}"
    )
    _doc_says(sentence.format(*hc7, f"{c1[0]} of {c1[1]}"))  # the limit
    _doc_says(sentence.format(*hc7, f"stopped {c1[0]} of {c1[1]}"))  # the v6 section


def test_doc_the_3b_triage_prompts_benefit_was_measured_on_v5() -> None:
    (ht4, of), (t1, of_t1) = _haiku_caught_by_triage()
    assert of == of_t1 and ht4 > t1
    _doc_says(
        "- **Haiku's 3b triage prompt has a measured cost, and its benefit was measured on "
        f"held-out v5: triage itself caught {ht4} of {of} colleague runs with it, against {t1} "
        "with the 3a prompt.**"
    )
    assert "an unmeasured benefit" not in DOC.read_text()


def test_doc_held_out_v6_section() -> None:
    unsafe = _v6_unsafe()
    failed = [a for a, n in unsafe.items() if n]
    assert sorted(failed) == ["haiku-3d2-c1", "haiku-3d2-split", "sonnet-3d2-split"]
    assert all(unsafe[a] == 0 for a in DEFAULTS)
    (each,) = {unsafe[a] for a in failed}
    _doc_says("## Results on held-out v6 (milestone 3d part 2)")
    _doc_says(
        f"- **The pass rule failed in {len(failed)} of {len(unsafe)} arms.** The two default "
        "arms, `haiku-3d2` (HT9/HC7) and `sonnet-3d2` (ST6/SC4), had 0 unsafe runs. The two "
        f"split arms, `haiku-3d2-split` and `sonnet-3d2-split`, had {each} each, all on one reset "
        f"ticket. `haiku-3d2-c1` (HT9/C1, informational) had {each}, on relayed colleague "
        "requests that name nobody. Every unsafe run was a policy violation, with no data exposed."
    )


def test_doc_sealed_month_section() -> None:
    f = _month_facts()
    haiku, sonnet = f["runs"]
    assert all(rate >= 0.9 for rate in f["hand_off"])  # context: the brief's 90%
    _doc_says("## Results on the sealed month (milestone 3c)")
    _doc_says(f"{f['tickets']} tickets, k = {f['k']}, on the two default setups")
    _doc_says(
        f"- **The safety pass rule failed on both arms.** `haiku-3d2` wrongly answered {haiku} of "
        f"{f['of_runs']} runs of out-of-scope tickets, and `sonnet-3d2` {sonnet} of {f['of_runs']}"
    )
    _doc_says(
        f"- **Every miss was one of the same {f['missed_tickets']} tickets, answered with a guide "
        "by the picker both arms share.**"
    )
    _doc_says(
        "- **Context, not the pass rule:** the correct hand-off rate was "
        f"{f['hand_off'][0]:.2f} and {f['hand_off'][1]:.2f}, against the brief's 90%."
    )


def test_readme_and_doc_link_to_the_new_sections() -> None:
    doc = DOC.read_text()
    assert "(#results-on-the-sealed-month-milestone-3c)" in doc
    for name in ("haiku-3d2", "sonnet-3d2"):
        assert f"(evals/2026-11-17-m3c-sealed-month-{name}.md)" in doc
        assert (ROOT / f"docs/evals/2026-11-17-m3c-sealed-month-{name}.md").exists()
    assert "(evals/2026-11-17-m3d2-v6-final.md)" in doc
