"""Tracing for ADR-007: one trace per work session, GenAI semantic conventions, no content.

Each work session on a ticket (an intake or a sweep) is one trace. Every span
carries the ticket ID, so a ticket's traces can be joined and its cost and
latency summed (`summarize`). Traces hold no message content by default and
only pseudonymised user IDs. Content capture is a flag meant for eval runs on
fake data. The re-identification key lives in `Pseudonymizer`, apart from the
traces (compliance.md).

Semantic conventions (ADR-012): we follow GenAI conventions 1.44.0
(opentelemetry-semantic-conventions 0.66b2). In 0.66b2 the GenAI attributes
(`opentelemetry.semconv._incubating.attributes.gen_ai_attributes`) are marked
"Deprecated: Moved to the OpenTelemetry GenAI semantic conventions repository"
(github.com/open-telemetry/semantic-conventions-genai), which has no release
yet as of 2026-11-08. The attribute names are unchanged and the 1.44.0 pin
stands. So tier0 defines its own string constants here, and a test checks that
they equal the pinned package's constants. Runtime code never imports the
`_incubating` module.

Global provider: the MCP SDK emits its own OpenTelemetry spans through the
GLOBAL tracer provider, and those could carry tool arguments or results. So
tier0 must NEVER call `trace.set_tracer_provider`. It uses a private
`TracerProvider` instance, which the MCP SDK's spans never reach.

Times: span start and end come from the wall clock on purpose, because latency
must be real. (The simulated `Clock` is for ticket time, not for tracing.)

Output: OTLP/JSON lines (the OpenTelemetry Collector file exporter format), one
ExportTraceServiceRequest per line, written to `<out_dir>/traces.jsonl`.
"""

import hashlib
import hmac
import json
import secrets
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Span, SpanKind, StatusCode

from tier0.models.base import ModelAdapter, ModelError, ModelRequest, ModelResponse

SEMCONV_VERSION = "1.44.0"
SCHEMA_URL = "https://opentelemetry.io/schemas/1.44.0"
TIER0_VERSION = "0.1.0"
AGENT_NAME = "tier0"

GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_OUTPUT_TYPE = "gen_ai.output.type"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_ID = "gen_ai.response.id"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_USAGE_CACHE_CREATION_TOKENS = "gen_ai.usage.cache_creation.input_tokens"
GEN_AI_USAGE_CACHE_READ_TOKENS = "gen_ai.usage.cache_read.input_tokens"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
GEN_AI_TOOL_TYPE = "gen_ai.tool.type"
GEN_AI_INPUT_MESSAGES = "gen_ai.input.messages"
GEN_AI_OUTPUT_MESSAGES = "gen_ai.output.messages"
ENDUSER_PSEUDO_ID = "enduser.pseudo.id"
ERROR_TYPE = "error.type"

OPERATION_CHAT = "chat"
OPERATION_INVOKE_AGENT = "invoke_agent"
OPERATION_EXECUTE_TOOL = "execute_tool"

# tier0's own attributes (not part of the GenAI conventions).
TIER0_TICKET_ID = "tier0.ticket.id"
TIER0_SESSION_KIND = "tier0.session.kind"

type SessionKind = Literal["intake", "sweep"]

_MAX_EVENT_VALUE_LENGTH = 64
_OTLP_KIND = {
    SpanKind.INTERNAL: 1,
    SpanKind.SERVER: 2,
    SpanKind.CLIENT: 3,
    SpanKind.PRODUCER: 4,
    SpanKind.CONSUMER: 5,
}
_OTLP_STATUS = {StatusCode.UNSET: 0, StatusCode.OK: 1, StatusCode.ERROR: 2}


class Pseudonymizer:
    """Turns a directory ID into a stable pseudonym for traces.

    The key is the re-identification key: keep it apart from the traces. The
    same key gives the same pseudonym, so a ticket's traces can be joined
    without revealing who it was. Without a key, a random one is made per run.
    """

    def __init__(self, key: bytes | None = None) -> None:
        self._key = key if key is not None else secrets.token_bytes(32)

    def pseudonym(self, directory_id: str) -> str:
        digest = hmac.new(self._key, directory_id.encode("utf-8"), hashlib.sha256)
        return digest.hexdigest()[:16]


