"""The site's simulated-Slack demo (ADR-036): one tuning ticket, end to end, as a web page.

    python -m tier0.site_demo --out site       # make site-demo: replays, calls no model
    python -m tier0.site_demo estimate         # what one recorded run would cost, offline
    python -m tier0.site_demo record           # the one paid run (the owner's ruling 10)

The page replays one real run of that ticket, recorded once in `demo-recorded-run.json`
(RECORDING). `record` ran the ticket through the real agent in process, on the offline mocks
(Slack, the directory, Linear), with the guide picker on and real models for one arm:

- triage and the own-account check on `haiku-3d2`'s model and prompts (Claude Haiku 5.5, HT9
  and HC7, with part 2's reset signal), as the lab's default Haiku setup;
- the guide picker on Claude Sonnet 5.5 with KS6A-H, milestone 3e's frozen picker prompt.

It saved each call's raw reply, the model ids, the prompt versions with their SHA-256, the
token counts, the cost and the UTC date. The default command runs the same agent again with
those replies played back (`Replay`), with no model call and no network, and writes two files:
`demo.json`, the run log, and `demo.html`, the page built from it. A replayed call must be the
very request that was recorded (its task, prompt, text, schema and size, by SHA-256), and every
recorded call must be used; otherwise the replay stops, because the page would no longer show
what the run did.

The run uses the default setting, so the guide waits for one click from a person in IT
(ADR-053): the requester gets the hand-off reply, the draft goes to #it-team, and a member of
the IT team (the first, by Slack id) presses "Send this guide" through the mock's signed
button request. Only then does code post the guide.

The run has no how-to gate step (milestone 3f). It was recorded on 2026-11-19, before the gate
was turned on, so the recording holds no gate call: the replay builds the picker with no gate,
explicitly (GATE_VERSION), and the page says so. Showing the gate would take a new recording.

The message is one template of the tuning month, word for word. Every fact on the page (the
labels, the checks, the guide, the replies, the ticket's comments and states) comes from the
run log; only the short notes beside each step are written here. If the agent stops going
the guide way on this ticket, the run fails rather than draw a different story.
The page around the steps is `site_demo.html`, next to this file. `tier0/tests/test_site_demo.py`
fails if either file drifts from what this module makes.
"""

import argparse
import hashlib
import html
import json
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from string import Template
from typing import Any

import anyio

from meridian_dataset import DEFAULT_SEED, World, generate
from meridian_dataset.knowledge import load_runbooks
from meridian_dataset.model import Person, Ticket
from meridian_dataset.month import TUNING_MONTH, load_templates
from tier0.agent import DRAFT_GUIDE, GUIDE_WAITS_FOR_IT
from tier0.clock import format_time, parse_time
from tier0.evals.knowledge import ScriptedPicker
from tier0.evals.setups import SETUPS_3D2, SONNET
from tier0.identity.binding import GATEWAY_PRINCIPAL
from tier0.knowledge import review
from tier0.knowledge.answer import reply_text
from tier0.knowledge.guide_screen import flag_guide
from tier0.knowledge.picker import HOW_TO
from tier0.knowledge.prompts import PICKER_VERSIONS
from tier0.knowledge.wiring import GuidePicker
from tier0.mocks.slack import CHANNELS
from tier0.models import ClaudeModel, ModelAdapter, ModelRequest, ModelResponse, StubModel
from tier0.models.claude import INPUT_OVERHEAD_TOKENS
from tier0.models.pricing import tokens_usd, worst_case_usd
from tier0.models.spend import ITEM_ENV, SpendCapReached, SpendLedger
from tier0.netguard import guard_for_model, loopback_only
from tier0.runtime import offline_runtime
from tier0.sealed import require_unsealed

LABEL = "Simulated Slack, fake data"
TEMPLATE_SET = TUNING_MONTH
TEMPLATE_ID = "T035"
# Countries whose people get English replies (their Slack locale is en-US or en-GB).
ENGLISH = ("US", "CA", "GB", "IE", "PL", "NL", "SG", "AU", "IN")
FILES = ("demo.json", "demo.html")
REPO = "https://github.com/kstarev/meridian-it-lab/blob/main"
# The repo's site/, where the page, its run log and the recording live.
DEFAULT_SITE = Path(__file__).resolve().parents[3] / "site"

# --- the recorded run (the owner's ruling 10, 2026-11-19) -------------------------------
RECORDING = "demo-recorded-run.json"
# The arm: haiku-3d2's triage model and prompts (ADR-048), and milestone 3e's frozen picker,
# KS6A-H on Sonnet (the plan the blind months ran with; its SHA-256 is pinned there and
# checked by the tests). The agent's own default picker prompt is still KS5 (ADR-046).
ARM = SETUPS_3D2["haiku-3d2"]
PICKER_MODEL = SONNET
PICKER_PROMPT = "KS6A-H"
# The recording was made before the how-to gate was turned on (the agent's default is now G3,
# tier0.knowledge.wiring.HOW_TO_GATE_VERSION), so it holds no gate call. The run builds the
# picker with no gate, explicitly, and the page says the run has no gate step. A gate here would
# take a new recording: one more paid run, with the owner's go.
GATE_VERSION: str | None = None
# The owner allowed one paid run, from this budget item, under this much in total.
BUDGET_ITEM = "tier0-3e"
RECORD_LIMIT_USD = 0.05
# The same rough size rule as the knowledge eval's estimate: 3.5 bytes a token, 20 out.
BYTES_PER_TOKEN = 3.5
EXPECTED_OUTPUT_TOKENS = 20


