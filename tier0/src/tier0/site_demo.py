"""The site's simulated-Slack demo (ADR-036): one tuning ticket, end to end, as a web page.

    python -m tier0.site_demo --out site

writes two files: `demo.json`, the run log, and `demo.html`, the page built from it. The run
goes through the real agent in process, on the offline mocks (Slack, the directory, Linear),
with the guide picker on. No model costs money and nothing leaves the process:

- triage is the repo's offline `StubModel`, a keyword stand-in for Claude Haiku;
- the guide pick comes from `ScriptedPicker`, the fake the code-only guide check uses, set to
  the answer key's guide. The page says so.

The run uses the default setting, so the guide waits for one click from a person in IT
(ADR-053): the requester gets the hand-off reply, the draft goes to #it-team, and a member of
the IT team (the first, by Slack id) presses "Send this guide" through the mock's signed
button request. Only then does code post the guide.

The message is one template of the tuning month, word for word. Every fact on the page (the
labels, the checks, the guide, the replies, the ticket's comments and states) comes from the
run log; only the short notes beside each step are written here. If the agent stops going
the guide way on this ticket, `record()` fails rather than draw a different story.
The page around the steps is `site_demo.html`, next to this file. `tier0/tests/test_site_demo.py`
fails if either file drifts from what this module makes.
"""

import argparse
import html
import json
import re
from collections.abc import Sequence
from datetime import timedelta
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
from tier0.identity.binding import GATEWAY_PRINCIPAL
from tier0.knowledge import review
from tier0.knowledge.answer import reply_text
from tier0.knowledge.guide_screen import flag_guide
from tier0.knowledge.picker import HOW_TO
from tier0.knowledge.wiring import GuidePicker
from tier0.mocks.slack import CHANNELS
from tier0.models import StubModel
from tier0.runtime import offline_runtime
from tier0.sealed import require_unsealed

LABEL = "Simulated Slack, fake data"
TEMPLATE_SET = TUNING_MONTH
TEMPLATE_ID = "T035"
# Countries whose people get English replies (their Slack locale is en-US or en-GB).
ENGLISH = ("US", "CA", "GB", "IE", "PL", "NL", "SG", "AU", "IN")
FILES = ("demo.json", "demo.html")
REPO = "https://github.com/kstarev/meridian-it-lab/blob/main"


def _sender(world: World) -> Person:
    """The first active Slack user who gets replies in English."""
    return next(
        p for p in world.people if p.status == "active" and p.slack_active and p.country in ENGLISH
    )


def _require(ok: bool, what: str) -> None:
    if not ok:
        raise RuntimeError(f"the demo run no longer goes the way the page tells it: {what}")


async def _run(world: World) -> dict[str, Any]:
    template = next(t for t in load_templates(TEMPLATE_SET) if t.id == TEMPLATE_ID)
    _require(template.expected_guide is not None, "the template has no guide in its answer key")
    guide_id = str(template.expected_guide)
    guide = next(g for g in load_runbooks() if g.id == guide_id)
    person = _sender(world)
    triage_model = StubModel()
    picker_model = ScriptedPicker(HOW_TO, guide_id)
    async with offline_runtime(
        world, model=triage_model, guide_picker=GuidePicker(model=picker_model)
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
            "models": {
                "triage": f"{triage_model.provider}/{triage_model.model}",
                "guide_pick": f"{picker_model.provider}/{picker_model.model}",
            },
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


def record(seed: int = DEFAULT_SEED) -> dict[str, Any]:
    """The run log: one tuning ticket through the agent, on the mocks and the stub."""
    world = generate(seed)
    require_unsealed(world)
    return anyio.run(_run, world)


# --- the page ---------------------------------------------------------------------------

_e = html.escape
# The page around the steps; $label, $when, $steps and $repo are filled in.
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


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def _none(count: int) -> str:
    return "none" if count == 0 else str(count)


def render(run: dict[str, Any]) -> str:
    """The page, from the run log alone."""
    src, req, msg, chk = run["source"], run["requester"], run["message"], run["checks"]
    pick, guide, tk = run["pick"], run["guide"], run["ticket"]
    draft, click = run["draft"], run["click"]
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
                f"On this page the model is the repo's offline stub "
                f"(<code>{_e(run['models']['triage'])}</code>), a keyword stand-in for Claude "
                "Haiku. It costs nothing and gives the same answer every time.",
            ),
        ),
        _step(
            4,
            "A guide is picked, and code checks it",
            "both",
            _log(
                "Guide picker and posting gate",
                [
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
                "On this page no AI model made the pick: a scripted stand-in "
                f"(<code>{_e(run['models']['guide_pick'])}</code>) names the guide from the "
                "tuning month's answer key. In the lab the picker is Claude Sonnet 5.5, and it "
                "is off unless an option turns it on.",
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
        when=_e(_when(msg["at"])),
        steps="\n".join(steps),
        repo=REPO,
    )


def files(run: dict[str, Any] | None = None) -> dict[str, str]:
    """The two files, by name: the run log and the page built from it."""
    run = record() if run is None else run
    return {
        "demo.json": json.dumps(run, indent=2, ensure_ascii=False) + "\n",
        "demo.html": render(run),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tier0.site_demo", description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("site"))
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, text in files().items():
        (args.out / name).write_text(text)
        print(f"wrote {args.out / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
