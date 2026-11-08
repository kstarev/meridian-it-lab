"""Tracing: GenAI conventions, one trace per session, and no content or raw IDs in traces."""

import json
from pathlib import Path

import pytest
from opentelemetry import trace
from opentelemetry.semconv._incubating.attributes import enduser_attributes as enduser
from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as gen_ai
from opentelemetry.semconv.attributes import error_attributes
from opentelemetry.semconv.schemas import Schemas
from opentelemetry.trace import StatusCode

from tier0 import tracing
from tier0.models import ModelError, ModelRequest, ModelResponse, StubModel
from tier0.tracing import Pseudonymizer, Tracer, summarize

TICKET = "TKT-1"
MARKER = "zebra-umbrella-quartz-marker sentence"
EMAIL = "alex.kim@meridian.example"
REQUESTER = "123456789012345678901"


def triage_request(text: str = "What is my username?") -> ModelRequest:
    return ModelRequest(task="triage", system="Classify.", user=text)


def by_name(tracer: Tracer) -> dict:
    return {s.name: s for s in tracer.finished_spans()}


def test_constants_match_the_pinned_package() -> None:
    pairs = {
        "GEN_AI_OPERATION_NAME": gen_ai.GEN_AI_OPERATION_NAME,
        "GEN_AI_PROVIDER_NAME": gen_ai.GEN_AI_PROVIDER_NAME,
        "GEN_AI_AGENT_NAME": gen_ai.GEN_AI_AGENT_NAME,
        "GEN_AI_CONVERSATION_ID": gen_ai.GEN_AI_CONVERSATION_ID,
        "GEN_AI_REQUEST_MODEL": gen_ai.GEN_AI_REQUEST_MODEL,
        "GEN_AI_REQUEST_MAX_TOKENS": gen_ai.GEN_AI_REQUEST_MAX_TOKENS,
        "GEN_AI_OUTPUT_TYPE": gen_ai.GEN_AI_OUTPUT_TYPE,
        "GEN_AI_RESPONSE_MODEL": gen_ai.GEN_AI_RESPONSE_MODEL,
        "GEN_AI_RESPONSE_ID": gen_ai.GEN_AI_RESPONSE_ID,
        "GEN_AI_RESPONSE_FINISH_REASONS": gen_ai.GEN_AI_RESPONSE_FINISH_REASONS,
        "GEN_AI_USAGE_INPUT_TOKENS": gen_ai.GEN_AI_USAGE_INPUT_TOKENS,
        "GEN_AI_USAGE_OUTPUT_TOKENS": gen_ai.GEN_AI_USAGE_OUTPUT_TOKENS,
        "GEN_AI_TOOL_NAME": gen_ai.GEN_AI_TOOL_NAME,
        "GEN_AI_TOOL_CALL_ID": gen_ai.GEN_AI_TOOL_CALL_ID,
        "GEN_AI_TOOL_TYPE": gen_ai.GEN_AI_TOOL_TYPE,
        "GEN_AI_INPUT_MESSAGES": gen_ai.GEN_AI_INPUT_MESSAGES,
        "GEN_AI_OUTPUT_MESSAGES": gen_ai.GEN_AI_OUTPUT_MESSAGES,
        "ENDUSER_PSEUDO_ID": enduser.ENDUSER_PSEUDO_ID,
        "ERROR_TYPE": error_attributes.ERROR_TYPE,
    }
    for name, expected in pairs.items():
        assert getattr(tracing, name) == expected, name
    ops = gen_ai.GenAiOperationNameValues
    assert ops.CHAT.value == tracing.OPERATION_CHAT
    assert ops.INVOKE_AGENT.value == tracing.OPERATION_INVOKE_AGENT
    assert ops.EXECUTE_TOOL.value == tracing.OPERATION_EXECUTE_TOOL
    assert Schemas.V1_44_0.value == tracing.SCHEMA_URL