@dataclass(frozen=True, slots=True)
class Picker3e(GuidePicker):
    """The agent's guide picker with milestone 3e's frozen prompt, KS6A-H, as the owner's
    ruling 10 asks for the recorded run. Everything else is GuidePicker's: the model must be
    Sonnet 5.5, it sees ids, titles and summaries only, and every code gate after it holds.
    KS6A-H is a one-call prompt, like KS5, so GuidePicker's single call is all it needs."""

    @property
    def version(self) -> str:
        return PICKER_PROMPT

    @property
    def system(self) -> str:
        return PICKER_VERSIONS[PICKER_PROMPT]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _prompt_texts() -> dict[str, tuple[str, str]]:
    arm = ARM.prompts()
    return {
        "triage": (ARM.triage_prompt, arm.triage or ""),
        "own_account_check": (ARM.check_prompt, arm.check or ""),
        "guide_pick": (PICKER_PROMPT, PICKER_VERSIONS[PICKER_PROMPT]),
    }


def prompts() -> dict[str, dict[str, str]]:
    """The arm's prompt for each task the run can call, by version and SHA-256."""
    return {
        task: {"version": version, "sha256": _sha256(text)}
        for task, (version, text) in _prompt_texts().items()
    }


def _on_the_arm(request: ModelRequest) -> bool:
    """The call's system prompt is the arm's for its task. The picker's ends with the guide
    list, after its prompt (tier0.knowledge.picker)."""
    _, text = _prompt_texts()[request.task]
    if request.task == "guide_pick":
        return request.system.startswith(text + "\n\n")
    return request.system == text


def _request(request: ModelRequest) -> dict[str, Any]:
    """What identifies a call: its task, and its prompt, text and schema by SHA-256."""
    schema = request.json_schema
    return {
        "task": request.task,
        "system_sha256": _sha256(request.system),
        "user_sha256": _sha256(request.user),
        "schema_sha256": None if schema is None else _sha256(json.dumps(schema, sort_keys=True)),
        "max_tokens": request.max_tokens,
    }


def _request_bytes(request: ModelRequest) -> int:
    """The request's UTF-8 size, measured as ClaudeModel measures it for its worst case."""
    return len(json.dumps([request.system, request.user, request.json_schema]).encode("utf-8"))


def _worst_case(model: str, request: ModelRequest) -> float:
    """The cost bound ClaudeModel checks against the ledger before it sends the call."""
    return worst_case_usd(
        model, _request_bytes(request) + INPUT_OVERHEAD_TOKENS, request.max_tokens
    )


class Recorder:
    """Passes each call to a real model and keeps the raw reply, with the request's hashes, the
    token counts and the cost. Before each call it refuses one whose worst case would take the
    run past RECORD_LIMIT_USD; the ledger's own check (the budget item's cap) runs as well."""

    def __init__(self, inner: ModelAdapter, calls: list[dict[str, Any]]) -> None:
        self._inner = inner
        self._calls = calls
        self.provider = inner.provider
        self.model = inner.model
        self.temperature = inner.temperature
        self.seed = inner.seed
        self.supports_seed = inner.supports_seed

    def describe(self) -> dict[str, str]:
        return self._inner.describe()

    def complete(self, request: ModelRequest) -> ModelResponse:
        prompt = prompts()[request.task]
        if not _on_the_arm(request):
            raise RuntimeError(f"the {request.task} call is not on the arm's {prompt['version']}")
        spent = sum(float(c["cost_usd"]) for c in self._calls)
        worst = _worst_case(self.model, request)
        if spent + worst > RECORD_LIMIT_USD:
            raise SpendCapReached(
                f"the recorded run's limit: ${spent:.4f} spent, the next call could cost up to "
                f"${worst:.4f}, limit ${RECORD_LIMIT_USD:.2f}"
            )
        response = self._inner.complete(request)
        cost, _ = tokens_usd(
            str(response.model),
            response.input_tokens,
            response.output_tokens,
            cache_write_tokens=response.cache_write_tokens,
            cache_read_tokens=response.cache_read_tokens,
        )
        self._calls.append(
            {
                "model_asked": self.model,
                "prompt": prompt["version"],
                "request": _request(request),
                "reply": {
                    "text": response.text,
                    "model": str(response.model),
                    "finish_reason": response.finish_reason,
                    "response_id": response.response_id,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "cache_write_tokens": response.cache_write_tokens,
                    "cache_read_tokens": response.cache_read_tokens,
                },
                "cost_usd": round(cost, 8),
            }
        )
        return response


class ReplayMismatch(RuntimeError):
    """The agent asked for a call the recording doesn't hold, or left one unused."""


