"""The AI suggestion code, with a fake client only: no API call, no key, $0 spent."""

import json
from types import SimpleNamespace

import pytest

from meridian_access.ai import compare, maps
from meridian_access.ai.__main__ import main as ai_main
from meridian_access.ai.client import ClaudeClient, MissingKeyError
from meridian_access.ai.matching import (
    MAX_CANDIDATES,
    MatchSuggestion,
    account_payload,
    shortlist,
    suggest_owner,
)
from meridian_access.ai.pricing import HAIKU, OPUS, SONNET, model_id, worst_case_usd
from meridian_access.ai.spend import SpendCapReached, SpendLedger
from meridian_access.ai.titles import RoleSuggestion, suggest_role
from meridian_access.desired import desired_state
from meridian_access.models import Account
from meridian_dataset.access.model import HrisRecord


class FakeSDK:
    """Stands in for anthropic.Anthropic; records every request, never touches a network."""

    def __init__(self, text=None, stop_reason="end_turn", raises=None, model=HAIKU, answer=None):
        self.requests: list[dict] = []
        self._text, self._stop, self._raises, self._model, self._answer = (
            text,
            stop_reason,
            raises,
            model,
            answer,
        )
        self.messages = SimpleNamespace(create=self._create)

    @property
    def calls(self) -> int:
        return len(self.requests)

    def _create(self, **params):
        self.requests.append(params)
        if self._raises:
            raise self._raises
        text = self._text
        if text is None:
            schema = params["output_config"]["format"]["schema"]
            text = json.dumps(self._answer(schema) if self._answer else _first_answer(schema))
        return SimpleNamespace(
            model=self._model,
            stop_reason=self._stop,
            usage=SimpleNamespace(input_tokens=1000, output_tokens=50),
            content=[SimpleNamespace(type="text", text=text)],
            id="msg_fake",
        )


def _first_answer(schema):
    out = {}
    for name, prop in schema["properties"].items():
        if prop["type"] == "array":
            out[name] = []
        elif "enum" in prop:
            out[name] = prop["enum"][0]
        else:
            out[name] = "because"
    return out


@pytest.fixture
def ledger(tmp_path):
    return SpendLedger(tmp_path / "ledger.jsonl", cap_usd=3.0, run_limit_usd=0.5)


def client_for(sdk, ledger, model=HAIKU):
    return ClaudeClient(model, client=sdk, ledger=ledger)


SCHEMA = {
    "type": "object",
    "properties": {"role": {"type": "string", "enum": ["a", "none"]}},
    "required": ["role"],
    "additionalProperties": False,
}

# ------------------------------------------------------------------------- pricing


