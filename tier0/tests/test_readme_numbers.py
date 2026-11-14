"""The README's hand-written result numbers, checked against the published reports.

The README's tables are generated (test_summary.py, test_faults_readme.py). The numbers in its
prose are written by hand: the headline in "The short version", the paragraph under the
fault-injection table, the "What it shows" bullets under the v3 table, the hand-off range under
the cost table and the "Small sample" and "One Mac" limits. Each test here rebuilds one sentence
from the published reports (held-out v3, or the fault-injection runs) and requires it in the
README word for word, and checks the facts the sentence states. If a report or a sentence
changes, a test fails.
"""

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

from meridian_dataset import generator
from tier0.evals import compare_many as cm
from tier0.evals import faults_readme as fr

ROOT = Path(__file__).resolve().parents[2]
COMPARISON = ROOT / "docs/evals/2026-11-12-held-out-v3-comparison.json"
M2_COMPARISON = ROOT / "docs/evals/2026-11-09-held-out-v2-comparison.json"
V1_REPORT = ROOT / "docs/evals/2026-11-08-ollama-llama3.2.md"  # Markdown only, no JSON
WORDS = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
}


@cache
def _readme() -> str:
    return (ROOT / "README.md").read_text()


@cache
def _comparison() -> dict[str, Any]:
    return json.loads(COMPARISON.read_text())


@cache
def _raw() -> tuple[dict[str, Any], ...]:
    """Every setup's full report, in comparison order: safety and cost count every run."""
    return tuple(
        json.loads((COMPARISON.parent / Path(p).name).read_text()) for p in _comparison()["reports"]
    )


@cache
def _views() -> tuple[dict[str, Any], ...]:
    """The k = 3 view each setup is compared at (Claude: its first 3 runs)."""
    return tuple(cm.view(r) for r in _raw())


def _k() -> int:
    (k,) = {v["k"] for v in _views()}
    return k


def _n_tickets() -> int:
    (n,) = {len(v["tickets"]) for v in _views()}
    return n


def _claude() -> list[dict[str, Any]]:
    return [r for r in _raw() if r["provider"] == "anthropic"]


def _says(sentence: str) -> None:
    assert sentence in _readme(), f"README no longer says, word for word: {sentence!r}"


def _failed(view: dict[str, Any]) -> set[str]:
    return {t["ticket"] for t in view["tickets"] if t["runs_passed"] != t["runs"]}


def _self_described(raw: dict[str, Any]) -> set[str]:
    return {run["ticket"] for run in raw["runs_detail"] if run["self_described"]}


def _runs_of(raw: dict[str, Any], ticket: str) -> list[dict[str, Any]]:
    runs = [run for run in raw["runs_detail"] if run["ticket"] == ticket]
    assert runs, (cm.name(raw), ticket)
    return runs


@cache
def _fault_reports() -> tuple[dict[str, Any], ...]:
    """Each fault-injection report, in the table's run order."""
    return tuple(
        json.loads((ROOT / "docs/evals" / f"{stem}.json").read_text()) for stem in fr.PUBLISHED
    )


def _fault_runs() -> tuple[dict[str, Any], ...]:
    """Each fault-injection run's models (the stub left out)."""
    return tuple(fr._models(report) for report in _fault_reports())


def _one(values: Any) -> Any:
    """The single value every report shares."""
    (value,) = set(values)
    return value


def _fault_run(stem: str) -> tuple[int, dict[str, Any]]:
    """A fault-injection run's number in the README's table, and its models."""
    i = fr.PUBLISHED.index(stem)
    return i + 1, _fault_runs()[i]


def _answered(models: dict[str, Any]) -> int:
    return sum(g["answered"] for m in models.values() for g in fr._groups(m["summary"]))


# The short version: the headline result.


def test_headline_how_the_test_ran() -> None:
    (claude_k,) = {r["k"] for r in _claude()}
    _says(f"someone who never saw the code wrote {_n_tickets()} requests")
    _says(
        f"Each request ran {_k()} times on each of {len(_views())} different AI setups, from "
        f"small free models on a laptop to Claude (the {WORDS[len(_claude())]} Claude setups "
        f"ran {claude_k} times and were scored on their first {_k()})."
    )


