from copy import deepcopy
from types import MappingProxyType

import pytest

from collectors.parameter_value_accuracy import (
    build_parameter_value_catalog,
    normalize_chat_parameter_value_candidates,
    normalize_research_parameter_value_candidates,
)


def _manual() -> dict[str, object]:
    return {
        "manual_version": "0.1.0",
        "tools": [
            {
                "name": "quote",
                "description": "查询证券在指定时间范围内的行情。",
                "parameters": [
                    {
                        "name": "symbol",
                        "type": "string",
                        "required": True,
                        "description": "证券代码。",
                    },
                    {
                        "name": "start_date",
                        "type": "string",
                        "required": False,
                        "description": "查询开始日期。",
                    },
                    {
                        "name": "end_date",
                        "type": "string",
                        "required": False,
                        "description": "查询结束日期。",
                    },
                    {
                        "name": "options",
                        "type": "object",
                        "required": False,
                        "description": "附加选项。",
                        "nested": [
                            {
                                "name": "currency",
                                "type": "string",
                                "required": False,
                                "description": "币种。",
                            }
                        ],
                    },
                ],
            }
        ],
    }


def _research_call(call_id: object, arguments: object) -> dict[str, object]:
    return {
        "sessionID": "session-1",
        "userMessageID": "message-1",
        "query": "查询 ABC 在 2026 年 1 月的美元行情",
        "result": {
            "toolCalls": [
                {
                    "toolCallId": call_id,
                    "toolName": "quote",
                    "input": arguments,
                }
            ]
        },
    }


def test_chat_candidates_include_query_schema_and_immutable_arguments() -> None:
    query = "查询 ABC 在 2026 年 1 月的美元行情"
    trace = _canvas_trace(
        query,
        [
            _chat_call(
                "call-1",
                "quote",
                '{"symbol":"ABC","options":{"currency":"USD"}}',
            ),
            _chat_call("call-2", "unknown_tool", {}),
            {"agentName": "worker_search", "arguments": {"query": "private-subtask"}},
        ],
    )
    original = deepcopy(trace)

    normalized = normalize_chat_parameter_value_candidates(
        trace, build_parameter_value_catalog(_manual())
    )

    assert normalized["trace_id"] == "trace-1"
    assert normalized["total_call_count"] == 2
    assert normalized["eligible_call_count"] == 1
    assert normalized["diagnostics"]["unknown_schema_call_count"] == 1
    candidate = normalized["candidates"][0]
    assert candidate.call_id == "call-1"
    assert candidate.query == query
    assert candidate.tool_description == "查询证券在指定时间范围内的行情。"
    assert candidate.arguments["symbol"] == "ABC"
    assert candidate.schema_field_paths == frozenset(
        {"symbol", "start_date", "end_date", "options.currency"}
    )
    assert isinstance(candidate.arguments, MappingProxyType)
    with pytest.raises(TypeError):
        candidate.arguments["symbol"] = "MUTATED"  # type: ignore[index]
    assert trace == original


def test_chat_deduplicates_equivalent_definitions_across_messages() -> None:
    trace = {
        "canvas": {
            "meta": {"traceId": "trace-1", "query": "查询 ABC 行情"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [_chat_call("call-1", "quote", {"symbol": "ABC"})]
                            }
                        }
                    ]
                },
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [_chat_call("call-1", "quote", '{"symbol":"ABC"}')]
                            }
                        }
                    ]
                },
            ],
        }
    }

    normalized = normalize_chat_parameter_value_candidates(
        trace, build_parameter_value_catalog(_manual())
    )

    assert normalized["total_call_count"] == 1
    assert normalized["eligible_call_count"] == 1
    assert normalized["diagnostics"]["duplicate_call_definition_count"] == 1
    assert normalized["normalization_error"] is None


def test_chat_conflicting_duplicate_definition_is_not_computable() -> None:
    trace = _canvas_trace(
        "查询 ABC 行情",
        [
            _chat_call("call-1", "quote", {"symbol": "ABC"}),
            _chat_call("call-1", "quote", {"symbol": "XYZ"}),
        ],
    )

    normalized = normalize_chat_parameter_value_candidates(
        trace, build_parameter_value_catalog(_manual())
    )

    assert normalized["total_call_count"] == 1
    assert normalized["eligible_call_count"] == 1
    assert normalized["normalization_error"] == "conflicting_call_definition"