def test_session_with_chat_and_tool() -> None:
    tracer = Tracer()
    with tracer.session(ticket_id=TICKET, kind="intake") as session:
        session.chat(StubModel(), triage_request())
        with session.tool("get_my_username", "call-1"):
            pass
    spans = by_name(tracer)
    assert list(spans) == [
        "chat tier0-stub-1",
        "execute_tool get_my_username",
        "invoke_agent tier0",
    ]
    root = spans["invoke_agent tier0"]
    chat, tool = spans["chat tier0-stub-1"], spans["execute_tool get_my_username"]
    assert len({s.get_span_context().trace_id for s in spans.values()}) == 1
    assert root.parent is None
    assert chat.parent.span_id == root.get_span_context().span_id
    assert tool.parent.span_id == root.get_span_context().span_id
    assert all(s.attributes[tracing.TIER0_TICKET_ID] == TICKET for s in spans.values())
    assert root.attributes[tracing.GEN_AI_OPERATION_NAME] == "invoke_agent"
    assert root.attributes[tracing.GEN_AI_CONVERSATION_ID] == TICKET
    assert root.attributes[tracing.TIER0_SESSION_KIND] == "intake"
    a = chat.attributes
    assert a[tracing.GEN_AI_OPERATION_NAME] == "chat"
    assert a[tracing.GEN_AI_PROVIDER_NAME] == "tier0.stub"
    assert a[tracing.GEN_AI_REQUEST_MODEL] == "tier0-stub-1"
    assert a[tracing.GEN_AI_REQUEST_MAX_TOKENS] == 1024
    assert a[tracing.GEN_AI_OUTPUT_TYPE] == "text"
    assert a[tracing.GEN_AI_RESPONSE_MODEL] == "tier0-stub-1"
    assert a[tracing.GEN_AI_RESPONSE_ID] == "stub-1"
    assert tuple(a[tracing.GEN_AI_RESPONSE_FINISH_REASONS]) == ("stop",)
    assert a[tracing.GEN_AI_USAGE_INPUT_TOKENS] == 5  # "Classify. What is my username?"
    assert a[tracing.GEN_AI_USAGE_OUTPUT_TOKENS] == 2
    t = tool.attributes
    assert t[tracing.GEN_AI_OPERATION_NAME] == "execute_tool"
    assert t[tracing.GEN_AI_TOOL_NAME] == "get_my_username"
    assert t[tracing.GEN_AI_TOOL_CALL_ID] == "call-1"
    assert t[tracing.GEN_AI_TOOL_TYPE] == "function"


def test_json_schema_sets_output_type_json() -> None:
    tracer = Tracer()
    request = ModelRequest(task="triage", system="s", user="u", json_schema={"type": "object"})
    with tracer.session(ticket_id=TICKET, kind="intake") as session:
        session.chat(StubModel(), request)
    chat = by_name(tracer)["chat tier0-stub-1"]
    assert chat.attributes[tracing.GEN_AI_OUTPUT_TYPE] == "json"


def test_two_sessions_for_one_ticket_are_two_traces() -> None:
    tracer = Tracer()
    for kind in ("intake", "sweep"):
        with tracer.session(ticket_id=TICKET, kind=kind) as session:
            session.chat(StubModel(), triage_request())
            with session.tool("get_my_username", "c"):
                pass
    roots = [s for s in tracer.finished_spans() if s.parent is None]
    assert len({r.get_span_context().trace_id for r in roots}) == 2
    assert all(r.attributes[tracing.TIER0_TICKET_ID] == TICKET for r in roots)
    summary = summarize(tracer.finished_spans())[TICKET]
    assert summary.traces == 2
    assert summary.sessions == ("intake", "sweep")
    assert summary.model_calls == 2
    assert summary.tool_calls == 2
    assert summary.input_tokens == 10
    assert summary.output_tokens == 4
    assert summary.duration_ms >= 0


def test_session_inside_another_span_is_a_new_root() -> None:
    tracer = Tracer()
    with (
        tracer.tracer.start_as_current_span("outer"),
        tracer.session(ticket_id=TICKET, kind="intake"),
    ):
        pass
    spans = by_name(tracer)
    root = spans["invoke_agent tier0"]
    assert root.parent is None
    assert root.get_span_context().trace_id != spans["outer"].get_span_context().trace_id


def serialize(tracer: Tracer) -> str:
    parts = []
    for span in tracer.finished_spans():
        parts.append(span.name)
        parts.append(json.dumps(dict(span.attributes or {}), default=str))
        for event in span.events:
            parts.append(event.name)
            parts.append(json.dumps(dict(event.attributes or {}), default=str))
        parts.append(str(span.status.description))
    return "\n".join(parts)


def test_no_content_or_raw_ids_by_default() -> None:
    tracer = Tracer(pseudonymizer=Pseudonymizer(b"k" * 32))
    with tracer.session(ticket_id=TICKET, kind="intake", requester_id=REQUESTER) as session:
        session.chat(StubModel(), triage_request(f"{MARKER} from {EMAIL}"))
    text = serialize(tracer)
    assert "zebra-umbrella" not in text
    assert "@meridian.example" not in text
    assert REQUESTER not in text
    root = by_name(tracer)["invoke_agent tier0"]
    pseudo = root.attributes[tracing.ENDUSER_PSEUDO_ID]
    assert len(pseudo) == 16
    int(pseudo, 16)
    again = Tracer(pseudonymizer=Pseudonymizer(b"k" * 32))
    other = Tracer(pseudonymizer=Pseudonymizer(b"z" * 32))
    for t, same in ((again, True), (other, False)):
        with t.session(ticket_id=TICKET, kind="intake", requester_id=REQUESTER):
            pass
        got = t.finished_spans()[0].attributes[tracing.ENDUSER_PSEUDO_ID]
        assert (got == pseudo) is same


