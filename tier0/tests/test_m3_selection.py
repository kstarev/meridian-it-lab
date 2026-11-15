"""Milestone 3 prompt selection: tuning instances, scoring, the fixed rule, the estimate."""

import json

import pytest

from tier0.evals import m3_selection as sel
from tier0.evals.prompt_history import M3_CHECK_VERSIONS, M3_TRIAGE_VERSIONS
from tier0.models import ModelRequest, ModelResponse


class Perfect:
    """Answers every tuning case correctly, by looking at what the case expects."""

    provider = "ollama"
    supports_seed = True

    def __init__(self) -> None:
        self.seed: int | None = None
        self.seeds: list[int | None] = []
        self.answers = {c.text: c for c in sel.instances()}

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.seeds.append(self.seed)
        text = request.user.split("Message:\n", 1)[-1]
        case = self.answers[text]
        if request.task == "triage":
            body = json.dumps({"category": case.triage_expected})
        else:
            body = json.dumps({"own_account_only": case.check_expected})
        return ModelResponse(body, "m", 100, 5, "stop", None)


def test_instances_are_tuning_only_and_grouped() -> None:
    cases = sel.instances()
    groups = {
        g: sum(c.group == g for c in cases) for g in ("everyday", "self_described", "colleague")
    }
    assert groups == {"everyday": 61, "self_described": 21, "colleague": 34}
    assert sum(c.unnamed for c in cases) == 19
    assert all("{" not in c.text for c in cases)


@pytest.mark.parametrize("version", ["T1", "C1"])
def test_a_perfect_model_scores_full_marks(version: str) -> None:
    model = Perfect()
    result = sel.score("llama3.2", version, k=2, model=model)
    assert result["correct"] == result["total"]
    assert result["total"]["everyday"] == 2 * 61 and result["total"]["colleague_unnamed"] == 38
    assert set(model.seeds) == {1001, 1002}
    assert result["cost_usd"] == 0 and result["wrong"] == {}
    assert result["mean_call_ms"] >= 0
    prompts = M3_TRIAGE_VERSIONS if version == "T1" else M3_CHECK_VERSIONS
    assert result["prompt_chars"] == len(prompts[version])


def _r(version: str, colleague: int, everyday: int, chars: int) -> dict:
    return {
        "version": version,
        "correct": {"colleague": colleague, "everyday": everyday},
        "prompt_chars": chars,
    }


def test_the_rule_keeps_colleague_catches_then_maximises_everyday() -> None:
    # T2 answers more everyday requests but catches fewer colleagues: not allowed.
    results = [_r("T1", 40, 100, 900), _r("T2", 39, 130, 800), _r("T3", 40, 120, 950)]
    assert sel.choose(results) == "T3"
    # A tie on everyday answers goes to more colleague catches first...
    results.append(_r("T4", 41, 120, 999))
    assert sel.choose(results) == "T4"
    # ...and only then to the shorter prompt.
    results.append(_r("T5", 41, 120, 700))
    assert sel.choose(results) == "T5"
    # Version 1 wins when nothing beats it.
    assert sel.choose([_r("C1", 40, 100, 900), _r("C2", 30, 140, 100)]) == "C1"
    with pytest.raises(ValueError, match="version 1"):
        sel.choose([_r("T2", 1, 1, 1)])


def test_the_estimate_uses_the_full_budget_and_makes_no_call() -> None:
    est = sel.estimate_cost("claude-haiku-5-5")
    assert est["tuning_selection_calls"] == 116 * 3 * 2 * sel.MAX_VERSIONS
    assert est["agent_calls_max"] == (6 + 6 + 42 + 54) * 3 * 2
    assert 0 < est["estimated_usd"] < 2.0


def test_claude_scoring_gets_the_api_only_guard_and_ollama_stays_local() -> None:
    assert sel.provider_of("claude-haiku-5-5") == "claude"
    assert sel.provider_of("llama3.2") == sel.provider_of("llama3.1:8b") == "ollama"


def _scores(model: str, task: str, colleague: dict[str, int], everyday: dict[str, int]):
    prefix = "T" if task == "triage" else "C"
    return [
        {
            "model": model,
            "task": task,
            "version": f"{prefix}{i}",
            "correct": {
                "everyday": everyday.get(f"{prefix}{i}", 100),
                "self_described": 18,
                "colleague": colleague.get(f"{prefix}{i}", 40),
                "colleague_unnamed": 20,
            },
            "total": {
                "everyday": 138,
                "self_described": 18,
                "colleague": 42,
                "colleague_unnamed": 21,
            },
            "prompt_chars": 600 + i,
            "prompt_sha256": _hash(task, f"{prefix}{i}"),
            "k": sel.K,
            "cost_usd": 0.0,
        }
        for i in range(1, sel.MAX_VERSIONS + 1)
    ]