def test_headline_no_setup_exposed_data_or_broke_a_rule() -> None:
    for r in _raw():
        assert r["policy_violation_runs"] == 0, cm.name(r)
        assert r["data_exposed_runs"] == 0, cm.name(r)
        assert r["unsafe_runs"] == 0, cm.name(r)
    _says(
        f"None of the {len(_views())} ever gave anyone another person's details "
        "or broke a safety rule."
    )


def test_headline_best_and_weakest_pass_k() -> None:
    scores = [round(v["pass_k"], 9) for v in _views()]
    best, worst = max(scores), min(scores)
    assert scores.count(best) > 1  # "the best setups", plural
    _says(
        f"The best setups got all {_k()} tries right on {round(100 * best)}% of the requests, "
        f"and the weakest on {round(100 * worst)}%."
    )


def test_headline_every_colleague_request_was_refused() -> None:
    for r in (*_raw(), *_views()):
        for kind in ("named", "unnamed"):
            group = r["colleague_outcomes"][kind]
            assert group["refused"] > 0, (cm.name(r), kind)
            assert group["answered"] == group["handed_off"] == 0, (cm.name(r), kind)
    _says("Every request for a colleague's login was refused")


def test_headline_cheapest_cloud_cost() -> None:
    cheapest = min(_claude(), key=lambda r: r["cost"]["per_ticket_usd"])
    cents = round(cheapest["cost"]["per_ticket_usd"] * 1000 * 100)
    assert cm._cost(cheapest, per=1000) == f"${cents / 100:.2f}"  # the tables' rounding
    _says(f"The cheapest cloud model cost {cents} cents per 1,000 requests.")


def test_headline_weaknesses() -> None:
    assert _comparison()["verdict"]["winner"] is None
    _says(f"With only {_n_tickets()} requests, no setup was clearly better than the others.")
    (ticket,) = set.intersection(*(_failed(v) for v in _views()))
    for r in _raw():
        for run in _runs_of(r, ticket):
            assert run["self_described"] and run["action"] == "escalate", cm.name(r)
    _says(
        "And one request from a new starter or intern who described themselves was passed to "
        "a person every time, when it should have been answered."
    )


# How evaluation works: the held-out set and the statistics bullets.


def test_method_held_out_v3() -> None:
    assert _one(r["seed"] for r in _raw()) == generator.HOLDOUT_V3_SEED
    _says(f"- Held-out v3 ({_n_tickets()} tickets: ")


def test_method_intervals() -> None:
    methods = {(v["ci_method"]["pass_k"], v["ci_method"]["pass_1"]) for v in _views()}
    assert methods == {("Wilson score interval, 95%", "ticket bootstrap, 95% percentile interval")}
    _says(
        f"- pass^{_k()} gets a Wilson 95% interval. pass^1 gets a bootstrap that resamples "
        "whole tickets."
    )


def test_method_paired_bootstrap() -> None:
    method = _comparison()["verdict"]["method"]
    assert method["difference"].startswith("paired bootstrap over tickets,")
    _says(
        f"- Each pair of setups gets a paired bootstrap of the pass^{_k()} difference: "
        f"{method['bootstrap_samples']:,} resamples of the same tickets, seed "
        f"{method['bootstrap_seed']}."
    )


def test_method_bonferroni() -> None:
    method = _comparison()["verdict"]["method"]
    n = len(_views())
    pairs = len(_comparison()["pairs"])
    assert pairs == n * (n - 1) // 2
    assert method["level"] == cm.level(n)
    family = round(100 * (1 - cm.FAMILY_ALPHA))
    assert f"Bonferroni over {pairs} pairs, family-wise {family}%" in method["difference"]
    _says(
        f"- {WORDS[n].capitalize()} setups make {pairs} pairs, so each interval is "
        f"Bonferroni-corrected to {cm.pct(method['level'])} to keep the family-wise error at "
        f"{round(100 * cm.FAMILY_ALPHA)}%."
    )


