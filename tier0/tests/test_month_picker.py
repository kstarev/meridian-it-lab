"""The month runner's guide picker: Claude Sonnet with KS5 in every Claude arm, whatever the
arm's triage model (2026-11-17), and the sealed month's cost from measured calls.

Fake models and the tuning month only: no paid call, and no test here loads, sizes or runs the
sealed month.
"""

import hashlib
import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from meridian_dataset.month import MONTH_SIZE, TUNING_MONTH, month_for
from tier0.evals import month as mo
from tier0.evals.setups import ALL_SETUPS, HAIKU, SONNET, Setup
from tier0.knowledge.prompts import FROZEN, PICKER_VERSIONS
from tier0.models import ModelAdapter, ModelRequest, ModelResponse
from tier0.models.pricing import cost_usd

FEW = 200  # bootstrap resamples in tests: enough to check the shape, fast


class Fake:
    """A stand-in model that triages everything "other" and says yes to every check. Its
    provider and model can be set, so a test can check what a call is priced at."""

    temperature = None
    supports_seed = False

    def __init__(
        self, model: str = "fake-1", *, provider: str = "fake", crash_after: int | None = None
    ) -> None:
        self.model = model
        self.provider = provider
        self.crash_after = crash_after
        self.seed: int | None = None
        self.calls: Counter[str] = Counter()

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls[request.task] += 1
        if self.crash_after is not None and sum(self.calls.values()) > self.crash_after:
            raise RuntimeError("the machine went to sleep")
        if request.task == "triage":
            return ModelResponse(
                json.dumps({"category": "other"}), self.model, 300, 8, "stop", None
            )
        if request.task == "own_account_check":
            body = json.dumps({"own_account_only": True})
            return ModelResponse(body, self.model, 320, 9, "stop", None)
        return ModelResponse('{"kind": "hand_off"}', self.model, 2_700, 20, "stop", None)

    def describe(self) -> dict[str, str]:
        return {}


class Seen:
    """A make_picker stand-in: records what main hands the picker, and hands every message off
    after one guide_pick call on the adapter it was given."""

    def __init__(self) -> None:
        self.args: list[tuple[str, ModelAdapter, str | None]] = []

    def __call__(self, setup: Setup, model: ModelAdapter, prompt: str | None) -> mo.Picker:
        self.args.append((setup.name, model, prompt))

        def stage(text: str) -> tuple[str, str | None]:
            model.complete(ModelRequest(task="guide_pick", system="picker", user=text))
            return mo.STAGE_HAND_OFF, None

        return mo.Picker("knowledge", stage, prompt, "0" * 64)


def _never() -> ModelAdapter:
    raise AssertionError("no separate picker model is built here")


def _run(
    tmp: Path,
    setup: str,
    *extra: str,
    model: Fake,
    picker: Seen,
    picker_model: Callable[[], ModelAdapter] = _never,
) -> int:
    argv = ["run", "--set", TUNING_MONTH, "--setup", setup, "--k", "1", "--picker", "knowledge"]
    return mo.main(
        [*argv, *extra, "--out", str(tmp)],
        make_model=lambda: model,  # type: ignore[arg-type, return-value]
        make_picker=picker,
        make_picker_model=picker_model,
        samples=FEW,
    )


def _result(tmp: Path, setup: str) -> tuple[dict[str, Any], str]:
    stem = mo.result_stem(TUNING_MONTH, setup, "knowledge", 1, 1130)
    result = json.loads((tmp / f"{stem}.json").read_text(encoding="utf-8"))
    return result, (tmp / f"{stem}.md").read_text(encoding="utf-8")


# --- the mapping ------------------------------------------------------------------------------


def test_every_claude_setup_picks_guides_with_ks5_on_sonnet() -> None:
    claude = [s for s in ALL_SETUPS.values() if s.provider == "claude"]
    names = {s.name for s in claude}
    assert {"haiku-3d", "haiku-3d-hc7", "sonnet-3d", "haiku-3b", "sonnet-3b"} <= names
    assert set(mo.PICKER_PROMPT_FOR_SETUP) == names
    for setup in claude:
        assert mo.picker_model_for(setup) == SONNET == mo.PICKER_MODEL
        assert mo.picker_prompt_for(setup) == "KS5" == mo.PICKER_PROMPT
    # KS5 is milestone 4's frozen pick for Sonnet (ADR-046 makes Sonnet the picker's model).
    assert FROZEN[mo.PICKER_MODEL] == mo.PICKER_PROMPT and mo.PICKER_PROMPT in PICKER_VERSIONS