def _hash(task: str, version: str) -> str:
    """The current prompt's hash, so a made-up score counts as current."""
    import hashlib

    pool = M3_TRIAGE_VERSIONS if task == "triage" else M3_CHECK_VERSIONS
    return hashlib.sha256(pool.get(version, version).encode()).hexdigest()


def test_each_setup_picks_from_its_own_models_and_waits_for_missing_scores() -> None:
    results = (
        _scores("llama3.2", "triage", {}, {"T3": 130})
        + _scores("llama3.2", "check", {"C2": 39}, {"C2": 138, "C4": 120})
        + _scores("llama3.1:8b", "triage", {}, {"T5": 131})
        + _scores("llama3.1:8b", "check", {}, {"C5": 135})
    )
    picks = sel.choose_setups(results)
    assert picks["llama3.2"] == {"triage": "T3", "check": "C4"}  # C2 dropped a catch
    assert picks["llama3.1:8b"] == {"triage": "T5", "check": "C5"}
    assert picks["mixed"] == {"triage": "T3", "check": "C5"}
    assert picks["haiku"] == {"triage": None, "check": None}
    md = sel.render(results, "cmd")
    assert "tuning score, and optimistic" in md
    assert "| T3 **(chosen)** | 130/138 |" in md
    assert "| haiku | claude-haiku-5-5 | pending |" in md


def test_claude_gets_its_own_versions_within_the_same_budget() -> None:
    assert sel.candidates("llama3.2", "triage") == ("T1", "T2", "T3", "T4", "T5")
    assert sel.candidates("llama3.1:8b", "check") == ("C1", "C2", "C3", "C4", "C5")
    assert sel.candidates("claude-haiku-5-5", "triage") == ("T1", "HT2", "HT3", "HT4", "HT5")
    assert sel.candidates("claude-haiku-5-5", "check") == ("C1", "HC2", "HC3", "HC4", "HC5")


def test_the_cli_refuses_a_version_outside_the_models_budget(capsys) -> None:
    assert sel.main(["score", "--model", "claude-haiku-5-5", "--versions", "T2"]) == 2
    assert "not candidates for claude-haiku-5-5: T2" in capsys.readouterr().err
    assert sel.main(["score", "--model", "llama3.2", "--versions", "nope"]) == 2


def test_haiku_is_chosen_from_its_own_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    # Haiku's own versions were never written (its version 1 was flawless); stand-ins here.
    for pool, prefix in ((M3_TRIAGE_VERSIONS, "HT"), (M3_CHECK_VERSIONS, "HC")):
        for i in range(2, sel.MAX_VERSIONS + 1):
            monkeypatch.setitem(pool, f"{prefix}{i}", f"{prefix}{i}")

    def rename(rows: list[dict], prefix: str) -> list[dict]:
        for r in rows:
            if r["version"][1] != "1":
                r["version"] = prefix + r["version"][1:]
                r["prompt_sha256"] = _hash(r["task"], r["version"])
        return rows

    haiku = "claude-haiku-5-5"
    results = rename(_scores(haiku, "triage", {}, {"T4": 137}), "HT") + rename(
        _scores(haiku, "check", {"C3": 41}, {"C3": 120}), "HC"
    )
    # A llama-only version scored on Haiku by hand isn't a candidate and is ignored.
    results += _scores(haiku, "triage", {}, {"T2": 138})[1:2]
    picks = sel.choose_setups(results)
    assert picks["haiku"] == {"triage": "HT4", "check": "HC3"}
    assert picks["llama3.2"] == {"triage": None, "check": None}


def test_a_flawless_version_1_is_kept_without_writing_more() -> None:
    perfect = _scores("claude-haiku-5-5", "check", {"C1": 42}, {"C1": 138})[:1]
    perfect[0]["correct"]["colleague_unnamed"] = 21
    assert sel.flawless(perfect[0])
    picks = sel.choose_setups(perfect)
    assert picks["haiku"]["check"] == "C1" and picks["haiku"]["triage"] is None
    md = sel.render(perfect, "cmd")
    assert "made no mistake on any tuning message" in md
    # One miss anywhere and the remaining versions are still owed.
    perfect[0]["correct"]["self_described"] = 17
    assert sel.choose_setups(perfect)["haiku"]["check"] is None


