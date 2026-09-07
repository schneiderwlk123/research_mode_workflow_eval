from copy import deepcopy

import pytest

from collectors.parameter_value_accuracy import build_parameter_value_catalog
from collectors.time_range_accuracy import (
    normalize_chat_time_range,
    normalize_research_time_range,
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
                        "name": "frequency",
                        "type": "string",
                        "required": False,
                        "description": "数据频率。",
                    },
                ],
            }
        ],
    }


def test_chat_collects_query_calls_and_reference_time_without_mutation() -> None:
    trace = _chat_trace(
        "查询近三年的年度行情",
        [
            _chat_call(
                "call-1",
                {
                    "start_date": "2023-09-03",
                    "end_date": "2026-09-03",
                    "frequency": "year",
                },
            )
        ],
        create_time="2026-09-03T10:00:00+08:00",
    )
    original = deepcopy(trace)

    normalized = normalize_chat_time_range(trace, build_parameter_value_catalog(_manual()))

    candidate = normalized["candidate"]
    assert candidate is not None
    assert candidate.query == "查询近三年的年度行情"
    assert candidate.reference_time == "2026-09-03T10:00:00+08:00"
    assert candidate.tool_calls[0].call_id == "call-1"
    assert candidate.tool_calls[0].arguments["frequency"] == "year"
    assert normalized["reference_time_source"] == "canvas_meta"
    assert trace == original


def test_chat_explicit_reference_time_overrides_event_time() -> None:
    normalized = normalize_chat_time_range(
        _chat_trace(
            "查询今年以来行情",
            [_chat_call("call-1", {})],
            create_time="2026-09-03T10:00:00+08:00",
        ),
        build_parameter_value_catalog(_manual()),
        reference_time="2026-08-31T00:00:00Z",
    )

    assert normalized["candidate"] is not None
    assert normalized["candidate"].reference_time == "2026-08-31T00:00:00Z"
    assert normalized["reference_time_source"] == "provided"


def test_chat_no_calls_remains_judge_assessable_and_pre_failure_is_explicit() -> None:
    catalog = build_parameter_value_catalog(_manual())
    no_calls = normalize_chat_time_range(_chat_trace("查询 2025 年数据", []), catalog)
    pre_failure = normalize_chat_time_range(
        _chat_trace(
            "查询 2025 年数据",
            [],
            answer="模型服务暂时不可用，请稍后重试",
        ),
        catalog,
    )

    assert no_calls["candidate"] is not None
    assert no_calls["candidate"].tool_calls == ()
    assert no_calls["normalization_error"] is None
    assert pre_failure["candidate"] is None
    assert pre_failure["normalization_error"] == "pre_time_extraction_failure"


def test_research_deduplicates_calls_and_reads_state_start_time() -> None:
    event: dict[str, object] = {
        "sessionID": "session-1",
        "userMessageID": "message-1",
        "query": "查询 2024 至 2025 年数据",
        "part": {
            "state": {"time": {"start": "2026-09-03T02:00:00Z"}},
        },
        "result": {
            "toolCalls": [
                {
                    "toolCallId": "call-1",
                    "toolName": "quote",
                    "input": {"start_date": "2024-01-01", "end_date": "2025-12-31"},
                }
            ]
        },
    }

    normalized = normalize_research_time_range(
        [event, deepcopy(event)], build_parameter_value_catalog(_manual())
    )

    assert normalized["trace_id"] == "session-1:message-1"
    assert normalized["candidate"] is not None
    assert len(normalized["candidate"].tool_calls) == 1
    assert normalized["candidate"].reference_time == "2026-09-03T02:00:00Z"
    assert normalized["diagnostics"]["duplicate_call_definition_count"] == 1


