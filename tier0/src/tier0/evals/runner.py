"""Run the eval: every ticket k times, each on a fresh runtime (ADR-003)."""

import hashlib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from meridian_dataset import GENERATOR_VERSION, World, generate
from meridian_dataset.model import AnswerKeyEntry
from tier0 import messages
from tier0.clock import parse_time
from tier0.evals.explain import explain
from tier0.evals.grading import Grade, Observation, data_exposed, grade, policy_violation
from tier0.models import ModelAdapter, StubModel
from tier0.models.base import DEFAULT_MODEL_SEED_BASE
from tier0.runtime import offline_runtime
from tier0.sealed import require_unsealed
from tier0.tickets.adapter import QUEUE_IDENTITY_VERIFICATION, TODO, TRIAGE
from tier0.tracing import (
    ERROR_TYPE,
    GEN_AI_OPERATION_NAME,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_TOOL_NAME,
    SEMCONV_VERSION,
    TIER0_SESSION_KIND,
    Tracer,
    summarize,
)

type ModelFactory = Any  # Callable[[], ModelAdapter]


@dataclass(frozen=True, slots=True)
class RunResult:
    ticket_id: str
    run: int
    scenario: str
    attack: str | None
    grade: Grade
    traces: int
    model_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    duration_ms: float
    # Diagnostics for explaining failures (codes only, never model text).
    category: str | None = None
    action: str | None = None
    reasons: tuple[str, ...] = ()
    model_errors: tuple[str, ...] = ()
    finish_reasons: tuple[str, ...] = ()
    # The second checks (None when not reached: triage did not say own_username).
    own_account_check: str | None = None  # "yes", "no" or "failed"
    mention_others: int | None = None
    mention_namesakes: int | None = None
    mention_lookups: int = 0


@dataclass(frozen=True, slots=True)
class TicketRow:
    ticket: str
    scenario: str
    attack: str | None
    runs: int
    runs_passed: int
    final_state: str | None
    final_queue: str | None
    unsafe: tuple[str, ...]
    mean_input_tokens: float
    mean_output_tokens: float
    mean_duration_ms: float
    mean_traces: float


# Stub-model scores prove the pipeline and the safety controls, not AI quality.
PIPELINE_CHECK_LABEL = "pipeline check (stub model), not AI accuracy"


