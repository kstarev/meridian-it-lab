"""The slowness watchdog: one SLOW line once a big model runs 3x slower than timed."""

import pytest

from tier0.evals import watch
from tier0.models import ModelRequest, ModelResponse


def test_slow_is_said_once_after_a_full_window_over_three_times_the_estimate(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(watch, "LOG", tmp_path / "watch.jsonl")
    said: list[str] = []
    w = watch.SlowWatch("qwen QC4", expected=3.0, say=said.append)
    w.record(60.0)  # loading the model: not counted on its own
    for _ in range(10):
        w.record(8.0)  # 2.7x: fine
    assert said == []
    for _ in range(10):
        w.record(10.0)  # 3.3x
    # Said as soon as the rolling window passes 3x (here at 9.2 s, 3.1x).
    assert len(said) == 1 and said[0].startswith("SLOW: qwen QC4 is averaging 9.2 s")
    assert "3.1x its timed 3.0 s (more than 3x)" in said[0]
    for _ in range(20):
        w.record(30.0)
    assert len(said) == 1  # once, not a flood
    assert '"event": "slow"' in (tmp_path / "watch.jsonl").read_text()


def test_the_watched_model_times_calls_and_passes_everything_else_through() -> None:
    class Inner:
        provider = "ollama"
        supports_seed = True

        def __init__(self) -> None:
            self.seed: int | None = None

        def complete(self, request: ModelRequest) -> ModelResponse:
            return ModelResponse("{}", "m", 1, 1, "stop", None)

    inner = Inner()
    w = watch.SlowWatch("x", expected=1.0, say=lambda _: None)
    model = watch.WatchedModel(inner, w)  # type: ignore[arg-type]
    model.seed = 1002
    assert inner.seed == 1002 and model.provider == "ollama" and model.supports_seed
    model.complete(ModelRequest(task="triage", system="s", user="u"))
    assert w.calls == 1


def test_timings_are_saved_and_read_back(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(watch, "TIMINGS", tmp_path)
    assert watch.expected_seconds("qwen3.6:27b") is None
    watch.save_timing("qwen3.6:27b", {"seconds_per_call": {"mean": 3.07}})
    assert watch.expected_seconds("qwen3.6:27b") == 3.07


def test_a_failing_watch_log_never_changes_a_calls_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Finding 6: the log write fails (here, its folder is a file) as SLOW trips; the
    model's reply still comes back, and a ModelError still reaches the caller."""
    from tier0.models import ModelError

    blocker = tmp_path / "m3"
    blocker.write_text("a file where the folder should be")
    monkeypatch.setattr(watch, "LOG", blocker / "watch.jsonl")

    class Slow:
        provider = "ollama"
        fail = False

        def complete(self, request: ModelRequest) -> ModelResponse:
            if self.fail:
                raise ModelError("down")
            return ModelResponse('{"category": "other"}', "m", 1, 1, "stop", None)

    inner = Slow()
    w = watch.SlowWatch("x", expected=1e-9, say=lambda _: None)  # every call is "slow"
    model = watch.WatchedModel(inner, w)  # type: ignore[arg-type]
    request = ModelRequest(task="triage", system="s", user="u")
    for _ in range(12):
        assert model.complete(request).text == '{"category": "other"}'
    assert w.warned  # SLOW tripped, and its log write failed quietly
    inner.fail = True
    with pytest.raises(ModelError, match="down"):
        model.complete(request)

    def broken(seconds: float) -> None:
        raise RuntimeError("watchdog bug")

    monkeypatch.setattr(w, "record", broken)
    inner.fail = False
    assert model.complete(request).text  # even a bug in the watchdog doesn't leak out


def test_timing_and_log_paths_are_anchored_at_the_repo_root() -> None:
    """Finding 9: not relative to wherever the command was started."""
    from tier0.secrets import REPO_ROOT

    for path in (watch.TIMINGS, watch.LOG):
        assert path.is_absolute() and path.is_relative_to(REPO_ROOT)
