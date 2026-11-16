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