@dataclass(frozen=True, slots=True)
class EvalReport:
    seed: int
    generator_version: str
    k: int
    provider: str
    model: str
    semconv_version: str
    results: tuple[RunResult, ...]
    # needs_human by ticket id, so escalation can be computed from results alone
    expected_queues: tuple[tuple[str, str], ...]  # (ticket_id, queue) where needs_human
    run_date: str = "1970-01-01"
    answer_key: tuple[AnswerKeyEntry, ...] = ()
    temperature: float | None = None
    # One Ollama sampling seed per run (base + run number); empty when the model has none.
    model_seeds: tuple[int, ...] = ()
    model_seed_base: int | None = None
    # Ollama version and model digest (or {"error": ...}); empty for stub and Claude.
    provenance: dict[str, str] = field(default_factory=dict)
    # SHA-256 of the triage prompt in force when the eval ran (frozen, ADR-021).
    triage_prompt_sha256: str = ""

    def explanation(self, run: RunResult) -> str | None:
        """Plain-English reason a run failed; None when it passed (or no key is known)."""
        if run.grade.passed:
            return None
        entry = next((e for e in self.answer_key if e.ticket_id == run.ticket_id), None)
        return explain(run, entry) if entry is not None else None

    @property
    def _by_ticket(self) -> dict[str, list[RunResult]]:
        grouped: dict[str, list[RunResult]] = {}
        for r in self.results:
            grouped.setdefault(r.ticket_id, []).append(r)
        return grouped

    @property
    def is_pipeline_check(self) -> bool:
        """True when the deterministic stub answered, so the scores say nothing about AI quality."""
        return self.provider == StubModel.provider

    @property
    def result_kind(self) -> str:
        return "pipeline_check" if self.is_pipeline_check else "model_eval"

    @property
    def mean_latency_ms(self) -> float:
        return sum(r.duration_ms for r in self.results) / len(self.results) if self.results else 0.0

    @property
    def max_latency_ms(self) -> float:
        return max((r.duration_ms for r in self.results), default=0.0)

    @property
    def pass_1(self) -> float:
        return _mean([r.grade.passed for r in self.results])

    @property
    def pass_k(self) -> float:
        return _share_all(self._by_ticket.values())

    @property
    def everyday_pass_k(self) -> float:
        return _share_all(g for g in self._by_ticket.values() if g[0].attack is None)

    @property
    def attack_by_category(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for r in self.results:
            if r.attack is None:
                continue
            c = out.setdefault(
                r.attack,
                {
                    "tickets": 0,
                    "runs": 0,
                    "passed_runs": 0,
                    "policy_violation_runs": 0,
                    "data_exposed_runs": 0,
                },
            )
            c["runs"] += 1
            c["passed_runs"] += r.grade.passed
            c["policy_violation_runs"] += policy_violation(r.grade.unsafe)
            c["data_exposed_runs"] += data_exposed(r.grade.unsafe)
        for category, c in out.items():
            c["tickets"] = len({r.ticket_id for r in self.results if r.attack == category})
        return out

    @property
    def unsafe_runs(self) -> int:
        """Runs with any unsafe result, of either kind. Any of these fails the eval."""
        return sum(1 for r in self.results if r.grade.unsafe)

    @property
    def policy_violation_runs(self) -> int:
        return sum(1 for r in self.results if policy_violation(r.grade.unsafe))

    @property
    def data_exposed_runs(self) -> int:
        return sum(1 for r in self.results if data_exposed(r.grade.unsafe))

    @property
    def unsafe_reasons(self) -> dict[str, int]:
        return dict(sorted(Counter(c for r in self.results for c in r.grade.unsafe).items()))

    @property
    def escalation_total(self) -> int:
        queues = dict(self.expected_queues)
        return sum(1 for r in self.results if r.ticket_id in queues)

    @property
    def escalation_correct(self) -> int:
        queues = dict(self.expected_queues)
        return sum(
            1
            for r in self.results
            if r.ticket_id in queues and r.grade.final_queue == queues[r.ticket_id]
        )

    # --- second checks and hand-offs -----------------------------------------

    @property
    def _own_username_runs(self) -> tuple[RunResult, ...]:
        return tuple(r for r in self.results if r.category == "own_username")

    def _second_check_row(self, reached: Callable[[RunResult], bool], disagreed: Any) -> dict:
        runs = [r for r in self._own_username_runs if reached(r)]
        bad = [r for r in runs if disagreed(r)]
        return {
            "reached": len(runs),
            "disagreed": len(bad),
            "errors_caught": sum(1 for r in bad if r.scenario == "colleague_lookup"),
            "false_alarms": sum(1 for r in bad if r.scenario in ("normal", "duplicate_name")),
        }

    @property
    def second_checks(self) -> dict[str, Any]:
        """Over runs triage called own_username: how often each check disagreed, and why."""
        model = self._second_check_row(
            lambda r: r.own_account_check is not None,
            lambda r: r.own_account_check in ("no", "failed"),
        )
        mention = self._second_check_row(
            lambda r: r.mention_others is not None,
            lambda r: (r.mention_others or 0) > 0,
        )
        namesake_only = sum(
            1
            for r in self._own_username_runs
            if r.mention_others == 0 and (r.mention_namesakes or 0) > 0
        )
        return {
            "model_check": model,
            "mention_check": mention,
            "namesake_only_mentions": namesake_only,
        }

    def _is_handoff(self, r: RunResult) -> bool:
        g = r.grade
        return g.final_state == TRIAGE or (
            g.final_state == TODO and g.final_queue == QUEUE_IDENTITY_VERIFICATION
        )

    @property
    def handoff_rate(self) -> float:
        n = sum(1 for r in self.results if self._is_handoff(r))
        return n / len(self.results) if self.results else 0.0

    @property
    def handoff_runs(self) -> int:
        return sum(1 for r in self.results if self._is_handoff(r))

    @property
    def unexpected_handoffs(self) -> int:
        """Hand-offs where the answer key expected another state (or queue)."""
        keys = {e.ticket_id: e for e in self.answer_key}
        count = 0
        for r in self.results:
            if not self._is_handoff(r):
                continue
            e = keys.get(r.ticket_id)
            if e is None or (e.expected_state, e.expected_queue) != (
                r.grade.final_state,
                r.grade.final_queue,
            ):
                count += 1
        return count

    @property
    def mean_model_calls(self) -> float:
        n = len(self.results)
        return sum(r.model_calls for r in self.results) / n if n else 0.0

    @property
    def rows(self) -> tuple[TicketRow, ...]:
        rows = []
        for ticket_id, runs in self._by_ticket.items():
            last = runs[-1]
            n = len(runs)
            rows.append(
                TicketRow(
                    ticket=ticket_id,
                    scenario=last.scenario,
                    attack=last.attack,
                    runs=n,
                    runs_passed=sum(r.grade.passed for r in runs),
                    final_state=last.grade.final_state,
                    final_queue=last.grade.final_queue,
                    unsafe=tuple(sorted({c for r in runs for c in r.grade.unsafe})),
                    mean_input_tokens=sum(r.input_tokens for r in runs) / n,
                    mean_output_tokens=sum(r.output_tokens for r in runs) / n,
                    mean_duration_ms=sum(r.duration_ms for r in runs) / n,
                    mean_traces=sum(r.traces for r in runs) / n,
                )
            )
        return tuple(rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_date": self.run_date,
            "mean_latency_ms": self.mean_latency_ms,
            "max_latency_ms": self.max_latency_ms,
            "result_kind": self.result_kind,
            "label": PIPELINE_CHECK_LABEL if self.is_pipeline_check else None,
            "seed": self.seed,
            "generator_version": self.generator_version,
            "k": self.k,
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "model_seeds": list(self.model_seeds),
            "model_seed_base": self.model_seed_base,
            "provenance": dict(self.provenance),
            "triage_prompt_sha256": self.triage_prompt_sha256,
            "semconv_version": self.semconv_version,
            "pass_1": self.pass_1,
            "pass_k": self.pass_k,
            "everyday_pass_k": self.everyday_pass_k,
            "attack_by_category": self.attack_by_category,
            "unsafe_runs": self.unsafe_runs,
            "policy_violation_runs": self.policy_violation_runs,
            "data_exposed_runs": self.data_exposed_runs,
            "unsafe_reasons": self.unsafe_reasons,
            "escalation_correct": self.escalation_correct,
            "escalation_total": self.escalation_total,
            "second_checks": self.second_checks,
            "handoff_rate": self.handoff_rate,
            "handoff_runs": self.handoff_runs,
            "unexpected_handoffs": self.unexpected_handoffs,
            "mean_model_calls": self.mean_model_calls,
            "tickets": [
                {
                    "ticket": r.ticket,
                    "scenario": r.scenario,
                    "attack": r.attack,
                    "runs": r.runs,
                    "runs_passed": r.runs_passed,
                    "final_state": r.final_state,
                    "final_queue": r.final_queue,
                    "unsafe": list(r.unsafe),
                    "mean_input_tokens": r.mean_input_tokens,
                    "mean_output_tokens": r.mean_output_tokens,
                    "mean_duration_ms": r.mean_duration_ms,
                    "mean_traces": r.mean_traces,
                }
                for r in self.rows
            ],
            "runs_detail": [
                {
                    "ticket": r.ticket_id,
                    "run": r.run,
                    "passed": r.grade.passed,
                    "state_ok": r.grade.state_ok,
                    "missing_facts": list(r.grade.missing_facts),
                    "unsafe": list(r.grade.unsafe),
                    "final_state": r.grade.final_state,
                    "final_queue": r.grade.final_queue,
                    "category": r.category,
                    "action": r.action,
                    "reasons": list(r.reasons),
                    "model_errors": list(r.model_errors),
                    "finish_reasons": list(r.finish_reasons),
                    "own_account_check": r.own_account_check,
                    "mention_others": r.mention_others,
                    "mention_namesakes": r.mention_namesakes,
                    "mention_lookups": r.mention_lookups,
                    "explanation": self.explanation(r),
                }
                for r in self.results
            ],
        }


def _mean(values: Any) -> float:
    items = [bool(v) for v in values]
    return sum(items) / len(items) if items else 0.0


def _share_all(groups: Any) -> float:
    groups = list(groups)
    if not groups:
        return 0.0
    return sum(1 for g in groups if all(r.grade.passed for r in g)) / len(groups)


def build_report(
    world: World,
    results: tuple[RunResult, ...],
    *,
    seed: int,
    k: int,
    provider: str,
    model: str,
    run_date: str = "1970-01-01",
    temperature: float | None = None,
    model_seeds: tuple[int, ...] = (),
    model_seed_base: int | None = None,
    provenance: dict[str, str] | None = None,
    triage_prompt_sha256: str | None = None,
) -> EvalReport:
    """Pure: turn run results into the report and its metrics."""
    expected = tuple(
        (e.ticket_id, e.expected_queue) for e in world.answer_key.entries if e.needs_human
    )
    return EvalReport(
        seed=seed,
        generator_version=GENERATOR_VERSION,
        k=k,
        provider=provider,
        model=model,
        semconv_version=SEMCONV_VERSION,
        results=results,
        expected_queues=expected,
        run_date=run_date,
        answer_key=tuple(world.answer_key.entries),
        temperature=temperature,
        model_seeds=model_seeds,
        model_seed_base=model_seed_base,
        provenance=dict(provenance or {}),
        triage_prompt_sha256=(
            triage_prompt_sha256
            if triage_prompt_sha256 is not None
            else hashlib.sha256(messages.TRIAGE_SYSTEM.encode("utf-8")).hexdigest()
        ),
    )


def seeded_model(model_factory: ModelFactory, model_seed: int | None) -> ModelAdapter:
    """A fresh model; when it supports seeds and one is given, pinned to that seed."""
    model: ModelAdapter = model_factory()
    if model_seed is not None and getattr(model, "supports_seed", False):
        model.seed = model_seed
    return model


async def run_ticket(
    world: World,
    ticket_id: str,
    run: int,
    *,
    model_factory: ModelFactory,
    out_dir: Path | None = None,
    final_comparison: bool = False,
    model_seed: int | None = None,
) -> RunResult:
    """One run of one ticket on a fresh runtime, so nothing leaks between runs.

    Each run gets its own Tracer (its own subfolder), because the ticket
    identifier (IT-1, ...) repeats across runs and spans must not mix.
    """
    ticket = next(t for t in world.tickets if t.id == ticket_id)
    entry = world.answer_key.entry(ticket_id)
    trace_dir = out_dir / "traces" / f"{ticket_id}-run{run}" if out_dir is not None else None
    if trace_dir is not None:
        trace_dir.mkdir(parents=True, exist_ok=True)
    tracer = Tracer(out_dir=trace_dir)
    async with offline_runtime(
        world,
        model=seeded_model(model_factory, model_seed),
        tracer=tracer,
        final_comparison=final_comparison,
    ) as rt:
        rt.clock.advance_to(parse_time(ticket.received_at))
        outcome = await rt.agent.handle(rt.slack.message_event(ticket))
        rt.clock.advance(timedelta(days=3, minutes=1))
        rt.agent.sweep()

        spans = rt.tracer.finished_spans()
        tools = tuple(
            str((s.attributes or {})[GEN_AI_TOOL_NAME])
            for s in spans
            if (s.attributes or {}).get(GEN_AI_OPERATION_NAME) == "execute_tool"
            and GEN_AI_TOOL_NAME in (s.attributes or {})
        )
        obs = Observation(
            tickets=tuple((t.state, t.queue) for t in rt.tickets.find_tickets()),
            replies=tuple(m.text for m in rt.slack.sent_messages),
            tool_calls=tools,
            directory_reads=tuple(
                r.resolved_id
                for r in rt.directory.reads
                if r.method == "users.get" and r.resolved_id
            ),
            tokens_issued=len(rt.issuer.issued),
            directory_lookups=sum(1 for r in rt.directory.reads if r.method == "users.list"),
        )
        summaries = summarize(spans).values()
        reasons, model_errors, finish_reasons = _diagnostics(spans)
        checks = _second_check_events(spans)
    tracer.shutdown()
    return RunResult(
        ticket_id=ticket_id,
        run=run,
        scenario=str(entry.scenario),
        attack=entry.attack,
        grade=grade(entry, obs),
        traces=sum(s.traces for s in summaries),
        model_calls=sum(s.model_calls for s in summaries),
        tool_calls=sum(s.tool_calls for s in summaries),
        input_tokens=sum(s.input_tokens for s in summaries),
        output_tokens=sum(s.output_tokens for s in summaries),
        duration_ms=sum(s.duration_ms for s in summaries),
        category=outcome.category,
        action=outcome.action,
        reasons=reasons,
        model_errors=model_errors,
        finish_reasons=finish_reasons,
        own_account_check=checks[0],
        mention_others=checks[1],
        mention_namesakes=checks[2],
        mention_lookups=checks[3],
    )


def _second_check_events(spans: Any) -> tuple[str | None, int | None, int | None, int]:
    """The second checks' codes from the intake session's events."""
    intake = {
        s.context.trace_id
        for s in spans
        if (s.attributes or {}).get(TIER0_SESSION_KIND) == "intake"
    }
    result: str | None = None
    others: int | None = None
    namesakes: int | None = None
    lookups = 0
    for s in spans:
        if s.context.trace_id not in intake:
            continue
        for ev in s.events:
            ea = ev.attributes or {}
            if ev.name == "tier0.own_account_check":
                result = str(ea.get("result"))
            elif ev.name == "tier0.mention_check":
                others = int(ea.get("others", 0))  # type: ignore[call-overload]
                namesakes = int(ea.get("namesakes", 0))  # type: ignore[call-overload]
                lookups += int(ea.get("lookups", 0))  # type: ignore[call-overload]
    return result, others, namesakes, lookups


def _diagnostics(spans: Any) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Codes from the intake session: why it escalated, model failures, finish reasons."""
    intake = {
        s.context.trace_id
        for s in spans
        if (s.attributes or {}).get(TIER0_SESSION_KIND) == "intake"
    }
    reasons: set[str] = set()
    model_errors: list[str] = []
    finish: list[str] = []
    for s in spans:
        if s.context.trace_id not in intake:
            continue
        attrs = s.attributes or {}
        for ev in s.events:
            ea = ev.attributes or {}
            if ev.name == "tier0.escalated" and "reason" in ea:
                reasons.add(str(ea["reason"]))
            elif ev.name == "tier0.guard.blocked":
                reasons.add("guard_blocked")
            elif ev.name == "tier0.identity.unbound" and "reason" in ea:
                reasons.add(str(ea["reason"]))
            elif ev.name == "tier0.reply.not_sent":
                reasons.add("reply_not_sent")
        if attrs.get(GEN_AI_OPERATION_NAME) == "chat":
            if ERROR_TYPE in attrs:
                model_errors.append(str(attrs[ERROR_TYPE]))
            else:
                value = attrs.get(GEN_AI_RESPONSE_FINISH_REASONS) or ()
                if isinstance(value, str):
                    value = (value,)
                finish.extend(str(v) for v in value)
    return tuple(sorted(reasons)), tuple(model_errors), tuple(finish)


def describe_model(model: Any) -> dict[str, str]:
    """The model's provenance, or {"error": <exception class>} when it can't be read."""
    describe = getattr(model, "describe", None)
    if describe is None:
        return {}
    try:
        return dict(describe())
    except Exception as exc:
        return {"error": type(exc).__name__}


async def run_eval(
    seed: int,
    *,
    k: int = 3,
    model_factory: ModelFactory = StubModel,
    out_dir: Path | None = None,
    today: Callable[[], date] | None = None,
    final_comparison: bool = False,
    model_seed_base: int = DEFAULT_MODEL_SEED_BASE,
) -> EvalReport:
    """Run every ticket k times, one after another. Held-out v2 needs final_comparison."""
    world = generate(seed)
    require_unsealed(world, final_comparison=final_comparison)
    probe: ModelAdapter = model_factory()
    seeded = bool(getattr(probe, "supports_seed", False))
    provenance = describe_model(probe)
    results = []
    for entry in world.answer_key.entries:
        for run in range(1, k + 1):
            results.append(
                await run_ticket(
                    world,
                    entry.ticket_id,
                    run,
                    model_factory=model_factory,
                    out_dir=out_dir,
                    final_comparison=final_comparison,
                    model_seed=model_seed_base + run if seeded else None,
                )
            )
    run_date = (today() if today is not None else datetime.now(UTC).date()).isoformat()
    return build_report(
        world,
        tuple(results),
        seed=seed,
        k=k,
        provider=probe.provider,
        model=probe.model,
        run_date=run_date,
        temperature=getattr(probe, "temperature", None),
        model_seeds=tuple(model_seed_base + r for r in range(1, k + 1)) if seeded else (),
        model_seed_base=model_seed_base if seeded else None,
        provenance=provenance,
    )
