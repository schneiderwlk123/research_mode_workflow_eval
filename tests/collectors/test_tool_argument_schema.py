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
    trace = {
        "traceId": "trace-001",
        "tool_call_queue": [
            _chat_call("quote", {"window": 5}),
            _chat_call("quote", {"symbol": "AAA", "window": "5"}),
            _chat_call("quote", {"symbol": "AAA", "mode": "invalid"}),
            _chat_call("quote", {"filters": [{"op": "eq"}]}),
            _chat_call("quote", {"symbol": "AAA", "extra": True}),
            _chat_call("quote", "not-json"),
            _chat_call("not-in-manual", {"value": 1}),
        ],
    }

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
    trace = {
        "tool_call_queue": [
            _chat_call("batch", {"periods": [1, 5]}),
            _chat_call("batch", {"periods": [1, True]}),
            _chat_call("batch", {"periods": [1, 7]}),
        ]
    }

    normalized = normalize_chat_tool_argument_schema(trace, catalog)

    assert normalized["schema_valid_call_count"] == 1
    assert normalized["diagnostics"]["type_violation_call_count"] == 1
    assert normalized["diagnostics"]["enum_violation_call_count"] == 1


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


def _chat_call(tool_name: str, arguments: object) -> dict[str, object]:
    return {
        "event_type": "tool_call",
        "tool_request_args": {"toolName": tool_name, "arguments": arguments},
    }


def _research_call(call_id: str | None, tool_name: str, arguments: object) -> dict[str, object]:
    return {"toolCallId": call_id, "toolName": tool_name, "input": arguments}