def test_chat_malformed_arguments_are_not_submitted_to_judge() -> None:
    normalized = normalize_chat_parameter_value_candidates(
        _canvas_trace("查询 ABC 行情", [_chat_call("call-1", "quote", "not-json")]),
        build_parameter_value_catalog(_manual()),
    )

    assert normalized["total_call_count"] == 1
    assert normalized["eligible_call_count"] == 0
    assert normalized["candidates"] == ()
    assert normalized["diagnostics"]["malformed_arguments_call_count"] == 1


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
    normalized = normalize_chat_parameter_value_candidates(
        _canvas_trace("查询 ABC 行情", [call]), build_parameter_value_catalog(_manual())
    )

    assert normalized["normalization_error"] == reason
    assert normalized["diagnostics"]["malformed_event_count"] == 1


@pytest.mark.parametrize(
    ("trace", "reason"),
    [
        ({}, "invalid_chat_trace_structure"),
        ({"canvas": {"meta": {}, "agents": []}}, "missing_trace_id"),
        (
            {"canvas": {"meta": {"traceId": "trace-1"}, "agents": []}},
            "missing_query",
        ),
        (
            {
                "canvas": {
                    "meta": {"traceId": "trace-1", "query": "查询 ABC 行情"},
                    "agents": "invalid",
                }
            },
            "invalid_chat_trace_structure",
        ),
        (
            {
                "canvas": {
                    "meta": {"traceId": "trace-1", "query": "查询 ABC 行情"},
                    "agents": [{"messages": [{"content": {"tool_call": "invalid"}}]}],
                }
            },
            "invalid_chat_trace_structure",
        ),
    ],
)
def test_chat_invalid_canvas_or_missing_meta_fields_are_isolated(
    trace: dict[str, object], reason: str
) -> None:
    normalized = normalize_chat_parameter_value_candidates(
        trace, build_parameter_value_catalog(_manual())
    )

    assert normalized["normalization_error"] == reason


def test_legacy_chat_tool_call_queue_is_not_supported() -> None:
    normalized = normalize_chat_parameter_value_candidates(
        {
            "traceId": "legacy-trace",
            "query": "查询 ABC 行情",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {
                        "toolId": "call-1",
                        "toolName": "quote",
                        "arguments": {"symbol": "ABC"},
                    },
                }
            ],
        },
        build_parameter_value_catalog(_manual()),
    )

    assert normalized["total_call_count"] == 0
    assert normalized["normalization_error"] == "invalid_chat_trace_structure"


def test_research_candidates_deduplicate_repeated_call_observations() -> None:
    event = _research_call("call-1", {"symbol": "ABC"})

    normalized = normalize_research_parameter_value_candidates(
        [event, deepcopy(event)], build_parameter_value_catalog(_manual())
    )

    assert normalized["trace_id"] == "session-1:message-1"
    assert normalized["total_call_count"] == 1
    assert normalized["eligible_call_count"] == 1
    assert normalized["diagnostics"]["duplicate_call_definition_count"] == 1
    assert normalized["normalization_error"] is None


def test_research_prefers_top_level_query_over_compatibility_body_query() -> None:
    event = _research_call("call-1", {"symbol": "ABC"})
    events: list[object] = [
        {"body": {"query": "兼容路径中的旧 query"}},
        event,
    ]

    normalized = normalize_research_parameter_value_candidates(
        events, build_parameter_value_catalog(_manual())
    )

    assert normalized["candidates"][0].query == event["query"]


def test_missing_query_and_conflicting_research_call_are_not_normalized() -> None:
    catalog = build_parameter_value_catalog(_manual())
    missing_query = normalize_chat_parameter_value_candidates(
        {"canvas": {"meta": {"traceId": "trace-1"}, "agents": []}}, catalog
    )
    conflicting = normalize_research_parameter_value_candidates(
        [
            _research_call("call-1", {"symbol": "ABC"}),
            _research_call("call-1", {"symbol": "XYZ"}),
        ],
        catalog,
    )

    assert missing_query["normalization_error"] == "missing_query"
    assert conflicting["normalization_error"] == "conflicting_call_definition"


def test_invalid_manual_description_is_rejected() -> None:
    manual = _manual()
    tools = manual["tools"]
    assert isinstance(tools, list)
    tool = tools[0]
    assert isinstance(tool, dict)
    tool["description"] = ""

    with pytest.raises(ValueError, match="description"):
        build_parameter_value_catalog(manual)


def _chat_call(call_id: str, tool_name: str, arguments: object) -> dict[str, object]:
    return {"toolId": call_id, "toolName": tool_name, "arguments": arguments}


def _canvas_trace(query: str, calls: list[dict[str, object]]) -> dict[str, object]:
    return {
        "canvas": {
            "meta": {"traceId": "trace-1", "query": query},
            "agents": [{"messages": [{"content": {"tool_call": calls}}]}],
        }
    }
