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

This is a stand-alone path for now. Wiring it into the Slack flow (after triage says
"other") is left for a later step.
"""
