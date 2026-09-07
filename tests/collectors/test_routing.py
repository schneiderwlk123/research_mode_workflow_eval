from copy import deepcopy

import pytest

from collectors.routing import (
    build_capability_catalog,
    normalize_chat_routing,
    normalize_research_routing,
)


def _manual() -> dict[str, object]:
    return {
        "manual_version": "0.1.0",
        "tools": [
            {
                "name": "quote",
                "description": "查询证券行情。",
                "parameters": [
                    {
                        "name": "query",
                        "type": "string",
                        "required": True,
                        "description": "证券名称或代码。",
                    }
                ],
            },
            {
                "name": "news",
                "description": "检索新闻。",
                "parameters": [],
            },
        ],
    }


def test_chat_routing_collects_overall_and_all_subtask_intents() -> None:
    trace = {
        "canvas": {
            "meta": {"traceId": "trace-1", "query": "分析公司表现并核对新闻"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [
                                    {
                                        "agentName": "worker_search",
                                        "arguments": {"query": "查询公司行情"},
                                    },
                                    {
                                        "agentName": "worker_validation",
                                        "arguments": '{"task":"核对相关新闻"}',
                                    },
                                    {
                                        "agentName": "worker_search",
                                        "arguments": {"query": "查询公司行情"},
                                    },
                                    {
                                        "agentName": "worker_multimodal",
                                        "arguments": {"image": "private-image"},
                                    },
                                    _chat_call("call-1", "quote"),
                                    _chat_call("call-2", "respond"),
                                ]
                            }
                        }
                    ]
                },
                {"messages": [{"content": {"tool_call": [_chat_call("call-1", "quote")]}}]},
            ],
        }
    }
    original = deepcopy(trace)

    normalized = normalize_chat_routing(trace, build_capability_catalog(_manual()))

    assert normalized["trace_id"] == "trace-1"
    assert normalized["intent_fragments"] == (
        "分析公司表现并核对新闻",
        "查询公司行情",
        "核对相关新闻",
    )
    assert normalized["actual_capability_ids"] == ("quote",)
    assert normalized["diagnostics"]["out_of_scope_tool_call_count"] == 1
    assert normalized["diagnostics"]["duplicate_call_definition_count"] == 1
    assert normalized["diagnostics"]["malformed_event_count"] == 1
    assert normalized["normalization_error"] is None
    assert trace == original


def test_chat_conflicting_call_definition_is_not_computable() -> None:
    normalized = normalize_chat_routing(
        _canvas_trace(
            "查询行情",
            [_chat_call("call-1", "quote"), _chat_call("call-1", "news")],
        ),
        build_capability_catalog(_manual()),
    )

    assert normalized["actual_capability_ids"] == ("quote",)
    assert normalized["normalization_error"] == "conflicting_call_definition"


def test_chat_agent_call_is_identified_by_agent_name_without_tool_id() -> None:
    normalized = normalize_chat_routing(
        _canvas_trace(
            "查询行情",
            [
                {
                    "agentName": "worker_search",
                    "toolName": "agent_dispatch",
                    "arguments": {"task": "核对行情"},
                }
            ],
        ),
        build_capability_catalog(_manual()),
    )

    assert normalized["intent_fragments"] == ("查询行情", "核对行情")
    assert normalized["normalization_error"] is None


@pytest.mark.parametrize(
    ("call", "reason"),
    [
        ({"toolName": "quote", "arguments": {}}, "missing_call_id"),
        ({"toolId": "call-1", "arguments": {}}, "missing_tool_name"),
    ],
)
def test_chat_missing_request_identity_is_not_computable(
    call: dict[str, object], reason: str
) -> None:
    normalized = normalize_chat_routing(
        _canvas_trace("查询行情", [call]), build_capability_catalog(_manual())
    )

    assert normalized["normalization_error"] == reason


