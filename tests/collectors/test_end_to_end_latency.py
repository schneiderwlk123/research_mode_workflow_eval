from copy import deepcopy

from collectors.end_to_end_latency import (
    normalize_chat_end_to_end_latency,
    normalize_research_end_to_end_latency,
)


def test_chat_extracts_request_and_answer_boundaries_without_mutation() -> None:
    trace = {
        "canvas": {
            "meta": {"traceId": "trace-1", "create_time": "2026-09-08 10:00:00"},
            "answer": {
                "create_time": "2026-09-08 10:01:25",
                "durationSeconds": 42.5,
                "answer": "SENSITIVE",
            },
        }
    }
    original = deepcopy(trace)

    normalized = normalize_chat_end_to_end_latency(trace)

    assert normalized == {
        "trace_id": "trace-1",
        "mode": "chat",
        "request_start_ms": 1788861600000,
        "answer_end_ms": 1788861685000,
        "reported_duration_ms": None,
        "timestamp_precision": "seconds",
        "diagnostics": {"duplicate_boundary_event_count": 0},
        "normalization_error": None,
    }
    assert "SENSITIVE" not in repr(normalized)
    assert trace == original


def test_chat_missing_and_invalid_boundaries_are_explicit() -> None:
    missing_start = normalize_chat_end_to_end_latency(
        {"canvas": {"meta": {"traceId": "trace-1"}, "answer": {"create_time": 2_000}}}
    )
    missing_end = normalize_chat_end_to_end_latency(
        {"canvas": {"meta": {"traceId": "trace-1", "create_time": 1_000}}}
    )
    invalid = normalize_chat_end_to_end_latency(
        {
            "canvas": {
                "meta": {"traceId": "trace-1", "create_time": "bad"},
                "answer": {"create_time": 2_000},
            }
        }
    )
    legacy = normalize_chat_end_to_end_latency(
        {"traceId": "legacy", "create_time": 1_000, "answer": {"create_time": 2_000}}
    )

    assert missing_start["normalization_error"] == "missing_start_timestamp"
    assert missing_end["normalization_error"] == "missing_end_timestamp"
    assert invalid["normalization_error"] == "invalid_timestamp"
    assert legacy["normalization_error"] == "invalid_chat_trace_structure"


def test_research_extracts_reported_duration_and_timestamp_cross_check() -> None:
    events = [
        {
            "type": "turn.start",
            "sessionID": "session-1",
            "userMessageID": "message-1",
            "ts": "2026-08-24T03:25:38.806Z",
        },
        {
            "type": "turn.end",
            "sessionID": "session-1",
            "userMessageID": "message-1",
            "ts": "2026-08-24T03:41:15.209Z",
            "durationMs": 936_405,
        },
    ]

    normalized = normalize_research_end_to_end_latency(events)

    assert normalized["trace_id"] == "session-1:message-1"
    assert normalized["request_start_ms"] == 1787541938806
    assert normalized["answer_end_ms"] == 1787542875209
    assert normalized["reported_duration_ms"] == 936_405
    assert normalized["timestamp_precision"] == "milliseconds"
    assert normalized["normalization_error"] is None


def test_research_allows_timestamp_fallback_and_deduplicates_equal_boundaries() -> None:
    start = {"type": "turn.start", "ts": 1_000}
    end = {"type": "turn.end", "ts": 2_500}

    normalized = normalize_research_end_to_end_latency([start, dict(start), end, dict(end)])

    assert normalized["request_start_ms"] == 1_000_000
    assert normalized["answer_end_ms"] == 2_500_000
    assert normalized["reported_duration_ms"] is None
    assert normalized["diagnostics"]["duplicate_boundary_event_count"] == 2
    assert normalized["normalization_error"] is None


def test_research_missing_invalid_and_conflicting_boundaries_are_explicit() -> None:
    missing_start = normalize_research_end_to_end_latency(
        [{"type": "turn.end", "ts": 2_000, "durationMs": 1_000}]
    )
    missing_end = normalize_research_end_to_end_latency([{"type": "turn.start", "ts": 1_000}])
    invalid_duration = normalize_research_end_to_end_latency(
        [
            {"type": "turn.start", "ts": 1_000},
            {"type": "turn.end", "ts": 2_000, "durationMs": "bad"},
        ]
    )
    conflicting = normalize_research_end_to_end_latency(
        [
            {"type": "turn.start", "ts": 1_000},
            {"type": "turn.start", "ts": 1_001},
            {"type": "turn.end", "ts": 2_000, "durationMs": 1_000_000},
        ]
    )

    assert missing_start["normalization_error"] == "missing_start_timestamp"
    assert missing_end["normalization_error"] == "missing_end_timestamp"
    assert invalid_duration["normalization_error"] == "invalid_duration"
    assert conflicting["normalization_error"] == "conflicting_turn_boundary"


def test_research_rejects_start_and_end_from_different_turns() -> None:
    normalized = normalize_research_end_to_end_latency(
        [
            {
                "type": "turn.start",
                "sessionID": "session-1",
                "userMessageID": "message-1",
                "ts": 1_000,
            },
            {
                "type": "turn.end",
                "sessionID": "session-2",
                "userMessageID": "message-1",
                "ts": 2_000,
                "durationMs": 1_000_000,
            },
        ]
    )

    assert normalized["normalization_error"] == "conflicting_turn_boundary"
