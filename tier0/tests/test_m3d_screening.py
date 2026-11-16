"""3d's screening on tuning data (fake models only: no paid call)."""

import json

from meridian_dataset.names import TUNING_3D_POOL
from tier0.evals import m3d_screening as sc
from tier0.models.base import ModelRequest, ModelResponse


class AlwaysOwn:
    """Reads every message as the sender's own and every check as "own account only"."""

    provider = "fake"
    model = "fake"
    temperature = None
    seed = None
    supports_seed = False

    def describe(self):
        return {}

    def complete(self, request: ModelRequest) -> ModelResponse:
        body = (
            {"category": "own_username"} if request.task == "triage" else {"own_account_only": True}
        )
        return ModelResponse(json.dumps(body), "fake", 10, 2, "stop", "r")


def test_the_3d_tuning_cases_and_their_labels() -> None:
    cases = sc.instances_3d()
    assert len(cases) == len(TUNING_3D_POOL.normal) + len(TUNING_3D_POOL.self_described) + len(
        TUNING_3D_POOL.colleague
    )
    labels = {(c.group, label) for c, label in cases}
    assert labels == {
        ("everyday", "plain"),
        ("everyday", "injected"),
        ("self_described", "self"),
        ("colleague", "relayed"),
        ("colleague", "forwarded"),
        ("colleague", "injected"),
    }
    assert not any("{" in c.text for c, _ in cases)


def test_screening_counts_by_label(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sc, "RESULTS", tmp_path)
    paths = sc.screen("haiku-3b", model_factory=lambda name: AlwaysOwn())
    assert len(paths) == 2
    triage = json.loads(paths[0].read_text())
    by = triage["by_label"]
    assert by["everyday/plain"]["correct"] == by["everyday/plain"]["runs"] > 0
    assert by["colleague/relayed"]["correct"] == 0
    assert by["colleague/injected"]["correct"] == 0
    # Scores are never overwritten: a second screen returns the saved files.
    assert sc.screen("haiku-3b", model_factory=None) == paths
    assert "colleague/forwarded: 0/" in sc.show()


def test_the_report_is_built_from_saved_scores(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sc, "RESULTS", tmp_path)
    sc.screen("haiku-3b", model_factory=lambda name: AlwaysOwn())
    md = sc.render()
    assert "tuning score, and optimistic" in md
    assert "| haiku-5-5 | HT4 |" in md and "| haiku-5-5 | C1 |" in md


# --- ruling R25 (2026-11-16): several requests in one message --------------------------------

import hashlib  # noqa: E402

import pytest  # noqa: E402

from tier0.evals import m3_selection  # noqa: E402
from tier0.models.base import OTHER  # noqa: E402


class Says:
    """Triage that always gives one category."""

    provider = "fake"
    model = "fake"
    temperature = None
    seed = None
    supports_seed = False

    def __init__(self, category: str) -> None:
        self.category = category

    def describe(self):
        return {}

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(json.dumps({"category": self.category}), "fake", 10, 2, "stop", "r")


def test_the_several_request_tuning_messages() -> None:
    cases = sc.instances_several()
    assert len(cases) == 12
    assert {c.group for c in cases} == {m3_selection.SEVERAL}
    assert {c.triage_expected for c in cases} == {OTHER}


def test_a_several_request_message_is_handed_off_unless_triage_says_own_username() -> None:
    cases = sc.instances_several()
    for category, right in (("own_username", 0), ("other", 36), ("about_someone_else", 36)):
        r = m3_selection.score("claude-haiku-5-5", "HT8", model=Says(category), cases=cases)
        assert (r["correct"]["several"], r["total"]["several"]) == (right, 36)


def _r(version: str, colleague: int, everyday: int, chars: int = 100) -> dict:
    return {
        "version": version,
        "correct": {"colleague": colleague, "everyday": everyday},
        "prompt_chars": chars,
    }


def test_r25s_rule_is_3as_rule_among_the_versions_that_clear_the_floor() -> None:
    results = [_r("T1", 100, 219), _r("HT7", 160, 219, 90), _r("HT8", 158, 218, 120)]
    # Only HT8 hands off every several-request message: it wins though 3a's rule alone keeps HT7.
    assert m3_selection.choose(results) == "HT7"
    assert m3_selection.choose_with_floor(results, {"HT8"}) == "HT8"
    # 3a's colleague floor (version 1's count) still applies, whether or not T1 cleared R25's.
    assert m3_selection.choose_with_floor([*results, _r("HT9", 99, 219)], {"HT9"}) is None
    assert m3_selection.choose_with_floor(results, set()) is None


def test_the_r25_pick_waits_for_every_candidate_and_refuses_stale_scores() -> None:
    def row(version: str, set_name: str, right: int = 36, **kw) -> dict:
        text = m3_selection.M3_TRIAGE_VERSIONS[version]
        r = {
            "model": "claude-sonnet-5-5",
            "version": version,
            "set": set_name,
            "k": 3,
            "prompt_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "prompt_chars": len(text),
            "correct": {"colleague": 170, "everyday": 219, "several": right},
            "total": {"colleague": 174, "everyday": 219, "several": 36},
        }
        return r | kw

    versions = sc.R25_CANDIDATES["claude-sonnet-5-5"]
    rows = [row(v, "all") for v in versions] + [row(v, "several", 30) for v in versions[:-1]]
    assert sc.r25_pick(rows, "claude-sonnet-5-5") is None  # ST6 not scored on several yet
    rows.append(row("ST6", "several"))
    pick, floors = sc.r25_pick(rows, "claude-sonnet-5-5")  # type: ignore[misc]
    assert pick == "ST6" and floors["ST6"] == (36, 36) and floors["ST5"] == (30, 36)
    rows[0]["prompt_sha256"] = "stale"
    with pytest.raises(ValueError):
        sc.r25_pick(rows, "claude-sonnet-5-5")


def test_r25s_floor_is_scored_on_triage_versions_only() -> None:
    with pytest.raises(ValueError):
        sc.score_several("claude-haiku-5-5", "C1")
