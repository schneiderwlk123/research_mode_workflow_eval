from copy import deepcopy

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
    trace: dict[str, object] = {
        "traceId": "trace-1",
        "query": "分析公司表现并核对新闻",
        "tool_call_queue": [
            {
                "event_type": "call_sub_agent_begin",
                "request_payload": {
                    "taskName": "查询公司行情",
                    "arguments": {"query": "查询公司行情"},
                },
            },
            {
                "event_type": "call_sub_agent_begin",
                "request_payload": {"taskName": "核对相关新闻"},
            },
            {
                "event_type": "tool_call",
                "tool_request_args": {"toolName": "quote", "arguments": {}},
            },
            {
                "event_type": "tool_call",
                "tool_request_args": {"toolName": "respond", "arguments": {}},
            },
        ],
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
    assert normalized["normalization_error"] is None
    assert trace == original


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
    missing_intent = normalize_chat_routing({"traceId": "trace-1", "tool_call_queue": []}, catalog)
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
