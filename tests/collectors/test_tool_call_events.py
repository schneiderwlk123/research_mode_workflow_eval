from collectors.tool_call_events import (
    normalize_chat_tool_call_events,
    normalize_research_tool_call_events,
)


def test_chat_tool_calls_preserve_incomplete_events_without_sensitive_payloads() -> None:
    trace = {
        "traceId": "trace-001",
        "tool_call_queue": [
            {
                "event_type": "tool_call",
                "tool_request_args": {"secret_query": "不得进入规范化结果"},
                "tool_response_result": {"status": "failed", "raw": "不得保留"},
            },
            {
                "event_type": "tool_call",
                "tool_request_args": {"query": "only-request"},
            },
            {
                "event_type": "tool_call",
                "tool_response_result": {"status": "completed"},
            },
            {"event_type": "message"},
        ],
    }

    normalized = normalize_chat_tool_call_events(trace)

    assert normalized == {
        "trace_id": "trace-001",
        "mode": "chat",
        "tool_calls": (
            {
                "call_id": "chat-tool-call-0",
                "request_observed": True,
                "terminal_observed": True,
                "source_event_count": 1,
            },
            {
                "call_id": "chat-tool-call-1",
                "request_observed": True,
                "terminal_observed": False,
                "source_event_count": 1,
            },
            {
                "call_id": "chat-tool-call-2",
                "request_observed": False,
                "terminal_observed": True,
                "source_event_count": 1,
            },
        ),
        "diagnostics": {
            "duplicate_state_event_count": 0,
            "malformed_event_count": 0,
        },
        "normalization_error": None,
    }
    assert "不得进入规范化结果" not in repr(normalized)
    assert "不得保留" not in repr(normalized)


def test_research_tool_calls_are_deduplicated_and_failure_is_terminal() -> None:
    events = [
        _research_tool_event("call-1", "pending"),
        _research_tool_event("call-1", "running"),
        _research_tool_event("call-1", "running"),
        _research_tool_event("call-1", "completed"),
        _research_tool_event("call-2", "running"),
        _research_tool_event("call-2", "failed"),
        {"type": "message.part.updated", "part": {"type": "text"}},
    ]

    normalized = normalize_research_tool_call_events(
        events,
        session_id="session-001",
        user_message_id="message-001",
    )

    assert normalized["trace_id"] == "session-001:message-001"
    assert normalized["mode"] == "research"
    assert normalized["tool_calls"] == (
        {
            "call_id": "call-1",
            "request_observed": True,
            "terminal_observed": True,
            "source_event_count": 4,
        },
        {
            "call_id": "call-2",
            "request_observed": True,
            "terminal_observed": True,
            "source_event_count": 2,
        },
    )
    assert normalized["diagnostics"] == {
        "duplicate_state_event_count": 1,
        "malformed_event_count": 0,
    }
    assert normalized["normalization_error"] is None


def test_research_trace_id_is_inferred_from_turn_start() -> None:
    events = [
        {
            "type": "turn.start",
            "sessionID": "session-001",
            "userMessageID": "message-001",
        },
        _research_tool_event("call-1", "running"),
        _research_tool_event("call-1", "completed"),
    ]

    normalized = normalize_research_tool_call_events(events)

    assert normalized["trace_id"] == "session-001:message-001"


def test_research_tool_event_without_call_id_invalidates_the_session() -> None:
    events = [
        _research_tool_event("call-1", "completed"),
        _research_tool_event(None, "running"),
    ]

    normalized = normalize_research_tool_call_events(events)

    assert normalized["tool_calls"] == ()
    assert normalized["normalization_error"] == "missing_call_id"
    assert normalized["diagnostics"] == {
        "duplicate_state_event_count": 0,
        "malformed_event_count": 1,
    }


def test_research_unknown_or_missing_status_is_diagnosed_without_exposing_payload() -> None:
    events = [
        {
            "type": "message.part.updated",
            "part": {
                "type": "tool",
                "callID": "call-1",
                "state": {"input": {"token": "secret"}},
            },
        }
    ]

    normalized = normalize_research_tool_call_events(events)

    assert normalized["tool_calls"] == (
        {
            "call_id": "call-1",
            "request_observed": False,
            "terminal_observed": False,
            "source_event_count": 1,
        },
    )
    assert normalized["diagnostics"]["malformed_event_count"] == 1
    assert "secret" not in repr(normalized)


def _research_tool_event(call_id: str | None, status: str) -> dict[str, object]:
    part: dict[str, object] = {
        "type": "tool",
        "state": {"status": status, "input": {"query": "sensitive"}},
    }
    if call_id is not None:
        part["callID"] = call_id
    return {"type": "message.part.updated", "part": part}
