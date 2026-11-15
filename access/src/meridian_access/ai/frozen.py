"""The prompts of the final held-out score, frozen by SHA-256 (AAC-006, milestone 7).

The final run sends three kinds of call, all to the chosen model:

- `title`: a role for a job title (`titles.py`);
- `owner`: an owner for an account the rules left unmatched (`matching.py`);
- `summary`: the plain-English paragraph of a plan (`summary.py`).

For each kind four things are pinned:

- `system`: the system prompt, hashed as its exact UTF-8 bytes;
- `user`: the user message built from fixed probe inputs (fake data), so every fixed word of the
  template and every field it sends is covered;
- `schema`: the JSON schema sent as structured output, built from the same probe inputs;
- `request`: the whole request `messages.create` gets for the probe, with the chosen model, the
  token limit and the thinking setting.

The data in a real call (a title, the candidates, the plan) is not pinned, only the text and
shape around it. The policy's role list is pinned elsewhere: it is built from the public table
(`test_roles_yaml_equals_the_public_markdown_table`).

`problems()` lists what differs from the pins; the final run refuses unless it is empty.
`FrozenClaudeClient` refuses any other model, and any call whose system prompt or token limit is
not one of the three pinned ones. To change a prompt, change the pin in a reviewed pull request;
after the final score, that means a new sealed world (AAC-005).
"""

import hashlib
import json
from typing import Any

from meridian_access.ai import matching, pricing, titles
from meridian_access.ai.client import DEFAULT_MAX_TOKENS, ClaudeClient, request_params
from meridian_access.ai.spend import SpendLedger
from meridian_dataset.access.model import DOMAIN, HrisRecord

# The model the prompts were frozen with: the owner's pick of 2026-11-15 (AAC-006).
FROZEN_MODEL = "claude-haiku-5-5"
KINDS = ("title", "owner", "summary")
PARTS = ("system", "user", "schema", "request")

# Frozen on 2026-11-15 for the final held-out score (access/docs/m7-prep.md).
PINNED_SHA256: dict[str, str] = {
    "owner.request": "af829e688054fe887f9429d59937f873f1d06d3cee96a2993d87f1a5bc28b342",
    "owner.schema": "e117eafd019103a826bed8d168c564e1fa5786fb24f03a20002ba165afb01f48",
    "owner.system": "f37b44fe503ec699b5ec8772efcd51e906ad21f0030e108c51da6ac554c5b6ec",
    "owner.user": "d49e6a7f077f1d3ad3c641397a0299ff1f76ed0e1ba352ad611b77548f57e665",
    "summary.request": "06b6313877527749b3381b6df39e99d9ca15023be430836e88d2853d5775c91d",
    "summary.schema": "34ebde27d47aa44c7e7128cd27b29bb486b28f90959eac4209d20a86a4444e2e",
    "summary.system": "6ce3944b119b1ad0ff2a5bb801aa2555d0ececfa04b2ec96cbee0dc01e1a3fce",
    "summary.user": "89ba600d1dd352a4548d28fe8f228e4f85af7252863c41d73c5e5b8aa14a04fc",
    "title.request": "a9a630f5258c0ccc0face04631a1e2010c7c98a56d0729191c60e52843a8c0bb",
    "title.schema": "4c3a72b74622c422779571ed407cbe7a76b1437fedec6be6db4cc17ceec4d7dc",
    "title.system": "5ec9d59ce662e983919f7d231e819019e5df304ff79f7a490b7324081597af69",
    "title.user": "c0e4c0ded4e1aedd8754220d4984d726cfb08093fc75bfc859e5b2f372eef6c3",
}
# SHA-256 of the lines "<name> <hash>\n" of PINNED_SHA256, sorted by name: one number to confirm.
FREEZE_DIGEST = "f7f0d297e8890550ce1ccdef24f97fcb54f002148c16f15eb270051bf9c39005"

# Fixed probe inputs: fake, and never a real person.
PROBE_ROLES = ("probe-role-a", "probe-role-b")
PROBE_TITLE = ("Probe Title", "Probe Department", "Probe Team")


