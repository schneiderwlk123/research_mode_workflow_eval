import pytest

from collectors.tool_argument_schema import (
    ToolArgumentSchemaCatalog,
    build_tool_argument_schema_catalog,
    normalize_chat_tool_argument_schema,
    normalize_research_tool_argument_schema,
)


def _catalog() -> ToolArgumentSchemaCatalog:
    return build_tool_argument_schema_catalog(
        {
            "manual_version": "0.1.0",
            "tools": [
                {
                    "name": "quote",
                    "parameters": [
                        {"name": "symbol", "type": "string", "required": True, "enum": []},
                        {"name": "window", "type": "integer", "required": False, "enum": []},
                        {
                            "name": "mode",
                            "type": "string",
                            "required": False,
                            "enum": ["fast", "full"],
                        },
                        {
                            "name": "filters",
                            "type": "object[]",
                            "required": False,
                            "enum": [],
                            "nested": [
                                {
                                    "name": "field",
                                    "type": "string",
                                    "required": True,
                                },
                                {
                                    "name": "op",
                                    "type": "string",
                                    "required": True,
                                    "enum": ["eq", "gt"],
                                },
                            ],
                        },
                    ],
                }
            ],
        }
    )


def test_chat_validates_present_fields_without_rechecking_root_required_fields() -> None:
    trace = _canvas_trace(
        [
            _chat_call("call-1", "quote", {"window": 5}),
            _chat_call("call-2", "quote", {"symbol": "AAA", "window": "5"}),
            _chat_call("call-3", "quote", {"symbol": "AAA", "mode": "invalid"}),
            _chat_call("call-4", "quote", {"filters": [{"op": "eq"}]}),
            _chat_call("call-5", "quote", {"symbol": "AAA", "extra": "sensitive"}),
            _chat_call("call-6", "quote", "not-json"),
            _chat_call("call-7", "not-in-manual", {"value": 1}),
            {"agentName": "worker_search", "arguments": {"query": "private-query"}},
        ]
    )

    normalized = normalize_chat_tool_argument_schema(trace, _catalog())

    assert normalized == {
        "trace_id": "trace-001",
        "mode": "chat",
        "manual_version": "0.1.0",
        "total_call_count": 7,
        "schema_covered_call_count": 6,
        "schema_valid_call_count": 2,
        "schema_invalid_call_count": 4,
        "diagnostics": {
            "unknown_schema_call_count": 1,
            "malformed_arguments_call_count": 1,
            "type_violation_call_count": 1,
            "enum_violation_call_count": 1,
            "missing_nested_required_call_count": 1,
            "undeclared_argument_call_count": 1,
            "duplicate_call_definition_count": 0,
            "malformed_event_count": 0,
        },
        "normalization_error": None,
    }
    assert "sensitive" not in repr(normalized)
    assert "private-query" not in repr(normalized)


def test_array_element_types_and_enums_are_validated() -> None:
    catalog = build_tool_argument_schema_catalog(
        {
            "manual_version": "1",
            "tools": [
                {
                    "name": "batch",
                    "parameters": [
                        {
                            "name": "periods",
                            "type": "integer[]",
                            "required": False,
                            "enum": [1, 5, 10],
                        }
                    ],
                }
            ],
        }
    )
    trace = _canvas_trace(
        [
            _chat_call("call-1", "batch", {"periods": [1, 5]}),
            _chat_call("call-2", "batch", {"periods": [1, True]}),
            _chat_call("call-3", "batch", {"periods": [1, 7]}),
        ]
    )

    normalized = normalize_chat_tool_argument_schema(trace, catalog)

    assert normalized["schema_valid_call_count"] == 1
    assert normalized["diagnostics"]["type_violation_call_count"] == 1
    assert normalized["diagnostics"]["enum_violation_call_count"] == 1


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

    normalized = normalize_chat_tool_argument_schema(trace, _catalog())

    assert normalized["total_call_count"] == 1
    assert normalized["schema_valid_call_count"] == 1
    assert normalized["diagnostics"]["duplicate_call_definition_count"] == 1
    assert normalized["normalization_error"] is None


def test_chat_conflicting_duplicate_definition_is_not_computable() -> None:
    trace = _canvas_trace(
        [
            _chat_call("call-1", "quote", {"symbol": "AAA"}),
            _chat_call("call-1", "quote", {"symbol": "BBB"}),
        ]
    )

    normalized = normalize_chat_tool_argument_schema(trace, _catalog())

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
    normalized = normalize_chat_tool_argument_schema(_canvas_trace([call]), _catalog())

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
    normalized = normalize_chat_tool_argument_schema(trace, _catalog())

    assert normalized["normalization_error"] in {
        "invalid_chat_trace_structure",
        "missing_trace_id",
    }
    assert normalized["diagnostics"]["malformed_event_count"] == 1


def test_legacy_chat_tool_call_queue_is_not_supported() -> None:
    normalized = normalize_chat_tool_argument_schema(
        {
            "traceId": "legacy-trace",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {
                        "toolName": "quote",
                        "arguments": {"symbol": "AAA"},
                    },
                }
            ],
        },
        _catalog(),
    )

    assert normalized["total_call_count"] == 0
    assert normalized["normalization_error"] == "invalid_chat_trace_structure"


def test_research_deduplicates_identical_call_definitions() -> None:
    call = _research_call("call-1", "quote", {"symbol": "AAA", "window": 5})
    events = [
        {
            "sessionID": "session-1",
            "userMessageID": "message-1",
            "result": {"toolCalls": [call]},
        },
        {"result": {"toolCalls": [call]}},
    ]

    normalized = normalize_research_tool_argument_schema(events, _catalog())

    assert normalized["trace_id"] == "session-1:message-1"
    assert normalized["total_call_count"] == 1
    assert normalized["schema_valid_call_count"] == 1
    assert normalized["diagnostics"]["duplicate_call_definition_count"] == 1
    assert normalized["normalization_error"] is None


def test_research_missing_or_conflicting_call_id_is_not_computable() -> None:
    missing = normalize_research_tool_argument_schema(
        [{"result": {"toolCalls": [_research_call(None, "quote", {})]}}], _catalog()
    )
    conflicting = normalize_research_tool_argument_schema(
        [
            {"result": {"toolCalls": [_research_call("call-1", "quote", {})]}},
            {"result": {"toolCalls": [_research_call("call-1", "quote", {"window": 5})]}},
        ],
        _catalog(),
    )

    assert missing["normalization_error"] == "missing_call_id"
    assert conflicting["normalization_error"] == "conflicting_call_definition"


def test_unknown_manual_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="不支持的参数类型"):
        build_tool_argument_schema_catalog(
            {
                "manual_version": "1",
                "tools": [
                    {
                        "name": "quote",
                        "parameters": [{"name": "symbol", "type": "date", "required": False}],
                    }
                ],
            }
        )


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
