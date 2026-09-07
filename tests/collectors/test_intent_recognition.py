from copy import deepcopy

import pytest

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


def _subtask(arguments: object, *, tool_name: str | None = None) -> dict[str, object]:
    call: dict[str, object] = {"agentName": "worker_search", "arguments": arguments}
    if tool_name is not None:
        call["toolName"] = tool_name
    return call


def _ordinary_call(call_id: str, tool_name: str) -> dict[str, object]:
    return {"toolId": call_id, "toolName": tool_name, "arguments": {}}


def _canvas_trace(
    calls: list[dict[str, object]],
    *,
    query: str | None = "查询行情",
    answer: str | None = None,
) -> dict[str, object]:
    meta: dict[str, object] = {"traceId": "trace-1"}
    if query is not None:
        meta["query"] = query
    canvas: dict[str, object] = {
        "meta": meta,
        "agents": [{"messages": [{"content": {"tool_call": calls}}]}],
    }
    if answer is not None:
        canvas["answer"] = {"answer": answer}
    return {"canvas": canvas}


def test_chat_extracts_canvas_query_agent_evidence_capabilities_and_answer() -> None:
    trace = {
        "canvas": {
            "meta": {"traceId": "trace-1", "query": "分析公司行情和新闻"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [
                                    _subtask({"query": "查询近期行情"}),
                                    _ordinary_call("call-1", "quote"),
                                    _ordinary_call("call-2", "respond"),
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
                                    _subtask('{"task":"核对相关新闻"}'),
                                    _subtask({"query": "查询近期行情"}),
                                    _ordinary_call("call-1", "quote"),
                                ]
                            }
                        }
                    ]
                },
            ],
            "answer": {"answer": "根据行情和新闻形成结论。"},
        }
    }
    original = deepcopy(trace)

    normalized = normalize_chat_intent_recognition(trace, _catalog())

    candidate = normalized["candidate"]
    assert candidate is not None
    assert candidate.query == "分析公司行情和新闻"
    assert candidate.evidence_fragments == (
        IntentEvidenceFragment("subtask_query", "查询近期行情"),
        IntentEvidenceFragment("subtask_query", "核对相关新闻"),
        IntentEvidenceFragment("final_answer", "根据行情和新闻形成结论。"),
    )
    assert tuple(item.capability_id for item in candidate.capabilities) == ("quote",)
    assert normalized["diagnostics"] == {
        "malformed_event_count": 0,
        "out_of_scope_tool_call_count": 1,
        "duplicate_call_definition_count": 1,
        "duplicate_evidence_count": 1,
    }
    assert trace == original


def test_chat_agent_name_without_tool_id_wins_over_tool_name() -> None:
    normalized = normalize_chat_intent_recognition(
        _canvas_trace(
            [_subtask({"task": "分析盈利能力"}, tool_name="agent_dispatch")],
            answer="形成结论",
        ),
        _catalog(),
    )

    candidate = normalized["candidate"]
    assert candidate is not None
    assert candidate.evidence_fragments[0] == IntentEvidenceFragment(
        "subtask_query", "分析盈利能力"
    )
    assert normalized["normalization_error"] is None


def test_chat_invalid_subtask_is_diagnostic_when_other_evidence_exists() -> None:
    normalized = normalize_chat_intent_recognition(
        _canvas_trace([_subtask({"image": "private-image"})], answer="有效最终回答"),
        _catalog(),
    )

    assert normalized["candidate"] is not None
    assert normalized["evidence_fragment_count"] == 1
    assert normalized["diagnostics"]["malformed_event_count"] == 1
    assert normalized["normalization_error"] is None
    assert "private-image" not in repr(normalized)


@pytest.mark.parametrize(
    ("call", "reason", "malformed_count"),
    [
        ({"toolName": "quote", "arguments": {}}, "missing_call_id", 1),
        ({"toolId": "call-1", "arguments": {}}, "missing_tool_name", 1),
        (
            [
                _ordinary_call("call-1", "quote"),
                _ordinary_call("call-1", "news"),
            ],
            "conflicting_call_definition",
            0,
        ),
    ],
)
def test_chat_invalid_call_identity_is_not_computable(
    call: dict[str, object] | list[dict[str, object]], reason: str, malformed_count: int
) -> None:
    calls = call if isinstance(call, list) else [call]
    normalized = normalize_chat_intent_recognition(
        _canvas_trace(calls, answer="形成结论"),
        _catalog(),
    )

    assert normalized["candidate"] is None
    assert normalized["normalization_error"] == reason
    assert normalized["diagnostics"]["malformed_event_count"] == malformed_count


@pytest.mark.parametrize(
    ("trace", "reason"),
    [
        ({}, "invalid_chat_trace_structure"),
        ({"canvas": {"meta": {}, "agents": []}}, "missing_trace_id"),
        (
            {"canvas": {"meta": {"traceId": "trace-1"}, "agents": "invalid"}},
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
    normalized = normalize_chat_intent_recognition(trace, _catalog())

    assert normalized["candidate"] is None
    assert normalized["normalization_error"] == reason


def test_chat_missing_query_or_evidence_is_not_computable() -> None:
    missing_query = normalize_chat_intent_recognition(
        _canvas_trace([], query=None, answer="形成结论"),
        _catalog(),
    )
    missing_evidence = normalize_chat_intent_recognition(
        _canvas_trace([], query="仅有用户问题"),
        _catalog(),
    )

    assert missing_query["normalization_error"] == "missing_query"
    assert missing_evidence["normalization_error"] == "no_intent_evidence"


def test_chat_pre_intent_failure_is_explicit() -> None:
    normalized = normalize_chat_intent_recognition(
        _canvas_trace([], query="查询公司", answer="模型服务暂时不可用，请稍后重试"),
        _catalog(),
    )

    assert normalized["candidate"] is None
    assert normalized["normalization_error"] == "pre_intent_failure"


def test_legacy_chat_tool_call_queue_is_not_supported() -> None:
    normalized = normalize_chat_intent_recognition(
        {"traceId": "legacy", "query": "查询行情", "tool_call_queue": []},
        _catalog(),
    )

    assert normalized["candidate"] is None
    assert normalized["normalization_error"] == "invalid_chat_trace_structure"


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
