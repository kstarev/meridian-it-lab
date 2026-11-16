"""Milestone 3's setups: models per task, their prompts, and the routing adapter."""

import json

import pytest

from tier0 import messages
from tier0.evals import __main__ as cli
from tier0.evals.setups import BIG_MODELS, SETUPS, THINK, Setup
from tier0.models import ModelRequest, ModelResponse
from tier0.models.routed import RoutedModel


class Fake:
    provider = "ollama"
    temperature = 0.5
    supports_seed = True

    def __init__(self, model: str) -> None:
        self.model = model
        self.seed: int | None = None
        self.tasks: list[str] = []
        self.texts: list[str] = []

    def describe(self) -> dict[str, str]:
        return {"model_id": self.model[:4], "model_digest": self.model, "ollama_version": "1"}

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.tasks.append(request.task)
        self.texts.append(request.user)
        text = (
            '{"category": "own_username"}'
            if request.task == "triage"
            else '{"own_account_only": true}'
        )
        return ModelResponse(text, self.model, 1, 1, "stop", None)


@pytest.fixture(autouse=True)
def no_real_ollama(monkeypatch: pytest.MonkeyPatch) -> list[set[str]]:
    """The CLI frees memory on the real Ollama before a local setup runs; not in tests."""
    rooms: list[set[str]] = []
    monkeypatch.setattr(cli, "make_room", lambda wanted, **_: rooms.append(set(wanted)) or [])
    return rooms


def test_the_seven_setups() -> None:
    assert set(SETUPS) == {
        "llama3.2",
        "llama3.1:8b",
        "mixed",
        "haiku",
        "sonnet",
        "gpt-oss:20b",
        "qwen3.6:27b",
    }
    assert SETUPS["sonnet"].triage_model == "claude-sonnet-5-5"
    assert {s.name: s.v3_k for s in SETUPS.values() if s.provider == "claude"} == {
        "haiku": 5,
        "sonnet": 5,
    }
    assert all(s.v3_k == 3 for s in SETUPS.values() if s.provider == "ollama")
    assert SETUPS["mixed"].label == "llama3.2+llama3.1:8b"
    assert (
        SETUPS["haiku"].provider == "claude" and SETUPS["haiku"].triage_model == "claude-haiku-5-5"
    )


def test_no_setup_pairs_two_big_models_and_gpt_oss_reasons_at_its_lowest() -> None:
    assert {"gpt-oss:20b", "qwen3.6:27b"} == BIG_MODELS
    for setup in SETUPS.values():
        assert len(setup.models & BIG_MODELS) <= 1
    with pytest.raises(ValueError, match="two big models"):
        Setup("both", "ollama", "gpt-oss:20b", "qwen3.6:27b", "T1", "C1")
    assert THINK["gpt-oss:20b"] == "low"
    model = SETUPS["gpt-oss:20b"].factory()()
    assert model.think == "low"  # type: ignore[attr-defined]
    assert SETUPS["llama3.2"].factory()().think is None  # type: ignore[attr-defined]


def test_a_local_setup_frees_memory_first(no_real_ollama: list[set[str]], tmp_path) -> None:
    setup = SETUPS["mixed"]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(type(setup), "factory", lambda self, temperature=0.5: lambda: Fake("m"))
        args = ["--setup", "mixed", "--seed", "1108", "--k", "1", "--out", str(tmp_path)]
        assert cli.main(args) == 0
    assert no_real_ollama == [{"llama3.2", "llama3.1:8b"}]


def test_the_routed_model_sends_each_task_to_its_own_model() -> None:
    triage, check = Fake("llama3.2"), Fake("llama3.1:8b")
    routed = RoutedModel(triage=triage, check=check)
    routed.seed = 1001
    assert triage.seed == check.seed == 1001 and routed.supports_seed
    routed.complete(ModelRequest(task="triage", system="s", user="u"))
    routed.complete(ModelRequest(task="own_account_check", system="s", user="u"))
    assert triage.tasks == ["triage"] and check.tasks == ["own_account_check"]
    assert routed.model == "llama3.2+llama3.1:8b"
    described = routed.describe()
    assert described["model_digest"] == "llama3.2 (triage), llama3.1:8b (check)"


