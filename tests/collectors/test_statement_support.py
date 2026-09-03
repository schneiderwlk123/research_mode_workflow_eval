from collectors.evidence_quality import EvidenceSourceCatalog, build_evidence_source_catalog
from collectors.statement_support import (
    normalize_chat_statement_support,
    normalize_research_statement_support,
)


def _catalog() -> EvidenceSourceCatalog:
    return build_evidence_source_catalog(
        {
            "manual_version": "0.1.0",
            "tools": [{"name": "search", "description": "检索", "parameters": []}],
        },
        evidence_tool_names={"search"},
    )


def test_chat_collects_answer_and_structured_evidence() -> None:
    normalized = normalize_chat_statement_support(
        {
            "traceId": "trace-1",
            "query": "分析公司收入",
            "answer": "公司 2025 年收入为 100 亿元。",
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
    assert candidate.query == "分析公司收入"
    assert candidate.answer == "公司 2025 年收入为 100 亿元。"
    assert [unit.unit_id for unit in candidate.evidence_units] == ["call-1:0"]
    assert normalized["evidence_unit_count"] == 1
    assert normalized["normalization_error"] is None


def test_chat_without_evidence_is_still_judgeable_but_missing_answer_is_not() -> None:
    no_evidence = normalize_chat_statement_support(
        {"query": "公司收入是多少", "answer": "公司收入为 100 亿元。"},
        _catalog(),
    )
    missing_answer = normalize_chat_statement_support(
        {"query": "公司收入是多少"},
        _catalog(),
    )
    service_failure = normalize_chat_statement_support(
        {"query": "公司收入是多少", "answer": "模型服务暂时不可用，请稍后重试。"},
        _catalog(),
    )

    assert no_evidence["candidate"] is not None
    assert no_evidence["evidence_unit_count"] == 0
    assert no_evidence["normalization_error"] is None
    assert missing_answer["normalization_error"] == "missing_final_answer"
    assert service_failure["normalization_error"] == "pre_answer_failure"


def test_chat_evidence_call_without_terminal_response_is_not_computable() -> None:
    normalized = normalize_chat_statement_support(
        {
            "query": "公司收入是多少",
            "answer": "公司收入为 100 亿元。",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"toolId": "call-1", "toolName": "search"},
                }
            ],
        },
        _catalog(),
    )

    assert normalized["candidate"] is None
    assert normalized["normalization_error"] == "incomplete_evidence_observation"


def test_partial_unsegmentable_evidence_does_not_silently_shrink_support_basis() -> None:
    normalized = normalize_chat_statement_support(
        {
            "query": "分析公司收入",
            "answer": "公司收入为 100 亿元。",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"toolId": "call-1", "toolName": "search"},
                    "tool_response_result": {"result": {"data": [{"revenue": 100}]}},
                },
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"toolId": "call-2", "toolName": "search"},
                    "tool_response_result": {"result": "无法稳定拆分的长文本"},
                },
            ],
        },
        _catalog(),
    )

    assert normalized["candidate"] is None
    assert normalized["evidence_unit_count"] == 1
    assert normalized["normalization_error"] == "incomplete_evidence_segmentation"


def test_research_uses_last_assistant_text_before_turn_end() -> None:
    events: list[object] = [
        {"sessionID": "s1", "userMessageID": "u1", "body": {"query": "分析收入"}},
        {"type": "message.updated", "info": {"id": "a1", "role": "assistant"}},
        {
            "type": "message.part.updated",
            "part": {"type": "text", "messageID": "a1", "text": "中间分析。"},
        },
        {"type": "message.updated", "info": {"id": "a2", "role": "assistant"}},
        {
            "type": "message.part.updated",
            "part": {"type": "text", "messageID": "a2", "text": "最终结论为 100 亿元。"},
        },
        {"type": "turn.end"},
        {
            "type": "message.part.updated",
            "part": {"type": "text", "messageID": "a2", "text": "结束后的噪声。"},
        },
    ]

    normalized = normalize_research_statement_support(events, _catalog())

    candidate = normalized["candidate"]
    assert candidate is not None
    assert candidate.answer == "最终结论为 100 亿元。"
    assert candidate.evidence_units == ()
    assert normalized["trace_id"] == "s1:u1"


def test_research_successful_artifact_without_final_text_is_explicit() -> None:
    normalized = normalize_research_statement_support(
        [
            {"query": "生成报告"},
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

    assert normalized["normalization_error"] == "missing_final_artifact_content"
