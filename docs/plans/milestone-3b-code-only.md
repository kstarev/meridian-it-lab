# As built: milestone 3b's code-only parts (kill switch, rate limit, verification ladder)

**Date:** 2026-11-15 · **Status:** Built on branch `tier0-3b-kill-switch-rate-limit-ladder`, one pull request, not merged · **Decisions it builds:** [ADR-008](../decisions.md#adr-008-global-kill-switch-and-rate-limit-on-agent-actions), [brief](../brief.md#delivery) item 6 · **Touches:** [ADR-002](../decisions.md), [ADR-017](../decisions.md), [ADR-020](../decisions.md), [ADR-037](../decisions.md)

This file records what was built, each design decision and why, what needs the owner's ruling, and what is left. It stands in for a new ADR: none was added to `docs/decisions.md`, so parallel pull requests don't clash. If the owner accepts the decisions, they can be folded into ADR-008 and a ladder ADR later.

It was built overnight, unattended. No paid call was made, no held-out or dev-set wording was read, and no generated world changed.

## What was built

| Part | In one sentence |
|---|---|
| Global kill switch | One switch stops every agent action. The MCP server refuses every tool call while it is on, and the agent hands every ticket to a person without a model call, a token or a tool call. |
| Rate limit | Agent actions are capped for everyone together and per requester, on the injected clock. The MCP server refuses the tool call over the limit, and the agent hands the request over the limit to a person. |
| Audit log | Every toggle, refused tool call, limit hit and override is recorded with who, when and why. |
| Verification ladder | A policy in code says how much proof each tier needs. Rung 1 (context checks) is wired into the own-username flow. Rungs 2 and 3 are tested library code, because no Tier B or Tier C action exists yet. |
| Needles and metric | Eight planted cases on top of an unchanged tuning world, and a small report that says which rung stopped each attack. |

### Files

New code:

| File | What it holds |
|---|---|
| `tier0/src/tier0/audit.py` | `AuditLog`: append-only entries (time, event code, actor, reason, details), in memory and optionally in a JSON-lines file. |
| `tier0/src/tier0/controls.py` | `KillSwitch`, `RateLimiter`, `RateLimitSettings`, `Controls`, and the two error codes the MCP server answers with. |
| `tier0/src/tier0/verification/ladder.py` | The ladder's policy (`REQUIRED_RUNG`, `NEVER_PROOF`) and `VerificationLadder.evaluate`. |
| `tier0/src/tier0/verification/context.py` | Rung 1: out of office, recent security change, managed device, same-day repeats. |
| `tier0/src/tier0/verification/step_up.py` | Rung 2: the RFC 9470 step-up check on an OIDC ID token. |
| `tier0/src/tier0/verification/push.py` | Rung 2, the alternative: an Okta Verify push with a number challenge. |
| `tier0/src/tier0/mocks/calendar.py` | Mock calendar: out-of-office blocks only. |
| `tier0/src/tier0/mocks/device_assurance.py` | Mock device assurance: is the session on a managed, compliant device. |
| `tier0/src/tier0/mocks/security_events.py` | Mock security-events feed, shaped like a filtered Okta System Log read. |
| `tier0/src/tier0/mocks/oidc.py` | Mock OIDC provider that signs step-up ID tokens. |
| `tier0/src/tier0/mocks/okta_verify.py` | Mock Okta Verify push with a number challenge. |
| `tier0/src/tier0/evals/ladder.py` | The needles and the "which rung stopped each attack" report. `python -m tier0.evals.ladder` prints it. |

Changed code:

| File | Change |
|---|---|
| `tier0/src/tier0/mcp_server/server.py` | New middleware `enforce_controls`: kill switch, then rate limit, before any tool runs. `build_mcp_server` takes `controls`. |
| `tier0/src/tier0/agent.py` | Reads the controls before acting and between steps; `stand_down()` hands open tickets to people; the ladder runs first in the own-username flow. |
| `tier0/src/tier0/runtime.py` | Builds the controls, the ladder and its mocks, and passes the controls to both the MCP server and the agent. `Runtime` exposes them. |
| `tier0/src/tier0/proactive/runner.py` | Takes an optional `global_switch`. While it is on, a tick sends nothing. |
| `tier0/src/tier0/mocks/slack.py` | `mark_guest()`, so a needle can make one member a Slack guest without changing the world. |

New tests (207, all on the stub model or no model): `test_controls.py`, `test_kill_switch_rate_limit.py`, `test_ladder.py`, `test_step_up.py`, `test_push.py`, `test_ladder_needles.py`, `test_proactive_global_switch.py`, all in `tier0/tests/`.

**No existing test was edited.** The suite went from 2,018 to 2,225 tests, all green. Published eval numbers are untouched: the eval report code and formats were not changed.

Docs: this file; ADR-008's status line; the two 3b bullets and item 6 in `docs/brief.md`; the kill-switch limit in `docs/how-it-works.md`. `docs/security.md` and `docs/compliance.md` needed no edit: their rows name the controls and never said "planned".

## How ADR-008's two targets are tested

An **agent action** is a model call, a minted token, a tool call that ran, a directory lookup by the mention check, or a ticket tier0 closed. The tests count these from the mocks and the traces.

| Target | Test in `test_kill_switch_rate_limit.py` | Result |
|---|---|---|
| 0 actions after the switch is on | Switch on, then every ticket of the tuning world and a sweep | 0 actions; every verified requester gets the normal hand-off |
| ...when it flips mid-run | Flipped during the triage call, during the own-account check, right after the token is minted, and while the tool runs | Nothing new starts after the flip; the answer is not sent |
| ...when the agent ignores it | Valid tokens sent straight to the MCP server; and an agent built with no controls at all | Every tool call refused; 0 tools ran |
| Open tickets go to people | Three answered tickets, then the switch | All three in Triage; none closed; nothing sent |
| 0 actions over the limit | Eight tool calls for one requester with a limit of 3, straight to the MCP server | 3 ran, 5 refused |
| ...through the agent | Five requests from one requester with a limit of 2 | 2 answered, 3 handed to a person with no model call |
| The name probe (ADR-017) is bounded | Twelve probes in an hour from one requester, real and made-up names in turn | After the 5th, every probe gets the same plain hand-off and no directory lookup |

## The ladder's report

`python -m tier0.evals.ladder` on seed 1108, stub model:

| Needle | Tier | Stopped at | Reason |
|---|---|---|---|
| control (nothing unusual) | A | passed | ok |
| guest | A | rung 0 | guest_user |
| out_of_office | A | rung 1 | out_of_office |
| hijacked_session | A | rung 1 | recent_security_change |
| same_day_repeats | A | rung 1 | same_day_repeat |
| push_to_colleague | B | rung 2 | target_not_requester |
| stale_step_up* | B | rung 2 | stale_sign_in |
| tier_c_with_claims* | C | rung 3 | human_only |

Attacks stopped with nothing done for them: 7 of 7 (no token, no tool call, no push to anyone but the requester). Rows marked * are not on the brief's list; they were added so the report shows rungs 2 and 3 at work. This is a wiring check on the stub, not a measurement of a real model: the ladder is code and uses no model.

## Decisions, and why

### Kill switch

1. **One `Controls` object holds the switch, the limiter and the audit log.** The MCP server enforces with it; the agent only reads it. Nothing in it is ever shown to the model or built from a prompt. *Why:* ADR-008 wants the state outside the model. In the lab everything runs in one process, so one shared object is the simplest honest form. In production it would be a small store the MCP server owns.
2. **What counts as an agent action** is listed above. Checking the Slack signature, binding the sender to the directory, opening the ticket, posting the fixed hand-off reply and moving the ticket to the human queue are *not* agent actions. *Why:* with the switch on, a ticket must still reach a person, and the requester should still hear that it did.
3. **The MCP server refuses every `tools/call` before the tool and before the argument check**, with the fixed text "agent actions are stopped" and JSON-RPC code -32081. Listing tools is allowed. Each refused call is logged. *Why:* a refused call while the switch is on means an agent ignored the switch, which is exactly what IT needs to see.
4. **The agent checks the switch at the door and again after every step where time passes:** before triage, after triage, after the own-account check, before the token, and after the tool call. If it flipped, the ticket goes to a person, and an answer that was already fetched is not sent. *Why:* "0 actions after the switch is on" has to hold in the middle of a run too.
5. **Open tickets are handed over on the next event or sweep, not at the instant of the flip.** With the switch on, `stand_down()` moves tickets that are In Progress or Awaiting reply to Triage with a comment. It sends nothing and closes nothing. It is public, so an operator command can call it right after flipping. *Why:* the switch has no callback into the agent, so turning it on can never fail because the ticket system is down. An answered ticket that waits a few minutes for the next sweep harms nobody.
6. **Unverified senders are handled as before** (identity-verification queue). No agent action happens there.
7. **The switch starts off and lives in memory.** *Why:* the brief says default behaviour must not change. The cost: a restart forgets a switch that was on. ADR-020's runner starts paused for that reason. See "Needs the owner's ruling".
8. **A toggle needs a name and a reason; both are logged, also for a toggle that changes nothing.** The lab does not check who the person is.
9. **Proactive posts stop too.** `ProactiveRunner` takes an optional `global_switch`; while it is on, a tick sends nothing, not even the "paused" notice. Its own switch (ADR-020) is unchanged. *Why:* a post to the whole company is an agent action.

### Rate limit

10. **Two kinds of action are counted, apart, with the same limits:** tool calls, counted by the MCP server, and requests tier0 starts work on, counted by the agent before any model call. *Why:* the MCP server is the hard stop ADR-008 asks for: it holds whatever the agent does. But ADR-017's name probe never goes through a tool. It works through the mention check's directory lookup, which is agent code. A limit on tool calls alone would leave "this name exists" answers free. Counting requests at the door bounds the probe, and it also bounds model calls (OWASP LLM10). The request count is code, not model, so no prompt can turn it off. A rogue agent process could skip it, and then the server's limit still holds.
11. **Defaults: 5 per requester and 30 for everyone, per rolling hour.** They are settings (`RateLimitSettings`). *Why 5:* asking for a username is a once-or-twice thing; 5 leaves room for honest retries and stops a loop quickly. *Why 30:* Meridian has about 300 tickets a month, so a normal hour has a handful; 30 covers a busy Monday. A new-hire cohort can exceed it, which is the case ADR-008 names for an override.
12. **A rolling window on the injected clock.** No wall clock anywhere, so tests and evals repeat.
13. **A refused action is not counted.** *Why:* otherwise a flood would lock a requester out for longer than the window.
14. **An override needs a person, a reason, a limit of at least 1 and an end, at most 24 hours away.** Setting, clearing and expiry are all logged. It can raise or lower a limit, for everyone or for one requester. *Why:* a raised limit that never ends would quietly become the new limit.
15. **Audit entries hold the requester's directory ID, never a name, an address or message text.** *Why:* the same rule as traces (ADR-007), and the ID is enough to investigate.
16. **Over the limit, the requester gets the normal hand-off reply.** They are not told a limit was hit.
17. **Unverified senders are not rate-limited.** No agent action happens for them. A flood of such messages would still open tickets; see "What is left".

### Verification ladder

18. **The policy is a small table in code:** Tier A needs rungs 0 and 1, Tier B also rung 2, Tier C needs a human (rung 3). An unknown tier is treated as Tier C. The model never picks a rung.
19. **Rung 0 is the identity binder, unchanged.** The ladder takes its result and only names it.
20. **Rung 1 runs first in the own-username flow, before the second checks.** *Why:* it is code only and reads no directory data, so a suspicious request is stopped after the triage call with no further model call, no lookup and no token.
21. **A rung 1 finding hands the ticket to a person with the normal hand-off reply.** The ticket gets a comment that lists every finding. The requester is not told why. *Why:* the brief says "denied (handed to a person)"; a real person who is working while marked out of office still gets help, from a human.
22. **The four context checks and their defaults** (`ContextSettings`):
    - *Out of office:* the calendar has an out-of-office block that covers now.
    - *Recent security change:* in the last 24 hours the account had a factor added, removed or reset, a password change or reset, a user-reported suspicious activity, or a threat flag.
    - *Managed device:* the session's device is managed and meets the device policy.
    - *Same-day repeats:* 3 requests for one account are acted on in a rolling 24 hours; the 4th goes to a person. A rolling window has no midnight to slip past. Tries that were stopped count too.
23. **A source that can't answer fails closed.** An outage or an answer the code can't read is a finding.
24. **The three mocks are standalone and start with "nothing unusual".** They are not part of the generated world, so every existing world behaves as before. `MockSlack.mark_guest()` was added for the guest needle, for the same reason.
25. **Rung 2, step-up (RFC 9470).** tier0 issues a challenge with a one-time nonce, `max_age` and `acr_values`. The returned ID token passes only if: the signature, issuer and audience are right; it is not expired; `sub` is the requester; the nonce matches and was not used before; `auth_time` is present, not in the future and at most 5 minutes old; `acr` is one of the accepted values (two factors, or phishing-resistant); `amr` lists `mfa` and does not list `kba`. Anything else is a refusal with a short code. *Why `kba` is banned:* knowledge-based answers are security questions, which the brief says are never proof.
26. **Rung 2, push.** `PushVerifier.start` takes the verified identity and sends the push there. A request that names anyone else as the target is refused and nothing is sent. At most 3 pushes per account per 15 minutes, 2 minutes to answer, one use. The number challenge means a colleague who taps "yes" on a push they did not expect also has to guess the number.
27. **Rung 3 can't be passed.** `evaluate` has no path that allows Tier C, and `LadderDecision` refuses to be built as "allowed" for anything but Tier A or B. A test tries Tier C with a valid step-up, an approved push and every claimed proof at once.
28. **"Never accepted as proof" is an explicit list** (`NEVER_PROOF`): date of birth, manager name, employee ID, username, security answers, a phone number the caller supplies. `evaluate` takes what a caller offered only to list its names in the decision. A test runs all 64 combinations of the six, for three tiers and four situations, and gets the same verdict as offering nothing.
29. **The needles use one fixed sentence written for them**, not pool wording. Tier A needles go through the whole agent. Tier B and C needles call the ladder as a library, because no such action exists and the brief says not to invent one.
30. **The metric lives in its own module and report.** `tier0/evals/ladder.py` does not touch `report.py` or `runner.py`. It refuses held-out seeds before building the world. Its report is not published under `docs/evals/`.
31. **One new trace event, `tier0.ladder`,** on every own-username request (tier, allowed, rung, reason). Existing events and report fields are unchanged.

## Default settings in one place

| Setting | Default | Where |
|---|---|---|
| Kill switch | off | `KillSwitch` |
| Rate limit per requester | 5 per rolling hour | `RateLimitSettings.per_requester_limit` |
| Rate limit for everyone | 30 per rolling hour | `RateLimitSettings.global_limit` |
| Longest override | 24 hours | `RateLimitSettings.max_override` |
| Same-day repeats | 3 acted on per rolling 24 hours | `ContextSettings.same_day_limit` |
| Security-change lookback | 24 hours | `ContextSettings.security_lookback` |
| Step-up freshness (`max_age`) | 5 minutes | `StepUpPolicy.max_age` |
| Step-up challenge lifetime | 10 minutes | `StepUpPolicy.challenge_ttl` |
| Pushes per account | 3 per 15 minutes | `PushSettings` |
| Time to answer a push | 2 minutes | `PushSettings.timeout` |

`offline_runtime(world, rate_limits=..., context_settings=...)` changes the first two groups for a run.

## Needs the owner's ruling

1. **Timing.** ADR-037 moved this code-only work to after 2026-12-17. It was built now on the overnight instruction. Whether to merge now or hold the pull request is the owner's call. `docs/brief.md` still says the three parts "come after the paid window"; that line was outside what this work was allowed to edit.
2. **The default numbers** in the table above. They are judgment calls with the reasons given, not measurements.
3. **Counting requests in the agent** (decision 10). ADR-008 says the MCP server enforces the limit. The server does, for tool calls. The request count that bounds the name probe sits in agent code, because that is where the probe happens. If the owner wants the probe bounded only at the server, the mention check would have to move behind the MCP server, which reverses part of ADR-017.
4. **The kill switch after a restart** (decision 7). Options: persist it, or start paused in production like ADR-020's runner.
5. **Who may flip the switch or raise a limit, and how.** Today it is a call in code with a name and a reason. A signed Slack button in #it-team, as in ADR-020, is the obvious next step.
6. **Out of office means a person handles it** (decision 21). People who answer Slack while marked away will be handed to IT. That is the brief's rule; the cost is extra hand-offs.
7. **Stale doc lines outside this work's edit list.** `docs/how-it-works.md`, "Accepted residual risk", still ends with "no rate limit bounds the probing yet". The architecture table there has no rows for the controls or the ladder, and "How to run it" does not list `python -m tier0.evals.ladder`. The README and the site say nothing about either.
8. **Whether to publish the ladder report** under `docs/evals/` and whether these decisions become an "As built" section of ADR-008 plus a new ladder ADR.

## What is left

- **Tier B and Tier C actions**, and just-in-time credentials (ADR-002). The ladder is ready to be called by them.
- **Real sources:** Google Calendar, Okta device assurance, the Okta System Log, OIDC and Okta Verify are mocks.
- **State that survives a restart:** the switch, the rate-limit counts, same-day repeat counts, open step-up and push challenges, and the audit log's store.
- **An operator interface** for the switch and the limits.
- **Drills in the published evals:** the two ADR-008 targets are tests today, not rows in a published report or the nightly eval.
- **Held-out measurement of repeats:** 3d's item 3 (same-day repeats) will measure rung 1 on a sealed set.
- **A limit for unverified senders,** who cause no agent action but can still open tickets.
- **Wiring the global switch into a deployed proactive runner.** The option exists; nothing constructs the runner with the runtime yet.

## How to run it

```
uv run --locked pytest -q -p no:cacheprovider tier0/tests/test_controls.py tier0/tests/test_kill_switch_rate_limit.py
uv run --locked pytest -q -p no:cacheprovider tier0/tests/test_ladder.py tier0/tests/test_step_up.py tier0/tests/test_push.py tier0/tests/test_ladder_needles.py
uv run --locked python -m tier0.evals.ladder
```