class Replay:
    """Plays one model's recorded replies back, in order, and calls nothing. Each call must be
    the very request that was recorded, by its hashes; anything else stops the replay."""

    provider = "replay"
    temperature: float | None = None
    seed: int | None = None
    supports_seed = False

    def __init__(self, model: str, calls: Sequence[dict[str, Any]]) -> None:
        self.model = model
        self._calls = [c for c in calls if c["model_asked"] == model]

    def describe(self) -> dict[str, str]:
        return {"replay_of": self.model, "recording": RECORDING}

    @property
    def left(self) -> int:
        return len(self._calls)

    def complete(self, request: ModelRequest) -> ModelResponse:
        if not self._calls:
            raise ReplayMismatch(f"the recording holds no more {self.model} calls")
        call = self._calls.pop(0)
        asked = _request(request)
        if asked != call["request"]:
            changed = sorted(k for k in asked if asked[k] != call["request"].get(k))
            raise ReplayMismatch(
                f"the {request.task} call differs from the recorded one in {', '.join(changed)}: "
                "the code, a prompt or a guide changed since the run was recorded. Record it "
                "again (one paid run, with the owner's go) or undo the change."
            )
        reply = call["reply"]
        return ModelResponse(
            text=reply["text"],
            model=reply["model"],
            input_tokens=reply["input_tokens"],
            output_tokens=reply["output_tokens"],
            finish_reason=reply["finish_reason"],
            response_id=reply["response_id"],
            cache_write_tokens=reply["cache_write_tokens"],
            cache_read_tokens=reply["cache_read_tokens"],
        )


class _Capture:
    """Offline: passes each call to a fake and keeps the request, for the estimate."""

    def __init__(self, inner: ModelAdapter, model: str, seen: list[tuple[str, ModelRequest]]):
        self._inner = inner
        self._seen = seen
        self.provider = inner.provider
        self.model = model
        self.temperature = inner.temperature
        self.seed = inner.seed
        self.supports_seed = inner.supports_seed

    def describe(self) -> dict[str, str]:
        return self._inner.describe()

    def complete(self, request: ModelRequest) -> ModelResponse:
        self._seen.append((self.model, request))
        return self._inner.complete(request)


def _sender(world: World) -> Person:
    """The first active Slack user who gets replies in English."""
    return next(
        p for p in world.people if p.status == "active" and p.slack_active and p.country in ENGLISH
    )


def _require(ok: bool, what: str) -> None:
    if not ok:
        raise RuntimeError(f"the demo run no longer goes the way the page tells it: {what}")