class PromptsNotFrozen(RuntimeError):
    """A prompt, schema, token limit or model differs from the frozen one; nothing is sent."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(data: object) -> str:
    """SHA-256 of canonical JSON: sorted keys, no spaces, UTF-8."""
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_text(text)


def _probe_record(n: int) -> HrisRecord:
    return HrisRecord(
        employee_id=f"E-PROBE-{n}",
        given_name="Probe",
        family_name=f"Person{n}",
        preferred_name="",
        work_email=f"probe.person{n}@{DOMAIN}",
        personal_email=f"probe{n}@personal.example",
        job_title="Probe Title",
        department="Probe Department",
        team="Probe Team",
        manager_id="",
        country="XX",
        hub="Probe Hub",
        employment_type="employee",
        status="active",
        start_date="2026-01-01",
        end_date="",
        leave_start="",
        leave_end="",
    )


def _probe_plan() -> Any:
    from meridian_access.plan import Plan
    from meridian_access.summary import TYPE_WORDS

    change = {
        "type": "drift",
        "employee_id": "E-PROBE-1",
        "account": f"probe.person1@{DOMAIN}",
        "system": "okta",
        "app_id": "probe-app",
        "app_name": "Probe App",
        "action": "remove",
        "reason": "probe reason",
    }
    by_type = dict.fromkeys(sorted(TYPE_WORDS), 1)
    return Plan({"counts": {"total": len(by_type), "by_type": by_type}, "changes": [change]})


def prompt_parts() -> dict[str, dict[str, Any]]:
    """Per kind of call: the system prompt, the probe user message and schema, and the token
    limit, built by the same functions the run uses."""
    from meridian_access import summary
    from meridian_access.models import Account

    account = Account(
        system="probe-app",
        account="probe.person1",
        email=f"probe.person1@{DOMAIN}",
        given="Probe",
        family="Person1",
        display="Probe Person1",
    )
    candidates = [_probe_record(1), _probe_record(2)]
    return {
        "title": {
            "system": titles.SYSTEM,
            "user": titles.title_prompt(*PROBE_TITLE, PROBE_ROLES),
            "schema": titles.title_schema(PROBE_ROLES),
            "max_tokens": DEFAULT_MAX_TOKENS,
        },
        "owner": {
            "system": matching.SYSTEM,
            "user": matching.match_prompt(account, candidates),
            "schema": matching.match_schema([c.employee_id for c in candidates]),
            "max_tokens": DEFAULT_MAX_TOKENS,
        },
        "summary": {
            "system": summary.SYSTEM,
            "user": summary.model_prompt(_probe_plan()),
            "schema": summary.SCHEMA,
            "max_tokens": summary.MAX_TOKENS,
        },
    }


def current_hashes() -> dict[str, str]:
    """The hashes of what the code would send now, named `<kind>.<part>`."""
    out: dict[str, str] = {}
    for kind, p in prompt_parts().items():
        request = request_params(
            pricing.CHOSEN_MODEL, p["system"], p["user"], p["schema"], p["max_tokens"]
        )
        out[f"{kind}.system"] = sha256_text(p["system"])
        out[f"{kind}.user"] = sha256_text(p["user"])
        out[f"{kind}.schema"] = sha256_json(p["schema"])
        out[f"{kind}.request"] = sha256_json(request)
    return dict(sorted(out.items()))


def freeze_digest(hashes: dict[str, str]) -> str:
    return sha256_text("".join(f"{name} {h}\n" for name, h in sorted(hashes.items())))


def problems() -> list[str]:
    """Why the run must not go ahead: names only, never prompt text. Empty when frozen."""
    out: list[str] = []
    if pricing.CHOSEN_MODEL != FROZEN_MODEL:
        out.append(f"the chosen model is {pricing.CHOSEN_MODEL}, not the frozen {FROZEN_MODEL}")
    now = current_hashes()
    for name in sorted(set(PINNED_SHA256) | set(now)):
        if PINNED_SHA256.get(name) != now.get(name):
            out.append(f"{name} differs from its pinned hash")
    if freeze_digest(PINNED_SHA256) != FREEZE_DIGEST:
        out.append("the freeze digest does not match the pinned hashes")
    return out


def require_frozen() -> None:
    issues = problems()
    if issues:
        raise PromptsNotFrozen("; ".join(issues))


def kind_of(system: str) -> str | None:
    """Which kind of call a system prompt belongs to, by its pinned hash; None if unpinned."""
    digest = sha256_text(system)
    return next((k for k in KINDS if PINNED_SHA256.get(f"{k}.system") == digest), None)


class FrozenClaudeClient(ClaudeClient):
    """A client for the final run: the frozen model only, and only calls whose system prompt
    and token limit are pinned. Anything else raises PromptsNotFrozen before the spend check,
    so nothing is sent."""

    def __init__(
        self, model: str, client: Any | None = None, ledger: SpendLedger | None = None
    ) -> None:
        if model != FROZEN_MODEL or pricing.CHOSEN_MODEL != FROZEN_MODEL:
            raise PromptsNotFrozen(f"the final run uses {FROZEN_MODEL} only, not {model}")
        super().__init__(model, client=client, ledger=ledger)
        self._limits = {k: p["max_tokens"] for k, p in prompt_parts().items()}

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> dict[str, Any] | None:
        kind = kind_of(system)
        if kind is None or self._limits[kind] != max_tokens:
            raise PromptsNotFrozen("a call whose system prompt or token limit is not frozen")
        return super().complete_json(system, user, schema, max_tokens)
