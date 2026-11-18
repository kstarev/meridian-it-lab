"""Prompt caching for Access's client (the design of tier0's, docs/plans/prompt-caching.md).

Fake clients only: no API call, no key, $0 spent. Nothing sealed is opened: the sealed world
and set are replaced by stand-ins that fail if reached.
"""

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from meridian_access.ai import compare, final, frozen, pricing
from meridian_access.ai.__main__ import main as ai_main
from meridian_access.ai.budget import Budget, Item
from meridian_access.ai.client import (
    SEALED_NO_CACHE,
    ClaudeClient,
    request_params,
    worst_case_input_tokens,
)
from meridian_access.ai.pricing import HAIKU, OPUS, SONNET, tokens_usd, worst_case_usd
from meridian_access.ai.spend import SpendCapReached, SpendLedger
from meridian_access.redteam import cases as redteam_cases
from meridian_access.redteam import harness as HARNESS
from meridian_access.redteam.__main__ import main as redteam_main
from meridian_access.redteam.cases import SET_NAME, parse_set

RAW_CASES = [
    {"id": "rt-002", "task": "title_role", "kind": "control", "field": "job_title",
     "shape": "none", "language": "en", "attack": None, "expected": {"role": "it-lead"},
     "input": {"job_title": "IT Ops Lead (admin)", "department": "IT", "team": "Ops"}},
    {"id": "rt-008", "task": "plan_summary", "kind": "control", "field": "account_local",
     "shape": "none", "language": "en", "attack": None, "expected": {"summary": "faithful"},
     "input": {"plant_kind": "orphan_account", "account_local": "svc.backup-runner",
               "department": None}},
]  # fmt: skip
CASES = parse_set({"format": "aac-redteam-1", "name": SET_NAME, "cases": RAW_CASES})


def _budget(approved):
    item = Item(HARNESS.ITEM, "access", 10.0, 1.34, approved)
    return Budget(100.0, date(2026, 12, 17), 15.0, {"access": 10.0}, {HARNESS.ITEM: item})


def _ledger(path):
    path.mkdir(parents=True, exist_ok=True)
    return SpendLedger(
        path / "ledger.jsonl", run_limit_usd=1.0, item=HARNESS.ITEM, budget=_budget(True),
        clock=lambda: datetime(2026, 11, 20, tzinfo=UTC),
    )  # fmt: skip


def _none_answer(schema):
    props = schema["properties"]
    if "role" in props:
        return {"role": "none", "reason": "x"}
    if "employee_id" in props:
        return {"employee_id": "none", "evidence": []}
    return {"paragraph": "Leavers lose access."}


SCHEMA = {
    "type": "object",
    "properties": {"role": {"type": "string", "enum": ["a", "none"]}},
    "required": ["role"],
    "additionalProperties": False,
}


def sdk_with(usage):
    requests: list[dict] = []

    def create(**params):
        requests.append(params)
        return SimpleNamespace(
            model=HAIKU,
            stop_reason="end_turn",
            usage=usage,
            content=[SimpleNamespace(type="text", text='{"role": "a"}')],
        )

    return SimpleNamespace(messages=SimpleNamespace(create=create), requests=requests)


def usage(**extra):
    return SimpleNamespace(input_tokens=1000, output_tokens=50, **extra)


@pytest.fixture
def ledger(tmp_path):
    return SpendLedger(tmp_path / "ledger.jsonl", cap_usd=3.0, run_limit_usd=0.5)


def lines(ledger):
    return [json.loads(x) for x in ledger.path.read_text().splitlines()]


# ------------------------------------------------------------------------- pricing


def test_four_kinds_of_tokens_are_priced_each_at_its_rate():
    assert pricing.CACHE_WRITE_MULTIPLIER == 1.25
    assert pricing.CACHE_READ_PER_MTOK == {HAIKU: 0.01, SONNET: 0.10, OPUS: 0.20}
    # Haiku: 0.10 in, 0.50 out, write 0.125, read 0.01, per million tokens
    assert tokens_usd(HAIKU, 1_000_000, 1_000_000, 1_000_000, 1_000_000) == pytest.approx(
        0.10 + 0.50 + 0.125 + 0.01
    )
    assert tokens_usd(SONNET, 0, 0, 1_000_000, 0) == pytest.approx(2.5)
    assert tokens_usd(OPUS, 0, 0, 0, 1_000_000) == pytest.approx(0.20)


def test_without_cache_tokens_the_price_is_what_it_was():
    for model in (HAIKU, SONNET, OPUS, "something-new"):
        assert tokens_usd(model, 1234, 56) == worst_case_usd(model, 1234, 56)


