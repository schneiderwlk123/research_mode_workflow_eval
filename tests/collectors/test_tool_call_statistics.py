from copy import deepcopy

import pytest

from collectors.tool_call_statistics import (
    normalize_chat_tool_call_statistics,
    normalize_research_tool_call_statistics,
)


def test_chat_counts_unique_business_calls_without_control_events_or_mutation() -> None:
    trace = {
        "canvas": {
            "meta": {"traceId": "trace-1"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [
                                    _chat_call("call-1", "search", {"query": "SENSITIVE"}),
                                    _chat_call("call-1", "search", {"query": "SENSITIVE"}),
                                    _chat_call("call-2", "quote", {"symbol": "SECRET"}),
                                    _chat_call("call-3", "Respond", {"text": "PRIVATE"}),
                                    {
                                        "taskId": "worker-1",
                                        "agentName": "worker_search",
                                        "arguments": {"query": "HIDDEN"},
                                    },
                                ]
                            }
                        }
                    ]
                }
            ],
        }
    }
    original = deepcopy(trace)

    normalized = normalize_chat_tool_call_statistics(trace)

    assert normalized == {
        "trace_id": "trace-1",
        "mode": "chat",
        "per_tool_statistics": (
            {"tool_name": "quote", "call_count": 1, "repeat_call_count": 0},
            {"tool_name": "search", "call_count": 1, "repeat_call_count": 0},
        ),
        "diagnostics": {
            "duplicate_call_observation_count": 1,
            "excluded_agent_delegation_count": 1,
            "excluded_agent_return_count": 1,
            "malformed_event_count": 0,
        },
        "normalization_error": None,
    }
    assert "SENSITIVE" not in repr(normalized)
    assert "SECRET" not in repr(normalized)
    assert "PRIVATE" not in repr(normalized)
    assert trace == original


def test_both_modes_compute_empty_statistics_when_no_tools_exist() -> None:
    chat = normalize_chat_tool_call_statistics(
        {"canvas": {"meta": {"traceId": "trace-empty"}, "agents": []}}
    )
    research = normalize_research_tool_call_statistics([])

    assert chat["per_tool_statistics"] == ()
    assert chat["normalization_error"] is None
    assert research["per_tool_statistics"] == ()
    assert research["normalization_error"] is None


def test_research_merges_lifecycle_events_and_groups_exact_tool_names() -> None:
    events = [
        _research_event("call-1", "search", "pending"),
        _research_event("call-1", "search", "running"),
        _research_event("call-1", "search", "completed"),
        _research_event("call-2", "search", "completed"),
        _research_event("call-3", "quote", "completed"),
    ]

    normalized = normalize_research_tool_call_statistics(
        events, session_id="session-1", user_message_id="message-1"
    )

    assert normalized["trace_id"] == "session-1:message-1"
    assert normalized["per_tool_statistics"] == (
        {"tool_name": "quote", "call_count": 1, "repeat_call_count": 0},
        {"tool_name": "search", "call_count": 2, "repeat_call_count": 1},
    )
    assert normalized["diagnostics"]["duplicate_call_observation_count"] == 2


@pytest.mark.parametrize(
    ("trace", "reason"),
    [
        (
            {
                "canvas": {
                    "meta": {"traceId": "trace-1"},
                    "agents": [
                        {"messages": [{"content": {"tool_call": [{"toolName": "search"}]}}]}
                    ],
                }
            },
            "missing_call_id",
        ),
        (
            {
                "canvas": {
                    "meta": {"traceId": "trace-1"},
                    "agents": [{"messages": [{"content": {"tool_call": [{"toolId": "call-1"}]}}]}],
                }
            },
            "missing_tool_name",
        ),
    ],
)
def test_chat_rejects_missing_call_identity(trace: dict[str, object], reason: str) -> None:
    normalized = normalize_chat_tool_call_statistics(trace)

    assert normalized["per_tool_statistics"] == ()
    assert normalized["normalization_error"] == reason


def test_both_modes_reject_conflicting_call_definitions() -> None:
    chat = normalize_chat_tool_call_statistics(
        {
            "canvas": {
                "meta": {"traceId": "trace-1"},
                "agents": [
                    {
                        "messages": [
                            {
                                "content": {
                                    "tool_call": [
                                        _chat_call("call-1", "search", {}),
                                        _chat_call("call-1", "quote", {}),
                                    ]
                                }
                            }
                        ]
                    }
                ],
            }
        }
    )
    research = normalize_research_tool_call_statistics(
        [
            _research_event("call-1", "search", "running"),
            _research_event("call-1", "quote", "completed"),
        ]
    )

    assert chat["normalization_error"] == "conflicting_call_definition"
    assert research["normalization_error"] == "conflicting_call_definition"


def test_research_requires_call_id_and_tool_name() -> None:
    missing_id = normalize_research_tool_call_statistics(
        [{"type": "message.part.updated", "part": {"type": "tool", "tool": "search"}}]
    )
    missing_name = normalize_research_tool_call_statistics(
        [{"type": "message.part.updated", "part": {"type": "tool", "callID": "call-1"}}]
    )

    assert missing_id["normalization_error"] == "missing_call_id"
    assert missing_name["normalization_error"] == "missing_tool_name"


def _chat_call(call_id: str, tool_name: str, arguments: object) -> dict[str, object]:
    return {"toolId": call_id, "toolName": tool_name, "arguments": arguments}


def _research_event(call_id: str, tool_name: str, status: str) -> dict[str, object]:
    return {
        "type": "message.part.updated",
        "part": {
            "type": "tool",
            "callID": call_id,
            "tool": tool_name,
            "state": {"status": status, "input": {"secret": "DO_NOT_KEEP"}},
        },
    }
