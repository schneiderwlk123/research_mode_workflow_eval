from copy import deepcopy

from collectors.intent_recognition import (
    IntentEvidenceFragment,
    normalize_chat_intent_recognition,
    normalize_research_intent_recognition,
)
from collectors.routing import CapabilityCatalog, build_capability_catalog


def _catalog() -> CapabilityCatalog:
    return build_capability_catalog(
        {
            "manual_version": "0.1.0",
            "tools": [
                {"name": "quote", "description": "查询行情。", "parameters": []},
                {"name": "news", "description": "检索新闻。", "parameters": []},
            ],
        }
    )


def test_chat_extracts_query_agent_evidence_and_capabilities() -> None:
    trace: dict[str, object] = {
        "traceId": "trace-1",
        "query": "分析公司行情和新闻",
        "tool_call_queue": [
            {
                "event_type": "call_sub_agent_begin",
                "request_payload": {
                    "taskName": "查询行情",
                    "arguments": {"query": "查询近期行情"},
                },
            },
            {
                "event_type": "tool_call",
                "tool_request_args": {"toolName": "quote"},
            },
            {
                "event_type": "tool_call",
                "tool_request_args": {"toolName": "respond"},
            },
        ],
        "answer": "根据行情信息形成结论。",
    }
    original = deepcopy(trace)

    normalized = normalize_chat_intent_recognition(trace, _catalog())

    candidate = normalized["candidate"]
    assert candidate is not None
    assert candidate.query == "分析公司行情和新闻"
    assert candidate.evidence_fragments == (
        IntentEvidenceFragment("subtask_title", "查询行情"),
        IntentEvidenceFragment("subtask_query", "查询近期行情"),
        IntentEvidenceFragment("final_answer", "根据行情信息形成结论。"),
    )
    assert tuple(item.capability_id for item in candidate.capabilities) == ("quote",)
    assert normalized["diagnostics"]["out_of_scope_tool_call_count"] == 1
    assert trace == original


def test_chat_supports_compatibility_fields_and_deduplicates_evidence() -> None:
    normalized = normalize_chat_intent_recognition(
        {
            "query": "分析公司",
            "tool_call_queue": [
                {
                    "event_type": "call_sub_agent_begin",
                    "request_payload": {
                        "taskDescription": "分析盈利能力",
                        "arguments": {"task": "分析盈利能力"},
                    },
                }
            ],
        },
        _catalog(),
    )

    candidate = normalized["candidate"]
    assert candidate is not None
    assert candidate.evidence_fragments == (
        IntentEvidenceFragment("subtask_description", "分析盈利能力"),
    )
    assert normalized["diagnostics"]["duplicate_evidence_count"] == 1


def test_chat_missing_query_or_evidence_is_not_computable() -> None:
    missing_query = normalize_chat_intent_recognition(
        {"tool_call_queue": []},
        _catalog(),
    )
    missing_evidence = normalize_chat_intent_recognition(
        {"query": "仅有用户问题", "tool_call_queue": []},
        _catalog(),
    )

    assert missing_query["normalization_error"] == "missing_query"
    assert missing_evidence["normalization_error"] == "no_intent_evidence"


def test_chat_pre_intent_failure_is_explicit() -> None:
    normalized = normalize_chat_intent_recognition(
        {
            "query": "查询公司",
            "tool_call_queue": [],
            "answer": "模型服务暂时不可用，请稍后重试",
        },
        _catalog(),
    )

    assert normalized["candidate"] is None
    assert normalized["normalization_error"] == "pre_intent_failure"


def test_research_extracts_planning_evidence_and_deduplicated_capabilities() -> None:
    events: list[object] = [
        {
            "sessionID": "session-1",
            "userMessageID": "message-1",
            "query": "分析行情与新闻",
            "body": {"task": "查询行情", "description": "获取价格变化"},
            "result": {
                "toolCalls": [
                    {"toolCallId": "call-1", "toolName": "mcp__finance__quote"},
                ]
            },
        },
        {
            "body": {"task": "查询行情"},
            "result": {
                "toolCalls": [
                    {"toolCallId": "call-1", "toolName": "mcp__finance__quote"},
                ]
            },
        },
    ]

    normalized = normalize_research_intent_recognition(events, _catalog())

    candidate = normalized["candidate"]
    assert candidate is not None
    assert candidate.query == "分析行情与新闻"
    assert candidate.evidence_fragments == (
        IntentEvidenceFragment("plan_task", "查询行情"),
        IntentEvidenceFragment("plan_description", "获取价格变化"),
    )
    assert tuple(item.capability_id for item in candidate.capabilities) == ("quote",)
    assert normalized["diagnostics"]["duplicate_call_definition_count"] == 1
    assert normalized["diagnostics"]["duplicate_evidence_count"] == 1


def test_research_missing_call_id_and_conflict_are_not_computable() -> None:
    missing = normalize_research_intent_recognition(
        [
            {
                "query": "查询行情",
                "result": {"toolCalls": [{"toolName": "quote"}]},
            }
        ],
        _catalog(),
    )
    conflicting = normalize_research_intent_recognition(
        [
            {
                "query": "查询行情",
                "body": {"task": "查询行情"},
                "result": {"toolCalls": [{"toolCallId": "call-1", "toolName": "quote"}]},
            },
            {"result": {"toolCalls": [{"toolCallId": "call-1", "toolName": "news"}]}},
        ],
        _catalog(),
    )

    assert missing["normalization_error"] == "missing_call_id"
    assert conflicting["normalization_error"] == "conflicting_call_definition"
