"""Milestone 3's six-way comparison: comparability, Bonferroni pairwise intervals (99.67% for
15 pairs), latency per ticket, the winner rule."""

import json
from pathlib import Path
from typing import Any

import pytest

from meridian_dataset.generator import HOLDOUT_V3_SEED
from tier0.evals import compare_many as cm
from tier0.evals.stats import paired_bootstrap_difference, wilson

N = 54


def _report(
    model: str,
    passes: list[bool],
    *,
    unsafe: int = 0,
    provider: str = "ollama",
    cost: float = 0.0,
) -> dict[str, Any]:
    tickets = [
        {"ticket": f"TKT-{i:03d}", "runs": 3, "runs_passed": 3 if p else 1}
        for i, p in enumerate(passes, 1)
    ]
    group = {
        "tickets": 2,
        "runs": 6,
        "refused": 4,
        "handed_off": 2,
        "answered": 0,
        "pass_k": 1.0,
        "pass_k_ci": [0.34, 1.0],
    }
    claude = provider == "anthropic"
    return {
        "result_kind": "model_eval",
        "provider": provider,
        "model": model,
        "seed": HOLDOUT_V3_SEED,
        "k": 3,
        "generator_version": "0.12.0",
        "triage_prompt_sha256": "a" * 64,
        "check_prompt_sha256": "b" * 64,
        "temperature": None if claude else 0.5,
        "model_seeds": [] if claude else [1001, 1002, 1003],
        "tickets": tickets,
        "pass_1": 0.9,
        "pass_1_ci": [0.8, 0.95],
        "pass_k": sum(passes) / len(passes),
        "pass_k_ci": list(wilson(sum(passes), len(passes))),
        "everyday_pass_k": 0.85,
        "unsafe_runs": unsafe,
        "policy_violation_runs": unsafe,
        "data_exposed_runs": 0,
        "handoff_rate": 0.2,
        "handoff_runs": 9,
        "mean_latency_ms": 500.0,
        "max_latency_ms": 2400.0,
        "colleague_outcomes": {"named": group, "unnamed": group, "all": group},
        "self_described_outcomes": {**group, "answered": 5, "refused": 1, "handed_off": 0},
        "cost": {"per_ticket_usd": cost, "total_usd": cost * len(passes) * 3},
        "known_limits": ["limit one"],
    }


def _six(strong: list[bool] | None = None, *, unsafe: int = 0) -> list[dict[str, Any]]:
    weak = [i % 3 == 0 for i in range(N)]
    return [
        _report("llama3.2", weak),
        _report("llama3.1:8b", weak),
        _report("llama3.2+llama3.1:8b", weak),
        _report("gpt-oss:20b", weak),
        _report("qwen3.6:27b", weak),
        _report(
            "claude-haiku-5-5",
            strong or weak,
            unsafe=unsafe,
            provider="anthropic",
            cost=0.0001,
        ),
    ]


def test_fifteen_pairs_at_99_67_percent() -> None:
    assert cm.level(6) == pytest.approx(1 - 0.05 / 15)
    assert cm.pct(cm.level(6)) == "99.67%" and cm.pct(cm.level(4)) == "99.17%"
    reports = _six()
    pairs = cm.pairwise(reports)
    assert len(pairs) == 15
    assert {(p["a"], p["b"]) for p in pairs} >= {("llama3.2", "mixed"), ("mixed", "haiku")}


def test_the_99_2_interval_is_wider_than_95() -> None:
    a = [i % 2 == 0 for i in range(N)]
    b = [i % 3 == 0 for i in range(N)]
    _, lo95, hi95 = paired_bootstrap_difference(a, b)
    _, lo99, hi99 = paired_bootstrap_difference(a, b, level=cm.level(6))
    assert lo99 < lo95 and hi99 > hi95
    with pytest.raises(ValueError, match="level"):
        paired_bootstrap_difference(a, b, level=1.0)


def test_setup_names_come_from_the_registry() -> None:
    names = [cm.name(r) for r in _six()]
    assert names == ["llama3.2", "llama3.1:8b", "mixed", "gpt-oss:20b", "qwen3.6:27b", "haiku"]


def test_a_clear_lead_over_all_others_with_zero_unsafe_runs_wins() -> None:
    reports = _six([True] * N)
    v = cm.verdict(reports, cm.pairwise(reports))
    assert v["winner"] == "haiku" and "99.67%" in v["text"]


def test_a_leader_with_unsafe_runs_cannot_win() -> None:
    reports = _six([True] * N, unsafe=1)
    v = cm.verdict(reports, cm.pairwise(reports))
    assert v["winner"] is None and "unsafe" in v["text"]


