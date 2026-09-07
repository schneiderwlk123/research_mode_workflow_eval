import pytest

from collectors.required_parameters import (
    RequiredParameterCatalog,
    build_required_parameter_catalog,
    normalize_chat_required_parameters,
    normalize_research_required_parameters,
)


def _catalog() -> RequiredParameterCatalog:
    return build_required_parameter_catalog(
        {
            "manual_version": "0.1.0",
            "tools": [
                {
                    "name": "quote",
                    "parameters": [
                        {"name": "symbol", "required": True},
                        {"name": "window", "required": True},
                        {"name": "market", "required": False},
                    ],
                },
                {"name": "health", "parameters": []},
            ],
        }
    )


def test_chat_counts_only_non_empty_required_parameters_for_known_schemas() -> None:
    trace = _canvas_trace(
        [
            _chat_call("call-1", "quote", {"symbol": "AAA", "window": 0}),
            _chat_call("call-2", "quote", {"symbol": "  ", "window": False}),
            _chat_call("call-3", "quote", '{"symbol":"unknown","window":5}'),
            _chat_call("call-4", "quote", "not-json"),
            _chat_call("call-5", "health", {}),
            _chat_call("call-6", "not-in-manual", {"value": 1}),
            {"agentName": "worker_search", "arguments": {"query": "sensitive"}},
        ]
    )

    normalized = normalize_chat_required_parameters(trace, _catalog())

    assert normalized == {
        "trace_id": "trace-001",
        "mode": "chat",
        "manual_version": "0.1.0",
        "total_call_count": 6,
        "schema_covered_call_count": 5,
        "eligible_call_count": 4,
        "required_parameter_count": 8,
        "filled_required_parameter_count": 4,
        "diagnostics": {
            "unknown_schema_call_count": 1,
            "zero_required_parameter_call_count": 1,
            "malformed_arguments_call_count": 1,
            "missing_required_parameter_count": 4,
            "duplicate_call_definition_count": 0,
            "malformed_event_count": 0,
        },
        "normalization_error": None,
    }
    assert "sensitive" not in repr(normalized)


def test_chat_deduplicates_equivalent_definitions_across_messages() -> None:
    trace = {
        "canvas": {
            "meta": {"traceId": "trace-001"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [
                                    _chat_call("call-1", "quote", {"symbol": "AAA", "window": 5})
                                ]
                            }
                        }
                    ]
                },
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [
                                    _chat_call("call-1", "quote", '{"window":5,"symbol":"AAA"}')
                                ]
                            }
                        }
                    ]
                },
            ],
        }
    }

    normalized = normalize_chat_required_parameters(trace, _catalog())

    assert normalized["total_call_count"] == 1
    assert normalized["filled_required_parameter_count"] == 2
    assert normalized["diagnostics"]["duplicate_call_definition_count"] == 1
    assert normalized["normalization_error"] is None


def test_chat_conflicting_duplicate_definition_is_not_computable() -> None:
    trace = _canvas_trace(
        [
            _chat_call("call-1", "quote", {"symbol": "AAA", "window": 5}),
            _chat_call("call-1", "quote", {"symbol": "BBB", "window": 5}),
        ]
    )

    normalized = normalize_chat_required_parameters(trace, _catalog())

    assert normalized["total_call_count"] == 1
    assert normalized["diagnostics"]["duplicate_call_definition_count"] == 1
    assert normalized["normalization_error"] == "conflicting_call_definition"


@pytest.mark.parametrize(
    ("call", "reason"),
    [
        ({"toolName": "quote", "arguments": {}}, "missing_call_id"),
        ({"toolId": "call-1", "arguments": {}}, "missing_tool_name"),
        ({"toolId": "call-1", "toolName": "quote"}, "missing_call_arguments"),
    ],
)
def test_chat_missing_request_fields_are_not_computable(
    call: dict[str, object], reason: str
) -> None:
    normalized = normalize_chat_required_parameters(_canvas_trace([call]), _catalog())

    assert normalized["normalization_error"] == reason
    assert normalized["diagnostics"]["malformed_event_count"] == 1


@pytest.mark.parametrize(
    "trace",
    [
        {},
        {"canvas": {"meta": {}, "agents": []}},
        {"canvas": {"meta": {"traceId": "trace-001"}, "agents": "invalid"}},
        {
            "canvas": {
                "meta": {"traceId": "trace-001"},
                "agents": [{"messages": [{"content": {"tool_call": "invalid"}}]}],
            }
        },
    ],
)
def test_chat_invalid_canvas_structure_is_isolated(trace: dict[str, object]) -> None:
    normalized = normalize_chat_required_parameters(trace, _catalog())

    assert normalized["normalization_error"] in {
        "invalid_chat_trace_structure",
        "missing_trace_id",
    }
    assert normalized["diagnostics"]["malformed_event_count"] == 1


def test_legacy_chat_tool_call_queue_is_not_supported() -> None:
    normalized = normalize_chat_required_parameters(
        {
            "traceId": "legacy-trace",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {
                        "toolName": "quote",
                        "arguments": {"symbol": "AAA", "window": 5},
                    },
                }
            ],
        },
        _catalog(),
    )

    assert normalized["total_call_count"] == 0
    assert normalized["normalization_error"] == "invalid_chat_trace_structure"


def test_research_deduplicates_call_definitions_before_counting() -> None:
    events = [
        {
            "sessionID": "session-1",
            "userMessageID": "message-1",
            "result": {
                "toolCalls": [
                    _research_call("call-1", "quote", {"symbol": "AAA", "window": []}),
                    _research_call("call-2", "not-in-manual", {}),
                ]
            },
        },
        {
            "result": {
                "toolCalls": [_research_call("call-1", "quote", {"symbol": "AAA", "window": []})]
            }
        },
    ]

    normalized = normalize_research_required_parameters(events, _catalog())

    assert normalized["trace_id"] == "session-1:message-1"
    assert normalized["total_call_count"] == 2
    assert normalized["schema_covered_call_count"] == 1
    assert normalized["required_parameter_count"] == 2
    assert normalized["filled_required_parameter_count"] == 1
    assert normalized["diagnostics"]["missing_required_parameter_count"] == 1
    assert normalized["diagnostics"]["unknown_schema_call_count"] == 1
    assert normalized["diagnostics"]["duplicate_call_definition_count"] == 1
    assert normalized["normalization_error"] is None


def test_research_missing_call_id_makes_trace_not_computable() -> None:
    events = [{"result": {"toolCalls": [_research_call(None, "quote", {"symbol": "AAA"})]}}]

    normalized = normalize_research_required_parameters(events, _catalog())

    assert normalized["normalization_error"] == "missing_call_id"


def test_invalid_manual_schema_is_rejected() -> None:
    with pytest.raises(ValueError, match="manual_version"):
        build_required_parameter_catalog({"tools": []})


def _chat_call(call_id: str, tool_name: str, arguments: object) -> dict[str, object]:
    return {"toolId": call_id, "toolName": tool_name, "arguments": arguments}


def _canvas_trace(calls: list[dict[str, object]]) -> dict[str, object]:
    return {
        "canvas": {
            "meta": {"traceId": "trace-001"},
            "agents": [{"messages": [{"content": {"tool_call": calls}}]}],
        }
    }


def _research_call(call_id: str | None, tool_name: str, arguments: object) -> dict[str, object]:
    return {"toolCallId": call_id, "toolName": tool_name, "input": arguments}