def test_missing_query_conflict_and_malformed_arguments_are_explicit() -> None:
    catalog = build_parameter_value_catalog(_manual())
    missing_query = normalize_chat_time_range(
        {"canvas": {"meta": {"traceId": "trace-1"}, "agents": []}}, catalog
    )
    conflicting = normalize_research_time_range(
        [
            {
                "query": "查询 2025 年数据",
                "result": {
                    "toolCalls": [
                        {
                            "toolCallId": "call-1",
                            "toolName": "quote",
                            "input": {"start_date": "2025-01-01"},
                        }
                    ]
                },
            },
            {
                "result": {
                    "toolCalls": [
                        {
                            "toolCallId": "call-1",
                            "toolName": "quote",
                            "input": {"start_date": "2024-01-01"},
                        }
                    ]
                }
            },
        ],
        catalog,
    )
    malformed = normalize_chat_time_range(
        _chat_trace("查询 2025 年数据", [_chat_call("call-1", "not-json")]),
        catalog,
    )

    assert missing_query["normalization_error"] == "missing_query"
    assert conflicting["normalization_error"] == "conflicting_call_definition"
    assert malformed["normalization_error"] == "incomplete_time_observation"


@pytest.mark.parametrize(
    ("trace", "reason"),
    [
        ({}, "invalid_chat_trace_structure"),
        ({"canvas": {"meta": {}, "agents": []}}, "missing_trace_id"),
        (
            {
                "canvas": {
                    "meta": {"traceId": "trace-1", "query": "查询 2025 年数据"},
                    "agents": "invalid",
                }
            },
            "invalid_chat_trace_structure",
        ),
        (
            {
                "canvas": {
                    "meta": {"traceId": "trace-1", "query": "查询 2025 年数据"},
                    "agents": [{"messages": [{"content": {"tool_call": "invalid"}}]}],
                }
            },
            "invalid_chat_trace_structure",
        ),
    ],
)
def test_chat_invalid_canvas_structure_is_isolated(trace: dict[str, object], reason: str) -> None:
    normalized = normalize_chat_time_range(trace, build_parameter_value_catalog(_manual()))

    assert normalized["candidate"] is None
    assert normalized["normalization_error"] == reason


@pytest.mark.parametrize(
    ("call", "reason"),
    [
        ({"toolName": "quote", "arguments": {}}, "missing_call_id"),
        ({"toolId": "call-1", "arguments": {}}, "missing_tool_name"),
        ({"toolId": "call-1", "toolName": "quote"}, "missing_call_arguments"),
    ],
)
def test_chat_missing_call_fields_are_not_computable(call: dict[str, object], reason: str) -> None:
    normalized = normalize_chat_time_range(
        _chat_trace("查询 2025 年数据", [call]),
        build_parameter_value_catalog(_manual()),
    )

    assert normalized["candidate"] is None
    assert normalized["normalization_error"] == reason


def test_legacy_chat_tool_call_queue_is_not_supported() -> None:
    normalized = normalize_chat_time_range(
        {
            "traceId": "legacy",
            "query": "查询 2025 年数据",
            "tool_call_queue": [_chat_call("call-1", {"start_date": "2025-01-01"})],
        },
        build_parameter_value_catalog(_manual()),
    )

    assert normalized["candidate"] is None
    assert normalized["normalization_error"] == "invalid_chat_trace_structure"


def _chat_call(call_id: str, arguments: object) -> dict[str, object]:
    return {"toolId": call_id, "toolName": "quote", "arguments": arguments}


def _chat_trace(
    query: str,
    calls: list[dict[str, object]],
    *,
    create_time: str | None = None,
    answer: str | None = None,
) -> dict[str, object]:
    meta: dict[str, object] = {"traceId": "trace-1", "query": query}
    if create_time is not None:
        meta["create_time"] = create_time
    canvas: dict[str, object] = {
        "meta": meta,
        "agents": [{"messages": [{"content": {"tool_call": calls}}]}],
    }
    if answer is not None:
        canvas["answer"] = {"answer": answer}
    return {"canvas": canvas}