def test_method_claude_first_runs() -> None:
    k = _k()
    claude_k = _one(r["k"] for r in _claude())
    for r in _claude():
        v = cm.view(r)
        assert v["full_k"] == claude_k
        # The k = 3 view is the first k runs of each ticket.
        first = sorted({run["run"] for run in r["runs_detail"]})[:k]
        passed = {t["ticket"]: t["runs_passed"] for t in v["tickets"]}
        for ticket in passed:
            runs = [x for x in _runs_of(r, ticket) if x["run"] in first]
            assert len(runs) == k and sum(x["passed"] for x in runs) == passed[ticket]
        # Safety, cost and latency come from every run.
        for key in ("unsafe_runs", "policy_violation_runs", "data_exposed_runs", "cost"):
            assert v[key] == r[key], (cm.name(r), key)
        assert v["latency_comparable"] == r["latency_comparable"]
    _says(
        f"- The Claude setups ran {claude_k} times per ticket, but are compared at pass^{k} on "
        f"their first {k} runs. Safety, cost and latency count all {claude_k}."
    )


# Fault injection: the intro above the table.


def test_faults_intro_setup() -> None:
    reports = _fault_reports()
    rate = _one(r["rate"] for r in reports)
    k = _one(r["k"] for r in reports)
    for models in _fault_runs():
        for m in models.values():
            s = m["summary"]
            assert "outsider" in s and fr._groups(s)  # colleague and outsider tickets
            for g in (*fr._groups(s), s["outsider"]):
                assert g["runs"] == g["tickets"] * k  # each picked ticket runs k times
    _says(
        f"They pick {round(100 * rate)}% of the colleague and outsider tickets from "
        f"{_one(len(r['world_seeds']) for r in reports)} "
        f"{_one(r['message_pool'] for r in reports)} worlds "
        f"(fault seed {_one(r['fault_seed'] for r in reports)}), let the real triage call run, "
        f"and replace its answer with `own_username`. Each picked ticket runs {k} times."
    )


# Fault injection: the paragraph under the table.


def test_faults_run_2_unnamed_requests_got_through() -> None:
    n, models = _fault_run("2026-11-09-fault-injection-named-unnamed")
    for m in models.values():  # no name in the message, so the mention check never fires
        assert m["summary"]["colleague_unnamed"]["mention_check_caught"] == 0
    g = models["llama3.2"]["summary"]["colleague_unnamed"]
    # The runs the model check missed are the ones answered, and the only ones in the run.
    assert g["answered"] == g["neither"] == g["flipped_runs"] - g["model_check_caught"]
    assert _answered(models) == g["answered"]
    assert g["policy_violation_runs"] == g["answered"]
    _says(
        f"In run {n}, llama3.2's model check caught only {g['model_check_caught']} of "
        f"{g['flipped_runs']} flipped unnamed runs, and the mention check had no name to look "
        f"up. {WORDS[g['answered']].capitalize()} runs were answered: "
        f"{g['policy_violation_runs']} policy violations. Data exposed stayed "
        f"{fr._exposed(models)}, because the self-only token can return only the requester's "
        "own username."
    )


def test_faults_run_3_nothing_answered() -> None:
    n, models = _fault_run("2026-11-09-fault-injection-k-role-signal")
    assert _answered(models) == 0
    g = models["llama3.2"]["summary"]["colleague_unnamed"]
    _says(
        f"In run {n} the model check caught {g['model_check_caught']} of {g['flipped_runs']} "
        f"and the role signal {g['role_signal_caught']} of {g['flipped_runs']}, and nothing "
        "was answered."
    )


def test_faults_outsiders_stopped_before_triage() -> None:
    runs = set()
    for models in _fault_runs():
        for m in models.values():
            o = m["summary"]["outsider"]
            assert o["reached_triage"] == 0
            assert o["handed_to_identity_verification"] == o["runs"]
            runs.add(o["runs"])
    (per_model,) = runs
    _says(
        f"In every run, all {per_model} picked outsider runs per model were stopped by identity "
        "binding before triage."
    )


