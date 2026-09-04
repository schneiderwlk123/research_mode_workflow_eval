from copy import deepcopy

from collectors.first_tool_decision_latency import (
    normalize_chat_first_tool_decision_latency,
    normalize_research_first_tool_decision_latency,
)


def test_chat_uses_earliest_semantic_decision_time_instead_of_queue_order() -> None:
    trace: dict[str, object] = {
        "traceId": "trace-1",
        "create_time": "2026-08-28 10:00:00",
        "tool_call_queue": [
            {
                "event_type": "tool_call",
                "tool_request_args": {
                    "toolId": "call-1",
                    "toolName": "quote",
                    "create_time": "2026-08-28 10:00:09",
                },
            },
            {
                "event_type": "call_sub_agent_begin",
                "request_payload": {"create_time": "2026-08-28 10:00:06"},
            },
        ],
    }
    original = deepcopy(trace)

    normalized = normalize_chat_first_tool_decision_latency(trace)

    assert normalized["trace_id"] == "trace-1"
    assert normalized["mode"] == "chat"
    assert normalized["request_start_ms"] == 1787911200000
    assert normalized["first_decision_ms"] == 1787911206000
    assert normalized["decision_source"] == "sub_agent"
    assert normalized["diagnostics"] == {
        "decision_event_count": 2,
        "malformed_decision_event_count": 0,
        "duplicate_pending_event_count": 0,
    }
    assert normalized["normalization_error"] is None
    assert trace == original


def test_chat_reports_missing_start_no_decision_and_incomplete_observation() -> None:
    missing_start = normalize_chat_first_tool_decision_latency(
        {
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"create_time": "2026-08-28 10:00:04"},
                }
            ]
        }
    )
    no_decision = normalize_chat_first_tool_decision_latency(
        {"create_time": "2026-08-28 10:00:00", "tool_call_queue": []}
    )
    incomplete = normalize_chat_first_tool_decision_latency(
        {
            "create_time": "2026-08-28 10:00:00",
            "tool_call_queue": [
                {"event_type": "call_sub_agent_begin", "request_payload": {}},
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"create_time": "2026-08-28 10:00:05"},
                },
            ],
        }
    )

    assert missing_start["normalization_error"] == "missing_turn_start_time"
    assert no_decision["normalization_error"] == "no_tool_decision"
    assert incomplete["normalization_error"] == "incomplete_tool_decision_observation"
    assert incomplete["diagnostics"]["malformed_decision_event_count"] == 1


def test_chat_rejects_invalid_timestamp_and_invalid_queue() -> None:
    invalid_time = normalize_chat_first_tool_decision_latency(
        {
            "create_time": "not-a-time",
            "tool_call_queue": [
                {
                    "event_type": "tool_call",
                    "tool_request_args": {"create_time": "2026-08-28 10:00:05"},
                }
            ],
        }
    )
    invalid_queue = normalize_chat_first_tool_decision_latency(
        {"create_time": "2026-08-28 10:00:00", "tool_call_queue": "invalid"}
    )
    oversized_time = normalize_chat_first_tool_decision_latency(
        {"create_time": 10**400, "tool_call_queue": []}
    )

    assert invalid_time["normalization_error"] == "invalid_timestamp"
    assert invalid_queue["normalization_error"] == "invalid_tool_call_queue"
    assert oversized_time["normalization_error"] == "invalid_timestamp"


def test_research_uses_turn_start_and_earliest_pending_event_timestamp() -> None:
    events: list[object] = [
        {
            "type": "turn.start",
            "sessionID": "session-1",
            "userMessageID": "message-1",
            "ts": "2026-08-24T03:25:38.806Z",
        },
        {
            "type": "message.part.updated",
            "ts": "2026-08-24T03:26:08.346Z",
            "part": {
                "type": "tool",
                "tool": "quote",
                "callID": "call-2",
                "state": {"status": "running", "time": {"start": 1787541968333}},
            },
        },
        {
            "type": "message.part.updated",
            "ts": "2026-08-24T03:26:07.665Z",
            "part": {
                "type": "tool",
                "tool": "formula_selector",
                "callID": "call-1",
                "state": {"status": "pending"},
            },
        },
        {
            "type": "message.part.updated",
            "ts": "2026-08-24T03:26:07.700Z",
            "part": {
                "type": "tool",
                "tool": "formula_selector",
                "callID": "call-1",
                "state": {"status": "pending"},
            },
        },
    ]

    normalized = normalize_research_first_tool_decision_latency(events)

    assert normalized["trace_id"] == "session-1:message-1"
    assert normalized["request_start_ms"] == 1787541938806
    assert normalized["first_decision_ms"] == 1787541967665
    assert normalized["decision_source"] == "tool_pending"
    assert normalized["diagnostics"]["duplicate_pending_event_count"] == 1
    assert normalized["normalization_error"] is None


def test_research_missing_pending_timestamp_and_call_id_are_explicit() -> None:
    start = {
        "type": "turn.start",
        "sessionID": "session-1",
        "userMessageID": "message-1",
        "ts": "2026-08-24T03:25:38.806Z",
    }
    no_pending = normalize_research_first_tool_decision_latency(
        [
            start,
            {
                "type": "message.part.updated",
                "ts": "2026-08-24T03:26:08.346Z",
                "part": {
                    "type": "tool",
                    "callID": "call-1",
                    "state": {"status": "running"},
                },
            },
        ]
    )
    missing_time = normalize_research_first_tool_decision_latency(
        [
            start,
            {
                "type": "message.part.updated",
                "part": {
                    "type": "tool",
                    "callID": "call-1",
                    "state": {"status": "pending"},
                },
            },
        ]
    )
    missing_call_id = normalize_research_first_tool_decision_latency(
        [
            start,
            {
                "type": "message.part.updated",
                "ts": "2026-08-24T03:26:07.665Z",
                "part": {"type": "tool", "state": {"status": "pending"}},
            },
        ]
    )

    assert no_pending["normalization_error"] == "missing_tool_decision_time"
    assert missing_time["normalization_error"] == "incomplete_tool_decision_observation"
    assert missing_call_id["normalization_error"] == "missing_call_id"


def test_research_no_tool_and_conflicting_turn_starts_are_explicit() -> None:
    no_tool = normalize_research_first_tool_decision_latency(
        [{"type": "turn.start", "ts": "2026-08-24T03:25:38.806Z"}]
    )
    conflicting = normalize_research_first_tool_decision_latency(
        [
            {"type": "turn.start", "ts": "2026-08-24T03:25:38.806Z"},
            {"type": "turn.start", "ts": "2026-08-24T03:25:39.000Z"},
        ]
    )

    assert no_tool["normalization_error"] == "no_tool_decision"
    assert conflicting["normalization_error"] == "conflicting_turn_start"
