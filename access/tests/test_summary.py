"""The plan summary: template, model paragraph (fake client only) and the code check."""

import json
from types import SimpleNamespace

import pytest

from meridian_access.ai.client import ClaudeClient
from meridian_access.ai.pricing import SONNET
from meridian_access.ai.spend import SpendLedger
from meridian_access.plan import build_plan
from meridian_access.summary import (
    MAX_LINES,
    check_summary,
    choose_summary,
    fill_summary,
    model_prompt,
    model_summary,
    summary_markdown,
    template_summary,
)


@pytest.fixture(scope="module")
def plan(world_dir, policy):
    return build_plan(world_dir, policy)


class FakeSDK:
    def __init__(self, paragraph=None, text=None, stop_reason="end_turn"):
        self.requests: list[dict] = []
        self.text = text if text is not None else json.dumps({"paragraph": paragraph})
        self.stop_reason = stop_reason
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **params):
        self.requests.append(params)
        return SimpleNamespace(
            model=SONNET,
            stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=2000, output_tokens=100),
            content=[SimpleNamespace(type="text", text=self.text)],
        )


def client(sdk, tmp_path, cap=3.0):
    ledger = SpendLedger(tmp_path / "ledger.jsonl", cap_usd=cap, run_limit_usd=cap)
    return ClaudeClient(SONNET, client=sdk, ledger=ledger)


def a_leaver_line(plan):
    c = next(c for c in plan.changes if c["type"] == "leaver_access_left" and c["app_id"])
    return c["account"].split("@")[0], c["app_name"]


def good_paragraph(plan):
    who, app = a_leaver_line(plan)
    return (
        "The plan has {total} changes and needs {approvals} approvals, with {held} held back. "
        f"For example {who} loses access to {app}. There are {{count:leaver_access_left}} "
        "leavers' accesses to remove and {count:orphaned_account} orphaned accounts."
    )


def test_the_template_is_deterministic_and_has_the_plan_numbers(plan):
    text = template_summary(plan)
    assert text == template_summary(plan)
    assert str(plan.counts["total"]) in text and plan.data["as_of"] in text


def test_a_good_paragraph_passes_and_its_placeholders_are_filled_from_the_plan(plan):
    filled = fill_summary(good_paragraph(plan), plan)
    assert filled is not None and check_summary(good_paragraph(plan), plan)
    counts = plan.counts
    assert f"The plan has {counts['total']} changes" in filled
    assert f"{counts['by_type']['leaver_access_left']} leavers' accesses" in filled
    assert f"{counts['by_type']['orphaned_account']} orphaned accounts" in filled
    assert "{" not in filled


@pytest.mark.parametrize(
    "text",
    [
        "The plan has 465 changes.",  # a raw digit, even a right one
        "The plan has {total} changes in a 30-day window.",  # "30-day"
        "The plan has forty-two changes.",  # spelled-out numbers
        "The plan has Seven changes.",
        "It needs a dozen approvals.",
        "There are twenty one leavers.",
        "The plan has {total} changes. Jsmith loses access.",  # a capitalised unknown word
        "The plan has {total} changes. Zorblax loses access.",
        "The plan has {total} changes. jsmith loses access.",  # a login that is not in the plan
        "The plan has {total} changes. Salesforce is affected.",  # an app that is not in it
        "The plan has {total} changes. salesforce is affected.",  # lower case
        "The plan has {total} changes and {bogus} more.",  # a placeholder not in the list
        "The plan has {count:no_such_type} changes.",
        "The plan has {total changes.",  # a stray brace
        "It runs on December {total}.",  # a month name is not an allowed word
        "Fixed on 2026-12-01.",  # a date is digits
        "zed.nobody@meridian.example loses access.",
        "x " * 700,  # too long after substitution
        "",
    ],
)
def test_a_paragraph_that_breaks_a_rule_fails(plan, text):
    assert not check_summary(text, plan)
    assert fill_summary(text, plan) is None


def test_a_name_or_app_from_the_plan_is_allowed_and_one_outside_it_is_not(plan):
    who, app = a_leaver_line(plan)
    assert check_summary(f"{who.upper()} loses {app.lower()}.", plan)
    assert not check_summary(f"{who}.zed loses {app}.", plan)


def test_a_wrong_label_cannot_be_written_because_a_number_is_always_a_placeholder(plan):
    text = "There are {count:drift} orphaned accounts."
    filled = fill_summary(text, plan)
    assert filled == f"There are {plan.counts['by_type']['drift']} orphaned accounts."


def test_the_length_limit_is_on_the_filled_paragraph(plan):
    assert fill_summary("plan " * 238, plan) is not None  # 1190 characters
    assert fill_summary("plan " * 241, plan) is None  # 1205 characters


def test_the_model_summary_is_used_when_it_passes_and_is_marked(plan, tmp_path):
    sdk = FakeSDK(good_paragraph(plan))
    paragraph, by_model = choose_summary(plan, client(sdk, tmp_path))
    assert by_model and paragraph == fill_summary(good_paragraph(plan), plan)
    assert "written by the model, checked" in summary_markdown(plan, client(sdk, tmp_path))
    schema = sdk.requests[0]["output_config"]["format"]["schema"]
    assert schema["required"] == ["paragraph"]


def test_a_failed_check_or_no_answer_gives_the_template(plan, tmp_path):
    template = template_summary(plan)
    bad = [
        FakeSDK(f"The plan has {plan.counts['total']} changes."),  # a raw number
        FakeSDK("Zoe Quill lost access to Zapier Pro."),
        FakeSDK(text="not json"),
        FakeSDK(stop_reason="refusal", paragraph="x"),
    ]
    for sdk in bad:
        paragraph, by_model = choose_summary(plan, client(sdk, tmp_path))
        assert (paragraph, by_model) == (template, False)
        assert "_Summary source: template._" in summary_markdown(plan, client(sdk, tmp_path))
    assert choose_summary(plan, None) == (template, False)


def test_the_cap_falls_back_to_the_template_and_sends_nothing(plan, tmp_path):
    sdk = FakeSDK(good_paragraph(plan))
    paragraph, by_model = choose_summary(plan, client(sdk, tmp_path, cap=0.000001))
    assert (paragraph, by_model) == (template_summary(plan), False)
    assert sdk.requests == []


def test_a_model_call_goes_through_the_ledger(plan, tmp_path):
    sdk = FakeSDK(good_paragraph(plan))
    c = client(sdk, tmp_path)
    assert model_summary(plan, c) == good_paragraph(plan).strip()
    assert c.ledger.total_usd() > 0


def test_the_model_sees_only_counts_and_at_most_30_short_lines(plan):
    prompt = model_prompt(plan)
    assert len(prompt.splitlines()) <= 7 + len(plan.counts["by_type"]) + MAX_LINES
    assert "@" not in prompt  # local parts only, never a full address
    assert "E1" not in prompt.split("Change lines", 1)[0]
    assert "actual_state_sha256" not in prompt


def test_any_error_on_the_model_path_gives_the_template(plan):
    class Broken:
        def complete_json(self, *args, **kwargs):
            raise ValueError("the SDK blew up")

    assert choose_summary(plan, Broken()) == (template_summary(plan), False)
    assert "_Summary source: template._" in summary_markdown(plan, Broken())


def test_the_prompt_asks_for_placeholders_and_gives_no_numbers_to_copy(plan):
    prompt = model_prompt(plan)
    assert "{total}" in prompt and "{count:leaver_access_left}" in prompt
    head = prompt.split("Change lines", 1)[0]
    assert not any(ch.isdigit() for ch in head)
