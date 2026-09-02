from collectors.exact_duplicate_calls import (
    normalize_chat_exact_duplicates,
    normalize_research_exact_duplicates,
)


def test_chat_counts_only_repeated_request_and_response_content() -> None:
    trace = {
        "traceId": "trace-001",
        "tool_call_queue": [
            _chat_call("quote", {"symbol": "AAA", "window": 1}, {"data": [1, 2]}),
            _chat_call("quote", {"window": 1, "symbol": "AAA"}, '{"data": [1, 2]}'),
            _chat_call("quote", {"symbol": "AAA", "window": 1}, {"data": [1, 3]}),
            _chat_call("quote", {"symbol": "BBB", "window": 1}, {"data": [1, 2]}),
        ],
    }

    normalized = normalize_chat_exact_duplicates(trace)

    assert normalized == {
        "trace_id": "trace-001",
        "mode": "chat",
        "unique_call_count": 4,
        "comparable_call_count": 4,
        "exact_duplicate_call_count": 1,
        "diagnostics": {
            "malformed_event_count": 0,
            "missing_response_payload_count": 0,
            "duplicate_state_event_count": 0,
        },
        "normalization_error": None,
    }
    assert "AAA" not in repr(normalized)


def test_chat_missing_response_is_not_counted_as_exact_duplicate() -> None:
    trace = {
        "tool_call_queue": [
            _chat_call("quote", {"symbol": "AAA"}, None),
            _chat_call("quote", {"symbol": "AAA"}, None),
        ]
    }

    normalized = normalize_chat_exact_duplicates(trace)

    assert normalized["unique_call_count"] == 2
    assert normalized["comparable_call_count"] == 0
    assert normalized["exact_duplicate_call_count"] == 0
    assert normalized["diagnostics"]["missing_response_payload_count"] == 2


def test_chat_missing_tool_name_or_arguments_is_not_computable() -> None:
    trace = {
        "tool_call_queue": [
            {
                "event_type": "tool_call",
                "tool_request_args": {"toolName": "quote"},
                "tool_response_result": {"result": {"data": []}},
            }
        ]
    }

    normalized = normalize_chat_exact_duplicates(trace)

    assert normalized["normalization_error"] == "missing_duplicate_key"


def test_invalid_chat_queue_is_isolated_as_normalization_error() -> None:
    normalized = normalize_chat_exact_duplicates({"tool_call_queue": "invalid"})

    assert normalized["unique_call_count"] == 0
    assert normalized["normalization_error"] == "invalid_tool_call_queue"


def test_research_deduplicates_lifecycle_events_before_comparing_calls() -> None:
    events = [
        _research_requests(
            ("call-1", "excel", {"range": "A1"}),
            ("call-2", "excel", {"range": "A1"}),
            ("call-3", "excel", {"range": "A1"}),
        ),
        _research_state("call-1", "pending"),
        _research_state("call-1", "completed", output={"value": 1}),
        _research_state("call-1", "completed", output={"value": 1}),
        _research_state("call-2", "running"),
        _research_state("call-2", "completed", output='{"value": 1}'),
        _research_state("call-3", "completed", output={"value": 2}),
    ]

    normalized = normalize_research_exact_duplicates(events)

    assert normalized == {
        "trace_id": None,
        "mode": "research",
        "unique_call_count": 3,
        "comparable_call_count": 3,
        "exact_duplicate_call_count": 1,
        "diagnostics": {
            "malformed_event_count": 0,
            "missing_response_payload_count": 0,
            "duplicate_state_event_count": 1,
        },
        "normalization_error": None,
    }
    assert "A1" not in repr(normalized)


def test_research_tool_event_without_request_mapping_is_not_computable() -> None:
    events = [_research_state("call-1", "completed", output={"value": 1})]

    normalized = normalize_research_exact_duplicates(events)

    assert normalized["normalization_error"] == "missing_duplicate_key"


def test_research_tool_event_without_call_id_is_not_computable() -> None:
    events = [
        {
            "type": "message.part.updated",
            "part": {"type": "tool", "state": {"status": "completed", "output": {}}},
        }
    ]

    normalized = normalize_research_exact_duplicates(events)

    assert normalized["normalization_error"] == "missing_call_id"


def _chat_call(tool_name: str, arguments: object, result: object | None) -> dict[str, object]:
    event: dict[str, object] = {
        "event_type": "tool_call",
        "tool_request_args": {
            "toolName": tool_name,
            "arguments": arguments,
        },
    }
    if result is not None:
        event["tool_response_result"] = {
            "create_time": "2026-01-01T00:00:00Z",
            "result": result,
        }
    return event


def _research_requests(*calls: tuple[str, str, object]) -> dict[str, object]:
    return {
        "type": "model.completed",
        "result": {
            "toolCalls": [
                {"toolCallId": call_id, "toolName": tool_name, "input": arguments}
                for call_id, tool_name, arguments in calls
            ]
        },
    }


def _research_state(
    call_id: str,
    status: str,
    *,
    output: object | None = None,
) -> dict[str, object]:
    state: dict[str, object] = {"status": status}
    if output is not None:
        state["output"] = output
    return {
        "type": "message.part.updated",
        "part": {"type": "tool", "callID": call_id, "state": state},
    }