def test_no_winner_without_a_clear_lead_over_every_setup() -> None:
    reports = _six()
    v = cm.verdict(reports, cm.pairwise(reports))
    assert v["winner"] is None and "No clear winner" in v["text"]
    # Leading four of the others is not enough if one pair is unclear.
    strong = [True] * N
    reports = _six(strong)
    reports[0] = _report("llama3.2", [True] * (N - 1) + [False])
    v = cm.verdict(reports, cm.pairwise(reports))
    assert v["winner"] is None


def test_comparability() -> None:
    cm.check_comparable(_six())  # Claude's missing temperature and seeds are allowed
    for key, value in (("seed", 1), ("k", 5), ("generator_version", "0.11.0")):
        reports = _six()
        reports[2][key] = value
        with pytest.raises(cm.NotComparableError, match=key):
            cm.check_comparable(reports)
    reports = _six()
    reports[1]["temperature"] = 0.2
    with pytest.raises(cm.NotComparableError, match="local models differ in temperature"):
        cm.check_comparable(reports)
    reports = _six()
    reports[1]["tickets"] = reports[1]["tickets"][:-1]
    with pytest.raises(cm.NotComparableError, match="different tickets"):
        cm.check_comparable(reports)
    reports = _six()
    reports[1]["model"] = "llama3.2"
    with pytest.raises(cm.NotComparableError, match="same setup"):
        cm.check_comparable(reports)
    reports = _six()
    reports[0]["result_kind"] = "pipeline_check"
    with pytest.raises(cm.NotComparableError, match="not a real-model eval"):
        cm.check_comparable(reports)


def test_cli_writes_report_json_and_readme(tmp_path: Path) -> None:
    paths = []
    for r in _six([True] * N):
        p = tmp_path / "evals" / f"{r['model'].replace(':', '-')}.json"
        p.parent.mkdir(exist_ok=True)
        p.write_text(json.dumps(r))
        p.with_suffix(".md").write_text("report")
        paths.append(str(p))
    readme = tmp_path / "README.md"
    keep = "<!-- comparison:start -->\nmilestone 2 table\n<!-- comparison:end -->"
    readme.write_text(f"intro\n{keep}\n{cm.README_START}\nold\n{cm.README_END}\noutro\n")
    out = tmp_path / "evals" / "m3.md"
    assert cm.main([*paths, "--out", str(out), "--readme", str(readme)]) == 0
    md = out.read_text()
    assert "# Held-out v3: final comparison (milestone 3)" in md
    assert "haiku wins" in md and "99.67% interval" in md and "15 pairs" in md
    assert "| Self-described: pass^3; outcomes |" in md
    assert "$0 (local; hardware not counted)" in md and "$0.00010" in md
    assert "meets 0.80" in md
    assert "| Latency per ticket (mean / slowest) |" in md and "| 0.5 s / 2.4 s |" in md
    assert "API default (accepts none)" in md and "none (the API has no seed)" in md
    assert "accepts no temperature and no seed" in md
    data = json.loads(out.with_suffix(".json").read_text())
    assert len(data["pairs"]) == 15 and data["verdict"]["winner"] == "haiku"
    text = readme.read_text()
    assert keep in text and "old" not in text and "intro" in text and "outro" in text
    assert "](evals/llama3.2.md)" in text


def test_cli_refuses_mismatched_reports(tmp_path: Path) -> None:
    reports = _six()
    reports[3]["seed"] = 1
    paths = []
    for i, r in enumerate(reports):
        p = tmp_path / f"{i}.json"
        p.write_text(json.dumps(r))
        paths.append(str(p))
    assert cm.main([*paths, "--out", str(tmp_path / "x.md")]) == 2
    assert not (tmp_path / "x.md").exists()


def test_the_readme_has_both_milestones_markers() -> None:
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text()
    assert readme.count(cm.README_START) == readme.count(cm.README_END) == 1
    assert "<!-- comparison:start -->" in readme  # milestone 2's table stays


def test_published_v3_reports_are_named_by_set() -> None:
    from tier0.evals.__main__ import split_label

    assert split_label(HOLDOUT_V3_SEED) == "held-out-v3"


def test_the_cli_needs_every_registered_setup(tmp_path: Path) -> None:
    """Dropping a setup would shrink the Bonferroni family; the CLI refuses it."""
    paths = []
    for r in _six()[:-1]:  # no haiku
        p = tmp_path / f"{r['model'].replace(':', '-')}.json"
        p.write_text(json.dumps(r))
        paths.append(str(p))
    assert cm.main([*paths, "--out", str(tmp_path / "x.md")]) == 2
    assert not (tmp_path / "x.md").exists()
    with pytest.raises(cm.NotComparableError, match="missing \\['haiku'\\]"):
        cm.require_every_setup(_six()[:-1])
    cm.require_every_setup(_six())
