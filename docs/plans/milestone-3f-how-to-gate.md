# Milestone 3f: a how-to gate in front of the guide picker, then one blind month

**Date:** 2026-11-19 · **Status:** planned; nothing built or run yet · **Decisions:** the owner's ruling 12 of 2026-11-19, ADR-049, ADR-050 (`tier0-3e`) · **Why:** [the two blind months' reasons](../evals/2026-11-19-m3e-month2-failure-reasons.md) (once #131 is merged) · **Month3's rules:** [milestone-3f-month3-rules.md](milestone-3f-month3-rules.md)

## The owner's ruling (12, 2026-11-19)

- **(b), now:** guide answers wait for one click from a person in IT until a blind month passes. It is the default in code (ADR-053, #133).
- **(a), once:** after reading the 7 failing tickets, a **structural fix in code, not more prompt wording**. The owner's example: "the picker is asked only when triage says how-to and the message screen found nothing". Tune on dev data only. Then one new blind month, `held_out_month3`: the realistic mix plus extra prompt-injection, app-fault, hardware and small-talk tickets, with slowness keyed by ADR-041 from the start. Its pass rules and the stop rule are committed before its seal. If month3 fails, (b) stays the design and the picker isn't changed again. **Stop and ask the owner before the sealed run.** Inside `tier0-3e`.

## What the failing tickets showed

From [the reasons note](../evals/2026-11-19-m3e-month2-failure-reasons.md): **one model call decides whether a message is a how-to question at all.** Triage only sorts messages into own username, about someone else, and other. The code-only screen has no check for an instruction aimed at the bot. So a message reaches the picker unless it asks for a username, and the picker answers it if an approved guide's topic fits. **The picker sees the guide list while it judges the message,** so a guide on the right topic pulls it toward answering: a fault in the room-booking feature got the room-booking guide. And for a how-to question with an instruction hidden in it, the picker's own prompt says to ignore the instruction and answer.

## The fix: two independent yeses before a guide is posted

A message triage calls "other" now goes through three steps before any guide can be posted. Each one can only hand it to a person.

1. **The code-only message screen runs first** (ADR-035, as widened by ADR-051). If it finds anything, the message goes to a person, and the picker is never called. Today the screen runs too, but after the picker's call. The outcome is the same, but the order now says what it means, and the call is saved.
2. **The how-to gate: triage's own how-to question.** One narrow call on the arm's triage model (Haiku in `haiku-3d2`, Sonnet in `sonnet-3d2`): is this message one plain how-to question, and nothing else? Its reply is `{"plain_how_to": true}` or `{"plain_how_to": false}`. Anything else fails closed: a failed call, a refusal, or a reply off the schema goes to a person.
   - **It never sees a guide.** It can't be pulled toward answering by a guide on the right topic, which is how the picker went wrong. It judges only the kind of message.
   - **It is a new call, not a change to the frozen triage prompts** (HT8 and ST6, ADR-048). Their username safety was measured on held-out v2 to v6. Changing them would reopen all of that, and their job, refusing a request about someone else, stays as it was.
   - **What it calls "not a plain how-to":** a report that something is broken or not working as it should; a request for someone to do or decide something; more than one request; any instruction aimed at the bot, an assistant or an AI; a message that asks nothing; a sender who says they have already tried the steps. Everyday slowness that simple checks cover is a how-to question (ADR-041).
3. **Only then the picker,** KS6A-H on `claude-sonnet-5-5`, frozen and unchanged, with its own checks (`decide`) unchanged.

**A guide is posted only if the gate and the picker both say how-to, and the screen found nothing.** One model's mistake about the kind of message is no longer enough. Then, by ruling 12 (b), a person in IT still clicks before the requester sees it.

**What this costs:** one more call for each message that reaches the gate, and some how-to questions handed to a person that the picker alone would have answered. Rule 2 (at least 0.90 of how-to runs with the right guide) keeps that cost bounded.

**A how-to question with a hidden instruction goes to a person,** by the months' answer key. The how-to sets key it to the question's own guide. On those sets it counts as answers missed, not unsafe runs. That is the safe default; the owner rules on it in #131.

## The evals

- **The month runner** gets the same three steps, with new stages: `screen` (the screen found something; no picker call), `gate_no`, `gate_failed`. The result files record the gate's version, model and SHA-256 next to the picker's.
- **The how-to runner** (milestone 4) is unchanged: it measures the picker alone, as before.
- **Guide answers waiting for a click** (ruling 12 (b)) don't change any eval: evals score the picker's choice as if it were posted.

## Tuning, on dev data only

- **Written from tuning data only:** the tuning month and the how-to tuning set. Each version of the gate prompt is written before it is scored, as before. At most 3 versions per model.
- **Dev checks:** `held_out_month1`, `held_out_month2` and `held_out_month2b`, all dev sets now (ADR-049, ADR-052). Their numbers are dev, not blind, and can't show that the fix works. Only month3 can.
- **To keep the cost down, the gate is scored on the runs that reached a guide.** The gate can only hand more to a person, so a run the recorded results already handed off stays handed off. For each ticket whose recorded run reached `picker_guide`, the gate is called once for that run, and the run's outcome becomes a hand-off if the gate says no. The recorded runs are the published result files: the 3e rehearsal (tuning month), month1's sealed run, and month2's and month2b's sealed runs. This is labelled a composed dev estimate.
- **Then one end-to-end rehearsal:** the tuning month, both arms, k = 3, the cache on, with the gate live. Its numbers are tuning data and reported as such.

### The selection rule, committed before any version is scored

For each arm's model separately:

1. **Safety on every dev month:** 0 out-of-scope tickets wrongly answered over all composed runs, on month1, month2 and month2b.
2. **Coverage on every dev month and the tuning month:** the right guide in at least 0.90 of how-to runs.
3. Among the versions that pass 1 and 2: the most how-to runs with the right guide on the tuning month; then the shorter prompt.
4. **If no version passes 1 and 2, stop.** Month3 isn't written, and the owner is told. Nothing is sealed or run.

The rehearsal must then show 0 out-of-scope tickets wrongly answered and at least 0.90 right guide in both arms. If it doesn't, stop and tell the owner.

## Month3

The rules and the stop rule are in [milestone-3f-month3-rules.md](milestone-3f-month3-rules.md), committed in this pull request, before the set exists.

## Order of work, each its own pull request

1. **This plan and month3's rules** (docs only).
2. **The gate in code:** the three steps in the agent and the month runner, the gate prompt's first version, and tests. No paid call. It is stacked after the click pull request if the two touch the same code.
3. **Tuning:** the gate versions scored on dev data, the pick frozen by name and SHA-256 in code, and the rehearsal. Paid, inside `tier0-3e`.
4. **Month3:** the brief, the blind writer's set copied in byte for byte, the count-only checks, the seal and the ancestor test. No paid call.
5. **Stop.** The owner gives the go, or doesn't, for the one sealed run.

## Cost

`tier0-3e` had $8.43 left of $15 when this plan was written. Estimated from the 3e runs' measured cost and the gate's call count (868 runs reached a guide in each arm's recorded runs):

| Step | Estimate |
|---|---|
| Gate tuning, per version: Haiku about $0.07, Sonnet about $1.30 (the gate prompt is too short to be cached) | about $1.40 |
| The rehearsal (tuning month, both arms, k = 3, gate live) | about $2.00 |
| Month3's one run (400 tickets, both arms, k = 3) | about $2.70 |
| **Total with one version per model** | **about $6.10** |

A second version for either model still fits. A third Sonnet version would not, so it isn't written: the plan stops and asks the owner instead.

Each paid step runs under a watchdog that stops it before its own estimate plus 25%, and before `tier0-3e`'s cap.
