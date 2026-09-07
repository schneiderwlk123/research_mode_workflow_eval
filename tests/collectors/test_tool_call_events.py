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
            "duplicate_request_observation_count": 0,
            "duplicate_result_observation_count": 0,
            "orphan_result_count": 0,
            "malformed_event_count": 0,
        },
        "normalization_error": None,
    }
    assert "不得进入规范化结果" not in repr(normalized)
    assert "不得保留" not in repr(normalized)


def test_canvas_chat_calls_are_joined_by_tool_id_across_messages() -> None:
    trace = {
        "userId": "user-redacted",
        "canvas": {
            "meta": {"traceId": "trace-canvas-001"},
            "agents": [
                {
                    "messages": [
                        {
                            "role": "assistant",
                            "content": {
                                "tool_call": [
                                    {
                                        "toolId": "tool-1",
                                        "toolName": "search",
                                        "arguments": {"query": "不得保留的请求"},
                                    },
                                    {
                                        "taskId": "worker-1",
                                        "agentName": "worker_search",
                                        "arguments": {"query": "不得保留的子任务"},
                                    },
                                    {
                                        "toolId": "tool-2",
                                        "toolName": "quote",
                                        "arguments": {},
                                    },
                                ]
                            },
                        },
                        {
                            "role": "assistant",
                            "content": {
                                "tool_result": [
                                    {"toolId": "tool-1", "result": {"secret": "不得保留"}},
                                    {
                                        "toolId": "worker-result-1",
                                        "toolName": "direct_answer",
                                    },
                                ]
                            },
                        },
                    ]
                }
            ],
            "answer": {},
        },
    }

    normalized = normalize_chat_tool_call_events(trace)

    assert normalized == {
        "trace_id": "trace-canvas-001",
        "mode": "chat",
        "tool_calls": (
            {
                "call_id": "tool-1",
                "request_observed": True,
                "terminal_observed": True,
                "source_event_count": 2,
            },
            {
                "call_id": "tool-2",
                "request_observed": True,
                "terminal_observed": False,
                "source_event_count": 1,
            },
        ),
        "diagnostics": {
            "duplicate_state_event_count": 0,
            "duplicate_request_observation_count": 0,
            "duplicate_result_observation_count": 0,
            "orphan_result_count": 1,
            "malformed_event_count": 0,
        },
        "normalization_error": None,
    }
    assert "不得保留" not in repr(normalized)


def test_canvas_chat_duplicate_observations_do_not_inflate_unique_calls() -> None:
    request = {"toolId": "tool-1", "toolName": "search", "arguments": {}}
    trace = {
        "canvas": {
            "meta": {"traceId": "trace-canvas-002"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [request, dict(request)],
                                "tool_result": [
                                    {"toolId": "tool-1"},
                                    {"toolId": "tool-1", "toolName": "search"},
                                ],
                            }
                        }
                    ]
                }
            ],
        }
    }

    normalized = normalize_chat_tool_call_events(trace)

    assert normalized["tool_calls"] == (
        {
            "call_id": "tool-1",
            "request_observed": True,
            "terminal_observed": True,
            "source_event_count": 4,
        },
    )
    assert normalized["diagnostics"]["duplicate_request_observation_count"] == 1
    assert normalized["diagnostics"]["duplicate_result_observation_count"] == 1


def test_canvas_chat_conflicting_tool_name_invalidates_trace() -> None:
    trace = {
        "canvas": {
            "meta": {"traceId": "trace-canvas-003"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [
                                    {"toolId": "tool-1", "toolName": "search", "arguments": {}}
                                ],
                                "tool_result": [{"toolId": "tool-1", "toolName": "quote"}],
                            }
                        }
                    ]
                }
            ],
        }
    }

    normalized = normalize_chat_tool_call_events(trace)

    assert normalized["normalization_error"] == "conflicting_call_definition"


def test_canvas_chat_requires_trace_and_tool_identifiers() -> None:
    missing_trace: dict[str, object] = {"canvas": {"meta": {}, "agents": []}}
    missing_call_id = {
        "canvas": {
            "meta": {"traceId": "trace-canvas-004"},
            "agents": [{"messages": [{"content": {"tool_call": [{"toolName": "search"}]}}]}],
        }
    }
    missing_tool_name = {
        "canvas": {
            "meta": {"traceId": "trace-canvas-005"},
            "agents": [{"messages": [{"content": {"tool_call": [{"toolId": "tool-1"}]}}]}],
        }
    }

    assert normalize_chat_tool_call_events(missing_trace)["normalization_error"] == (
        "missing_trace_id"
    )
    assert normalize_chat_tool_call_events(missing_call_id)["normalization_error"] == (
        "missing_call_id"
    )
    assert normalize_chat_tool_call_events(missing_tool_name)["normalization_error"] == (
        "missing_tool_name"
    )


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
        "duplicate_request_observation_count": 0,
        "duplicate_result_observation_count": 0,
        "orphan_result_count": 0,
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
        "duplicate_request_observation_count": 0,
        "duplicate_result_observation_count": 0,
        "orphan_result_count": 0,
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