def test_a_setups_prompts_reach_the_model(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    seen: list[ModelRequest] = []

    class Recording(Fake):
        def complete(self, request: ModelRequest) -> ModelResponse:
            seen.append(request)
            return super().complete(request)

    setup = SETUPS["llama3.2"]
    monkeypatch.setattr(
        type(setup), "factory", lambda self, temperature=0.5: lambda: Recording("llama3.2")
    )
    monkeypatch.setattr("tier0.evals.setups.M3_TRIAGE_VERSIONS", {"T1": "TRIAGE PROMPT UNDER TEST"})
    monkeypatch.setattr("tier0.evals.setups.M3_CHECK_VERSIONS", {"C1": "CHECK PROMPT UNDER TEST"})
    assert (
        cli.main(["--setup", "llama3.2", "--seed", "1108", "--k", "1", "--out", str(tmp_path)]) == 0
    )
    systems = {r.task: r.system for r in seen}
    assert systems == {
        "triage": "TRIAGE PROMPT UNDER TEST",
        "own_account_check": "CHECK PROMPT UNDER TEST",
    }
    report = json.loads((tmp_path / "report.json").read_text())
    import hashlib

    assert report["triage_prompt_sha256"] == hashlib.sha256(b"TRIAGE PROMPT UNDER TEST").hexdigest()
    assert report["check_prompt_sha256"] == hashlib.sha256(b"CHECK PROMPT UNDER TEST").hexdigest()
    assert messages.TRIAGE_SYSTEM != "TRIAGE PROMPT UNDER TEST"  # nothing global changed


def test_real_models_get_one_untimed_warm_up_call_per_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    made: list[Fake] = []

    def make() -> Fake:
        made.append(Fake("llama3.2"))
        return made[-1]

    setup = SETUPS["llama3.2"]
    monkeypatch.setattr(type(setup), "factory", lambda self, temperature=0.5: make)
    args = ["--setup", "llama3.2", "--seed", "1108", "--k", "1", "--out", str(tmp_path)]
    assert cli.main(args) == 0
    assert made[0].tasks == ["triage", "own_account_check"]  # the warm-up model
    assert all(cli.WARM_UP_TEXT in t for t in made[0].texts)
    report = json.loads((tmp_path / "report.json").read_text())
    assert len(report["runs_detail"]) == 6  # the warm-up isn't a run
    made.clear()
    assert cli.main([*args, "--no-warm-up"]) == 0
    assert not any(cli.WARM_UP_TEXT in t for m in made for t in m.texts)


# The frozen prompts, by hash (ADR-030). Changing a prompt or a pick breaks this test on
# purpose: a frozen setup can't change after the dev-set check or v3.
FROZEN_HASHES = {
    "llama3.2": ("T1", "C1", "114d1fc8c4f0dd5a", "eb1037ddaed10c7b"),
    "llama3.1:8b": ("T2", "C2", "472db65fe39577d9", "99302488ff68a5e8"),
    "mixed": ("T1", "C2", "114d1fc8c4f0dd5a", "99302488ff68a5e8"),
    "haiku": ("T1", "C1", "114d1fc8c4f0dd5a", "eb1037ddaed10c7b"),
    "sonnet": ("T1", "C1", "114d1fc8c4f0dd5a", "eb1037ddaed10c7b"),
    "gpt-oss:20b": ("T1", "GC5", "114d1fc8c4f0dd5a", "195d622ab6a65e7d"),
    "qwen3.6:27b": ("T1", "QC5", "114d1fc8c4f0dd5a", "535dcc8fa1dd521f"),
}


def test_the_frozen_setups_keep_their_prompts() -> None:
    import hashlib

    from tier0.evals.setups import FROZEN

    assert set(FROZEN) == set(FROZEN_HASHES)
    for name, (triage, check, t_hash, c_hash) in FROZEN_HASHES.items():
        setup = SETUPS[name]
        assert (setup.triage_prompt, setup.check_prompt) == (triage, check)
        prompts = setup.prompts()
        assert hashlib.sha256(prompts.triage.encode()).hexdigest().startswith(t_hash)
        assert hashlib.sha256(prompts.check.encode()).hexdigest().startswith(c_hash)


@pytest.mark.parametrize("seed", ["2611", "3107", "4417"])
def test_an_unfrozen_setup_never_runs_on_a_held_out_set(
    seed: str, capsys, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "FROZEN", frozenset(SETUPS) - {"gpt-oss:20b"})
    args = ["--setup", "gpt-oss:20b", "--seed", seed, "--k", "1", "--out", str(tmp_path)]
    assert cli.main(args) == 2
    assert "frozen" in capsys.readouterr().err  # v3 is refused even earlier: not all frozen
    assert not (tmp_path / "report.json").exists()


def test_off_format_replies_are_counted_apart_from_wrong_answers(tmp_path) -> None:
    from tier0.agent import check_off_format, triage_off_format

    def reply(text: str, finish: str = "stop") -> ModelResponse:
        return ModelResponse(text, "m", 1, 1, finish, None)

    assert triage_off_format(reply('```json\n{"category": "own_username"}\n```'))
    assert triage_off_format(reply('{"category": "own_username", "tier": 0}'))
    assert triage_off_format(reply('{"category": "password"}'))  # not a known value
    assert not triage_off_format(reply('{"category": "about_someone_else"}'))  # wrong, on-format
    assert not triage_off_format(reply("", "refusal")) and not triage_off_format(None)
    assert check_off_format(reply("yes")) and not check_off_format(
        reply('{"own_account_only": false}')
    )

    class Fenced(Fake):
        def complete(self, request: ModelRequest) -> ModelResponse:
            if request.task == "triage":
                return reply('```json\n{"category": "own_username"}\n```')
            return super().complete(request)

    setup = SETUPS["llama3.2"]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(type(setup), "factory", lambda self, temperature=0.5: lambda: Fenced("m"))
        args = ["--setup", "llama3.2", "--seed", "1108", "--k", "1", "--out", str(tmp_path)]
        cli.main([*args, "--no-warm-up"])
    report = json.loads((tmp_path / "report.json").read_text())
    fo = report["format_outcomes"]
    # Every run that reached triage (an unknown sender never does).
    triaged = [r for r in report["runs_detail"] if r["category"] is not None]
    assert fo["runs_off_format"] == fo["triage_replies"] == len(triaged) > 0
    assert fo["check_replies"] == 0
    assert fo["everyday_failed_off_format"] == fo["everyday_failed_runs"] > 0
    assert fo["everyday_failed_wrong_answer"] == 0
    assert all(r["format_failures"] == ["triage"] for r in triaged)
    md = (tmp_path / "report.md").read_text()
    assert "| Failed everyday runs: wrong answer / off-format reply | 0 / " in md


def test_nothing_runs_on_v3_until_every_setup_is_frozen(
    capsys, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "FROZEN", frozenset(SETUPS) - {"gpt-oss:20b", "qwen3.6:27b"})
    _seal_v3_again(monkeypatch)
    args = ["--setup", "haiku", "--final-comparison=held_out_v3", "--seed", "4417"]
    assert cli.main([*args, "--out", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "until every setup is frozen" in err and "gpt-oss:20b" in err
    assert not (tmp_path / "report.json").exists()


def test_v3_refuses_the_wrong_k_for_a_setup(
    monkeypatch: pytest.MonkeyPatch, capsys, tmp_path
) -> None:
    monkeypatch.setattr(cli, "FROZEN", frozenset(SETUPS))  # pretend all are frozen
    _seal_v3_again(monkeypatch)
    base = ["--final-comparison=held_out_v3", "--seed", "4417", "--out", str(tmp_path)]
    assert cli.main(["--setup", "haiku", "--k", "3", *base]) == 2
    assert "haiku runs at k = 5" in capsys.readouterr().err
    assert cli.main(["--setup", "llama3.2", "--k", "5", *base]) == 2
    assert "llama3.2 runs at k = 3" in capsys.readouterr().err
    assert not (tmp_path / "report.json").exists()


def test_a_k5_run_carries_its_first_3_runs_and_cost_per_1000(tmp_path) -> None:
    setup = SETUPS["llama3.2"]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(type(setup), "factory", lambda self, temperature=0.5: lambda: Fake("m"))
        args = ["--setup", "llama3.2", "--seed", "1108", "--k", "5", "--out", str(tmp_path)]
        cli.main([*args, "--no-warm-up"])
    report = json.loads((tmp_path / "report.json").read_text())
    view = report["at_k3"]
    assert report["k"] == 5 and view["k"] == 3 and "runs_detail" not in view
    assert {t["runs"] for t in view["tickets"]} == {3}
    assert {t["runs"] for t in report["tickets"]} == {5}
    assert report["cost"]["per_1000_tickets_usd"] == 1000 * report["cost"]["per_ticket_usd"]
    md = (tmp_path / "report.md").read_text()
    assert "| pass^3 on runs 1-3 (to compare with k = 3 setups) |" in md
    assert "| API cost per 1,000 tickets |" in md


def _seal_v3_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """v3 is a dev set now; seal it again as a stand-in for the next sealed set (v4)."""
    from tier0 import sealed

    for module in (sealed, cli):
        monkeypatch.setattr(module, "SEALED_POOLS", frozenset({"held_out_v3"}))


def test_a_setup_report_names_its_setup_in_the_rerun_command(tmp_path) -> None:
    """Finding 2: rerunning a report's own command must give the same setup."""
    setup = SETUPS["mixed"]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(type(setup), "factory", lambda self, temperature=0.5: lambda: Fake("m"))
        args = ["--setup", "mixed", "--seed", "1108", "--k", "1", "--out", str(tmp_path)]
        assert cli.main([*args, "--no-warm-up"]) == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["setup"] == "mixed"
    md = (tmp_path / "report.md").read_text()
    assert "Generated by `python -m tier0.evals --setup mixed --seed 1108 --k 1" in md
    assert "--model ollama" not in md.split("Generated by", 1)[1].split("\n", 1)[0]


def test_the_3b_setups_are_frozen_apart_from_the_seven() -> None:
    """Haiku and Sonnet re-tuned on the harder tuning set (ADR-033), frozen 2026-11-15."""
    import hashlib

    from tier0.evals.setups import ALL_SETUPS, SETUPS_3B

    assert set(SETUPS_3B) == {"haiku-3b", "sonnet-3b"} and not set(SETUPS_3B) & set(SETUPS)
    assert set(SETUPS_3B) <= set(ALL_SETUPS)
    frozen = {
        "haiku-3b": ("HT4", "C1", "9e81eed56db1315f", "eb1037ddaed10c7b"),
        "sonnet-3b": ("ST4", "SC4", "2d15e5464070af09", "2fe139fe68417c92"),
    }
    for name, (triage, check, t_hash, c_hash) in frozen.items():
        setup = SETUPS_3B[name]
        assert (setup.triage_prompt, setup.check_prompt) == (triage, check)
        prompts = setup.prompts()
        assert hashlib.sha256(prompts.triage.encode()).hexdigest().startswith(t_hash)
        assert hashlib.sha256(prompts.check.encode()).hexdigest().startswith(c_hash)


def test_the_3d_setups_are_frozen_apart_from_the_others() -> None:
    """Haiku and Sonnet tuned on 3d's tuning messages, frozen 2026-11-15 after v5 was sealed."""
    import hashlib

    from tier0.evals.setups import ALL_SETUPS, SETUPS_3B, SETUPS_3D

    assert set(SETUPS_3D) == {"haiku-3d", "sonnet-3d"}
    assert not set(SETUPS_3D) & (set(SETUPS) | set(SETUPS_3B))
    assert set(ALL_SETUPS) == set(SETUPS) | set(SETUPS_3B) | set(SETUPS_3D)
    frozen = {
        "haiku-3d": ("HT7", "C1", "97de44b6ff29275a", "eb1037ddaed10c7b"),
        "sonnet-3d": ("ST5", "SC4", "05962a83f7071a4c", "2fe139fe68417c92"),
    }
    for name, (triage, check, t_hash, c_hash) in frozen.items():
        setup = SETUPS_3D[name]
        assert (setup.triage_prompt, setup.check_prompt) == (triage, check)
        prompts = setup.prompts()
        assert hashlib.sha256(prompts.triage.encode()).hexdigest().startswith(t_hash)
        assert hashlib.sha256(prompts.check.encode()).hexdigest().startswith(c_hash)


def test_the_before_arm_switch_turns_the_fix_off_for_one_run(tmp_path, monkeypatch) -> None:
    from tier0 import roles

    monkeypatch.setattr(roles, "TKT004_FIX", True)
    assert cli.main(["--k", "1", "--out", str(tmp_path), "--tkt004-fix", "off"]) == 0
    assert roles.TKT004_FIX is False
    assert cli.main(["--k", "1", "--out", str(tmp_path)]) == 0
    assert roles.TKT004_FIX is True
