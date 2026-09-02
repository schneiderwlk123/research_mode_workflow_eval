from collectors.tool_call_events import (
    ToolOutcomeCondition,
    ToolSuccessRule,
    normalize_chat_tool_call_events,
    normalize_research_tool_call_events,
)

_RULES = {
    "quote_tool": ToolSuccessRule(
        rule_id="quote-tool-v1",
        success_conditions=(
            ToolOutcomeCondition(path=("result", "success"), accepted_values=(True,)),
        ),
        failure_conditions=(
            ToolOutcomeCondition(path=("result", "errorcode"), accepted_values=(500,)),
        ),
    ),
    "status_only_tool": ToolSuccessRule(
        rule_id="status-only-v1",
        completed_is_success=True,
    ),
}


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
                "tool_name": None,
                "request_observed": True,
                "terminal_observed": True,
                "success_observed": None,
                "success_rule_id": None,
                "source_event_count": 1,
            },
            {
                "call_id": "chat-tool-call-1",
                "tool_name": None,
                "request_observed": True,
                "terminal_observed": False,
                "success_observed": False,
                "success_rule_id": None,
                "source_event_count": 1,
            },
            {
                "call_id": "chat-tool-call-2",
                "tool_name": None,
                "request_observed": False,
                "terminal_observed": True,
                "success_observed": None,
                "success_rule_id": None,
                "source_event_count": 1,
            },
        ),
        "diagnostics": {
            "duplicate_state_event_count": 0,
            "malformed_event_count": 0,
        },
        "normalization_error": None,
        "success_normalization_error": None,
    }
    assert "不得进入规范化结果" not in repr(normalized)
    assert "不得保留" not in repr(normalized)


def test_chat_tool_success_uses_versioned_rule_without_retaining_payload() -> None:
    trace = {
        "traceId": "trace-001",
        "tool_call_queue": [
            {
                "event_type": "tool_call",
                "tool_request_args": {
                    "toolName": "quote_tool",
                    "arguments": {"token": "不得保留的请求"},
                },
                "tool_response_result": {
                    "toolName": "quote_tool",
                    "result": {"success": True, "content": "不得保留的响应"},
                },
            },
            {
                "event_type": "tool_call",
                "tool_request_args": {"toolName": "quote_tool"},
                "tool_response_result": {
                    "result": {"success": True, "errorcode": 500},
                },
            },
            {
                "event_type": "tool_call",
                "tool_request_args": {"toolName": "unknown_tool"},
                "tool_response_result": {"result": {"success": True}},
            },
            {
                "event_type": "tool_call",
                "tool_request_args": {"toolName": "quote_tool"},
            },
        ],
    }

    normalized = normalize_chat_tool_call_events(trace, success_rules=_RULES)

    assert [call["success_observed"] for call in normalized["tool_calls"]] == [
        True,
        False,
        None,
        False,
    ]
    assert [call["success_rule_id"] for call in normalized["tool_calls"]] == [
        "quote-tool-v1",
        "quote-tool-v1",
        None,
        None,
    ]
    assert "不得保留的请求" not in repr(normalized)
    assert "不得保留的响应" not in repr(normalized)


def test_chat_conflicting_tool_names_invalidate_strict_normalization() -> None:
    trace = {
        "tool_call_queue": [
            {
                "event_type": "tool_call",
                "tool_request_args": {"toolName": "quote_tool"},
                "tool_response_result": {
                    "toolName": "another_tool",
                    "result": {"success": True},
                },
            }
        ]
    }

    normalized = normalize_chat_tool_call_events(trace, success_rules=_RULES)

    assert len(normalized["tool_calls"]) == 1
    assert normalized["normalization_error"] is None
    assert normalized["success_normalization_error"] == "tool_name_mismatch"


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
            "tool_name": None,
            "request_observed": True,
            "terminal_observed": True,
            "success_observed": None,
            "success_rule_id": None,
            "source_event_count": 4,
        },
        {
            "call_id": "call-2",
            "tool_name": None,
            "request_observed": True,
            "terminal_observed": True,
            "success_observed": False,
            "success_rule_id": None,
            "source_event_count": 2,
        },
    )
    assert normalized["diagnostics"] == {
        "duplicate_state_event_count": 1,
        "malformed_event_count": 0,
    }
    assert normalized["normalization_error"] is None
    assert normalized["success_normalization_error"] is None


def test_research_tool_success_correlates_tool_name_by_call_id() -> None:
    events = [
        {
            "type": "model.completed",
            "result": {
                "toolCalls": [
                    {
                        "toolCallId": "call-1",
                        "toolName": "status_only_tool",
                        "input": {"token": "不得保留的输入"},
                    },
                    {"toolCallId": "call-2", "toolName": "status_only_tool"},
                ]
            },
        },
        _research_tool_event("call-1", "running"),
        _research_tool_event("call-1", "completed", raw={"secret": "不得保留"}),
        _research_tool_event("call-2", "failed"),
    ]

    normalized = normalize_research_tool_call_events(events, success_rules=_RULES)

    assert normalized["tool_calls"] == (
        {
            "call_id": "call-1",
            "tool_name": "status_only_tool",
            "request_observed": True,
            "terminal_observed": True,
            "success_observed": True,
            "success_rule_id": "status-only-v1",
            "source_event_count": 2,
        },
        {
            "call_id": "call-2",
            "tool_name": "status_only_tool",
            "request_observed": False,
            "terminal_observed": True,
            "success_observed": False,
            "success_rule_id": None,
            "source_event_count": 1,
        },
    )
    assert "不得保留的输入" not in repr(normalized)
    assert "不得保留" not in repr(normalized)


def test_research_duplicate_completed_event_does_not_erase_known_success() -> None:
    rules = {
        "quote_tool": ToolSuccessRule(
            rule_id="research-quote-v1",
            success_conditions=(
                ToolOutcomeCondition(path=("output", "success"), accepted_values=(True,)),
            ),
        )
    }
    events = [
        {"result": {"toolCalls": [{"toolCallId": "call-1", "toolName": "quote_tool"}]}},
        _research_tool_event("call-1", "completed", output='{"success": true}'),
        _research_tool_event("call-1", "completed"),
    ]

    normalized = normalize_research_tool_call_events(events, success_rules=rules)

    assert normalized["tool_calls"][0]["success_observed"] is True
    assert normalized["tool_calls"][0]["success_rule_id"] == "research-quote-v1"
    assert normalized["diagnostics"]["duplicate_state_event_count"] == 1


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
            "tool_name": None,
            "request_observed": False,
            "terminal_observed": False,
            "success_observed": False,
            "success_rule_id": None,
            "source_event_count": 1,
        },
    )
    assert normalized["diagnostics"]["malformed_event_count"] == 1
    assert "secret" not in repr(normalized)


def _research_tool_event(
    call_id: str | None,
    status: str,
    *,
    raw: object | None = None,
    output: object | None = None,
) -> dict[str, object]:
    state: dict[str, object] = {
        "status": status,
        "input": {"query": "sensitive"},
    }
    if raw is not None:
        state["raw"] = raw
    if output is not None:
        state["output"] = output
    part: dict[str, object] = {
        "type": "tool",
        "state": state,
    }
    if call_id is not None:
        part["callID"] = call_id
    return {"type": "message.part.updated", "part": part}
