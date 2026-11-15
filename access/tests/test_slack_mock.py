"""The mock Slack message: Block Kit shape, only numbers and names from the plan, no network."""

import ast
import json
import re
from pathlib import Path

import pytest

from meridian_access import slack_mock
from meridian_access.plan import build_plan

SRC = Path(slack_mock.__file__)
NETWORK = {"urllib", "http", "socket", "requests", "httpx", "slack_sdk", "ssl", "aiohttp"}


@pytest.fixture(scope="module")
def plan(world_dir, policy):
    return build_plan(world_dir, policy)


@pytest.fixture(scope="module")
def message(plan):
    return slack_mock.build_message(plan, "https://example.com/pull/PR")


def test_the_module_imports_no_network_library():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & NETWORK, imported & NETWORK


def test_the_message_is_valid_block_kit(message, plan):
    blocks = message["blocks"]
    assert isinstance(blocks, list) and 0 < len(blocks) <= 50
    assert message["text"]  # the fallback for notifications
    header = blocks[0]
    assert header["type"] == "header" and header["text"]["type"] == "plain_text"
    assert header["text"]["text"] == f"Access plan for {plan.data['as_of']}"
    assert len(header["text"]["text"]) <= 150
    sections = [b for b in blocks if b["type"] == "section"]
    assert len(sections) >= 2
    for s in sections:
        assert s["text"]["type"] == "mrkdwn" and 0 < len(s["text"]["text"]) <= 3000
    assert blocks[-1]["type"] == "context"
    assert "https://example.com/pull/PR" in blocks[-1]["elements"][0]["text"]


def test_every_number_in_the_message_is_in_the_plan(message, plan):
    text = json.dumps(message).replace("https://example.com/pull/PR", "")
    text = re.sub(r"\d{4}-\d{2}-\d{2}", "", text)
    numbers = set(re.findall(r"(?<![\w.])\d+(?![\w])", text))
    plan_numbers = set(re.findall(r"(?<![\w.])\d+(?![\w])", plan.json()))
    assert numbers and numbers <= plan_numbers
    assert plan.data["as_of"] in json.dumps(message)


def _labelled_numbers(message):
    """{label: number} from the section lines like `• Manual tasks: 111`."""
    out = {}
    for block in message["blocks"]:
        if block["type"] != "section":
            continue
        for line in block["text"]["text"].splitlines():
            label, sep, value = re.sub(r"^[•*]\s*", "", line).rpartition(": ")
            if sep and value.strip().isdigit():
                out[label.strip("*")] = int(value)
    return out


def test_each_labelled_number_is_the_matching_plan_count(message, plan):
    from meridian_access.plan import SECTIONS

    counts = plan.counts
    found = _labelled_numbers(message)
    expected = {
        title: counts["by_type"][key] for key, title in SECTIONS if counts["by_type"].get(key)
    }
    expected["Approvals required"] = plan.data["approvals_required"]
    if counts["held"]:
        expected["Changes held, not applied"] = counts["held"]
    if counts["by_type"].get("break_glass"):
        expected["Break-glass entries seen, never touched"] = counts["by_type"]["break_glass"]
    if counts["warnings"]:
        expected["Warnings in the plan"] = counts["warnings"]
    if counts["manual_tasks"]:
        expected["Manual tasks"] = counts["manual_tasks"]
    assert found == expected
    assert f"*{counts['total']} changes*" in message["blocks"][1]["text"]["text"]


def test_the_warnings_section_reflects_the_plan(message, plan):
    warnings = message["blocks"][2]["text"]["text"]
    assert f"Approvals required: {plan.data['approvals_required']}" in warnings
    if plan.counts["by_type"].get("hris_mass_termination"):
        assert "Bulk HR change held" in warnings
    if plan.counts["by_type"].get("break_glass"):
        assert "Break-glass" in warnings


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://x.example/pull/1?a=1&b=2", "https://x.example/pull/1?a=1&amp;b=2"),
        ("https://x.example/<b>", "https://x.example/&lt;b&gt;"),
        ("https://x.example/a|b", "https://x.example/a%7Cb"),
    ],
)
def test_markup_characters_in_the_pr_url_are_escaped(plan, url, expected):
    link = slack_mock.build_message(plan, url)["blocks"][-1]["elements"][0]["text"]
    assert link == f"<{expected}|Review the plan pull request>"
    target = link[1:].split("|Review", 1)[0]
    assert not set("<>|") & set(target)


def test_nothing_in_the_message_is_beyond_plan_md(message, plan):
    md = plan.markdown()
    body = "\n".join(b["text"]["text"] for b in message["blocks"] if b["type"] == "section")
    for line in body.splitlines():
        label = re.sub(r"^[•*]\s*", "", line).strip("*")
        label = label.split(":")[0].strip()
        assert (
            not label
            or label in md
            or label.endswith("changes")
            or label
            in {
                "Warnings",
                "Approvals required",
                "Bulk HR change held, two approvers needed",
                "Changes held, not applied",
                "Break-glass entries seen, never touched",
                "Warnings in the plan",
                "Manual tasks",
            }
        ), label
    # no person or app name appears in it at all
    for c in plan.changes:
        if c["account"]:
            assert c["account"] not in json.dumps(message)
        if c["app_name"]:
            assert c["app_name"] not in json.dumps(message)


def test_write_message_makes_a_json_file(message, tmp_path):
    path = tmp_path / "slack-message.json"
    slack_mock.write_message(message, path)
    assert json.loads(path.read_text(encoding="utf-8")) == message
