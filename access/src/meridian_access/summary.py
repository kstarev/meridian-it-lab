"""The plain-English summary of a plan (AAC-006): the model interprets, code checks.

`template_summary` is deterministic. `model_summary` asks the model for one paragraph in which
every number is a placeholder (`{total}`, `{held}` for bad HR data, `{held_plan}`, `{approvals}`,
`{count:<type>}`); code fills
them in from plan.json, so a number can never be wrong or carry the wrong label. The paragraph
is accepted only if it has no raw digit, no spelled-out number, no unknown placeholder, and
every word is in a closed allowlist (common English words, the finding-type words, the systems,
and the app names and change local parts of this plan). Anything else, any error, or no
answer, gives the template.
"""

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from meridian_access.ai.client import ClaudeClient
    from meridian_access.plan import Plan

MAX_LINES = 30
MAX_CHARS = 1200
MAX_TOKENS = 500  # the summary call's output limit (frozen for the final score)
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"paragraph": {"type": "string"}},
    "required": ["paragraph"],
    "additionalProperties": False,
}
SYSTEM = (
    "You summarise an access-change plan for IT staff in one short plain-English paragraph "
    "(at most 120 words). Write every number only as one of the placeholders you are given, "
    "such as {total} or {count:drift}, exactly as written; never write a digit or a number "
    "word, and never write a date. Refer to people only by the names shown (work-email local "
    "parts) and to apps by the names shown. Do not add advice, guesses or any other detail."
)
TYPE_WORDS = {
    "leaver_access_left": "leavers' access to remove",
    "orphaned_account": "orphaned accounts",
    "contractor_past_end": "contractor accesses past their end date",
    "leave_of_absence": "suspensions for people on leave",
    "mover_stale_access": "mover access changes",
    "rehire": "rehire rows to review",
    "drift": "drift items",
    "break_glass": "break-glass accounts left alone",
    "hris_mass_termination": "bulk HR changes blocked",
    "hr_data_error": "HR data errors",
}


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


NUMBER_WORDS = _words(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy "
    "eighty ninety hundred thousand million dozen"
)
# Common English words and plan vocabulary a summary may use. Nothing else gets through.
ALLOWED_WORDS = _words(
    """
    a about above across after again against all also although always am an and another any
    are as at be because been before being below between both but by can cannot could did do
    does doing done down during each either else even ever every few for from further had has
    have having he her here hers him his how however if in into is it its just last least less
    let like made make makes many may me might more most much must my need needs needed neither
    never new next no nor not now of off often on once only or other our out over own per
    plus same shall she should since so some still such than that the their theirs them then
    there these they this those through to too under until up upon us use used uses very was
    we were what when where whether which while who whom whose why will with within without
    would yet you your
    add added adds allow allowed apply applied applies approve approved approves ask asked
    await awaiting block blocked blocks call calls care carry change changed changes check
    checked checks clean close come comes confirm confirmed contain contains count counts
    cover covers cut data day days decide decision does drop dropped each end ended ends ensure
    expect expected expires find finds first fix flag flagged flags follow follows found full
    get gets give gives go goes good group grant granted grants hand handle has hold holds
    include included includes item items keep keeps kept know known later lead leads leave
    leaves leaving left lines list listed lists look looks lose loses lost mainly manual
    manually matter mean means mostly move moved mover movers moves moving name named names
    need open order orders pending person people plan plans please point present previously
    pull put puts raise raised raises ready reason reasons receive recently remain remains
    remaining remove removed removes removal removals report reported request requested
    require required requires result results return review reviewed reviews role roles run
    runs say says see seen send set sets show shown shows sign since small some start started
    step steps stop stopped suspend suspended suspends suspension suspensions take taken takes
    task tasks tell thing things time today total touch touched try turn turns unchanged
    unknown update updated wait waits want wants week weeks work works
    access accesses account accounts action actions admin app apps approval approvals approver
    approvers apps birthright change contractor contractors department directory employee
    employees export exports finding findings former hand holds list month moved owner owners
    policy pull request requests role roles row rows system systems team teams user users
    held back bad bulk import imports limit separate bucket buckets example examples
    around among along already almost appear appears affect
    affected affects available based certain currently different enough especially exactly
    following given happen happens important instead large main majority minor nothing others
    otherwise overall part partly possible rather really recent right several simple single
    soon sometimes somewhat specific sure therefore seems
    okta google slack hr sso
    """
)
_PLACEHOLDER = re.compile(r"\{[^{}]*\}")
_WORD = re.compile(r"[^\W\d_]+(?:[._'\u2019-][^\W\d_]+)*")
_SPLIT = re.compile(r"['\u2019-]")


def local_part(account: str) -> str:
    return account.split("@", 1)[0]


def change_line(c: dict[str, Any]) -> str:
    """A short line for the model: the action, the person (local part), the app, the why."""
    who = local_part(c["account"]) or "(bulk change)"
    app = c["app_name"] or c["app_id"] or c["system"]
    return f"{c['action']} {who} in {app}: {c['reason']}"


def placeholder_values(plan: "Plan") -> dict[str, int]:
    """Every placeholder a paragraph may use, with the number it stands for."""
    counts = plan.counts
    values = {
        "total": counts["total"],
        "held": counts["held_bad_data"],
        "held_plan": (plan.data.get("held_plan") or {}).get("changes", 0),
        "approvals": plan.data["approvals_required"],
    }
    values.update({f"count:{t}": n for t, n in counts["by_type"].items()})
    return values