async def _run(
    world: World, triage_model: ModelAdapter, picker_model: ModelAdapter, models: dict[str, Any]
) -> dict[str, Any]:
    template = next(t for t in load_templates(TEMPLATE_SET) if t.id == TEMPLATE_ID)
    _require(template.expected_guide is not None, "the template has no guide in its answer key")
    guide_id = str(template.expected_guide)
    guide = next(g for g in load_runbooks() if g.id == guide_id)
    person = _sender(world)
    async with offline_runtime(
        world,
        model=triage_model,
        prompts=ARM.prompts(),
        features=ARM.features(),
        guide_picker=Picker3e(model=picker_model, gate_version=GATE_VERSION),
    ) as rt:
        dm = rt.slack.dm_channel_for(person.slack_user_id)
        ticket = Ticket(
            id=template.id,
            channel=dm,
            sender_slack_user_id=person.slack_user_id,
            sender_team_id=world.slack_team.team_id,
            text=template.text,
            received_at=world.reference_time,
        )
        outcome = await rt.agent.handle(rt.slack.message_event(ticket))
        _require(outcome.status == "handled", f"the event was {outcome.status}")
        _require(outcome.action == DRAFT_GUIDE, f"the action was {outcome.action}")
        identifier = outcome.ticket or ""
        record = rt.tickets.get_ticket(identifier)
        drafted_state = record.state
        drafted_comments = [c.body for c in record.comments]
        (handoff,) = [m for m in rt.slack.sent_messages if m.channel == dm]
        (draft,) = [m for m in rt.slack.sent_messages if m.channel == CHANNELS["it-team"]]
        (buttons,) = [b["elements"] for b in draft.blocks if b["type"] == "actions"]
        _require(
            [b["action_id"] for b in buttons] == list(review.ACTIONS), "the draft's buttons changed"
        )
        drafted_at = rt.clock.now()

        # A member of the IT team presses "Send this guide" in #it-team.
        clicker_id = sorted(review.it_team(world))[0]
        clicker = next(p for p in world.people if p.slack_user_id == clicker_id)
        press = rt.slack.button_click(
            channel=review.IT_TEAM_CHANNEL,
            user=clicker_id,
            action_id=review.SEND_GUIDE_ACTION,
            value=identifier,
        )
        clicked = rt.agent.handle_interaction(press)
        _require(clicked == "sent", f"the click did {clicked}")
        record = rt.tickets.get_ticket(identifier)
        answered_state = record.state
        answered_comments = [c.body for c in record.comments][len(drafted_comments) :]
        _, reply = [m for m in rt.slack.sent_messages if m.channel == dm]
        replied_at = rt.clock.now()

        rt.clock.advance(timedelta(days=3, minutes=1))
        (swept,) = rt.agent.sweep()
        _require(swept.action == "close", f"the sweep did {swept.action}")
        record = rt.tickets.get_ticket(identifier)
        *_, closing = [m for m in rt.slack.sent_messages if m.channel == dm]

        spans = rt.tracer.finished_spans()
        events = [(e.name, dict(e.attributes or {})) for s in spans for e in s.events]
        names = [name for name, _ in events]
        _require("tier0.identity.unbound" not in names, "the sender was not verified")
        escalated = [a["reason"] for n, a in events if n == "tier0.escalated"]
        _require(escalated == [GUIDE_WAITS_FOR_IT], "the request went to a person another way")
        _require("tier0.guard.blocked" not in names, "the reply guard blocked a message")
        gate_calls = names.count("tier0.guide.gate")
        _require(
            gate_calls == 0 and "tier0.guide.screen_first" not in names,
            "the how-to gate's steps ran, and the page shows no gate step",
        )
        (policy,) = [a for n, a in events if n == "tier0.policy.decision"]
        (pick,) = [a for n, a in events if n == "tier0.guide.pick"]
        (posted,) = [a for n, a in events if n == "tier0.guide.posted"]
        _require(posted["guide"] == guide_id, "another guide was posted")
        principals = sorted({r.principal for r in rt.directory.reads})
        _require(principals == [GATEWAY_PRINCIPAL], "something but the identity check read data")
        tool_calls = sum(s.name.startswith("execute_tool") for s in spans)
        _require(not rt.issuer.issued and not tool_calls, "a token or a tool call was used")
        (sent,) = rt.controls.audit.events("guide_draft.sent")
        _require(sent.actor == clicker_id, "someone else's click sent the guide")
        return {
            "label": LABEL,
            "source": {
                "template_set": TEMPLATE_SET,
                "template": template.id,
                "kind": template.kind,
                "expected_guide": guide_id,
                "world_seed": world.answer_key.seed,
            },
            "models": models,
            "requester": {
                "name": f"{person.given_name} {person.family_name}",
                "first_name": person.given_name,
            },
            "message": {"at": world.reference_time, "text": template.text},
            "checks": {
                "slack_signature": "valid",
                "identity": "bound",
                "directory_reads_by": principals,
                "kill_switch": "off" if not rt.controls.kill_switch.is_on else "on",
                "rate_limit": "allowed",
            },
            "triage": {"category": outcome.category},
            "how_to_gate": {"version": GATE_VERSION, "calls": gate_calls},
            "policy": {k: policy[k] for k in ("tier", "action")},
            "pick": {k: pick[k] for k in ("version", "request_type", "guide", "screen", "outcome")},
            "guide": {
                "id": guide.id,
                "title": guide.title,
                "approved": guide.approved,
                "flagged": flag_guide(guide) is not None,
            },
            "handoff": {"at": format_time(drafted_at), "text": handoff.text},
            "draft": {
                "at": format_time(drafted_at),
                "channel": "#it-team",
                "header": review.draft_header(identifier, guide),
                "reply": reply_text(guide, "en"),
                "buttons": [b["text"]["text"] for b in buttons],
            },
            "click": {
                "at": format_time(replied_at),
                "by": {
                    "name": f"{clicker.given_name} {clicker.family_name}",
                    "department": clicker.department,
                },
                "button": buttons[0]["text"]["text"],
                "from_channel": "#it-team",
                "signed_by_slack": True,
                "in_it_team": clicker_id in review.it_team(world),
                "result": clicked,
            },
            "reply": {"at": format_time(replied_at), "text": reply.text},
            "guard": "passed",
            "tokens_issued": len(rt.issuer.issued),
            "tool_calls": tool_calls,
            "ticket": {
                "id": identifier,
                "queue": record.queue,
                "state_after_draft": drafted_state,
                "comments_after_draft": drafted_comments,
                "state_after_reply": answered_state,
                "comments_after_reply": answered_comments,
                "comments_at_close": [c.body for c in record.comments][
                    len(drafted_comments) + len(answered_comments) :
                ],
                "state_at_close": record.state,
            },
            "close": {"at": format_time(rt.clock.now()), "text": closing.text},
        }


def _world(seed: int) -> World:
    world = generate(seed)
    require_unsealed(world)
    return world


def _template_guide() -> str:
    template = next(t for t in load_templates(TEMPLATE_SET) if t.id == TEMPLATE_ID)
    return str(template.expected_guide)


def load_recording(path: Path) -> dict[str, Any]:
    """The recorded run. Only a run that went the whole way can be replayed."""
    recording: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if recording.get("complete") is not True:
        raise ReplayMismatch(f"{path} is not a complete run: {recording.get('error')}")
    return recording


def _models(recording: dict[str, Any]) -> dict[str, Any]:
    """Which model answered each task in the recorded run, with its prompt, and the date."""
    by_task: dict[str, dict[str, str]] = {}
    for call in recording["calls"]:
        by_task.setdefault(
            call["request"]["task"], {"model": call["reply"]["model"], "prompt": call["prompt"]}
        )
    return {"recorded_on": recording["recorded_on"], "recording": RECORDING, **by_task}


def replay(recording: dict[str, Any] | None = None) -> dict[str, Any]:
    """The run log: the recorded run played back through the agent on the mocks. No model is
    called and nothing leaves this machine."""
    recording = load_recording(DEFAULT_SITE / RECORDING) if recording is None else recording
    source = recording["source"]
    _require(
        (source["template_set"], source["template"]) == (TEMPLATE_SET, TEMPLATE_ID),
        "the recording is of another ticket",
    )
    world = _world(source["world_seed"])
    triage = Replay(ARM.triage_model, recording["calls"])
    picker = Replay(PICKER_MODEL, recording["calls"])
    with loopback_only():
        run: dict[str, Any] = anyio.run(_run, world, triage, picker, _models(recording))
    if triage.left or picker.left:
        raise ReplayMismatch("the agent made fewer calls than the recorded run")
    return run


