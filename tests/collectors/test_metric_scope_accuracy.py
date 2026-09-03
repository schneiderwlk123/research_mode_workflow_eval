from copy import deepcopy

from collectors.metric_scope_accuracy import (
    normalize_chat_metric_scope,
    normalize_research_metric_scope,
)
from collectors.parameter_value_accuracy import ParameterValueCatalog, build_parameter_value_catalog


def _catalog() -> ParameterValueCatalog:
    return build_parameter_value_catalog(
        {
            "manual_version": "0.1.0",
            "tools": [
                {
                    "name": "finance",
                    "description": "查询财务指标。",
                    "parameters": [
                        {
                            "name": "metrics",
                            "type": "string[]",
                            "required": True,
                            "description": "指标列表。",
                        },
                        {
                            "name": "unit",
                            "type": "string",
                            "required": False,
                            "description": "单位。",
                        },
                    ],
                }
            ],
        }
    )


def test_chat_collects_immutable_metric_evidence() -> None:
    trace = {
        "traceId": "t1",
        "query": "按人民币百万元查询营收",
        "tool_call_queue": [
            {
                "event_type": "tool_call",
                "tool_request_args": {
                    "toolId": "c1",
                    "toolName": "finance",
                    "arguments": {"metrics": ["营业收入"], "unit": "百万元"},
                },
            }
        ],
    }
    original = deepcopy(trace)
    result = normalize_chat_metric_scope(trace, _catalog())
    assert result["candidate"] is not None
    assert result["candidate"].query == trace["query"]
    assert result["candidate"].tool_calls[0].arguments["unit"] == "百万元"
    assert trace == original


def test_research_deduplicates_calls_and_reports_boundaries() -> None:
    event = {
        "sessionID": "s1",
        "userMessageID": "m1",
        "query": "查询营收",
        "result": {
            "toolCalls": [
                {"toolCallId": "c1", "toolName": "finance", "input": {"metrics": ["营业收入"]}}
            ]
        },
    }
    duplicate = normalize_research_metric_scope([event, deepcopy(event)], _catalog())
    missing = normalize_research_metric_scope(
        [{"query": "查询营收", "result": {"toolCalls": [{"toolName": "finance", "input": {}}]}}],
        _catalog(),
    )
    assert duplicate["candidate"] is not None
    assert len(duplicate["candidate"].tool_calls) == 1
    assert duplicate["diagnostics"]["duplicate_call_definition_count"] == 1
    assert missing["normalization_error"] == "missing_call_id"


def test_no_calls_is_assessable_but_pre_failure_and_malformed_are_not() -> None:
    no_calls = normalize_chat_metric_scope({"query": "查询营收", "tool_call_queue": []}, _catalog())
    failure = normalize_chat_metric_scope(
        {"query": "查询营收", "tool_call_queue": [], "answer": "模型服务暂时不可用，请稍后重试"},
        _catalog(),
    )
    malformed = normalize_chat_metric_scope(
        {
            "query": "查询营收",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {
                        "toolId": "c1",
                        "toolName": "finance",
                        "arguments": "bad",
                    },
                }
            ],
        },
        _catalog(),
    )
    assert no_calls["candidate"] is not None
    assert no_calls["candidate"].tool_calls == ()
    assert failure["normalization_error"] == "pre_metric_extraction_failure"
    assert malformed["normalization_error"] == "incomplete_metric_observation"
