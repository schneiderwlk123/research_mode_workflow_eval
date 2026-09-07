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


def _chat_trace(
    query: str,
    answer: str,
    *,
    calls: list[object],
    results: list[object] | None = None,
) -> dict[str, object]:
    messages: list[dict[str, object]] = [{"content": {"tool_call": calls}}]
    if results is not None:
        messages.append({"content": {"tool_result": results}})
    return {
        "canvas": {
            "meta": {"traceId": "trace-1", "query": query},
            "agents": [{"messages": messages}],
            "answer": {"answer": answer},
        },
    }


def test_chat_reuses_final_answer_and_structured_evidence() -> None:
    normalized = normalize_chat_numeric_consistency(
        _chat_trace(
            "分析收入",
            "2025 年收入为 123.5 亿元。",
            calls=[{"toolId": "call-1", "toolName": "search", "arguments": {}}],
            results=[
                {
                    "toolId": "call-1",
                    "result": {"data": [{"record_id": "r1", "revenue": 123.46}]},
                }
            ],
        ),
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
        _chat_trace(
            "分析收入",
            "收入为 100 亿元。",
            calls=[{"toolId": "call-1", "toolName": "search", "arguments": {}}],
        ),
        _catalog(),
    )

    assert normalized["candidate"] is None
    assert normalized["normalization_error"] == "incomplete_evidence_observation"


def test_chat_rejects_legacy_top_level_query_answer_and_queue() -> None:
    normalized = normalize_chat_numeric_consistency(
        {
            "traceId": "legacy",
            "query": "分析收入",
            "answer": "收入为 100 亿元。",
            "tool_call_queue": [],
        },
        _catalog(),
    )

    assert normalized["candidate"] is None
    assert normalized["normalization_error"] == "invalid_chat_trace_structure"


def test_chat_missing_trace_id_is_not_hidden_by_missing_answer() -> None:
    normalized = normalize_chat_numeric_consistency(
        {"canvas": {"meta": {"query": "分析收入"}, "agents": [], "answer": {}}},
        _catalog(),
    )

    assert normalized["candidate"] is None
    assert normalized["normalization_error"] == "missing_trace_id"
