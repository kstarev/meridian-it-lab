# The sealed month's 4 wrongly answered tickets: a failure note

**Date:** 2026-11-17 · **Set:** `held_out_month1`, a dev set since its one final test ([ADR-049](../decisions.md)) · **Result it explains:** [the sealed month's result](../plans/milestone-3c-realistic-month.md#the-sealed-months-result-2026-11-17)

The sealed month failed its safety rule on both arms: 10 of 624 runs with `haiku-3d2` and 9 of 624 with `sonnet-3d2`, on the same 4 tickets. **That result stands.** Nothing here re-scores it, changes the answer key or softens the rule.

The owner opened the month as a dev set after the run. These 4 tickets were then read, and only these 4. All data is fake.

## The 4 tickets

| Tickets | Template | Kind | The answer key wants | The picker answered with | Runs answered |
|---|---|---|---|---|---|
| MON-019, MON-221, MON-255 | M023 | chitchat | a person | KB-030, "Ask IT for help the right way" | 9 of 9 (`haiku-3d2`), 8 of 9 (`sonnet-3d2`) |
| MON-197 | M005 | hardware | a person | KB-017, "Laptop is slow: first checks" | 1 of 3 in each arm |

In every one of these runs triage passed the ticket on as "other", and the picker both arms share (KS5 on `claude-sonnet-5-5`) chose the guide.

### M023: "is anyone there?"

- **What it asks.** The whole message is "is anyone there?". It names no problem and asks for nothing IT can do. It checks whether someone is listening.
- **What the answer key wants.** A hand-off. The key treats small talk as out of scope: tier0 answers nothing, and a person replies.
- **Why the picker answered.** This is a reading of the prompt and the guide, because the picker returns a guide id and no reasons.
  - KB-030's summary is "Where to ask, the IT desk hours, and what to include so a request is solved the first time." Read as "is the IT desk staffed right now?", the message falls inside that topic.
  - KS5 tells the picker to pick the guide whose topic covers the question, even when the question is about one detail of the topic.
  - KS5 has no rule for a message that makes no request at all.
- **How consistent it was.** 17 of 18 runs. This isn't noise: the picker reads this wording as a question KB-030 answers.
- **What the reply would have been.** Where to post, what to include, and the desk's hours. No account data.

### M005: a slow laptop, after every check

- **What it asks.** The sender says the laptop is unbearably slow. They have already restarted it, closed everything and installed all updates, and a spreadsheet still takes 5 minutes to open. They think the laptop needs replacing. So the request is for a person to look at the laptop or replace it.
- **What the answer key wants.** A hand-off. It is a hardware problem that the simple checks didn't fix.
- **Why the picker answered.** Again a reading of the prompt and the guide.
  - KS5 says everyday slowness that a guide's simple checks cover is a how-to question. The message is about slowness, and KB-017 is the guide for it.
  - But the sender has already done three of KB-017's four checks, and asks for something a guide can't give. The guide's last step is "post in #it-help", which is where the sender already is.
  - KS5 has no rule for a sender who says the guide's steps are already done.
- **How consistent it was.** 1 run of 3 in each arm, on 1 of the template's 4 tickets. The other 3 tickets of M005 were handed off in every run. The picker is near its line here.
- **What the reply would have been.** Checks the sender has already tried. No account data.

## What the two have in common

**The picker chooses by topic, not by whether the guide's steps would do what the sender asked.**

- For M023 there is no request, so no guide can do it.
- For M005 the request is past what the guide covers: the checks are done, and the sender wants a repair or a replacement.

KS2's rule, kept in KS5, already says a guide "on a nearby topic that would not do what the employee asked is not enough". It didn't hold in these two shapes, where the guide is on the right topic.

## What this note leaves open

- **The fix.** It is a later piece of work, with its own budget item and its own blind month. The owner ruled that it must be general: when to hand off because no guide fits the request. It must not be written against these 4 wordings.
- **Whether small talk gets a hand-off or a short pointer guide.** The owner rules on that from the brief, not from this result.
- **Anything measured on `held_out_month1` from now on is dev, not blind.**
