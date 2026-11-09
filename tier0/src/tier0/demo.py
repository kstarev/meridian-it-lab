"""Command line: python -m tier0.demo [--seed N] [--model stub] [--out out/demo].

Runs the whole walking skeleton offline on fake data and prints a plain-English transcript.
"""

import argparse
import sys
import textwrap
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import anyio

from meridian_dataset import DEFAULT_SEED, World, generate
from tier0.clock import parse_time
from tier0.models import build_model
from tier0.runtime import offline_runtime
from tier0.sealed import SealedSetError, require_unsealed
from tier0.tracing import Tracer, summarize


def _who(world: World, slack_user_id: str) -> str:
    person = world.person_by_slack_id(slack_user_id)
    if person is not None:
        return person.primary_email.split("@")[0]
    for ext in world.external_slack_users:
        if ext.slack_user_id == slack_user_id:
            return f"{ext.real_name} ({ext.team_name}, outside Meridian)"
    return slack_user_id


def _indent(text: str, prefix: str) -> str:
    return textwrap.indent(text, prefix, lambda _line: True)


async def _run(world: World, model_name: str, out_dir: Path) -> None:
    trace_file = out_dir / "traces.jsonl"
    trace_file.unlink(missing_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    model = build_model(model_name)
    print(
        f"tier0 demo: Meridian's AI service desk on fake data "
        f"(seed {world.answer_key.seed}, model {model.provider}/{model.model})"
    )
    print(
        "Offline: mock Slack, Google Directory and Linear, "
        "an in-process MCP server, no credentials."
    )

    linear_ids: dict[str, str] = {}
    async with offline_runtime(world, model=model, tracer=Tracer(out_dir=out_dir)) as rt:
        for ticket in world.tickets:
            rt.clock.advance_to(parse_time(ticket.received_at))
            hhmm = parse_time(ticket.received_at).strftime("%H:%M")
            print()
            who = _who(world, ticket.sender_slack_user_id)
            print(f"[{hhmm} UTC] {ticket.id}: Slack message from {who}")
            print(_indent(f'"{ticket.text}"', "    "))
            sent_before = len(rt.slack.sent_messages)
            issued_before = len(rt.issuer.issued)
            outcome = await rt.agent.handle(rt.slack.message_event(ticket))
            if outcome.action == "verify_identity":
                print(f"  identity: NOT verified ({outcome.reason}), so no model call and no token")
            else:
                print("  identity: verified (Slack and the directory agree, account active)")
            if outcome.ticket is None:
                print(f"  not handled: {outcome.status} ({outcome.reason})")
                continue
            linear_ids[ticket.id] = outcome.ticket
            record = rt.tickets.get_ticket(outcome.ticket)
            print(f"  ticket {outcome.ticket} opened")
            for comment in record.comments:
                body = comment.body.removeprefix("tier0: ")
                print(_indent(f"- {body}", "  ").replace("\n  ", "\n    "))
            for token in rt.issuer.issued[issued_before:]:
                print(
                    f"  token: 5-minute access token for {token.ticket_id}, "
                    f"scope {' '.join(token.scopes)}, audience the MCP server only"
                )
            for sent in rt.slack.sent_messages[sent_before:]:
                print("  reply:")
                print(_indent(sent.text, "    "))
            print(f"  result: {record.identifier} is now '{record.state}' in {record.queue}")

        print()
        print("--- 3 days later: the sweeper closes tickets with no reply ---")
        rt.clock.advance(timedelta(days=3, minutes=1))
        for swept in rt.agent.sweep():
            print(f"{swept.ticket}: closed (Done) after 3 days with no reply")

        summaries = summarize(rt.tracer.finished_spans())
        rows = [("Ticket", "Linear", "Final state", "Queue", "Traces", "Model calls", "Tool calls")]
        for ticket in world.tickets:
            lid = linear_ids.get(ticket.id, "-")
            if lid == "-":
                rows.append((ticket.id, "-", "-", "-", "0", "0", "0"))
                continue
            record = rt.tickets.get_ticket(lid)
            s = summaries.get(lid)
            rows.append(
                (
                    ticket.id,
                    lid,
                    record.state,
                    record.queue,
                    str(s.traces if s else 0),
                    str(s.model_calls if s else 0),
                    str(s.tool_calls if s else 0),
                )
            )
        widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
        print()
        for n, row in enumerate(rows):
            print(" | ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip())
            if n == 0:
                print("-+-".join("-" * w for w in widths))
        print()
        print(f"Traces: {trace_file} (OTLP/JSON, one trace per work session, no message content)")
        print("All data is fake. Next: `make eval` grades these tickets against the answer key.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tier0.demo", description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--model", choices=("stub", "claude", "ollama"), default="stub")
    parser.add_argument("--out", type=Path, default=Path("out/demo"))
    args = parser.parse_args(argv)

    if args.model != "stub":
        print(f"WARNING: --model {args.model} may call a paid or local model.", file=sys.stderr)

    world = generate(args.seed)
    try:
        require_unsealed(world)  # the demo never unseals held-out v2 (ADR-015)
    except SealedSetError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    anyio.run(_run, world, args.model, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