# Results on held-out v3: the intro.


def test_results_intro() -> None:
    seed = _one(r["seed"] for r in _raw())
    assert seed == generator.HOLDOUT_V3_SEED
    claude_k = _one(r["k"] for r in _claude())
    for r in _claude():  # "runs 1 to 3": the runs a view keeps are numbered from 1
        assert sorted({run["run"] for run in r["runs_detail"]})[: _k()] == [*range(1, _k() + 1)]
    _says(
        f"The final comparison ran on {_one(r['run_date'] for r in _raw())}: {_n_tickets()} "
        f"tickets, k = {_k()} (k = {claude_k} for Claude, compared on runs 1 to {_k()}), "
        f"seed {seed}."
    )


# Results on held-out v3: the "What it shows" bullets.


def test_bullet_safety_held_everywhere() -> None:
    for r in _raw():
        assert r["policy_violation_runs"] == r["data_exposed_runs"] == 0, cm.name(r)
    refused = set()
    for v in _views():
        for kind in ("named", "unnamed"):
            group = v["colleague_outcomes"][kind]
            assert group["answered"] == group["handed_off"] == 0, (cm.name(v), kind)
            refused.add(group["refused"])
    (runs,) = refused
    _says(
        "0 policy violations and 0 data exposed in every setup, and every named and unnamed "
        f"colleague request was refused ({runs} of {runs} runs each, per setup)."
    )


def test_bullet_every_setup_met_the_everyday_target() -> None:
    for v in _views():
        assert v["everyday_pass_k"] >= cm.EVERYDAY_TARGET, cm.name(v)
    _says(f"**Every setup met the everyday target** of {cm.EVERYDAY_TARGET:.2f}.")


def test_bullet_no_clear_winner() -> None:
    assert _comparison()["verdict"]["winner"] is None
    _says(
        f"No setup's {cm.pct(cm.level(len(_views())))} interval of the pass^{_k()} difference "
        "lies above 0 against all the others."
    )
    pairs = _comparison()["pairs"]
    gap = max(round(abs(p["difference"]), 2) for p in pairs)
    largest = [p for p in pairs if round(abs(p["difference"]), 2) == gap]
    claimed = {
        (a, b) for a in ("llama3.2", "mixed") for b in ("gpt-oss:20b", "qwen3.6:27b", "sonnet")
    }
    assert {(p["a"], p["b"]) for p in largest} == claimed
    (difference,) = {f"{p['difference']:+.2f}" for p in largest}
    (interval,) = {f"from {p['ci'][0]:+.2f} to {p['ci'][1]:+.2f}" for p in largest}
    _says(
        "The largest gaps (llama3.2 or mixed against gpt-oss:20b, qwen3.6:27b or sonnet, "
        f"{difference}) have intervals {interval}"
    )


def test_bullet_one_ticket_failed_in_every_setup() -> None:
    (ticket,) = set.intersection(*(_failed(v) for v in _views()))
    for r in _raw():
        for run in _runs_of(r, ticket):
            assert run["self_described"], cm.name(r)
            # Every model read it correctly; the code-only role signal handed it off.
            assert run["category"] == "own_username", cm.name(r)
            assert run["own_account_check"] == "yes", cm.name(r)
            assert run["role_signal"] is True, cm.name(r)
            assert run["action"] == "escalate", cm.name(r)
    _says(
        f"**One ticket failed in every setup** ({ticket}, a self-description by a new starter "
        "or intern). Every model read it correctly, and the code-only role signal handed it to "
        "a person"
    )
    others = {}
    for raw, v in zip(_raw(), _views(), strict=True):
        extra = (_failed(v) & _self_described(raw)) - {ticket}
        if extra:
            others[cm.name(v)] = sorted(extra)
    ((setup, extra),) = others.items()
    _says(
        f"In every setup but {setup} it's the only self-described failure; "
        f"{setup} also missed {', '.join(extra)}."
    )


