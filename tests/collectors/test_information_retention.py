from collectors.evidence_quality import EvidenceSourceCatalog, build_evidence_source_catalog
from collectors.information_retention import (
    normalize_chat_information_retention,
    normalize_research_information_retention,
)


def _catalog() -> EvidenceSourceCatalog:
    return build_evidence_source_catalog(
        {
            "manual_version": "0.1.0",
            "tools": [{"name": "search", "description": "检索", "parameters": []}],
        },
        evidence_tool_names={"search"},
    )


def test_chat_collects_final_answer_and_upstream_evidence() -> None:
    normalized = normalize_chat_information_retention(
        {
            "traceId": "trace-1",
            "query": "分析收入",
            "answer": "公司收入为 100 亿元。",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"toolId": "call-1", "toolName": "search"},
                    "tool_response_result": {
                        "result": {"data": [{"record_id": "r1", "revenue": 100}]}
                    },
                }
            ],
        },
        _catalog(),
    )

    candidate = normalized["candidate"]
    assert candidate is not None
    assert candidate.query == "分析收入"
    assert candidate.answer == "公司收入为 100 亿元。"
    assert [unit.unit_id for unit in candidate.evidence_units] == ["call-1:0"]
    assert normalized["normalization_error"] is None


def test_chat_requires_upstream_facts_and_final_answer() -> None:
    no_calls = normalize_chat_information_retention(
        {"query": "分析收入", "answer": "公司收入为 100 亿元。"}, _catalog()
    )
    missing_answer = normalize_chat_information_retention(
        {
            "query": "分析收入",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"toolId": "call-1", "toolName": "search"},
                    "tool_response_result": {"result": {"data": [{"revenue": 100}]}},
                }
            ],
        },
        _catalog(),
    )

    assert no_calls["normalization_error"] == "no_upstream_evidence"
    assert missing_answer["normalization_error"] == "missing_final_answer"


def test_chat_explicit_service_failure_becomes_empty_final_output_candidate() -> None:
    normalized = normalize_chat_information_retention(
        {
            "query": "分析收入",
            "answer": "模型服务暂时不可用，请稍后重试。",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"toolId": "call-1", "toolName": "search"},
                    "tool_response_result": {"result": {"data": [{"revenue": 100}]}},
                }
            ],
        },
        _catalog(),
    )

    assert normalized["candidate"] is not None
    assert normalized["candidate"].answer == ""
    assert normalized["normalization_error"] is None


def test_chat_incomplete_or_unsegmentable_upstream_results_are_explicit() -> None:
    incomplete = normalize_chat_information_retention(
        {
            "query": "分析收入",
            "answer": "收入为 100 亿元。",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"toolId": "call-1", "toolName": "search"},
                }
            ],
        },
        _catalog(),
    )
    unsegmentable = normalize_chat_information_retention(
        {
            "query": "分析收入",
            "answer": "收入为 100 亿元。",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"toolId": "call-1", "toolName": "search"},
                    "tool_response_result": {"result": "无法稳定拆分的长文本"},
                }
            ],
        },
        _catalog(),
    )

    assert incomplete["normalization_error"] == "incomplete_evidence_observation"
    assert unsegmentable["normalization_error"] == "incomplete_fact_segmentation"


def test_chat_failed_or_empty_upstream_results_have_distinct_reasons() -> None:
    failed = normalize_chat_information_retention(
        {
            "query": "分析收入",
            "answer": "无法完成。",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"toolId": "call-1", "toolName": "search"},
                    "tool_response_result": {"status": "failed", "result": {"error": "x"}},
                }
            ],
        },
        _catalog(),
    )
    empty = normalize_chat_information_retention(
        {
            "query": "分析收入",
            "answer": "没有结果。",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"toolId": "call-1", "toolName": "search"},
                    "tool_response_result": {"status": "completed", "result": {"data": []}},
                }
            ],
        },
        _catalog(),
    )

    assert failed["normalization_error"] == "no_successful_upstream_results"
    assert empty["normalization_error"] == "no_upstream_facts"


def test_research_uses_last_assistant_text_before_turn_end() -> None:
    normalized = normalize_research_information_retention(
        [
            {"sessionID": "s1", "userMessageID": "u1", "query": "分析收入"},
            {
                "result": {
                    "toolCalls": [{"toolCallId": "call-1", "toolName": "search", "input": {}}]
                }
            },
            {
                "type": "message.part.updated",
                "part": {
                    "type": "tool",
                    "callID": "call-1",
                    "state": {
                        "status": "completed",
                        "output": {"data": [{"record_id": "r1", "revenue": 100}]},
                    },
                },
            },
            {"type": "message.updated", "info": {"id": "a1", "role": "assistant"}},
            {
                "type": "message.part.updated",
                "part": {"type": "text", "messageID": "a1", "text": "最终收入为 100 亿元。"},
            },
            {"type": "turn.end"},
        ],
        _catalog(),
    )

    candidate = normalized["candidate"]
    assert candidate is not None
    assert candidate.answer == "最终收入为 100 亿元。"
    assert normalized["trace_id"] == "s1:u1"


def test_research_empty_terminal_answer_is_zero_candidate_but_artifact_path_is_not() -> None:
    base_events: list[object] = [
        {"query": "分析收入"},
        {"result": {"toolCalls": [{"toolCallId": "call-1", "toolName": "search", "input": {}}]}},
        {
            "type": "message.part.updated",
            "part": {
                "type": "tool",
                "callID": "call-1",
                "state": {"status": "completed", "output": {"data": [{"revenue": 100}]}},
            },
        },
    ]
    empty_answer = normalize_research_information_retention(
        [*base_events, {"type": "turn.end"}], _catalog()
    )
    artifact_only = normalize_research_information_retention(
        [
            *base_events,
            {
                "type": "message.part.updated",
                "part": {
                    "type": "tool",
                    "callID": "artifact-1",
                    "state": {"status": "completed", "output": {"path": "report.md"}},
                },
            },
            {"type": "turn.end"},
        ],
        _catalog(),
    )

    assert empty_answer["candidate"] is not None
    assert empty_answer["candidate"].answer == ""
    assert artifact_only["normalization_error"] == "missing_final_artifact_content"


def test_research_without_turn_end_is_incomplete() -> None:
    normalized = normalize_research_information_retention(
        [
            {"query": "分析收入"},
            {
                "result": {
                    "toolCalls": [{"toolCallId": "call-1", "toolName": "search", "input": {}}]
                }
            },
            {
                "type": "message.part.updated",
                "part": {
                    "type": "tool",
                    "callID": "call-1",
                    "state": {"status": "completed", "output": {"data": [{"revenue": 100}]}},
                },
            },
        ],
        _catalog(),
    )

    assert normalized["normalization_error"] == "incomplete_session_observation"