def test_a_local_setups_picker_stays_on_its_own_model() -> None:
    local = ALL_SETUPS["llama3.2"]
    assert mo.picker_model_for(local) == "llama3.2"
    assert mo.picker_prompt_for(local) is None  # as before: it needs --picker-prompt


def test_the_old_pairing_is_kept_to_reproduce_earlier_summaries() -> None:
    old = {name: mo.picker_prompt_for(ALL_SETUPS[name], own_model=True) for name in ALL_SETUPS}
    assert {name: v for name, v in old.items() if v} == {
        "haiku-3b": "KH2",
        "sonnet-3b": "KS2",
        "haiku-3d": "KH5",
        "haiku-3d-hc7": "KH5",
        "sonnet-3d": "KS5",
    }
    assert mo.picker_model_for(ALL_SETUPS["haiku-3d"], own_model=True) == HAIKU


# --- the run ----------------------------------------------------------------------------------


def test_a_haiku_arm_sends_every_picker_call_to_sonnet_and_prices_it_there(tmp_path: Path) -> None:
    triage = Fake(HAIKU, provider="anthropic")
    sonnet = Fake(SONNET, provider="anthropic")
    seen = Seen()
    code = _run(tmp_path, "haiku-3d", model=triage, picker=seen, picker_model=lambda: sonnet)
    assert code == 0  # everything handed off: nothing out of scope answered
    [(name, adapter, prompt)] = seen.args
    assert (name, prompt, adapter.model) == ("haiku-3d", "KS5", SONNET)
    assert (triage.calls["guide_pick"], sonnet.calls["guide_pick"]) == (0, 300)
    assert (sonnet.calls["triage"], triage.calls["triage"]) == (0, 300)
    result, summary = _result(tmp_path, "haiku-3d")
    c = result["config"]
    assert (c["picker_model"], c["picker_prompt"]) == (SONNET, "KS5")
    assert (c["triage_model"], c["triage_prompt"]) == (HAIKU, "HT8")
    assert c["calls"] == {"guide_pick": 300, "triage": 300}
    expected = cost_usd("anthropic", HAIKU, 300 * 300, 300 * 8) + cost_usd(
        "anthropic", SONNET, 300 * 2_700, 300 * 20
    )
    assert c["cost_usd"] == round(expected, 6)
    assert "picker: knowledge, KS5 on claude-sonnet-5-5" in summary
    assert "--picker-on-own-model" not in summary


def test_the_old_pairing_runs_the_picker_on_the_setups_own_model(tmp_path: Path) -> None:
    model = Fake(HAIKU, provider="anthropic")
    seen = Seen()
    assert _run(tmp_path, "haiku-3d", "--picker-on-own-model", model=model, picker=seen) == 0
    [(_, adapter, prompt)] = seen.args
    assert (prompt, adapter.model) == ("KH5", HAIKU)
    assert (model.calls["triage"], model.calls["guide_pick"]) == (300, 300)
    result, summary = _result(tmp_path, "haiku-3d")
    assert (result["config"]["picker_model"], result["config"]["picker_prompt"]) == (HAIKU, "KH5")
    assert result["config"]["cost_usd"] == round(
        cost_usd("anthropic", HAIKU, 300 * 300 + 300 * 2_700, 300 * 8 + 300 * 20), 6
    )
    assert "--picker knowledge --picker-on-own-model" in summary
    assert "picker: knowledge, KH5 on claude-haiku-5-5" in summary


def test_a_sonnet_arm_keeps_one_adapter_for_triage_and_the_picker(tmp_path: Path) -> None:
    model = Fake(SONNET, provider="anthropic")
    seen = Seen()
    assert _run(tmp_path, "sonnet-3d", model=model, picker=seen) == 0
    [(_, adapter, prompt)] = seen.args
    assert prompt == "KS5" and adapter.model == SONNET
    assert (model.calls["triage"], model.calls["guide_pick"]) == (300, 300)
    result, _ = _result(tmp_path, "sonnet-3d")
    assert result["config"]["picker_model"] == SONNET