# --- OTLP/JSON file exporter -------------------------------------------------


def _any_value(value: Any) -> dict[str, Any]:
    # bool must come before int: bool is a subclass of int.
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, Sequence | tuple):
        return {"arrayValue": {"values": [_any_value(item) for item in value]}}
    return {"stringValue": str(value)}


def _attributes(attributes: Any) -> list[dict[str, Any]]:
    return [{"key": key, "value": _any_value(value)} for key, value in (attributes or {}).items()]


def _span_json(span: ReadableSpan) -> dict[str, Any]:
    context = span.get_span_context()
    assert context is not None  # noqa: S101 - finished spans always have a context
    out: dict[str, Any] = {
        "traceId": format(context.trace_id, "032x"),
        "spanId": format(context.span_id, "016x"),
        "name": span.name,
        "kind": _OTLP_KIND[span.kind],
        "startTimeUnixNano": str(span.start_time or 0),
        "endTimeUnixNano": str(span.end_time or 0),
        "attributes": _attributes(span.attributes),
    }
    if span.parent is not None:
        out["parentSpanId"] = format(span.parent.span_id, "016x")
    if span.events:
        out["events"] = [
            {
                "timeUnixNano": str(event.timestamp),
                "name": event.name,
                "attributes": _attributes(event.attributes),
            }
            for event in span.events
        ]
    status: dict[str, Any] = {"code": _OTLP_STATUS[span.status.status_code]}
    if span.status.description:
        status["message"] = span.status.description
    out["status"] = status
    return out