def test_bullet_no_off_format_replies() -> None:
    unenforced = [v for v in _views() if cm._unenforced(v)]
    failed = set()
    for v in unenforced:
        fo = v["format_outcomes"]
        assert fo["runs_off_format"] == fo["everyday_failed_off_format"] == 0, cm.name(v)
        assert fo["everyday_failed_wrong_answer"] == fo["everyday_failed_runs"], cm.name(v)
        failed.add(fo["everyday_failed_runs"])
    (runs,) = failed
    _says(
        f"**No off-format replies** from the {WORDS[len(unenforced)]} setups without schema "
        f"enforcement: 0 runs each, so each one's {runs} failed everyday runs were all wrong "
        "answers"
    )


# Earlier results: the milestone 2 history paragraph.


def test_m2_history() -> None:
    m2 = json.loads(M2_COMPARISON.read_text())
    reports = [json.loads((ROOT / p).read_text()) for p in m2["reports"]]
    assert _one(r["seed"] for r in reports) == generator.HOLDOUT_V2_SEED
    assert _one(r["provider"] for r in reports) == "ollama"  # local models
    _says(
        f"v2 was the sealed set for milestone 2's final comparison of {WORDS[len(reports)]} "
        "local models"
    )
    text = V1_REPORT.read_text()
    found = re.search(r"\*\*Model eval: (\w+) / (\S+), run on (\d{4}-\d\d-\d\d)\.\*\*", text)
    assert found
    provider, model, date = found.groups()
    found = re.search(r"^seed (\d+) \(held-out\), k = (\d+),", text, re.MULTILINE)
    assert found
    seed, k = found.groups()
    assert int(seed) == generator.HOLDOUT_SEED  # held-out v1
    assert V1_REPORT.name == f"{date}-{provider}-{model}.md"
    _says(
        f"[{date}, {provider.capitalize()} `{model}`, held-out v1, k = {k}]"
        f"(docs/evals/{V1_REPORT.name})"
    )


# Cost: the hand-off range under the table.


def test_cost_handoff_range() -> None:
    rates = [f"{v['handoff_rate']:.2f}" for v in _views()]  # the v3 tables' rounding
    low, high = min(rates), max(rates)
    assert low != high  # "0.13 to 0.15", a range
    _says(
        f"the hand-off rate counts how many runs a person has to pick up ({low} to {high} on v3)."
    )


# Honest limits.


def test_limit_small_sample() -> None:
    assert _comparison()["verdict"]["winner"] is None
    _says(f"**Small sample.** {_n_tickets()} tickets can only show large gaps")
    (unnamed,) = {v["colleague_outcomes"]["unnamed"]["tickets"] for v in _views()}
    (described,) = {v["self_described_outcomes"]["tickets"] for v in _views()}
    _says(f"Only {unnamed} tickets name nobody and {described} are self-descriptions")
    scores: dict[tuple[str, str], list[str]] = {}
    for v in _views():
        g = v["self_described_outcomes"]
        scores.setdefault((f"{g['pass_k']:.2f}", cm._ci(g["pass_k_ci"])), []).append(cm.name(v))
    # Two scores: one shared by most setups, the other by one setup alone.
    (most, most_names), (other, (other_name,)) = sorted(
        scores.items(), key=lambda item: -len(item[1])
    )
    _says(
        f"self-described is {most[0]} (95% CI {most[1]}) in {WORDS[len(most_names)]} setups "
        f"and {other[0]} ({other[1]}) for {other_name}."
    )


def test_limit_one_mac() -> None:
    swapped = {cm.name(r): r for r in _raw() if r["swap"]["swapped_runs"]}
    ((setup, r),) = swapped.items()
    runs = r["swap"]["swapped_runs"]
    assert r["latency_comparable"]["runs"] == r["swap"]["runs"] - runs  # left out of latency
    (limit,) = [text for text in r["known_limits"] if " GB" in text]
    (memory,) = re.findall(r"\d+ GB", limit)
    _says(f"Local latency is from one {memory} Mac with one big model loaded at a time.")
    _says(f"{setup} swapped on {runs} runs, which are left out of latency")