def test_picker_prompt_still_overrides_the_prompt(tmp_path: Path) -> None:
    seen = Seen()
    sonnet = Fake(SONNET)
    argv = ("--picker-prompt", "KS2")
    assert (
        _run(
            tmp_path, "haiku-3d", *argv, model=Fake(HAIKU), picker=seen, picker_model=lambda: sonnet
        )
        == 0
    )
    assert seen.args[0][2] == "KS2"


def test_a_stopped_run_never_resumes_under_another_picker_model(tmp_path: Path) -> None:
    crashing = Fake(HAIKU, crash_after=40)
    with pytest.raises(RuntimeError, match="went to sleep"):
        _run(tmp_path, "haiku-3d", "--picker-on-own-model", model=crashing, picker=Seen())
    sonnet = Fake(SONNET)
    with pytest.raises(SystemExit, match="another configuration"):
        _run(tmp_path, "haiku-3d", model=Fake(HAIKU), picker=Seen(), picker_model=lambda: sonnet)
    assert sum(sonnet.calls.values()) == 0


# --- the size-based estimate (tuning month) ---------------------------------------------------


def test_the_estimate_prices_a_haiku_arms_picker_on_sonnet() -> None:
    month = month_for(TUNING_MONTH)
    setup = ALL_SETUPS["haiku-3d"]
    new = mo.estimate(month, setup, k=1)
    old = mo.estimate(month, setup, k=1, own_model_picker=True)
    assert (new["picker_model"], new["picker_prompt"]) == (SONNET, "KS5")
    assert (old["picker_model"], old["picker_prompt"]) == (HAIKU, "KH5")
    row = new["upper"]["with_picker"]
    picks_in, picks_out = 300 * mo.PICKER_INPUT_TOKENS, 300 * mo.PICKER_OUTPUT_TOKENS
    triage = cost_usd(
        "anthropic", HAIKU, row["input_tokens"] - picks_in, row["output_tokens"] - picks_out
    )
    picks = cost_usd("anthropic", SONNET, picks_in, picks_out)
    assert row["usd"] == pytest.approx(triage + picks, abs=1e-4)
    assert old["upper"]["with_picker"]["usd"] < row["usd"]
    assert new["upper"]["without_picker"] == old["upper"]["without_picker"]
    sonnet = ALL_SETUPS["sonnet-3d"]
    assert mo.estimate(month, sonnet, k=1) == mo.estimate(month, sonnet, k=1, own_model_picker=True)


# --- the sealed month's cost from measured calls ----------------------------------------------


def test_measured_month_cost_scales_cost_per_call_by_calls_per_ticket() -> None:
    per_call = {"triage": mo.Measured(10, 0.01, "fake"), "guide_pick": mo.Measured(4, 0.02, "fake")}
    c = mo.measured_month_cost(
        per_call, {"triage": 10, "guide_pick": 5}, route_tickets=10, tickets=20, k=3
    )
    # triage: $0.001 x 1 per ticket x 20 x 3; the picker: $0.005 x 0.5 per ticket x 20 x 3
    assert c["usd_by_task"] == pytest.approx({"triage": 0.06, "guide_pick": 0.15})
    assert c["usd"] == 0.21
    assert c["calls"] == {"triage": 60, "guide_pick": 30}


def test_a_measurement_is_priced_like_the_runs_own_cost_line() -> None:
    m = mo.Measured.from_tokens(SONNET, 116, 96_206, 1_160, "fake")
    assert m.usd == cost_usd("anthropic", SONNET, 96_206, 1_160)
    assert m.usd_per_call == pytest.approx(0.0017587, abs=1e-7)