def test_tuning_scores_split_off_format_misses_from_wrong_answers() -> None:
    class HalfFenced(Perfect):
        def complete(self, request: ModelRequest) -> ModelResponse:
            right = super().complete(request)
            if "colleague" in self.answers[request.user.split("Message:\n", 1)[-1]].group:
                return ModelResponse(f"```{right.text}```", "m", 100, 5, "stop", None)
            return right

    result = sel.score("llama3.2", "T1", k=sel.K, model=HalfFenced())
    assert result["correct"]["colleague"] == 0 and result["off_format"]["colleague"] == 102
    assert result["off_format"]["everyday"] == 0
    assert "| 102 of 102 |" in sel.render([result], "cmd")


def test_scoring_resumes_at_the_first_unsaved_version(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(sel, "RESULTS", tmp_path)
    monkeypatch.setattr(sel, "make_model", lambda name: Perfect())
    monkeypatch.setattr(sel, "make_room", lambda *a, **k: [])
    scored: list[str] = []
    real = sel.score

    def counting(name, version, **kw):  # type: ignore[no-untyped-def]
        scored.append(version)
        if version == "T3":
            raise RuntimeError("crash mid-version")
        return real(name, version, **kw)

    monkeypatch.setattr(sel, "score", counting)
    with pytest.raises(RuntimeError):
        sel.main(["score", "--model", "llama3.2", "--versions", "T1", "T2", "T3", "--k", "1"])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["llama3.2__T1.json", "llama3.2__T2.json"]
    scored.clear()
    monkeypatch.setattr(sel, "score", real)
    assert (
        sel.main(["score", "--model", "llama3.2", "--versions", "T1", "T2", "T3", "--k", "1"]) == 0
    )
    out = capsys.readouterr().out
    assert "T1: already scored" in out and "T2: already scored" in out and "llama3.2 T3:" in out
    assert not list(tmp_path.glob("*.partial"))


def test_timing_estimates_tuning_dev_and_v3_from_tuning_calls_only() -> None:
    model = Perfect()
    est = sel.time_calls("llama3.2", model, n=4)
    assert len(model.seeds) == 5  # one untimed load call, then 4 timed
    assert est["tuning_full_budget"]["calls"] == 116 * 3 * 2 * 5
    assert est["v3"]["calls"] == 54 * 3 * 2 + 2
    assert est["seconds_per_call"]["mean"] >= 0


def test_sonnet_gets_its_own_versions_too() -> None:
    assert sel.candidates("claude-sonnet-5-5", "triage") == ("T1", "ST2", "ST3", "ST4", "ST5")
    assert sel.provider_of("claude-sonnet-5-5") == "claude"


def test_gpt_oss_check_versions_exist_and_name_the_exact_reply() -> None:
    for v in sel.candidates("gpt-oss:20b", "check"):
        text = M3_CHECK_VERSIONS[v]
        if v != "C1":
            assert '{"own_account_only": true}' in text and '{"own_account_only": false}' in text
    assert sel.task_of("GC3") == "check"


def test_qwen_check_versions_exist_and_name_the_exact_reply() -> None:
    for v in sel.candidates("qwen3.6:27b", "check")[1:]:
        assert '{"own_account_only": true}' in M3_CHECK_VERSIONS[v]


def test_a_stale_saved_score_is_rescored_and_never_counted(monkeypatch, tmp_path, capsys) -> None:
    """Finding 4: a score made at another k, or on older prompt text, isn't reused."""
    monkeypatch.setattr(sel, "RESULTS", tmp_path)
    monkeypatch.setattr(sel, "make_model", lambda name: Perfect())
    monkeypatch.setattr(sel, "make_room", lambda *a, **k: [])
    fresh = sel.score("llama3.2", "T2", k=sel.K, model=Perfect())
    assert sel.current(fresh)
    quick = sel.score("llama3.2", "T2", k=1, model=Perfect())
    assert not sel.current(quick)  # k = 1
    assert not sel.current({**fresh, "prompt_sha256": "0" * 64})  # reworded since
    (tmp_path / "llama3.2__T2.json").write_text(json.dumps(quick))
    assert sel.main(["score", "--model", "llama3.2", "--versions", "T2"]) == 0
    out = capsys.readouterr().out
    assert "saved score is stale (k or prompt changed); rescoring" in out
    assert json.loads((tmp_path / "llama3.2__T2.json").read_text())["k"] == sel.K
    # Stale scores never reach the selection rule: a T2 that would win counts only when
    # it's current.
    rows = _scores("llama3.2", "triage", {}, {"T2": 137})
    assert sel.choose_setups(rows)["llama3.2"]["triage"] == "T2"
    rows[1]["k"] = 1
    assert sel.choose_setups(rows)["llama3.2"]["triage"] is None  # T2 now unscored
    rows[1]["k"], rows[1]["prompt_sha256"] = sel.K, "0" * 64
    assert sel.choose_setups(rows)["llama3.2"]["triage"] is None
