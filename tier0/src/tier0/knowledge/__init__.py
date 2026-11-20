"""Milestone 4, Knowledge (ADR-035): answer a how-to question from an approved guide.

The model only picks. It sees the message and the list of approved guides (id, title and one
line each), and returns the kind of request and one guide id or "none". Code decides what
happens: it posts the picked guide's approved steps word for word, with a link, or it hands
the message to a person. The model never sees a guide's steps and never writes a reply.

    picker.py   the model request, its strict parser and the list of guides shown
    screen.py   a code-only signal for requests that must never be answered from a guide
    answer.py   the decision and the reply, both in code
    prompts.py  the picker's prompt versions, tuned on tuning data only
    sealed.py   the sealed how-to test: nothing runs on it before its final test
    guide_screen.py  a code-only check that a guide's text holds no instruction or trap
    wiring.py   the path in the Slack flow, after triage says "other"; off by default
    gate.py     the how-to gate, asked on triage's own model before the picker (milestone 3f);
                on in the Slack flow with G3 (wiring.HOW_TO_GATE_VERSION), from the 3f dev tuning
    review.py   the draft in #it-team and its buttons: a guide is posted only after one click
                from a person in IT (ADR-053, on by default)

The weekly job that drafts guides from resolved tickets (ADR-005) lives here too:

    drafting.py the job and its command: tickets in, draft pull requests out, on the stub
    scrub.py    removes personal details before anything reaches the drafting model

The Slack flow uses it only when the agent is given a `GuidePicker` (the `guide_picker`
option, default off), on the mocks; off, every "other" goes to a person as before. On, a chosen
guide waits for one click from a person in IT until a blind month passes (ADR-053).
"""