class OtlpJsonLinesExporter(SpanExporter):
    """Appends one OTLP/JSON ExportTraceServiceRequest per export call."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        grouped: dict[int, tuple[Resource, dict[int, tuple[Any, list[ReadableSpan]]]]] = {}
        for span in spans:
            _, scopes = grouped.setdefault(id(span.resource), (span.resource, {}))
            scope = span.instrumentation_scope
            _, members = scopes.setdefault(id(scope), (scope, []))
            members.append(span)

        resource_spans = []
        for resource, scopes in grouped.values():
            scope_spans = []
            for scope, members in scopes.values():
                scope_json: dict[str, Any] = {"name": scope.name if scope else ""}
                if scope and scope.version:
                    scope_json["version"] = scope.version
                scope_spans.append(
                    {
                        "scope": scope_json,
                        "schemaUrl": (scope.schema_url if scope else "") or "",
                        "spans": [_span_json(member) for member in members],
                    }
                )
            resource_spans.append(
                {
                    "resource": {"attributes": _attributes(resource.attributes)},
                    "schemaUrl": resource.schema_url or "",
                    "scopeSpans": scope_spans,
                }
            )
        line = json.dumps({"resourceSpans": resource_spans}, separators=(",", ":"))
        try:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


# --- Facade ------------------------------------------------------------------


def _mark_error(span: Span, error: BaseException) -> None:
    # The class name only: exception messages can contain user data.
    span.set_attribute(ERROR_TYPE, type(error).__name__)
    span.set_status(StatusCode.ERROR)


def _check_event_value(name: str, value: str | int | bool) -> None:
    if isinstance(value, str) and (len(value) > _MAX_EVENT_VALUE_LENGTH or "@" in value):
        raise ValueError(f"event attribute {name!r} looks like content; pass a short code")


class Session:
    """One work session on one ticket. Create it with `Tracer.session`."""

    def __init__(self, tracer: "Tracer", span: Span, ticket_id: str) -> None:
        self._tracer = tracer
        self._span = span
        self._ticket_id = ticket_id

    @property
    def trace_id(self) -> str:
        return format(self._span.get_span_context().trace_id, "032x")

    def chat(self, model: ModelAdapter, request: ModelRequest) -> ModelResponse:
        """Call the model inside a `chat` span (the session span is its parent)."""
        attributes: dict[str, str | int] = {
            GEN_AI_OPERATION_NAME: OPERATION_CHAT,
            GEN_AI_PROVIDER_NAME: model.provider,
            GEN_AI_REQUEST_MODEL: model.model,
            GEN_AI_REQUEST_MAX_TOKENS: request.max_tokens,
            GEN_AI_OUTPUT_TYPE: "json" if request.json_schema else "text",
            TIER0_TICKET_ID: self._ticket_id,
        }
        with self._tracer.start_child(f"chat {model.model}", SpanKind.CLIENT, attributes) as span:
            try:
                response = model.complete(request)
            except ModelError as error:
                _mark_error(span, error)
                raise
            span.set_attribute(GEN_AI_RESPONSE_MODEL, response.model)
            if response.response_id is not None:
                span.set_attribute(GEN_AI_RESPONSE_ID, response.response_id)
            span.set_attribute(GEN_AI_RESPONSE_FINISH_REASONS, [response.finish_reason])
            span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, response.input_tokens)
            span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, response.output_tokens)
            if response.cache_write_tokens:
                span.set_attribute(GEN_AI_USAGE_CACHE_CREATION_TOKENS, response.cache_write_tokens)
            if response.cache_read_tokens:
                span.set_attribute(GEN_AI_USAGE_CACHE_READ_TOKENS, response.cache_read_tokens)
            if self._tracer.capture_content:
                span.set_attribute(
                    GEN_AI_INPUT_MESSAGES,
                    json.dumps(
                        [
                            {"role": "system", "content": request.system},
                            {"role": "user", "content": request.user},
                        ]
                    ),
                )
                span.set_attribute(
                    GEN_AI_OUTPUT_MESSAGES,
                    json.dumps([{"role": "assistant", "content": response.text}]),
                )
            return response

    @contextmanager
    def tool(self, name: str, call_id: str) -> Iterator[Span]:
        """A span around one tool call. No arguments or results are recorded."""
        attributes: dict[str, str | int] = {
            GEN_AI_OPERATION_NAME: OPERATION_EXECUTE_TOOL,
            GEN_AI_TOOL_NAME: name,
            GEN_AI_TOOL_CALL_ID: call_id,
            GEN_AI_TOOL_TYPE: "function",
            TIER0_TICKET_ID: self._ticket_id,
        }
        with self._tracer.start_child(
            f"execute_tool {name}", SpanKind.INTERNAL, attributes
        ) as span:
            try:
                yield span
            except Exception as error:
                _mark_error(span, error)
                raise

    def event(self, name: str, **attributes: str | int | bool) -> None:
        """Record a decision on the session span. Pass codes, never free text."""
        for key, value in attributes.items():
            _check_event_value(key, value)
        self._span.add_event(name, attributes)


class Tracer:
    """tier0's tracing facade, one per run. Holds a private provider (never the global one)."""

    def __init__(
        self,
        *,
        out_dir: Path | None = None,
        pseudonymizer: Pseudonymizer | None = None,
        capture_content: bool = False,
    ) -> None:
        self.capture_content = capture_content
        self._pseudonymizer = pseudonymizer if pseudonymizer is not None else Pseudonymizer()
        self._memory = InMemorySpanExporter()
        self._trace_file = out_dir / "traces.jsonl" if out_dir is not None else None
        self._provider = TracerProvider(
            resource=Resource.create({"service.name": "tier0", "service.version": TIER0_VERSION})
        )
        self._provider.add_span_processor(SimpleSpanProcessor(self._memory))
        if self._trace_file is not None:
            self._provider.add_span_processor(
                SimpleSpanProcessor(OtlpJsonLinesExporter(self._trace_file))
            )
        self._tracer = self._provider.get_tracer("tier0", TIER0_VERSION, schema_url=SCHEMA_URL)

    @property
    def provider(self) -> TracerProvider:
        return self._provider

    @property
    def tracer(self) -> trace.Tracer:
        return self._tracer

    @property
    def trace_file(self) -> Path | None:
        return self._trace_file

    def finished_spans(self) -> tuple[ReadableSpan, ...]:
        return tuple(self._memory.get_finished_spans())

    def shutdown(self) -> None:
        self._provider.shutdown()

    @contextmanager
    def start_child(
        self, name: str, kind: SpanKind, attributes: dict[str, str | int]
    ) -> Iterator[Span]:
        """A span under the currently active span; it records no exception text."""
        with self._tracer.start_as_current_span(
            name,
            kind=kind,
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            yield span

    @contextmanager
    def session(
        self,
        *,
        ticket_id: str,
        kind: SessionKind,
        requester_id: str | None = None,
    ) -> Iterator[Session]:
        """Start a new root span for one work session (never joins an outer trace)."""
        attributes: dict[str, str | int] = {
            GEN_AI_OPERATION_NAME: OPERATION_INVOKE_AGENT,
            GEN_AI_AGENT_NAME: AGENT_NAME,
            GEN_AI_CONVERSATION_ID: ticket_id,
            TIER0_TICKET_ID: ticket_id,
            TIER0_SESSION_KIND: kind,
        }
        if requester_id is not None:
            attributes[ENDUSER_PSEUDO_ID] = self._pseudonymizer.pseudonym(requester_id)
        with self._tracer.start_as_current_span(
            f"{OPERATION_INVOKE_AGENT} {AGENT_NAME}",
            context=otel_context.Context(),
            kind=SpanKind.INTERNAL,
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield Session(self, span, ticket_id)
            except Exception as error:
                _mark_error(span, error)
                raise


# --- Per-ticket roll-up ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TicketTraceSummary:
    """Cost and latency for one ticket, added up across its traces."""

    ticket_id: str
    traces: int
    sessions: tuple[str, ...]
    model_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    duration_ms: float
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0


def summarize(spans: Iterable[ReadableSpan]) -> dict[str, TicketTraceSummary]:
    """Group finished spans by ticket ID (ADR-007: cost and latency per ticket)."""
    by_ticket: dict[str, list[ReadableSpan]] = defaultdict(list)
    for span in spans:
        ticket_id = (span.attributes or {}).get(TIER0_TICKET_ID)
        if isinstance(ticket_id, str):
            by_ticket[ticket_id].append(span)

    result: dict[str, TicketTraceSummary] = {}
    for ticket_id, members in by_ticket.items():
        sessions = sorted(
            (
                s
                for s in members
                if (s.attributes or {}).get(GEN_AI_OPERATION_NAME) == "invoke_agent"
            ),
            key=lambda s: s.start_time or 0,
        )
        input_tokens = output_tokens = model_calls = tool_calls = 0
        cache_write_tokens = cache_read_tokens = 0
        for span in members:
            attrs = span.attributes or {}
            operation = attrs.get(GEN_AI_OPERATION_NAME)
            if operation == OPERATION_CHAT:
                model_calls += 1
                input_tokens += int(attrs.get(GEN_AI_USAGE_INPUT_TOKENS, 0))  # type: ignore[call-overload]
                output_tokens += int(attrs.get(GEN_AI_USAGE_OUTPUT_TOKENS, 0))  # type: ignore[call-overload]
                cache_write_tokens += int(attrs.get(GEN_AI_USAGE_CACHE_CREATION_TOKENS, 0))  # type: ignore[call-overload]
                cache_read_tokens += int(attrs.get(GEN_AI_USAGE_CACHE_READ_TOKENS, 0))  # type: ignore[call-overload]
            elif operation == OPERATION_EXECUTE_TOOL:
                tool_calls += 1
        result[ticket_id] = TicketTraceSummary(
            ticket_id=ticket_id,
            traces=len({s.get_span_context().trace_id for s in sessions}),  # type: ignore[union-attr]
            sessions=tuple(str((s.attributes or {}).get(TIER0_SESSION_KIND)) for s in sessions),
            model_calls=model_calls,
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=sum(((s.end_time or 0) - (s.start_time or 0)) / 1e6 for s in sessions),
            cache_write_tokens=cache_write_tokens,
            cache_read_tokens=cache_read_tokens,
        )
    return result