def test_the_sealed_months_cost_comes_from_tuning_calls_and_reads_no_month(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_month(*args: object, **kwargs: object) -> None:
        raise AssertionError("the cost from measured calls reads no month")

    monkeypatch.setattr(mo, "month_for", no_month)
    assert MONTH_SIZE == 300
    for name in ("haiku-3d", "sonnet-3d"):
        assert mo.MEASURED_ARMS[name]["guide_pick"] is mo.MEASURED["picker_KS5"]
    assert mo.MEASURED["picker_KS5"].usd_per_call == pytest.approx(2.5021 / 440)
    costs = mo.sealed_month_costs()
    usd = {arm: {k: c["usd"] for k, c in by_k.items()} for arm, by_k in costs.items()}
    # The figures in the 3c plan's "R26 options after the Sonnet picker (2026-11-17)".
    assert usd == {
        "haiku-3d": {1: 1.3662, 2: 2.7324, 3: 4.0985},
        "sonnet-3d": {1: 1.9182, 2: 3.8363, 3: 5.7545},
        "haiku-3d2": {1: 1.1681, 2: 2.3362, 3: 3.5043},
        "sonnet-3d2": {1: 1.7147, 2: 3.4294, 3: 5.1441},
    }
    assert costs["haiku-3d"][3]["calls"] == {
        "triage": 900,
        "own_account_check": 120,
        "guide_pick": 705,
    }
    assert mo.main(["sealed-cost", "--k", "3"]) == 0
    out = capsys.readouterr().out
    assert "$4.0985" in out and "$5.7545" in out and "k = 2" not in out
    assert "$3.5043" in out and "$5.1441" in out
    assert "the sealed run (haiku-3d2 + sonnet-3d2, k = 3): $8.6484" in out
    assert mo.main(["sealed-cost", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["sonnet-3d"]["2"]["usd"] == 3.8363


def test_the_shipping_arms_are_priced_from_their_own_rehearsal() -> None:
    assert mo.ARM_ROUTE["haiku-3d2"] == {"triage": 300, "own_account_check": 40, "guide_pick": 202}
    assert mo.ARM_ROUTE["sonnet-3d2"] == {
        "triage": 300,
        "own_account_check": 40,
        "guide_pick": 201,
    }
    assert mo.ARM_ROUTE["haiku-3d"] is mo.TUNING_ROUTE
    costs = mo.sealed_month_costs((1,))
    assert costs["haiku-3d2"][1]["usd"] == pytest.approx(1.1681, abs=1e-4)
    assert costs["sonnet-3d2"][1]["usd"] == pytest.approx(1.7147, abs=1e-4)
    haiku_picker = mo.MEASURED["picker_KS5_haiku_3d2"].usd_per_call
    sonnet_picker = mo.MEASURED["picker_KS5_sonnet_3d2"].usd_per_call
    assert haiku_picker == pytest.approx(sonnet_picker, rel=0.01)
    assert haiku_picker == pytest.approx(mo.MEASURED["picker_KS5"].usd_per_call, rel=0.02)
    assert sonnet_picker == pytest.approx(mo.MEASURED["picker_KS5"].usd_per_call, rel=0.02)


def test_the_sealed_run_is_frozen_to_the_prompts_v6_and_r1_ran() -> None:
    assert mo.SEALED_ARMS == ("haiku-3d2", "sonnet-3d2")
    assert mo.SEALED_K == 3
    hashes = {
        "haiku-3d2": (
            "5469f3352b35f9e034eaeaf32c616d484f656a335f4a128e896f1b3ddc4df737",
            "0fc143e4f36cb40f2657be891419eb367899d40a664feccad069487d5fdeaf91",
        ),
        "sonnet-3d2": (
            "5b33dff857664e31b22eefe76b69fc09587dc5f6840cfb3419434770b4177ce2",
            "2fe139fe68417c92bd53f8dcdf93668e3b15e911538268a2c68bd92f86838666",
        ),
    }
    ks5 = "45906bd1b29e19d18c3ff282c4fb0e68b83d0d09e50998d7b28ef82ff1415cdf"
    for arm, (triage, check) in hashes.items():
        setup = ALL_SETUPS[arm]
        assert setup.tier_c is True
        assert setup.split_prompt is None
        prompts = setup.prompts()
        assert hashlib.sha256(prompts.triage.encode("utf-8")).hexdigest() == triage
        assert hashlib.sha256(prompts.check.encode("utf-8")).hexdigest() == check
        assert mo.picker_model_for(setup) == SONNET
        assert mo.picker_prompt_for(setup) == "KS5"
        assert hashlib.sha256(PICKER_VERSIONS["KS5"].encode("utf-8")).hexdigest() == ks5