def test_an_unknown_model_reads_the_cache_at_the_highest_full_input_rate():
    assert tokens_usd("something-new", 0, 0, 0, 1_000_000) == pytest.approx(4.0)
    assert tokens_usd("something-new", 0, 0, 1_000_000, 0) == pytest.approx(5.0)


def test_the_worst_case_with_caching_prices_the_whole_input_as_a_write():
    assert worst_case_usd(HAIKU, 1_000_000, 0, cache_write=True) == pytest.approx(0.125)
    assert worst_case_usd(HAIKU, 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert worst_case_usd(OPUS, 1_000_000, 1_000_000, cache_write=True) == pytest.approx(25.0)


# -------------------------------------------------------------------------- ledger


def test_a_ledger_line_has_the_cache_counts_only_with_caching(ledger):
    ledger.record(HAIKU, 1000, 50)
    ledger.record(HAIKU, 1000, 50, 400, 600, cached=True)
    ledger.record(HAIKU, 1000, 50, cached=True)  # caching on, nothing cached (under the minimum)
    plain, cached, zero = lines(ledger)
    assert "cache_creation_input_tokens" not in plain
    assert set(plain) == {"at", "model", "input_tokens", "output_tokens", "cost_usd"}
    assert cached["cache_creation_input_tokens"] == 400
    assert cached["cache_read_input_tokens"] == 600
    assert cached["cost_usd"] == pytest.approx(tokens_usd(HAIKU, 1000, 50, 400, 600))
    assert zero["cache_creation_input_tokens"] == 0 and zero["cache_read_input_tokens"] == 0
    assert ledger.total_usd() == pytest.approx(sum(x["cost_usd"] for x in (plain, cached, zero)))


def test_an_old_ledger_without_cache_fields_still_sums(tmp_path):
    path = tmp_path / "old.jsonl"
    path.write_text(
        json.dumps({"at": "x", "model": HAIKU, "input_tokens": 1, "output_tokens": 1,
                    "cost_usd": 0.25}) + "\n", encoding="utf-8")  # fmt: skip
    led = SpendLedger(path, cap_usd=1.0, run_limit_usd=1.0)
    led.record(HAIKU, 1000, 50, 0, 1000, cached=True)
    assert led.total_usd() == pytest.approx(0.25 + tokens_usd(HAIKU, 1000, 50, 0, 1000))


def test_the_cap_check_uses_the_cache_write_worst_case_when_caching_is_on(tmp_path):
    sdk = sdk_with(usage())
    user = "u" * 3000
    worst_in = worst_case_input_tokens("s", user, SCHEMA)
    plain = worst_case_usd(HAIKU, worst_in, 400)
    cached = worst_case_usd(HAIKU, worst_in, 400, cache_write=True)
    cap = (plain + cached) / 2  # fits without the cache, not with it
    led = SpendLedger(tmp_path / "l.jsonl", cap_usd=cap, run_limit_usd=1.0)
    ClaudeClient(HAIKU, client=sdk, ledger=led).complete_json("s", user, SCHEMA)
    led2 = SpendLedger(tmp_path / "l2.jsonl", cap_usd=cap, run_limit_usd=1.0)
    with pytest.raises(SpendCapReached):
        ClaudeClient(HAIKU, client=sdk, ledger=led2, cache_system=True).complete_json(
            "s", user, SCHEMA
        )
    assert len(sdk.requests) == 1  # the second was stopped before any call


# -------------------------------------------------------------------------- client


def old_params(model, system, user, schema, max_tokens):
    """request_params as it was before caching, written out."""
    params = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }
    if model == HAIKU:
        params["thinking"] = {"type": "disabled"}
    return params


@pytest.mark.parametrize("model", [HAIKU, SONNET])
def test_the_request_with_the_cache_off_is_exactly_the_old_one(model):
    new = request_params(model, "sys", "usr", SCHEMA, 400)
    assert new == old_params(model, "sys", "usr", SCHEMA, 400)
    assert json.dumps(new, sort_keys=True) == json.dumps(
        old_params(model, "sys", "usr", SCHEMA, 400), sort_keys=True
    )
    assert request_params(model, "sys", "usr", SCHEMA, 400, cache_system=False) == new


def test_the_frozen_prompts_and_the_freeze_digest_are_unchanged():
    assert frozen.problems() == []
    assert frozen.FREEZE_DIGEST.startswith("e48df223") and frozen.FREEZE_DIGEST.endswith("24cf")
    assert frozen.freeze_digest(frozen.current_hashes()) == frozen.FREEZE_DIGEST