def test_prices_match_the_tier0_table():
    assert model_id("haiku") == HAIKU and model_id("opus") == OPUS and model_id(SONNET) == SONNET
    assert worst_case_usd(HAIKU, 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert worst_case_usd(SONNET, 1_000_000, 1_000_000) == pytest.approx(12.0)
    assert worst_case_usd(OPUS, 1_000_000, 1_000_000) == pytest.approx(24.0)
    assert worst_case_usd("something-new", 1_000_000, 0) == 4.0  # highest known rate
    with pytest.raises(ValueError):
        model_id("gpt")


# ---------------------------------------------------------------------------- spend


def test_ledger_defaults_and_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("ACCESS_CLAUDE_SPEND_CAP_USD", raising=False)
    monkeypatch.delenv("ACCESS_CLAUDE_RUN_LIMIT_USD", raising=False)
    monkeypatch.delenv("ACCESS_CLAUDE_SPEND_LEDGER", raising=False)
    default = SpendLedger()
    assert default.cap_usd == 3.0 and default.run_limit_usd == 0.5
    assert default.path.parts[-2:] == ("out", "access-claude-spend.jsonl")
    monkeypatch.setenv("ACCESS_CLAUDE_SPEND_CAP_USD", "1.25")
    monkeypatch.setenv("ACCESS_CLAUDE_RUN_LIMIT_USD", "0.1")
    monkeypatch.setenv("ACCESS_CLAUDE_SPEND_LEDGER", str(tmp_path / "x.jsonl"))
    custom = SpendLedger()
    assert (custom.cap_usd, custom.run_limit_usd, custom.path) == (1.25, 0.1, tmp_path / "x.jsonl")


def test_a_call_that_would_cross_the_cap_is_not_made(tmp_path):
    ledger = SpendLedger(tmp_path / "l.jsonl", cap_usd=0.0001, run_limit_usd=10)
    sdk = FakeSDK()
    with pytest.raises(SpendCapReached):
        client_for(sdk, ledger, OPUS).complete_json("sys", "user", SCHEMA)
    assert sdk.calls == 0 and not (tmp_path / "l.jsonl").exists()


def test_the_cap_holds_across_runs_because_the_ledger_is_a_file(tmp_path):
    path = tmp_path / "l.jsonl"
    for _ in range(3):
        SpendLedger(path, cap_usd=3.0).record(OPUS, 200_000, 10_000)  # $1.00 each, three runs
    sdk = FakeSDK()
    with pytest.raises(SpendCapReached):
        client_for(sdk, SpendLedger(path, cap_usd=3.0, run_limit_usd=5)).complete_json(
            "s", "u", SCHEMA
        )
    assert sdk.calls == 0


def test_the_per_run_limit_stops_a_run_but_not_the_next(tmp_path):
    path = tmp_path / "l.jsonl"
    run = SpendLedger(path, cap_usd=3.0, run_limit_usd=0.012)
    sdk = FakeSDK(model=SONNET)
    c = client_for(sdk, run, SONNET)
    calls = 0
    with pytest.raises(SpendCapReached):
        for _ in range(100):
            c.complete_json("s", "u", SCHEMA)
            calls += 1
    assert calls == 3  # the fourth could cross the run limit
    fresh = SpendLedger(path, cap_usd=3.0, run_limit_usd=0.5)
    client_for(sdk, fresh, SONNET).complete_json("s", "u", SCHEMA)  # a new run is fine


@pytest.mark.parametrize("bad", [0, -1.0, float("nan"), float("inf")])
def test_the_ledger_rejects_a_cap_or_limit_that_is_not_a_positive_number(tmp_path, bad):
    with pytest.raises(ValueError):
        SpendLedger(tmp_path / "l.jsonl", cap_usd=bad)
    with pytest.raises(ValueError):
        SpendLedger(tmp_path / "l.jsonl", run_limit_usd=bad)


@pytest.mark.parametrize("bad", ["0", "-3", "nan", "inf"])
def test_the_ledger_rejects_a_bad_environment_value(monkeypatch, tmp_path, bad):
    monkeypatch.setenv("ACCESS_CLAUDE_SPEND_CAP_USD", bad)
    with pytest.raises(ValueError):
        SpendLedger(tmp_path / "l.jsonl")


def test_a_failed_request_is_ledgered_at_the_worst_case(ledger):
    sdk = FakeSDK(raises=RuntimeError("timeout after the request was sent"))
    assert client_for(sdk, ledger).complete_json("s", "u", SCHEMA) is None
    (line,) = [json.loads(x) for x in ledger.path.read_text().splitlines()]
    assert line["output_tokens"] == 400 and line["input_tokens"] > 512 and line["cost_usd"] > 0


def test_unreadable_usage_is_ledgered_at_the_worst_case(ledger):
    sdk = FakeSDK(text='{"role": "a"}')
    real = sdk._create

    def no_usage(**params):
        r = real(**params)
        r.usage = None
        return r

    sdk.messages = SimpleNamespace(create=no_usage)
    assert client_for(sdk, ledger).complete_json("s", "u", SCHEMA) == {"role": "a"}
    (line,) = [json.loads(x) for x in ledger.path.read_text().splitlines()]
    assert line["output_tokens"] == 400 and line["input_tokens"] > 512


def test_the_real_client_has_no_retries_and_a_timeout(monkeypatch, ledger):
    import anthropic

    seen = {}

    def fake_ctor(**kwargs):
        seen.update(kwargs)
        return FakeSDK(text='{"role": "a"}')

    monkeypatch.setattr(anthropic, "Anthropic", fake_ctor)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    ClaudeClient(HAIKU, ledger=ledger).complete_json("s", "u", SCHEMA)
    assert seen["max_retries"] == 0 and seen["timeout"] == 60


def test_the_real_sdk_is_blocked_in_tests_and_no_key_is_set():
    import os

    import anthropic

    assert "ANTHROPIC_API_KEY" not in os.environ
    with pytest.raises(AssertionError):
        anthropic.Anthropic(api_key="x")


def test_the_actual_cost_is_recorded_after_a_call(ledger):
    client_for(FakeSDK(text='{"role": "a"}'), ledger).complete_json("s", "u", SCHEMA)
    line = json.loads(ledger.path.read_text().splitlines()[0])
    assert line["model"] == HAIKU and line["input_tokens"] == 1000
    assert line["cost_usd"] == pytest.approx(1000 * 0.10 / 1e6 + 50 * 0.50 / 1e6)
    assert set(line) == {"at", "model", "input_tokens", "output_tokens", "cost_usd"}  # no text


# ---------------------------------------------------------------------------- client


def test_a_good_answer_and_the_request_shape(ledger):
    sdk = FakeSDK(text='{"role": "a"}')
    assert client_for(sdk, ledger).complete_json("sys", "user", SCHEMA) == {"role": "a"}
    (req,) = sdk.requests
    assert req["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}
    assert req["thinking"] == {"type": "disabled"}  # Haiku
    assert "temperature" not in req and "prefill" not in req
    assert req["messages"] == [{"role": "user", "content": "user"}]  # no assistant turn


def test_only_haiku_turns_thinking_off(ledger):
    sdk = FakeSDK(text='{"role": "a"}', model=SONNET)
    client_for(sdk, ledger, SONNET).complete_json("s", "u", SCHEMA)
    assert "thinking" not in sdk.requests[0]


@pytest.mark.parametrize(
    "sdk",
    [
        FakeSDK(text='{"role": "a"}', stop_reason="refusal"),
        FakeSDK(text='{"role": "a"}', stop_reason="max_tokens"),
        FakeSDK(text='{"role": "a'),  # malformed JSON
        FakeSDK(text="not json at all"),
        FakeSDK(text='["a"]'),  # not an object
        FakeSDK(text='{"role": "zzz"}'),  # outside the enum
        FakeSDK(text='{"other": 1}'),  # missing key
        FakeSDK(text='{"role": "a", "extra": 1}'),  # extra key
        FakeSDK(raises=RuntimeError("boom")),
    ],
    ids=[
        "refusal",
        "max_tokens",
        "malformed",
        "not-json",
        "list",
        "enum",
        "missing",
        "extra",
        "api-error",
    ],
)
def test_every_failure_is_no_suggestion(sdk, ledger):
    assert client_for(sdk, ledger).complete_json("s", "u", SCHEMA) is None
    assert sdk.calls == 1


def test_the_key_comes_from_the_environment_and_is_never_shown(monkeypatch, ledger):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(MissingKeyError):
        ClaudeClient(HAIKU, ledger=ledger).complete_json("s", "u", SCHEMA)  # no client, no key
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key-for-the-repr-test")
    c = client_for(FakeSDK(), ledger)
    assert "not-a-real-key" not in repr(c) and repr(c) == f"ClaudeClient(model={HAIKU!r})"


def test_the_sdk_is_not_imported_when_a_client_is_given(monkeypatch, ledger):
    import anthropic

    def boom(*a, **k):
        raise AssertionError("a real client must never be built in tests")

    monkeypatch.setattr(anthropic, "Anthropic", boom)
    assert client_for(FakeSDK(text='{"role": "a"}'), ledger).complete_json("s", "u", SCHEMA)


# ------------------------------------------------------------------ the two suggesters


def test_suggest_role_sends_only_title_department_and_team(ledger):
    sdk = FakeSDK(text='{"role": "sales-ae", "reason": "AE means account executive"}')
    s = suggest_role(
        "Sr. Acct Exec, EMEA", "Sales", "EMEA", ["sales-ae", "eng-sre"], client_for(sdk, ledger)
    )
    assert s == RoleSuggestion("sales-ae", "AE means account executive", HAIKU)
    req = sdk.requests[0]
    assert req["output_config"]["format"]["schema"]["properties"]["role"]["enum"] == [
        "eng-sre",
        "sales-ae",
        "none",
    ]
    assert req["messages"][0]["content"] == (
        "Job title: Sr. Acct Exec, EMEA\nDepartment: Sales\nTeam: EMEA\nRoles: eng-sre, sales-ae"
    )
    assert (
        suggest_role("t", "d", "t", ["a"], client_for(FakeSDK(stop_reason="refusal"), ledger))
        is None
    )


def _record(i, given="Ann", family="Lee"):
    return HrisRecord(
        f"E{i:03d}",
        given,
        family,
        "",
        f"{given.lower()}.{family.lower()}{i}@meridian.example",
        f"SECRET.PERSONAL{i}@mail.example",
        "Engineer",
        "Engineering",
        "Core",
        "",
        "DE",
        "x",
        "employee",
        "active",
        "2024-05-06",
        "",
        "",
        "",
    )


def test_suggest_owner_sends_names_and_candidates_only(ledger):
    people = [_record(i, family=f"Name{chr(97 + i % 20)}") for i in range(40)]
    account = Account(
        "app-one",
        "alee",
        given="Ann",
        family="Lee",
        app_id="app-one",
        created="2026-11-03",
        last_login="2026-11-20",
        email="private@mail.example",
    )
    cands = shortlist(account, people)
    assert len(cands) == MAX_CANDIDATES == 20
    sdk = FakeSDK(
        answer=lambda schema: {"employee_id": cands[0].employee_id, "evidence": ["family_name"]}
    )
    s = suggest_owner(account, people, client_for(sdk, ledger))  # more than 20 are cut to 20
    assert s == MatchSuggestion(cands[0].employee_id, ("family_name",), HAIKU)
    sent = json.dumps(sdk.requests[0]["messages"]) + json.dumps(sdk.requests[0]["system"])
    assert "SECRET.PERSONAL" not in sent and "mail.example" not in sent
    assert "2026-11" not in sent and "2024-05-06" not in sent  # no dates
    body = json.loads(sdk.requests[0]["messages"][0]["content"])
    assert len(body["candidates"]) == 20
    assert set(body["candidates"][0]) == {
        "employee_id",
        "given_name",
        "family_name",
        "preferred_name",
        "email_local",
        "department",
    }
    assert set(body["account"]) == {"given_name", "family_name", "display_name", "username"}


def test_a_work_address_account_sends_only_its_local_part():
    a = Account("okta", "ann.lee@meridian.example", email="ann.lee@meridian.example")
    assert account_payload(a)["username"] == "ann.lee"
    other = Account("okta", "ann@mail.example", email="ann@mail.example")
    assert account_payload(other)["username"] == ""


def test_suggest_owner_rejects_an_id_outside_the_candidates_and_allows_none(ledger):
    people = [_record(1), _record(2, "Bo", "Li")]
    account = Account("app-one", "x", app_id="app-one", given="Bo")
    bad = FakeSDK(text='{"employee_id": "E999", "evidence": []}')
    assert suggest_owner(account, people, client_for(bad, ledger)) is None
    none = FakeSDK(text='{"employee_id": "none", "evidence": ["given_name"]}')
    assert suggest_owner(account, people, client_for(none, ledger)).employee_id == "none"
    assert suggest_owner(account, [], client_for(none, ledger)) is None


# ----------------------------------------------------------------------------- maps


def test_suggestions_are_written_unreviewed_and_never_overwrite(tmp_path):
    path = tmp_path / "suggested_maps.yaml"
    e1 = maps.title_entry("Sr. Acct Exec", RoleSuggestion("sales-ae", "r", SONNET))
    e2 = maps.account_entry(
        "app-one", "app-one", "alee", MatchSuggestion("E1", ("family_name",), SONNET)
    )
    assert e1 == {
        "kind": "title",
        "key": "Sr. Acct Exec",
        "value": "sales-ae",
        "source": "ai",
        "model": SONNET,
        "reviewed": False,
    }
    assert e2["kind"] == "account" and e2["reviewed"] is False
    assert maps.write_entries([e1, e2], path) == 2
    assert maps.write_entries([e1], path) == 0
    assert [e["reviewed"] for e in maps.load_entries(path)] == [False, False]
    assert maps.reviewed_only(maps.load_entries(path)) == []
    assert maps.title_entry("t", RoleSuggestion("none", "r", HAIKU)) is None
    assert maps.account_entry("s", "a", "x", MatchSuggestion("none", (), HAIKU)) is None
    assert maps.reviewed_only([{"kind": "title", "key": "k", "reviewed": True}])


def test_the_shipped_suggestion_file_is_empty():
    assert maps.load_entries() == []


def test_no_unreviewed_suggestion_reaches_desired_state(world, policy, tmp_path, monkeypatch):
    import dataclasses

    odd = dataclasses.replace(world.hris.records[0], job_title="Sr. Quantum Wrangler")
    records = (odd, *world.hris.records[1:])
    before = desired_state(records, world.hris.events, policy)
    assert "Sr. Quantum Wrangler" in before.unmapped_titles
    path = tmp_path / "suggested_maps.yaml"
    maps.write_entries(
        [maps.title_entry("Sr. Quantum Wrangler", RoleSuggestion("sales-ae", "r", SONNET))], path
    )
    monkeypatch.setattr(maps, "default_path", lambda: path)
    after = desired_state(records, world.hris.events, policy)
    assert after.digest() == before.digest()
    person = next(p for p in after.people if p.employee_id == odd.employee_id)
    assert person.role is None and person.unmapped_title


def test_desired_matching_and_collecting_never_import_the_ai_code():
    from pathlib import Path

    import meridian_access.collect as collect
    import meridian_access.desired as desired
    import meridian_access.match as match

    for module in (desired, match, collect):
        text = Path(module.__file__).read_text()
        assert "meridian_access.ai" not in text and "suggested_maps" not in text, module.__name__


# ---------------------------------------------------------------------------- compare


def _explode(*a, **k):
    raise AssertionError("no API call may be made in a dry run")


def test_the_dry_run_prices_the_comparison_and_makes_no_calls(world_dir, monkeypatch, capsys):
    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", _explode)
    monkeypatch.setattr(ClaudeClient, "complete_json", _explode)
    monkeypatch.setattr(ClaudeClient, "_sdk_client", _explode)
    for argv in (
        ["compare", "--models", "haiku,sonnet,opus", "--world", str(world_dir), "--dry-run"],
        ["compare", "--models", "haiku,sonnet,opus", "--world", str(world_dir)],  # the default
    ):
        assert ai_main(argv) == 0
        out = capsys.readouterr().out
        assert "no calls made" in out and "worst case" in out and "calls per model" in out
    plan = compare.dry_run([HAIKU, SONNET, OPUS], world_dir)
    assert plan["calls_made"] == 0 and plan["title_calls"] > 10 and plan["owner_calls"] >= 1
    cost = plan["worst_case_usd"]
    assert cost[HAIKU] < cost[SONNET] < cost[OPUS] and plan["worst_case_total_usd"] < 3.0


def test_a_live_run_is_refused_without_the_switch(world_dir, monkeypatch, capsys):
    monkeypatch.delenv("ACCESS_AI_LIVE", raising=False)
    monkeypatch.setattr(ClaudeClient, "_sdk_client", _explode)
    assert ai_main(["compare", "--world", str(world_dir), "--no-dry-run"]) == 2
    with pytest.raises(RuntimeError):
        compare.live_run([HAIKU], world_dir)


def test_a_live_run_end_to_end_with_a_fake_client(keyed_world_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("ACCESS_AI_LIVE", "1")
    monkeypatch.setattr(compare, "REPORT_PATH", tmp_path / "report.json")
    ledger = SpendLedger(tmp_path / "l.jsonl", cap_usd=3.0, run_limit_usd=2.5)
    sdk = FakeSDK(model=SONNET)
    report = compare.live_run(
        [SONNET],
        keyed_world_dir,
        client_factory=lambda m, led: ClaudeClient(m, client=sdk, ledger=led),
        ledger=ledger,
        suggestions_path=tmp_path / "suggested.yaml",
    )
    m = report["models"][SONNET]
    assert m["calls"] == sdk.calls > 10 and m["stopped"] == "" and m["cost_usd"] > 0
    assert m["matching_rules_plus_ai"][0] <= 1.0
    assert json.loads((tmp_path / "report.json").read_text()) == report
    assert all(e["reviewed"] is False for e in maps.load_entries(tmp_path / "suggested.yaml"))


def test_a_live_run_stops_cleanly_at_the_cap(keyed_world_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("ACCESS_AI_LIVE", "1")
    monkeypatch.setattr(compare, "REPORT_PATH", tmp_path / "report.json")
    ledger = SpendLedger(tmp_path / "l.jsonl", cap_usd=0.0001, run_limit_usd=2.5)
    sdk = FakeSDK(model=OPUS)
    report = compare.live_run(
        [OPUS],
        keyed_world_dir,
        client_factory=lambda m, led: ClaudeClient(m, client=sdk, ledger=led),
        ledger=ledger,
    )
    assert sdk.calls == 0 and "spend cap" in report["models"][OPUS]["stopped"]


def test_a_live_run_refuses_a_world_that_is_not_the_tuning_seed(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_AI_LIVE", "1")
    other = tmp_path / "w"
    other.mkdir()
    (other / "hris.json").write_text(json.dumps({"seed": 4242, "records": [], "events": []}))
    sdk = FakeSDK()
    with pytest.raises(RuntimeError, match="tuning"):
        compare.live_run(
            [HAIKU],
            other,
            client_factory=lambda m, led: ClaudeClient(m, client=sdk, ledger=led),
            ledger=SpendLedger(tmp_path / "l.jsonl", cap_usd=3.0, run_limit_usd=2.5),
        )
    assert sdk.calls == 0


def test_shortlists_use_only_current_records(world_dir, policy):
    from meridian_access.desired import desired_state, load_hris

    work = compare.build_workload(world_dir, policy)
    records, events = load_hris(world_dir / "hris.json")
    superseded = {
        p.employee_id
        for p in desired_state(records, events, policy).people
        if p.category == "superseded"
    }
    assert superseded and not superseded & {r.employee_id for r in work.records}
    for a in work.leftovers:
        assert not superseded & {r.employee_id for r in shortlist(a, work.records)}


def test_addresses_in_names_are_not_sent():
    a = Account(
        "app-one",
        "alee",
        app_id="app-one",
        given="Ann",
        family="Lee",
        display="Ann Lee ann.private@mail.example <@ann>",
    )
    payload = account_payload(a)
    assert payload["display_name"] == "Ann Lee"
    assert "@" not in json.dumps(payload)


def test_the_cli_writes_suggestions_next_to_the_report(keyed_world_dir, monkeypatch, tmp_path):
    seen = {}

    def fake_live(models, world, **kw):
        seen.update(kw)
        return {"models": {}}

    monkeypatch.setenv("ACCESS_AI_LIVE", "1")
    monkeypatch.setattr(compare, "live_run", fake_live)
    assert ai_main(["compare", "--world", str(keyed_world_dir), "--no-dry-run"]) == 0
    assert seen["suggestions_path"].parts[-2:] == ("out", "access-ai-suggestions.yaml")