@pytest.mark.parametrize(
    ("trace", "reason"),
    [
        ({}, "invalid_chat_trace_structure"),
        ({"canvas": {"meta": {}, "agents": []}}, "missing_trace_id"),
        (
            {"canvas": {"meta": {"traceId": "trace-1"}, "agents": []}},
            "missing_intent",
        ),
        (
            {
                "canvas": {
                    "meta": {"traceId": "trace-1", "query": "查询行情"},
                    "agents": "invalid",
                }
            },
            "invalid_chat_trace_structure",
        ),
        (
            {
                "canvas": {
                    "meta": {"traceId": "trace-1", "query": "查询行情"},
                    "agents": [{"messages": [{"content": {"tool_call": "invalid"}}]}],
                }
            },
            "invalid_chat_trace_structure",
        ),
    ],
)
def test_chat_invalid_canvas_structure_is_isolated(trace: dict[str, object], reason: str) -> None:
    normalized = normalize_chat_routing(trace, build_capability_catalog(_manual()))

    assert normalized["normalization_error"] == reason


def test_legacy_chat_tool_call_queue_is_not_supported() -> None:
    normalized = normalize_chat_routing(
        {
            "traceId": "legacy-trace",
            "query": "查询行情",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"toolName": "quote", "arguments": {}},
                }
            ],
        },
        build_capability_catalog(_manual()),
    )

    assert normalized["actual_capability_ids"] == ()
    assert normalized["normalization_error"] == "invalid_chat_trace_structure"


def test_research_routing_normalizes_mcp_prefix_and_deduplicates_call_id() -> None:
    event = {
        "sessionID": "session-1",
        "userMessageID": "message-1",
        "query": "查询行情",
        "result": {
            "toolCalls": [
                {
                    "toolCallId": "call-1",
                    "toolName": "mcp__finance__quote",
                    "input": {"query": "ABC"},
                }
            ]
        },
    }

    normalized = normalize_research_routing(
        [event, deepcopy(event)], build_capability_catalog(_manual())
    )

    assert normalized["trace_id"] == "session-1:message-1"
    assert normalized["intent_fragments"] == ("查询行情",)
    assert normalized["actual_capability_ids"] == ("quote",)
    assert normalized["diagnostics"]["duplicate_call_definition_count"] == 1
    assert normalized["normalization_error"] is None


def test_missing_intent_and_conflicting_research_call_are_not_computable() -> None:
    catalog = build_capability_catalog(_manual())
    missing_intent = normalize_chat_routing(
        {"canvas": {"meta": {"traceId": "trace-1"}, "agents": []}}, catalog
    )
    conflicting = normalize_research_routing(
        [
            {
                "sessionID": "session-1",
                "userMessageID": "message-1",
                "query": "查询行情",
                "result": {
                    "toolCalls": [{"toolCallId": "call-1", "toolName": "quote", "input": {}}]
                },
            },
            {"result": {"toolCalls": [{"toolCallId": "call-1", "toolName": "news", "input": {}}]}},
        ],
        catalog,
    )

    assert missing_intent["normalization_error"] == "missing_intent"
    assert conflicting["normalization_error"] == "conflicting_call_definition"


def test_chat_service_failure_before_routing_is_explicitly_ineligible() -> None:
    normalized = normalize_chat_routing(
        _canvas_trace(
            "查询公司表现",
            [],
            answer="模型服务暂时不可用，请稍后重试",
        ),
        build_capability_catalog(_manual()),
    )

    assert normalized["actual_capability_ids"] == ()
    assert normalized["normalization_error"] == "pre_routing_failure"


def _chat_call(call_id: str, tool_name: str) -> dict[str, object]:
    return {"toolId": call_id, "toolName": tool_name, "arguments": {}}


def _canvas_trace(
    query: str,
    calls: list[dict[str, object]],
    *,
    answer: str | None = None,
) -> dict[str, object]:
    canvas: dict[str, object] = {
        "meta": {"traceId": "trace-1", "query": query},
        "agents": [{"messages": [{"content": {"tool_call": calls}}]}],
    }
    if answer is not None:
        canvas["answer"] = {"answer": answer}
    return {"canvas": canvas}