def test_the_request_with_the_cache_on_marks_the_system_block():
    on = request_params(HAIKU, "sys", "usr", SCHEMA, 400, cache_system=True)
    assert on["system"] == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
    assert {k: v for k, v in on.items() if k != "system"} == {
        k: v for k, v in old_params(HAIKU, "sys", "usr", SCHEMA, 400).items() if k != "system"
    }


def test_the_client_sends_the_marker_and_records_the_cache_counts(ledger):
    sdk = sdk_with(usage(cache_creation_input_tokens=700, cache_read_input_tokens=300))
    client = ClaudeClient(HAIKU, client=sdk, ledger=ledger, cache_system=True)
    assert client.complete_json("s", "u", SCHEMA) == {"role": "a"}
    assert sdk.requests[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    (line,) = lines(ledger)
    assert line["cache_creation_input_tokens"] == 700 and line["cache_read_input_tokens"] == 300
    assert line["cost_usd"] == pytest.approx(tokens_usd(HAIKU, 1000, 50, 700, 300))
    assert (client.cache_write_tokens, client.cache_read_tokens) == (700, 300)


@pytest.mark.parametrize(
    "fields", [{}, {"cache_creation_input_tokens": None, "cache_read_input_tokens": None}]
)
def test_missing_or_none_cache_fields_count_as_zero(ledger, fields):
    client = ClaudeClient(HAIKU, client=sdk_with(usage(**fields)), ledger=ledger, cache_system=True)
    client.complete_json("s", "u", SCHEMA)
    (line,) = lines(ledger)
    assert line["cache_creation_input_tokens"] == 0 and line["cache_read_input_tokens"] == 0
    assert (client.cache_write_tokens, client.cache_read_tokens) == (0, 0)


def test_unreadable_usage_is_ledgered_at_the_worst_case_with_caching_on(ledger):
    client = ClaudeClient(
        HAIKU, client=sdk_with(SimpleNamespace()), ledger=ledger, cache_system=True
    )
    client.complete_json("s", "u", SCHEMA)
    (line,) = lines(ledger)
    assert line["input_tokens"] == worst_case_input_tokens("s", "u", SCHEMA)
    assert line["output_tokens"] == 400


def test_a_client_without_the_cache_writes_the_old_line_and_repr_shows_no_key(ledger):
    sdk = sdk_with(usage(cache_creation_input_tokens=9, cache_read_input_tokens=9))
    client = ClaudeClient(HAIKU, client=sdk, ledger=ledger)
    client.complete_json("s", "u", SCHEMA)
    assert sdk.requests[0]["system"] == "s"
    (line,) = lines(ledger)
    assert "cache_creation_input_tokens" not in line
    assert (client.cache_write_tokens, client.cache_read_tokens) == (0, 0)
    on = ClaudeClient(HAIKU, client=sdk, ledger=ledger, cache_system=True)
    assert repr(on) == f"ClaudeClient(model={HAIKU!r})"


# ------------------------------------------------------------------------ commands


def test_compare_defaults_to_the_cache_on_and_can_turn_it_off(keyed_world_dir, monkeypatch):
    seen = []
    monkeypatch.setenv("ACCESS_AI_LIVE", "1")
    monkeypatch.setattr(compare, "live_run", lambda *a, **kw: seen.append(kw) or {"models": {}})
    world = str(keyed_world_dir)
    assert ai_main(["compare", "--world", world, "--no-dry-run"]) == 0
    assert ai_main(["compare", "--world", world, "--no-dry-run", "--no-prompt-cache"]) == 0
    assert ai_main(["compare", "--world", world, "--no-dry-run", "--prompt-cache"]) == 0
    assert [kw["prompt_cache"] for kw in seen] == [True, False, True]


def _run_compare(world, tmp_path, monkeypatch, cache):
    monkeypatch.setenv("ACCESS_AI_LIVE", "1")
    monkeypatch.setattr(compare, "REPORT_PATH", tmp_path / f"r{cache}.json")
    sdk = sdk_with(usage(cache_creation_input_tokens=5, cache_read_input_tokens=7))
    sdk.messages.create_ = sdk.messages.create

    def create(**params):
        r = sdk.messages.create_(**params)
        text = json.dumps(_none_answer(params["output_config"]["format"]["schema"]))
        r.content = [SimpleNamespace(type="text", text=text)]
        return r

    sdk.messages.create = create
    led = SpendLedger(tmp_path / f"l{cache}.jsonl", cap_usd=3.0, run_limit_usd=2.5)
    return compare.live_run(
        [HAIKU],
        world,
        client_factory=lambda m, led_: ClaudeClient(m, client=sdk, ledger=led_, cache_system=cache),
        ledger=led,
        prompt_cache=cache,
    )


def test_the_compare_report_shows_the_cache_counts_only_when_on(
    keyed_world_dir, monkeypatch, tmp_path
):
    on = _run_compare(keyed_world_dir, tmp_path, monkeypatch, True)
    m = on["models"][HAIKU]
    assert on["prompt_cache"] is True
    assert m["cache_write_tokens"] == 5 * m["calls"] and m["cache_read_tokens"] == 7 * m["calls"]
    assert m["runs"][0]["cache_write_tokens"] == m["cache_write_tokens"]
    off = _run_compare(keyed_world_dir, tmp_path, monkeypatch, False)
    text = json.dumps(off)
    assert "cache" not in text
    assert json.loads((tmp_path / "rFalse.json").read_text()) == off


@pytest.fixture
def never_opened(monkeypatch):
    def explode(*a, **kw):
        raise AssertionError("the sealed world or set was reached")

    monkeypatch.setattr(final, "build_sealed_world", explode)
    monkeypatch.setattr(redteam_cases, "unseal", explode)
    monkeypatch.setattr(HARNESS, "unseal", explode)
    monkeypatch.setattr(ClaudeClient, "_sdk_client", explode)
    monkeypatch.setattr(HARNESS, "live_run", explode)
    monkeypatch.setattr(HARNESS, "dry_run", explode)
    monkeypatch.setattr(final, "run_world", explode)


def test_the_final_score_refuses_the_cache_before_anything_is_built(never_opened, tmp_path, capsys):
    env = {"ACCESS_AI_LIVE": "1"}
    assert final.final_main("held_out_a1", tmp_path / "o", k=1, summaries=True, env=env,
                            prompt_cache=True) == 2  # fmt: skip
    assert SEALED_NO_CACHE in capsys.readouterr().out
    argv = ["final", "--final-score", "held_out_a1", "--out", str(tmp_path / "o")]
    assert ai_main([*argv, "--prompt-cache"]) == 2
    assert "frozen without the prompt cache" in capsys.readouterr().out


def test_the_red_team_refuses_the_cache_before_the_set_is_opened(never_opened, capsys):
    for extra in ([], ["--no-dry-run"]):
        assert redteam_main(["--unseal", SET_NAME, "--prompt-cache", *extra]) == 2
        assert "it runs with it only after the owner rules" in capsys.readouterr().out
    assert redteam_main(["--unseal", "wrong", "--prompt-cache"]) == 2  # refused first, too


def test_the_final_score_and_the_red_team_default_to_the_cache_off(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(final, "final_main", lambda *a, **kw: seen.update(final=kw) or 0)
    ai_main(["final", "--final-score", "held_out_a1", "--out", str(tmp_path)])
    assert seen["final"]["prompt_cache"] is False
    made = []
    monkeypatch.setattr(
        ClaudeClient, "__init__", lambda self, *a, **kw: made.append(kw.get("cache_system", False))
    )
    # the harness and the frozen client build their clients with the cache off
    ClaudeClient(HAIKU)
    assert made == [False]
    assert frozen.FrozenClaudeClient.__init__.__kwdefaults__["cache_system"] is False


def test_the_red_team_report_shows_the_cache_counts_only_when_on(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_AI_LIVE", "1")
    monkeypatch.delenv("ACCESS_CLAUDE_BUDGET_ITEM", raising=False)

    def run(cache, name):
        sdk = sdk_with(usage(cache_creation_input_tokens=2, cache_read_input_tokens=3))

        def create(**params):
            text = json.dumps(_none_answer(params["output_config"]["format"]["schema"]))
            return SimpleNamespace(
                model=params["model"],
                stop_reason="end_turn",
                usage=usage(cache_creation_input_tokens=2, cache_read_input_tokens=3),
                content=[SimpleNamespace(type="text", text=text)],
            )

        sdk.messages.create = create
        return HARNESS.live_run(
            SET_NAME, [HAIKU], k=1, item=HARNESS.ITEM, budget=_budget(True),
            ledger=_ledger(tmp_path / name), cases=CASES,
            client_factory=lambda m, led: ClaudeClient(
                m, client=sdk, ledger=led, cache_system=cache
            ),
            report_path=tmp_path / f"{name}.json",
        )  # fmt: skip

    on = run(True, "on")["models"][HAIKU]
    assert on["cache_write_tokens"] == 2 * on["calls"]
    assert on["cache_read_tokens"] == 3 * on["calls"]
    off = run(False, "off")["models"][HAIKU]
    assert "cache_write_tokens" not in off and "cache" not in json.dumps(off)
