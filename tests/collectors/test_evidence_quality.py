from copy import deepcopy

import pytest

from collectors.evidence_quality import (
    EvidenceSourceCatalog,
    build_evidence_source_catalog,
    normalize_chat_evidence,
    normalize_research_evidence,
)


def _manual() -> dict[str, object]:
    return {
        "manual_version": "0.1.0",
        "tools": [
            {"name": "search", "description": "检索外部资料。", "parameters": []},
            {"name": "quote", "description": "查询结构化行情。", "parameters": []},
            {"name": "formula", "description": "计算派生结果。", "parameters": []},
        ],
    }


def _catalog() -> EvidenceSourceCatalog:
    return build_evidence_source_catalog(
        _manual(),
        evidence_tool_names={"search", "quote"},
    )


def test_chat_extracts_structured_units_deduplicates_and_preserves_input() -> None:
    trace: dict[str, object] = {
        "traceId": "trace-1",
        "query": "比较两家公司收入",
        "tool_call_queue": [
            {
                "event_type": "tool_call",
                "tool_request_args": {"toolId": "call-1", "toolName": "search"},
                "tool_response_result": {
                    "result": {
                        "data": [
                            {"record_id": "r1", "company": "甲", "revenue": 10},
                            {"record_id": "r2", "company": "乙", "revenue": 20},
                            {"record_id": "r1", "company": "甲", "revenue": 10},
                        ]
                    }
                },
            },
            {
                "event_type": "tool_call",
                "tool_request_args": {"toolName": "formula"},
                "tool_response_result": {"result": {"value": 2}},
            },
            {"event_type": "tool_call", "tool_request_args": {"toolName": "respond"}},
        ],
    }
    original = deepcopy(trace)

    normalized = normalize_chat_evidence(trace, _catalog())

    candidate = normalized["candidate"]
    assert candidate is not None
    assert candidate.query == "比较两家公司收入"
    assert [unit.unit_id for unit in candidate.evidence_units] == ["call-1:0", "call-1:1"]
    assert [unit.tool_name for unit in candidate.evidence_units] == ["search", "search"]
    assert candidate.evidence_units[0].content == ('{"company":"甲","record_id":"r1","revenue":10}')
    assert normalized["evidence_unit_count"] == 2
    assert normalized["evidence_scope_version"].startswith("evidence-scope-")
    assert normalized["diagnostics"] == {
        "malformed_event_count": 0,
        "out_of_scope_tool_call_count": 2,
        "evidence_call_count": 1,
        "successful_evidence_response_count": 1,
        "unsegmentable_evidence_response_count": 0,
        "duplicate_evidence_unit_count": 1,
        "duplicate_call_definition_count": 0,
    }
    assert trace == original


def test_chat_parses_stringified_json_and_uses_stable_position_fallback() -> None:
    normalized = normalize_chat_evidence(
        {
            "query": "查询行情",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"toolName": "quote"},
                    "tool_response_result": {"rawResult": '[{"price":10},{"price":11}]'},
                }
            ],
        },
        _catalog(),
    )

    candidate = normalized["candidate"]
    assert candidate is not None
    assert [unit.unit_id for unit in candidate.evidence_units] == [
        "chat-call-0:0",
        "chat-call-0:1",
    ]


def test_identical_content_without_source_identity_is_not_merged() -> None:
    normalized = normalize_chat_evidence(
        {
            "query": "查询两条独立来源记录",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"toolId": "call-1", "toolName": "search"},
                    "tool_response_result": {"result": {"data": [{"value": 10}, {"value": 10}]}},
                }
            ],
        },
        _catalog(),
    )

    assert normalized["evidence_unit_count"] == 2
    assert normalized["diagnostics"]["duplicate_evidence_unit_count"] == 0


def test_chat_missing_inputs_and_unsegmentable_text_are_explicit() -> None:
    catalog = _catalog()
    missing_query = normalize_chat_evidence({"tool_call_queue": []}, catalog)
    no_calls = normalize_chat_evidence({"query": "查询行情", "tool_call_queue": []}, catalog)
    no_response = normalize_chat_evidence(
        {
            "query": "查询行情",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"toolId": "call-1", "toolName": "quote"},
                }
            ],
        },
        catalog,
    )
    unsegmentable = normalize_chat_evidence(
        {
            "query": "查询报告",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"toolId": "call-1", "toolName": "search"},
                    "tool_response_result": {"result": "没有可靠边界的长文本"},
                }
            ],
        },
        catalog,
    )

    assert missing_query["normalization_error"] == "missing_query"
    assert no_calls["normalization_error"] == "no_evidence_calls"
    assert no_response["normalization_error"] == "no_successful_evidence_responses"
    assert unsegmentable["normalization_error"] == "no_stable_evidence_units"
    assert unsegmentable["diagnostics"]["unsegmentable_evidence_response_count"] == 1


