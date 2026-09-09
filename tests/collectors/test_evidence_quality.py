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


def _chat_trace(
    query: str,
    *,
    calls: list[object] | None = None,
    results: list[object] | None = None,
) -> dict[str, object]:
    messages: list[dict[str, object]] = []
    if calls is not None:
        messages.append({"content": {"tool_call": calls}})
    if results is not None:
        messages.append({"content": {"tool_result": results}})
    return {
        "canvas": {
            "meta": {"traceId": "trace-1", "query": query},
            "agents": [{"messages": messages}],
        }
    }


def _chat_call(call_id: str, tool_name: str) -> dict[str, object]:
    return {"toolId": call_id, "toolName": tool_name, "arguments": {}}


def _chat_result(
    call_id: str, result: object, *, tool_name: str | None = None
) -> dict[str, object]:
    value: dict[str, object] = {"toolId": call_id, "result": result}
    if tool_name is not None:
        value["toolName"] = tool_name
    return value


def test_chat_extracts_structured_units_deduplicates_and_preserves_input() -> None:
    trace = _chat_trace(
        "比较两家公司收入",
        calls=[
            _chat_call("call-1", "search"),
            _chat_call("call-2", "formula"),
            _chat_call("call-3", "respond"),
        ],
        results=[
            _chat_result(
                "call-1",
                {
                    "data": [
                        {"record_id": "r1", "company": "甲", "revenue": 10},
                        {"record_id": "r2", "company": "乙", "revenue": 20},
                        {"record_id": "r1", "company": "甲", "revenue": 10},
                    ]
                },
            ),
            _chat_result("call-2", {"value": 2}),
        ],
    )
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
        "duplicate_result_observation_count": 0,
        "orphan_result_count": 0,
    }
    assert trace == original


def test_chat_parses_stringified_json_and_uses_stable_position_fallback() -> None:
    normalized = normalize_chat_evidence(
        _chat_trace(
            "查询行情",
            calls=[_chat_call("call-1", "quote")],
            results=[{"toolId": "call-1", "rawResult": '[{"price":10},{"price":11}]'}],
        ),
        _catalog(),
    )

    candidate = normalized["candidate"]
    assert candidate is not None
    assert [unit.unit_id for unit in candidate.evidence_units] == [
        "call-1:0",
        "call-1:1",
    ]


def test_identical_content_without_source_identity_is_not_merged() -> None:
    normalized = normalize_chat_evidence(
        _chat_trace(
            "查询两条独立来源记录",
            calls=[_chat_call("call-1", "search")],
            results=[_chat_result("call-1", {"data": [{"value": 10}, {"value": 10}]})],
        ),
        _catalog(),
    )

    assert normalized["evidence_unit_count"] == 2
    assert normalized["diagnostics"]["duplicate_evidence_unit_count"] == 0


def test_chat_missing_inputs_and_plain_text_evidence_are_explicit() -> None:
    catalog = _catalog()
    missing_query = normalize_chat_evidence(
        {"canvas": {"meta": {"traceId": "trace-1"}, "agents": []}}, catalog
    )
    no_calls = normalize_chat_evidence(_chat_trace("查询行情"), catalog)
    no_response = normalize_chat_evidence(
        _chat_trace("查询行情", calls=[_chat_call("call-1", "quote")]),
        catalog,
    )
    plain_text = normalize_chat_evidence(
        _chat_trace(
            "查询报告",
            calls=[_chat_call("call-1", "search")],
            results=[_chat_result("call-1", "没有可靠边界的长文本")],
        ),
        catalog,
    )

    assert missing_query["normalization_error"] == "missing_query"
    assert no_calls["normalization_error"] == "no_evidence_calls"
    assert no_response["normalization_error"] == "no_successful_evidence_responses"
    assert plain_text["normalization_error"] is None
    assert plain_text["evidence_unit_count"] == 1
    assert plain_text["diagnostics"]["unsegmentable_evidence_response_count"] == 0


def test_plain_text_and_structured_responses_are_both_retained() -> None:
    normalized = normalize_chat_evidence(
        _chat_trace(
            "查询行情和研报",
            calls=[_chat_call("call-1", "quote"), _chat_call("call-2", "search")],
            results=[
                _chat_result("call-1", {"data": [{"price": 10}]}),
                _chat_result("call-2", "无法可靠拆分的报告合集"),
            ],
        ),
        _catalog(),
    )

    assert normalized["evidence_unit_count"] == 2
    assert normalized["candidate"] is not None
    assert normalized["normalization_error"] is None


def test_nested_plain_text_payload_is_retained_as_response_level_evidence() -> None:
    normalized = normalize_chat_evidence(
        _chat_trace(
            "查询行业指标",
            calls=[_chat_call("call-1", "search")],
            results=[
                _chat_result(
                    "call-1",
                    {"code": 0, "data": "指标名称 | 单位\n产量 | 万吨", "msg": "ok"},
                )
            ],
        ),
        _catalog(),
    )

    assert normalized["candidate"] is not None
    assert normalized["evidence_unit_count"] == 1
    assert normalized["normalization_error"] is None


def test_non_text_scalar_response_remains_unsegmentable() -> None:
    normalized = normalize_chat_evidence(
        _chat_trace(
            "查询报告",
            calls=[_chat_call("call-1", "search")],
            results=[_chat_result("call-1", 42)],
        ),
        _catalog(),
    )

    assert normalized["candidate"] is None
    assert normalized["normalization_error"] == "no_stable_evidence_units"


