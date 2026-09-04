from collectors.evidence_quality import EvidenceSourceCatalog, build_evidence_source_catalog
from collectors.numeric_consistency import (
    NumericConsistencyCandidate,
    normalize_chat_numeric_consistency,
    normalize_research_numeric_consistency,
)


def _catalog() -> EvidenceSourceCatalog:
    return build_evidence_source_catalog(
        {
            "manual_version": "0.1.0",
            "tools": [{"name": "search", "description": "检索", "parameters": []}],
        },
        evidence_tool_names={"search"},
    )


def test_chat_reuses_final_answer_and_structured_evidence() -> None:
    normalized = normalize_chat_numeric_consistency(
        {
            "traceId": "trace-1",
            "query": "分析收入",
            "answer": "2025 年收入为 123.5 亿元。",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"toolId": "call-1", "toolName": "search"},
                    "tool_response_result": {
                        "result": {"data": [{"record_id": "r1", "revenue": 123.46}]}
                    },
                }
            ],
        },
        _catalog(),
    )

    candidate = normalized["candidate"]
    assert isinstance(candidate, NumericConsistencyCandidate)
    assert candidate.answer == "2025 年收入为 123.5 亿元。"
    assert [unit.unit_id for unit in candidate.evidence_units] == ["call-1:0"]
    assert normalized["normalization_error"] is None


def test_research_reuses_last_answer_selection() -> None:
    events: list[object] = [
        {"sessionID": "s1", "userMessageID": "u1", "body": {"query": "分析收入"}},
        {"type": "message.updated", "info": {"id": "a1", "role": "assistant"}},
        {
            "type": "message.part.updated",
            "part": {"type": "text", "messageID": "a1", "text": "收入为 100 亿元。"},
        },
        {"type": "turn.end"},
    ]

    normalized = normalize_research_numeric_consistency(events, _catalog())

    candidate = normalized["candidate"]
    assert isinstance(candidate, NumericConsistencyCandidate)
    assert candidate.answer == "收入为 100 亿元。"
    assert normalized["trace_id"] == "s1:u1"


def test_incomplete_evidence_is_preserved_as_not_computable() -> None:
    normalized = normalize_chat_numeric_consistency(
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

    assert normalized["candidate"] is None
    assert normalized["normalization_error"] == "incomplete_evidence_observation"