def test_partial_unsegmentable_responses_do_not_silently_shrink_denominator() -> None:
    normalized = normalize_chat_evidence(
        {
            "query": "查询行情和研报",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"toolId": "call-1", "toolName": "quote"},
                    "tool_response_result": {"result": {"data": [{"price": 10}]}},
                },
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"toolId": "call-2", "toolName": "search"},
                    "tool_response_result": {"result": "无法可靠拆分的报告合集"},
                },
            ],
        },
        _catalog(),
    )

    assert normalized["evidence_unit_count"] == 1
    assert normalized["candidate"] is None
    assert normalized["normalization_error"] == "incomplete_evidence_segmentation"


def test_successful_empty_result_has_no_evidence_units_without_segmentation_error() -> None:
    normalized = normalize_chat_evidence(
        {
            "query": "查询不存在的记录",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"toolId": "call-1", "toolName": "search"},
                    "tool_response_result": {"result": {"code": 0, "data": [], "msg": "ok"}},
                }
            ],
        },
        _catalog(),
    )

    assert normalized["candidate"] is None
    assert normalized["normalization_error"] == "no_evidence_units"
    assert normalized["diagnostics"]["unsegmentable_evidence_response_count"] == 0


def test_research_joins_completed_outputs_by_call_id_and_deduplicates_lifecycle() -> None:
    events: list[object] = [
        {
            "sessionID": "session-1",
            "userMessageID": "message-1",
            "query": "查询行情",
            "result": {"toolCalls": [{"toolCallId": "call-1", "toolName": "mcp__finance__quote"}]},
        },
        {"result": {"toolCalls": [{"toolCallId": "call-1", "toolName": "quote"}]}},
        {
            "part": {
                "type": "tool",
                "callID": "call-1",
                "state": {"status": "completed", "output": '{"data":[{"price":10}]}'},
            }
        },
        {
            "part": {
                "type": "tool",
                "callID": "call-1",
                "state": {"status": "completed", "output": '{"data":[{"price":10}]}'},
            }
        },
    ]

    normalized = normalize_research_evidence(events, _catalog())

    assert normalized["trace_id"] == "session-1:message-1"
    candidate = normalized["candidate"]
    assert candidate is not None
    assert len(candidate.evidence_units) == 1
    assert candidate.evidence_units[0].tool_name == "quote"
    assert normalized["diagnostics"]["duplicate_call_definition_count"] == 1
    assert normalized["diagnostics"]["successful_evidence_response_count"] == 1


def test_research_missing_call_id_and_conflicting_definitions_are_not_computable() -> None:
    missing = normalize_research_evidence(
        [
            {
                "query": "查询行情",
                "result": {"toolCalls": [{"toolName": "quote"}]},
            }
        ],
        _catalog(),
    )
    conflicting = normalize_research_evidence(
        [
            {
                "query": "查询行情",
                "result": {"toolCalls": [{"toolCallId": "call-1", "toolName": "quote"}]},
            },
            {"result": {"toolCalls": [{"toolCallId": "call-1", "toolName": "search"}]}},
        ],
        _catalog(),
    )

    assert missing["normalization_error"] == "missing_call_id"
    assert conflicting["normalization_error"] == "conflicting_call_definition"


def test_catalog_rejects_unknown_or_derived_evidence_tools() -> None:
    search_scope = build_evidence_source_catalog(_manual(), evidence_tool_names={"search"})
    quote_scope = build_evidence_source_catalog(_manual(), evidence_tool_names={"quote"})

    assert search_scope.scope_version != quote_scope.scope_version

    with pytest.raises(ValueError, match="未知"):
        build_evidence_source_catalog(_manual(), evidence_tool_names={"not_registered"})

    with pytest.raises(ValueError, match="非空字符串"):
        build_evidence_source_catalog(_manual(), evidence_tool_names={""})

    with pytest.raises(ValueError, match="不能为空"):
        build_evidence_source_catalog(_manual(), evidence_tool_names=set())