def test_successful_empty_result_has_no_evidence_units_without_segmentation_error() -> None:
    normalized = normalize_chat_evidence(
        _chat_trace(
            "查询不存在的记录",
            calls=[_chat_call("call-1", "search")],
            results=[_chat_result("call-1", {"code": 0, "data": [], "msg": "ok"})],
        ),
        _catalog(),
    )

    assert normalized["candidate"] is None
    assert normalized["normalization_error"] == "no_evidence_units"
    assert normalized["diagnostics"]["unsegmentable_evidence_response_count"] == 0


def test_chat_joins_cross_agent_results_and_ignores_orphans() -> None:
    trace = {
        "canvas": {
            "meta": {"traceId": "trace-1", "query": "查询行情"},
            "agents": [
                {"messages": [{"content": {"tool_call": [_chat_call("call-1", "quote")]}}]},
                {
                    "messages": [
                        {
                            "content": {
                                "tool_result": [
                                    _chat_result("call-1", {"data": [{"price": 10}]}),
                                    _chat_result("orphan", {"data": [{"price": 99}]}),
                                ]
                            }
                        }
                    ]
                },
            ],
        }
    }

    normalized = normalize_chat_evidence(trace, _catalog())

    assert normalized["evidence_unit_count"] == 1
    assert normalized["diagnostics"]["orphan_result_count"] == 1


def test_chat_ignores_subagent_calls_without_counting_them_as_tools() -> None:
    normalized = normalize_chat_evidence(
        _chat_trace(
            "查询行情",
            calls=[
                {"agentName": "worker", "arguments": {"query": "执行子任务"}},
                _chat_call("call-1", "quote"),
            ],
            results=[_chat_result("call-1", {"data": [{"price": 10}]})],
        ),
        _catalog(),
    )

    assert normalized["candidate"] is not None
    assert normalized["diagnostics"]["evidence_call_count"] == 1
    assert normalized["diagnostics"]["out_of_scope_tool_call_count"] == 0


def test_chat_conflicting_call_definitions_are_not_computable() -> None:
    normalized = normalize_chat_evidence(
        _chat_trace(
            "查询行情",
            calls=[_chat_call("call-1", "quote"), _chat_call("call-1", "search")],
            results=[_chat_result("call-1", {"data": [{"price": 10}]})],
        ),
        _catalog(),
    )

    assert normalized["candidate"] is None
    assert normalized["normalization_error"] == "conflicting_call_definition"
    assert normalized["diagnostics"]["duplicate_call_definition_count"] == 1


def test_chat_duplicate_results_are_deduplicated_and_conflicts_are_rejected() -> None:
    duplicate_result = _chat_result("call-1", {"data": [{"price": 10}]})
    duplicate = normalize_chat_evidence(
        _chat_trace(
            "查询行情",
            calls=[_chat_call("call-1", "quote")],
            results=[duplicate_result, deepcopy(duplicate_result) | {"toolName": "quote"}],
        ),
        _catalog(),
    )
    conflicting = normalize_chat_evidence(
        _chat_trace(
            "查询行情",
            calls=[_chat_call("call-1", "quote")],
            results=[
                duplicate_result,
                _chat_result("call-1", {"data": [{"price": 11}]}),
            ],
        ),
        _catalog(),
    )

    assert duplicate["evidence_unit_count"] == 1
    assert duplicate["diagnostics"]["duplicate_result_observation_count"] == 1
    assert conflicting["candidate"] is None
    assert conflicting["normalization_error"] == "conflicting_call_result"


@pytest.mark.parametrize(
    ("trace", "reason"),
    [
        ({}, "invalid_chat_trace_structure"),
        ({"canvas": {"meta": {}, "agents": []}}, "missing_trace_id"),
        (
            {"canvas": {"meta": {"traceId": "trace-1", "query": "查询"}, "agents": "bad"}},
            "invalid_chat_trace_structure",
        ),
        (
            _chat_trace("查询", calls=[{"toolName": "quote"}]),
            "missing_call_id",
        ),
        (
            _chat_trace("查询", calls=[{"toolId": "call-1"}]),
            "missing_tool_name",
        ),
        (
            _chat_trace("查询", results=[{"result": {"data": []}}]),
            "missing_call_id",
        ),
    ],
)
def test_chat_invalid_structures_are_explicit(trace: dict[str, object], reason: str) -> None:
    normalized = normalize_chat_evidence(trace, _catalog())

    assert normalized["candidate"] is None
    assert normalized["normalization_error"] == reason


def test_legacy_chat_tool_call_queue_is_rejected() -> None:
    normalized = normalize_chat_evidence(
        {"traceId": "legacy", "query": "查询", "tool_call_queue": []},
        _catalog(),
    )

    assert normalized["normalization_error"] == "invalid_chat_trace_structure"


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


def test_research_plain_text_output_is_retained_as_response_level_evidence() -> None:
    normalized = normalize_research_evidence(
        [
            {
                "sessionID": "session-1",
                "userMessageID": "message-1",
                "query": "查询财务数据",
                "result": {
                    "toolCalls": [{"toolCallId": "call-1", "toolName": "mcp__finance__quote"}]
                },
            },
            {
                "part": {
                    "type": "tool",
                    "callID": "call-1",
                    "state": {"status": "completed", "output": "收入为 100 亿元"},
                }
            },
        ],
        _catalog(),
    )

    assert normalized["candidate"] is not None
    assert normalized["evidence_unit_count"] == 1
    assert normalized["normalization_error"] is None


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