def estimate(seed: int = DEFAULT_SEED) -> list[dict[str, Any]]:
    """Offline, before the paid run: the calls the run makes, found by running it with the
    fakes (the stub's triage and the scripted pick), and what each would cost. `expected_usd`
    is the size rule's rough figure; `worst_case_usd` is the bound the ledger checks."""
    seen: list[tuple[str, ModelRequest]] = []
    triage = _Capture(StubModel(), ARM.triage_model, seen)
    picker = _Capture(ScriptedPicker(HOW_TO, _template_guide()), PICKER_MODEL, seen)
    with loopback_only():
        anyio.run(_run, _world(seed), triage, picker, {})
    rows = []
    for model, request in seen:
        size = _request_bytes(request)
        expected, _ = tokens_usd(model, round(size / BYTES_PER_TOKEN), EXPECTED_OUTPUT_TOKENS)
        rows.append(
            {
                "task": request.task,
                "model": model,
                "bytes": size,
                "expected_usd": expected,
                "worst_case_usd": _worst_case(model, request),
            }
        )
    return rows


def record_once(
    out: Path,
    *,
    seed: int = DEFAULT_SEED,
    replace: bool = False,
    make_model: Callable[[str], ModelAdapter] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """The one paid run: the ticket through the agent on real models, saved to out/RECORDING.

    Before any call it refuses unless the budget item is BUDGET_ITEM, no recording is there
    yet (or `replace`), and the run's worst case is under RECORD_LIMIT_USD. The replies are
    saved even when the run then goes another way, so a paid call is never lost; such a
    recording is marked incomplete, and the page can't be built from it."""
    path = out / RECORDING
    if path.exists() and not replace:
        raise FileExistsError(f"{path} exists: the run is recorded once (--replace records again)")
    item = os.environ.get(ITEM_ENV)
    if item != BUDGET_ITEM:
        raise SpendCapReached(f"set {ITEM_ENV}={BUDGET_ITEM}: the owner allowed this run from it")
    worst = sum(r["worst_case_usd"] for r in estimate(seed))
    if worst > RECORD_LIMIT_USD:
        raise SpendCapReached(
            f"the run could cost up to ${worst:.4f}, over its limit of ${RECORD_LIMIT_USD:.2f}"
        )
    make = make_model or (lambda model: ClaudeModel(model=model))
    calls: list[dict[str, Any]] = []
    triage = Recorder(make(ARM.triage_model), calls)
    picker = Recorder(make(PICKER_MODEL), calls)
    world = _world(seed)
    started = (clock or (lambda: datetime.now(UTC)))().astimezone(UTC)
    recording: dict[str, Any] = {
        "what": (
            "One real run of the demo's ticket through tier0's agent on the mocks, recorded "
            "once for the site (the owner's ruling 10, 2026-11-19). make site-demo replays "
            "these replies and calls no model."
        ),
        "recorded_at": started.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "recorded_on": started.date().isoformat(),
        "command": "python -m tier0.site_demo record",
        "budget_item": item,
        "limit_usd": RECORD_LIMIT_USD,
        "arm": ARM.name,
        "source": {
            "template_set": TEMPLATE_SET,
            "template": TEMPLATE_ID,
            "world_seed": world.answer_key.seed,
        },
        "prompt_cache": False,
        "arm_prompts": prompts(),
        "calls": calls,
    }
    try:
        with guard_for_model("claude"):
            anyio.run(_run, world, triage, picker, {})
        recording["complete"] = True
    except Exception as exc:
        recording["complete"] = False
        recording["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        recording["cost_usd"] = round(sum(float(c["cost_usd"]) for c in calls), 8)
        if calls:
            out.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(recording, indent=2, ensure_ascii=False) + "\n")
    return recording


# --- the page ---------------------------------------------------------------------------

_e = html.escape
# The page around the steps; $label, $recorded, $gate, $recording, $when, $steps and $repo are
# filled in.
_PAGE = Template((Path(__file__).parent / "site_demo.html").read_text())


def _when(at: str) -> str:
    t = parse_time(at)
    return f"{t:%a} {t.day} {t:%b} {t:%Y}, {t:%H:%M} UTC"


def _clock(at: str) -> str:
    return f"{parse_time(at):%H:%M}"


_BOLD = re.compile(r"\*([^*\n]+)\*")


def _slack_text(text: str) -> str:
    """Slack's own formatting, as Slack shows it: *bold*, and a link on its own line."""
    out = []
    for line in text.split("\n"):
        if line.startswith("https://"):
            out.append(f'<span class="url">{_e(line)}</span>')
        else:
            out.append(_BOLD.sub(r"<strong>\1</strong>", _e(line)))
    return "<br>".join(out)


def _initials(name: str) -> str:
    return "".join(part[0] for part in name.split()[:2]).upper()


def _message(
    who: str,
    avatar: str,
    at: str,
    text: str,
    *,
    app: bool = False,
    where: str = "Direct message with tier0",
    buttons: Sequence[str] = (),
) -> str:
    tag = '<span class="app">APP</span>' if app else ""
    cls = "avatar bot" if app else "avatar"
    actions = ""
    if buttons:
        first, *rest = buttons
        spans = f'<span class="btn primary">{_e(first)}</span>' + "".join(
            f'<span class="btn">{_e(b)}</span>' for b in rest
        )
        actions = f'<p class="msg-actions">{spans}</p>'
    return (
        '<div class="slack">'
        f'<p class="slack-bar"><strong>{_e(where)}</strong><span>{LABEL}</span></p>'
        '<div class="msg">'
        f'<span class="{cls}" aria-hidden="true">{avatar}</span>'
        '<div class="msg-body">'
        f'<p class="msg-head"><strong>{_e(who)}</strong>{tag}'
        f'<time datetime="{_e(at)}">{_clock(at)}</time></p>'
        f'<p class="msg-text">{_slack_text(text)}</p>'
        f"{actions}"
        "</div></div></div>"
    )


def _facts(rows: Sequence[tuple[str, str]]) -> str:
    items = "".join(f"<div><dt>{_e(k)}</dt><dd>{_e(v)}</dd></div>" for k, v in rows)
    return f'<dl class="facts">{items}</dl>'


def _log(title: str, rows: Sequence[tuple[str, str]]) -> str:
    return f'<div class="log"><p class="log-title">{_e(title)}</p>{_facts(rows)}</div>'


def _comments(comments: Sequence[str]) -> str:
    items = "".join(f"<li>{_e(c)}</li>" for c in comments)
    return f'<ol class="comments">{items}</ol>'


def _who(kind: str) -> str:
    text = {
        "person": "The employee",
        "it": "A person in IT",
        "code": "Code decided",
        "both": "Model suggested, code decided",
    }[kind]
    return f'<span class="who {kind}">{text}</span>'


def _step(n: int, title: str, kind: str, shown: str, note: str) -> str:
    return (
        f'<li class="step" id="step-{n}">'
        f'<div class="step-head"><span class="num" aria-hidden="true">{n:02d}</span>'
        f"<h3>{_e(title)}</h3>{_who(kind)}</div>"
        f'<div class="step-grid"><div class="shown">{shown}</div>'
        f'<div class="note">{note}</div></div>'
        "</li>"
    )


def _p(*paragraphs: str) -> str:
    return "".join(f"<p>{p}</p>" for p in paragraphs)


_MODEL_ID = re.compile(r"claude-(haiku|sonnet|opus)-(\d+)-(\d+)")


def model_name(model_id: str) -> str:
    """A Claude model's name as people write it: claude-haiku-5-5 is Claude Haiku 5.5."""
    m = _MODEL_ID.fullmatch(model_id)
    return f"Claude {m[1].title()} {m[2]}.{m[3]}" if m else model_id


def recorded_line(models: dict[str, Any]) -> str:
    """The page's plain statement of what ran: the date and the models, from the recording."""
    triage, pick = models["triage"], models["guide_pick"]
    return (
        f"Recorded once on {models['recorded_on']} with {model_name(triage['model'])} "
        f"(triage) and {model_name(pick['model'])} (guide picker); the page replays that run, "
        "it doesn't call a model."
    )


def gate_line(run: dict[str, Any]) -> str:
    """The page's plain statement that the run has no how-to gate step, and why. The page has no
    gate step to show, so a run log with a gate in it can't be drawn."""
    gate = run["how_to_gate"]
    if gate != {"version": None, "calls": 0}:
        raise RuntimeError(f"the page shows no how-to gate step, but the run had one: {gate}")
    return (
        f"This run was recorded on {run['models']['recorded_on']}, before the how-to gate was "
        "turned on, so the page shows no gate step. With the gate on, triage's model is asked "
        "one more question before the guide picker: is the message one plain how-to question?"
    )


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def _none(count: int) -> str:
    return "none" if count == 0 else str(count)


def render(run: dict[str, Any]) -> str:
    """The page, from the run log alone."""
    src, req, msg, chk = run["source"], run["requester"], run["message"], run["checks"]
    pick, guide, tk = run["pick"], run["guide"], run["ticket"]
    draft, click, models = run["draft"], run["click"], run["models"]
    triage_by, pick_by = models["triage"], models["guide_pick"]
    recording = f'<a href="{_e(models["recording"])}">recording</a>'
    name = req["name"]
    template_link = (
        f'<a href="{REPO}/dataset/src/meridian_dataset/month/pools/{_e(src["template_set"])}.json">'
        f"{_e(src['template_set'])}</a>"
    )
    steps = [
        _step(
            1,
            "A person asks in Slack",
            "person",
            _message(name, _initials(name), msg["at"], msg["text"]),
            _p(
                f"The message is template <strong>{_e(src['template'])}</strong> of the tuning "
                f"month ({template_link}), word for word. Tuning data is what the project "
                "builds and tunes on; no sealed test set appears on this page.",
                f"The sender, {_e(name)}, is a fake person from the lab's fake directory. "
                "Tier0 gets the message as a Slack event, in a direct message.",
            ),
        ),
        _step(
            2,
            "Code checks who sent it",
            "code",
            _log(
                "Checks before any model call",
                [
                    ("Slack signature", chk["slack_signature"]),
                    (
                        "Sender matches an active directory account",
                        _yes(chk["identity"] == "bound"),
                    ),
                    ("Kill switch", chk["kill_switch"]),
                    ("Rate limit", chk["rate_limit"]),
                ],
            ),
            _p(
                "Code checks that the event really came from Slack and that the Slack user "
                "is an active account in the company directory. The sender's name is taken "
                "from the directory, not from the message.",
                "The kill switch is off and the sender is under the rate limit, so tier0 may "
                "act. No AI model has seen the message yet.",
            ),
        ),
        _step(
            3,
            "Triage labels the request",
            "both",
            _log(
                "Triage and the policy table",
                [
                    ("Model", triage_by["model"]),
                    ("Triage prompt", triage_by["prompt"]),
                    ("Label the model gave", run["triage"]["category"]),
                    ("Policy tier", run["policy"]["tier"]),
                    ("Policy action", run["policy"]["action"]),
                ],
            ),
            _p(
                f"The model picks one label from a fixed list. Here it said "
                f"<strong>{_e(run['triage']['category'])}</strong>: not a username question "
                "and not about someone else.",
                "Code looks the label up in a fixed table. For this label the table says "
                f"<strong>{_e(run['policy']['action'])}</strong>: hand it to a person, unless "
                "an approved guide answers it.",
                f"The label is {_e(model_name(triage_by['model']))}'s own reply in the recorded "
                f"run, with triage prompt <code>{_e(triage_by['prompt'])}</code> of the lab's "
                f"default Haiku setup. The page plays it back from the {recording}.",
            ),
        ),
        _step(
            4,
            "A guide is picked, and code checks it",
            "both",
            _log(
                "Guide picker and posting gate",
                [
                    ("Model", pick_by["model"]),
                    ("Picker prompt", pick["version"]),
                    ("Request type the model gave", pick["request_type"]),
                    ("Guide the model named", pick["guide"]),
                    (
                        "Message screen (code only)",
                        "nothing found" if pick["screen"] == "none" else pick["screen"],
                    ),
                    (f"{guide['id']} approved by a person", _yes(guide["approved"])),
                    (f"{guide['id']} flagged by the guide screen", _yes(guide["flagged"])),
                    (
                        "Outcome",
                        "a draft for IT" if pick["outcome"] == "guide" else pick["outcome"],
                    ),
                ],
            ),
            _p(
                "The model sees only each guide's id, title and summary, never its steps. It "
                f"names one guide, <strong>{_e(pick['guide'])}</strong> "
                f"({_e(guide['title'])}), or none.",
                "Code decides whether the guide may be used: the message screen found nothing "
                "that needs a person, and the guide is approved by a person and not flagged. Any "
                "no would have sent the request to a person instead.",
                "A yes does not post the guide. Guide answers wait for one click from a person in "
                "IT until a sealed month of tests passes, because the guide picker failed its "
                "safety rule on the last two (ADR-053).",
                f"The pick is {_e(model_name(pick_by['model']))}'s own reply in the same "
                f"recorded run, with picker prompt <code>{_e(pick['version'])}</code>: the "
                "prompt that failed that safety rule on the last two blind months. In the lab "
                "the picker is off unless an option turns it on.",
            ),
        ),
        _step(
            5,
            "The requester hears that IT will look at it",
            "code",
            _message("tier0", "T0", run["handoff"]["at"], run["handoff"]["text"], app=True),
            _p(
                "Code sends the same reply as for any request that goes to a person. It makes no "
                "new promise, such as a time. The greeting uses the first name from the "
                "directory, and the first message says that tier0 is an AI assistant.",
                "The guide is not in this message. The ticket is in the queue for a person.",
            ),
        ),
        _step(
            6,
            "A draft waits in the IT team's channel",
            "code",
            _message(
                "tier0",
                "T0",
                draft["at"],
                f"{draft['header']}\n\n{draft['reply']}",
                app=True,
                where=draft["channel"],
                buttons=draft["buttons"],
            ),
            _p(
                f"Code posts a draft to {_e(draft['channel'])}: the reply tier0 would send, "
                "word for word, and two buttons. The draft names the ticket and the guide. It "
                "does not repeat the message or the requester's name.",
                f"<strong>{_e(draft['buttons'][0])}</strong> sends the guide. "
                f"<strong>{_e(draft['buttons'][1])}</strong> leaves the ticket with the person, "
                "and tier0 sends nothing.",
            ),
        ),
        _step(
            7,
            "A person in IT clicks once",
            "it",
            _log(
                "The click, before code acts on it",
                [
                    ("Button", click["button"]),
                    ("Clicked by", f"{click['by']['name']} ({click['by']['department']})"),
                    ("Signed by Slack", _yes(click["signed_by_slack"])),
                    (f"From {click['from_channel']}", "yes"),
                    ("Clicker is on the IT team", _yes(click["in_it_team"])),
                    ("Result", click["result"]),
                ],
            ),
            _p(
                f"{_e(click['by']['name'])}, a fake person on the IT team, presses "
                f"<strong>{_e(click['button'])}</strong>.",
                "Code counts the click only if Slack signed it, it came from the IT team's "
                "channel, and the person is on the IT team in the directory. Anyone else's click "
                "is refused and logged. A second click sends nothing.",
                "The requester waits until someone in IT clicks. On the lab's clock the click "
                "comes at once.",
            ),
        ),
        _step(
            8,
            "Code posts the guide",
            "code",
            _message("tier0", "T0", run["reply"]["at"], run["reply"]["text"], app=True)
            + _log(
                "Before and after sending",
                [
                    ("Reply guard", run["guard"]),
                    ("Access tokens asked for", _none(run["tokens_issued"])),
                    ("Tool calls on an account", _none(run["tool_calls"])),
                ],
            ),
            _p(
                "Code wrote every word of this reply. The header comes from a fixed template, "
                "and the steps are the approved guide's, word for word, with its link. It is the "
                "reply from the draft.",
                "The reply guard checked the message again before it was sent. Tier0 asked for "
                "no access token and called no tool, because a guide reads no account.",
            ),
        ),
        _step(
            9,
            "The ticket records what happened",
            "code",
            f'<div class="log"><p class="log-title">Ticket {_e(tk["id"])} · queue '
            f"{_e(tk['queue'])} · {_e(tk['state_after_draft'])}</p>"
            f"{_comments(tk['comments_after_draft'])}</div>"
            f'<div class="log"><p class="log-title">Ticket {_e(tk["id"])} · after the click · '
            f"{_e(tk['state_after_reply'])}</p>"
            f"{_comments(tk['comments_after_reply'])}</div>",
            _p(
                "Tier0 opens a ticket for every request and notes each step on it, so a "
                "person on the IT team can see what tier0 did and why.",
                "Until the click, the ticket is in "
                f"<strong>{_e(tk['state_after_draft'])}</strong>, the queue for a person. After "
                "it, the ticket waits for a reply: "
                f"<strong>{_e(tk['state_after_reply'])}</strong>.",
            ),
        ),
        _step(
            10,
            "Three days later, code closes the ticket",
            "code",
            _message("tier0", "T0", run["close"]["at"], run["close"]["text"], app=True)
            + f'<div class="log"><p class="log-title">Ticket {_e(tk["id"])} · '
            f"{_e(tk['state_at_close'])}</p>{_comments(tk['comments_at_close'])}</div>",
            _p(
                "Nobody replied, so after three days a scheduled sweep closes the ticket and "
                "tells the person how to reopen the question. The note is a fixed template, "
                f"too. The ticket is now <strong>{_e(tk['state_at_close'])}</strong>.",
            ),
        ),
    ]
    return _PAGE.substitute(
        label=_e(run["label"]),
        recorded=html.escape(recorded_line(models), quote=False),
        gate=html.escape(gate_line(run), quote=False),
        recording=_e(models["recording"]),
        when=_e(_when(msg["at"])),
        steps="\n".join(steps),
        repo=REPO,
    )


def files(run: dict[str, Any] | None = None, site: Path | None = None) -> dict[str, str]:
    """The two files, by name: the run log and the page built from it. The run log is the
    recording in `site` (by default the repo's site/) played back."""
    if run is None:
        run = replay(load_recording((DEFAULT_SITE if site is None else site) / RECORDING))
    return {
        "demo.json": json.dumps(run, indent=2, ensure_ascii=False) + "\n",
        "demo.html": render(run),
    }


def _print_estimate(rows: Sequence[dict[str, Any]]) -> None:
    for row in rows:
        print(
            f"{row['task']:<18} {row['model']:<18} {row['bytes']:>6} bytes  "
            f"about ${row['expected_usd']:.4f}, at most ${row['worst_case_usd']:.4f}"
        )
    expected = sum(r["expected_usd"] for r in rows)
    worst = sum(r["worst_case_usd"] for r in rows)
    print(
        f"{len(rows)} calls: about ${expected:.4f}, at most ${worst:.4f} "
        f"(limit ${RECORD_LIMIT_USD:.2f}); nothing was called"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tier0.site_demo",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="build",
        choices=("build", "estimate", "record"),
        help="build (the default) replays the recording and writes the page; estimate prints "
        "what a recorded run would cost, offline; record makes the one paid run",
    )
    parser.add_argument("--out", type=Path, default=Path("site"))
    parser.add_argument(
        "--replace", action="store_true", help="record again over the recording (another paid run)"
    )
    args = parser.parse_args(argv)
    if args.command == "estimate":
        _print_estimate(estimate())
        return 0
    if args.command == "record":
        _print_estimate(estimate())
        ledger = SpendLedger()
        print(f"recording: budget item {os.environ.get(ITEM_ENV)}, ledger {ledger.path}")
        recording = record_once(args.out, replace=args.replace)
        for call in recording["calls"]:
            reply = call["reply"]
            print(
                f"{call['request']['task']:<18} {reply['model']:<18} {call['prompt']:<7} "
                f"{reply['input_tokens']:>5} in {reply['output_tokens']:>4} out  "
                f"${call['cost_usd']:.6f}"
            )
        print(f"wrote {args.out / RECORDING}: ${recording['cost_usd']:.6f} in all")
    args.out.mkdir(parents=True, exist_ok=True)
    for name, text in files(site=args.out).items():
        (args.out / name).write_text(text)
        print(f"wrote {args.out / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
