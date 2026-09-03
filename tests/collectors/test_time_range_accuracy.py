from copy import deepcopy

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
    trace: dict[str, object] = {
        "traceId": "trace-1",
        "query": "查询近三年的年度行情",
        "tool_call_queue": [
            {
                "event_type": "tool_call",
                "tool_request_args": {
                    "toolId": "call-1",
                    "toolName": "quote",
                    "create_time": "2026-09-03T10:00:00+08:00",
                    "arguments": {
                        "start_date": "2023-09-03",
                        "end_date": "2026-09-03",
                        "frequency": "year",
                    },
                },
            }
        ],
    }
    original = deepcopy(trace)

    normalized = normalize_chat_time_range(trace, build_parameter_value_catalog(_manual()))

    candidate = normalized["candidate"]
    assert candidate is not None
    assert candidate.query == "查询近三年的年度行情"
    assert candidate.reference_time == "2026-09-03T10:00:00+08:00"
    assert candidate.tool_calls[0].call_id == "call-1"
    assert candidate.tool_calls[0].arguments["frequency"] == "year"
    assert normalized["reference_time_source"] == "tool_request"
    assert trace == original


def test_chat_explicit_reference_time_overrides_event_time() -> None:
    normalized = normalize_chat_time_range(
        {
            "query": "查询今年以来行情",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {
                        "toolId": "call-1",
                        "toolName": "quote",
                        "create_time": "2026-09-03T10:00:00+08:00",
                        "arguments": {},
                    },
                }
            ],
        },
        build_parameter_value_catalog(_manual()),
        reference_time="2026-08-31T00:00:00Z",
    )

    assert normalized["candidate"] is not None
    assert normalized["candidate"].reference_time == "2026-08-31T00:00:00Z"
    assert normalized["reference_time_source"] == "provided"


def test_chat_no_calls_remains_judge_assessable_and_pre_failure_is_explicit() -> None:
    catalog = build_parameter_value_catalog(_manual())
    no_calls = normalize_chat_time_range(
        {"query": "查询 2025 年数据", "tool_call_queue": []}, catalog
    )
    pre_failure = normalize_chat_time_range(
        {
            "query": "查询 2025 年数据",
            "tool_call_queue": [],
            "answer": "模型服务暂时不可用，请稍后重试",
        },
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
    missing_query = normalize_chat_time_range({"tool_call_queue": []}, catalog)
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
        {
            "query": "查询 2025 年数据",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {
                        "toolId": "call-1",
                        "toolName": "quote",
                        "arguments": "not-json",
                    },
                }
            ],
        },
        catalog,
    )

    assert missing_query["normalization_error"] == "missing_query"
    assert conflicting["normalization_error"] == "conflicting_call_definition"
    assert malformed["normalization_error"] == "incomplete_time_observation"