def test_capture_content_is_the_only_way_content_gets_in() -> None:
    tracer = Tracer(capture_content=True)
    with tracer.session(ticket_id=TICKET, kind="intake") as session:
        session.chat(StubModel(), triage_request(MARKER))
    chat = by_name(tracer)["chat tier0-stub-1"]
    inputs = json.loads(chat.attributes[tracing.GEN_AI_INPUT_MESSAGES])
    assert [m["role"] for m in inputs] == ["system", "user"]
    assert MARKER in inputs[1]["content"]
    outputs = json.loads(chat.attributes[tracing.GEN_AI_OUTPUT_MESSAGES])
    assert outputs[0]["role"] == "assistant"


def test_event_records_codes_and_rejects_content() -> None:
    tracer = Tracer()
    with tracer.session(ticket_id=TICKET, kind="intake") as session:
        session.event("tier0.policy.decision", category="own_username", tier=0, action="reply")
        with pytest.raises(ValueError, match="short code"):
            session.event("x", note="a@b.example")
        with pytest.raises(ValueError, match="short code"):
            session.event("x", note="a" * 65)
    root = by_name(tracer)["invoke_agent tier0"]
    assert [e.name for e in root.events] == ["tier0.policy.decision"]
    assert dict(root.events[0].attributes) == {
        "category": "own_username",
        "tier": 0,
        "action": "reply",
    }


class FailingModel:
    provider = "test"
    model = "failing-1"

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise ModelError("secret detail alex.kim@meridian.example")


def test_model_error_marks_chat_span() -> None:
    tracer = Tracer()
    with tracer.session(ticket_id=TICKET, kind="intake") as session, pytest.raises(ModelError):
        session.chat(FailingModel(), triage_request())
    chat = by_name(tracer)["chat failing-1"]
    assert chat.status.status_code == StatusCode.ERROR
    assert chat.status.description is None
    assert chat.attributes[tracing.ERROR_TYPE] == "ModelError"
    assert chat.events == ()
    assert "secret detail" not in serialize(tracer)


def test_exception_in_session_marks_root_and_reraises() -> None:
    tracer = Tracer()
    with pytest.raises(RuntimeError), tracer.session(ticket_id=TICKET, kind="sweep"):
        raise RuntimeError("boom alex.kim@meridian.example")
    root = by_name(tracer)["invoke_agent tier0"]
    assert root.status.status_code == StatusCode.ERROR
    assert root.attributes[tracing.ERROR_TYPE] == "RuntimeError"
    assert root.events == ()


def test_tool_error_marks_tool_span() -> None:
    tracer = Tracer()
    with (
        pytest.raises(KeyError),
        tracer.session(ticket_id=TICKET, kind="intake") as session,
        session.tool("t", "c"),
    ):
        raise KeyError("x")
    tool = by_name(tracer)["execute_tool t"]
    assert tool.attributes[tracing.ERROR_TYPE] == "KeyError"


def test_exporter_writes_otlp_json_lines(tmp_path: Path) -> None:
    tracer = Tracer(out_dir=tmp_path / "traces")
    with tracer.session(ticket_id=TICKET, kind="intake", requester_id=REQUESTER) as session:
        session.chat(StubModel(), triage_request())
        session.event("tier0.policy.decision", action="reply")
    assert tracer.trace_file == tmp_path / "traces" / "traces.jsonl"
    lines = tracer.trace_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # one export call per finished span
    spans = []
    for line in lines:
        request = json.loads(line)
        (resource_spans,) = request["resourceSpans"]
        resource = {a["key"]: a["value"] for a in resource_spans["resource"]["attributes"]}
        assert resource["service.name"] == {"stringValue": "tier0"}
        (scope_spans,) = resource_spans["scopeSpans"]
        assert scope_spans["scope"]["name"] == "tier0"
        assert scope_spans["schemaUrl"] == tracing.SCHEMA_URL
        spans.extend(scope_spans["spans"])
    chat = next(s for s in spans if s["name"].startswith("chat"))
    root = next(s for s in spans if s["name"] == "invoke_agent tier0")
    assert len(chat["traceId"]) == 32 and len(chat["spanId"]) == 16
    assert chat["parentSpanId"] == root["spanId"]
    assert "parentSpanId" not in root
    assert chat["kind"] == 3 and root["kind"] == 1
    assert chat["startTimeUnixNano"].isdigit() and chat["endTimeUnixNano"].isdigit()
    attrs = {a["key"]: a["value"] for a in chat["attributes"]}
    assert attrs[tracing.GEN_AI_USAGE_INPUT_TOKENS] == {"intValue": "5"}
    assert attrs[tracing.GEN_AI_RESPONSE_FINISH_REASONS] == {
        "arrayValue": {"values": [{"stringValue": "stop"}]}
    }
    assert root["status"] == {"code": 0}
    assert root["events"][0]["name"] == "tier0.policy.decision"
    assert REQUESTER not in "".join(lines)


def test_global_tracer_provider_is_untouched() -> None:
    tracer = Tracer()
    assert trace.get_tracer_provider() is not tracer.provider
