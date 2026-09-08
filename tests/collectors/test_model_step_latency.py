from copy import deepcopy

from collectors.model_step_latency import (
    normalize_chat_model_step_latency,
    normalize_research_model_step_latency,
)


def test_chat_collects_message_durations_and_ignores_answer_duplicate() -> None:
    trace = {
        "canvas": {
            "meta": {"traceId": "trace-1"},
            "agents": [
                {
                    "agentName": "orchestrator",
                    "messages": [
                        {"role": "assistant", "content": {"durationSeconds": 1.25}},
                        {"role": "assistant", "content": {}},
                    ],
                },
                {
                    "agentName": "worker_search",
                    "messages": [{"role": "assistant", "content": {"durationSeconds": 2}}],
                },
            ],
            "answer": {"durationSeconds": 2, "answer": "SENSITIVE"},
        }
    }
    original = deepcopy(trace)

    normalized = normalize_chat_model_step_latency(trace)

    assert normalized == {
        "trace_id": "trace-1",
        "mode": "chat",
        "step_durations_ms": (1_250, 2_000),
        "diagnostics": {
            "valid_step_count": 2,
            "latency_missing_count": 1,
            "duplicate_boundary_event_count": 0,
            "timestamp_cross_check_count": 0,
            "max_abs_boundary_difference_ms": None,
        },
        "normalization_error": None,
    }
    assert "SENSITIVE" not in repr(normalized)
    assert trace == original


def test_chat_missing_all_durations_is_not_observable() -> None:
    normalized = normalize_chat_model_step_latency(
        {
            "canvas": {
                "meta": {"traceId": "trace-1"},
                "agents": [{"messages": [{"content": {}}, {"content": {}}]}],
            }
        }
    )

    assert normalized["step_durations_ms"] == ()
    assert normalized["diagnostics"]["latency_missing_count"] == 2
    assert normalized["normalization_error"] == "no_observable_model_steps"


def test_chat_rejects_legacy_invalid_and_negative_durations() -> None:
    legacy = normalize_chat_model_step_latency({"agents": []})
    invalid = normalize_chat_model_step_latency(
        {
            "canvas": {
                "meta": {"traceId": "trace-1"},
                "agents": [{"messages": [{"content": {"durationSeconds": "bad"}}]}],
            }
        }
    )
    negative = normalize_chat_model_step_latency(
        {
            "canvas": {
                "meta": {"traceId": "trace-1"},
                "agents": [{"messages": [{"content": {"durationSeconds": -1}}]}],
            }
        }
    )

    assert legacy["normalization_error"] == "invalid_chat_trace_structure"
    assert invalid["normalization_error"] == "invalid_duration"
    assert negative["normalization_error"] == "invalid_event_order"


def test_research_pairs_step_boundaries_by_message_id_and_cross_checks_ts() -> None:
    events = [
        {
            "type": "turn.start",
            "sessionID": "session-1",
            "userMessageID": "user-1",
        },
        {
            "type": "message.part.updated",
            "time": 1_000,
            "ts": "1970-01-01T00:00:01.010Z",
            "part": {"type": "step-start", "messageID": "assistant-1"},
        },
        {
            "type": "message.part.updated",
            "time": 1_500,
            "ts": "1970-01-01T00:00:01.530Z",
            "part": {"type": "step-finish", "messageID": "assistant-1"},
        },
        {
            "type": "message.part.updated",
            "time": 2_000,
            "part": {"type": "step-start", "messageID": "assistant-2"},
        },
    ]

    normalized = normalize_research_model_step_latency(events)

    assert normalized["trace_id"] == "session-1:user-1"
    assert normalized["step_durations_ms"] == (500,)
    assert normalized["diagnostics"] == {
        "valid_step_count": 1,
        "latency_missing_count": 1,
        "duplicate_boundary_event_count": 0,
        "timestamp_cross_check_count": 1,
        "max_abs_boundary_difference_ms": 20,
    }
    assert normalized["normalization_error"] is None


def test_research_deduplicates_equal_boundaries() -> None:
    start = {
        "type": "message.part.updated",
        "time": 1_000,
        "part": {"type": "step-start", "messageID": "assistant-1"},
    }
    finish = {
        "type": "message.part.updated",
        "time": 2_000,
        "part": {"type": "step-finish", "messageID": "assistant-1"},
    }

    normalized = normalize_research_model_step_latency([start, dict(start), finish, dict(finish)])

    assert normalized["step_durations_ms"] == (1_000,)
    assert normalized["diagnostics"]["duplicate_boundary_event_count"] == 2
    assert normalized["normalization_error"] is None


def test_research_with_only_incomplete_steps_is_not_observable() -> None:
    normalized = normalize_research_model_step_latency(
        [
            {
                "type": "message.part.updated",
                "time": 1_000,
                "part": {"type": "step-start", "messageID": "assistant-1"},
            }
        ]
    )

    assert normalized["step_durations_ms"] == ()
    assert normalized["diagnostics"]["latency_missing_count"] == 1
    assert normalized["normalization_error"] == "no_observable_model_steps"


def test_research_rejects_conflicting_invalid_and_negative_boundaries() -> None:
    conflicting = normalize_research_model_step_latency(
        [
            {
                "type": "message.part.updated",
                "time": 1_000,
                "part": {"type": "step-start", "messageID": "assistant-1"},
            },
            {
                "type": "message.part.updated",
                "time": 1_001,
                "part": {"type": "step-start", "messageID": "assistant-1"},
            },
        ]
    )
    invalid = normalize_research_model_step_latency(
        [
            {
                "type": "message.part.updated",
                "time": "bad",
                "part": {"type": "step-start", "messageID": "assistant-1"},
            }
        ]
    )
    negative = normalize_research_model_step_latency(
        [
            {
                "type": "message.part.updated",
                "time": 2_000,
                "part": {"type": "step-start", "messageID": "assistant-1"},
            },
            {
                "type": "message.part.updated",
                "time": 1_000,
                "part": {"type": "step-finish", "messageID": "assistant-1"},
            },
        ]
    )

    assert conflicting["normalization_error"] == "conflicting_step_boundary"
    assert invalid["normalization_error"] == "invalid_timestamp"
    assert negative["normalization_error"] == "invalid_event_order"


def test_research_requires_message_id_for_step_boundaries() -> None:
    normalized = normalize_research_model_step_latency(
        [
            {
                "type": "message.part.updated",
                "time": 1_000,
                "part": {"type": "step-start"},
            }
        ]
    )

    assert normalized["normalization_error"] == "missing_step_id"