def _plan_words(plan: "Plan") -> tuple[set[str], set[str]]:
    """(whole tokens such as local parts, single words) this plan adds to the allowlist."""
    whole: set[str] = {"break-glass", "export", "exports", "approver", "approvers"}
    words: set[str] = set()
    for text in TYPE_WORDS.values():
        words |= {w.casefold() for w in re.findall(r"[^\W\d_]+", text)}
    for c in [*plan.changes, *plan.data["held"]]:
        local = local_part(c["account"].strip()).casefold()
        if local:
            whole.add(local)
        label = c["app_name"]
        if label:
            words |= {w.casefold() for w in re.findall(r"[^\W\d_]+", label)}
    return whole, words


def fill_summary(paragraph: object, plan: "Plan") -> str | None:
    """The paragraph with its placeholders filled in, or None if it breaks any rule."""
    if not isinstance(paragraph, str) or not paragraph.strip():
        return None
    values = placeholder_values(plan)
    for found in _PLACEHOLDER.findall(paragraph):
        if found[1:-1] not in values:
            return None
    rest = _PLACEHOLDER.sub(" ", paragraph)
    if "{" in rest or "}" in rest or re.search(r"\d", rest):
        return None
    whole, words = _plan_words(plan)
    allowed = ALLOWED_WORDS | words
    for token in _WORD.findall(rest):
        folded = token.casefold()
        if folded in whole:
            continue
        if "." in folded or "_" in folded:
            return None  # looks like a login, and it is not one in this plan
        for part in _SPLIT.split(folded):
            if part in NUMBER_WORDS:
                return None
            if part and part != "s" and part not in allowed:
                return None
    filled = _PLACEHOLDER.sub(lambda m: str(values[m.group(0)[1:-1]]), paragraph).strip()
    return filled if len(filled) <= MAX_CHARS else None


def check_summary(paragraph: object, plan: "Plan") -> bool:
    """True if the paragraph follows the slot-filling rules (see `fill_summary`)."""
    return fill_summary(paragraph, plan) is not None


def template_summary(plan: "Plan") -> str:
    counts = plan.counts
    by_type = counts["by_type"]
    parts = [
        f"{by_type[t]} {TYPE_WORDS.get(t, t)}"
        for t in TYPE_WORDS
        if by_type.get(t)  # fixed order, only the types present
    ]
    text = f"The access plan for {plan.data['as_of']} has {counts['total']} changes"
    text += f": {', '.join(parts)}." if parts else "."
    n = plan.data["approvals_required"]
    text += f" It needs {n} approval{'s' if n != 1 else ''}."
    if plan.data.get("part") == "held":
        text += (
            " This is the held part of the plan: it has the changes of days over the limit and "
            "any bulk import, and is applied only after two approvals."
        )
    held_plan = plan.data.get("held_plan")
    if held_plan:
        text += (
            f" {held_plan['changes']} changes are in the held plan "
            "(applied only after two approvals)."
        )
    if counts["held_bad_data"]:
        text += f" {counts['held_bad_data']} changes are held for bad HR data and never applied."
    if counts["warnings"]:
        text += f" {counts['warnings']} warnings need a look."
    if counts["manual_tasks"]:
        text += f" {counts['manual_tasks']} manual tasks are for apps without an API."
    return text


def model_prompt(plan: "Plan") -> str:
    counts = plan.counts
    lines = [change_line(c) for c in plan.changes[:MAX_LINES]]
    head = [
        "Placeholders you may use for numbers (write them exactly, never digits):",
        "{total}: all changes",
        "{approvals}: approvals required",
        "{held}: changes held for bad HR data, never applied",
        "{held_plan}: changes in the held plan, applied only after two approvals",
        *(f"{{count:{t}}}: {TYPE_WORDS.get(t, t)}" for t in counts["by_type"]),
        f"Change lines (the first {len(lines)} of {counts['total']} changes):",
    ]
    return "\n".join([*head, *lines])


def model_summary(plan: "Plan", client: "ClaudeClient") -> str | None:
    """The model's paragraph with placeholders, or None (refusal or a bad answer). It may
    raise (spend cap, API error); `choose_summary` falls back to the template."""
    data = client.complete_json(SYSTEM, model_prompt(plan), SCHEMA, max_tokens=MAX_TOKENS)
    if data is None:
        return None
    paragraph = data.get("paragraph")
    return paragraph.strip() if isinstance(paragraph, str) else None


def choose_summary(plan: "Plan", client: "ClaudeClient | None") -> tuple[str, bool]:
    """(paragraph, written_by_model). The template when there's no client, the cap is reached,
    the model gives nothing or fails in any way, or the check fails."""
    if client is not None:
        try:
            paragraph = model_summary(plan, client)
            filled = fill_summary(paragraph, plan) if paragraph is not None else None
        except Exception:  # the model path may fail any way; the template is the safe fallback
            filled = None
        if filled is not None:
            return filled, True
    return template_summary(plan), False


def summary_markdown(plan: "Plan", client: "ClaudeClient | None" = None) -> str:
    paragraph, by_model = choose_summary(plan, client)
    source = "written by the model, checked" if by_model else "template"
    counts = plan.counts
    rows = ["| Type | Changes |", "|---|---|"]
    rows += [f"| {t} | {n} |" for t, n in counts["by_type"].items()]
    rows += [f"| total | {counts['total']} |"]
    return (
        f"# Access plan summary for {plan.data['as_of']}\n\n"
        + "\n".join(rows)
        + f"\n\n{paragraph}\n\n_Summary source: {source}._\n"
    )
