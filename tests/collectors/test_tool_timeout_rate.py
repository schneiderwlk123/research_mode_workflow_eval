import pytest

from collectors.tool_timeout_rate import (
    ToolTimeoutPolicyCatalog,
    build_tool_timeout_policy_catalog,
    normalize_chat_tool_timeout_rate,
    normalize_research_tool_timeout_rate,
)


def _catalog() -> ToolTimeoutPolicyCatalog:
    return build_tool_timeout_policy_catalog(
        {
            "policy_version": "2026-09-04",
            "explicit_timeout_statuses": ["timeout", "timed_out"],
            "tools": [
                {
                    "name": "tool_a",
                    "timeout_ms": 5_000,
                    "aliases": ["chat_tool_a"],
                },
                {"name": "tool_b", "timeout_ms": 2_000},
            ],
        }
    )


def test_policy_catalog_validates_thresholds_and_resolves_mcp_suffix() -> None:
    catalog = _catalog()

    assert catalog.policy_version == "2026-09-04"
    assert catalog.resolve("chat_tool_a") == ("tool_a", 5_000)
    assert catalog.resolve("mcp__server__tool_b") == ("tool_b", 2_000)

    with pytest.raises(ValueError, match="timeout_ms"):
        build_tool_timeout_policy_catalog(
            {
                "policy_version": "v1",
                "tools": [{"name": "tool_a", "timeout_ms": 0}],
            }
        )
    with pytest.raises(ValueError, match="别名冲突"):
        build_tool_timeout_policy_catalog(
            {
                "policy_version": "v1",
                "tools": [
                    {"name": "tool_a", "timeout_ms": 1, "aliases": ["tool_b"]},
                    {"name": "tool_b", "timeout_ms": 1},
                ],
            }
        )


def test_chat_canvas_marks_sla_explicit_and_missing_time_timeouts() -> None:
    trace = _chat_trace(
        [
            {
                "content": {
                    "tool_call": [
                        _chat_call("call-1", "chat_tool_a"),
                        _chat_call("call-2", "tool_b"),
                        {"taskId": "worker-1", "agentName": "worker_search"},
                    ]
                }
            },
            {
                "content": {
                    "tool_result": [
                        _chat_result("call-1", "tool_a", duration_seconds=4.0),
                        _chat_result("call-2", "tool_b", duration_seconds=3.0),
                    ]
                }
            },
            {
                "content": {
                    "tool_call": [
                        _chat_call("call-3", "tool_a"),
                        _chat_call("call-4", "tool_a"),
                        _chat_call("call-5", "tool_b"),
                    ],
                    "tool_result": [
                        _chat_result(
                            "call-3",
                            "tool_a",
                            duration_seconds=1.0,
                            result={"status": "timed_out", "secret": "SENSITIVE"},
                        ),
                        _chat_result("call-4", "tool_a"),
                        _chat_result("call-5", "tool_b", duration_seconds=2.0),
                        _chat_result("orphan", "tool_a", duration_seconds=99.0),
                    ],
                }
            },
        ]
    )

    normalized = normalize_chat_tool_timeout_rate(trace, _catalog())

    assert [call["timeout_reason"] for call in normalized["tool_calls"]] == [
        None,
        "sla_exceeded",
        "explicit_timeout",
        "missing_timestamps",
        None,
    ]
    assert normalized["diagnostics"] == {
        "explicit_timeout_count": 1,
        "sla_exceeded_count": 1,
        "missing_time_timeout_count": 1,
        "duplicate_state_event_count": 0,
        "malformed_event_count": 0,
    }
    assert "SENSITIVE" not in repr(normalized)


def test_chat_unknown_tool_or_invalid_time_is_not_silently_scored() -> None:
    unknown = normalize_chat_tool_timeout_rate(
        _chat_trace(
            [
                {
                    "content": {
                        "tool_call": [_chat_call("call-1", "unknown")],
                        "tool_result": [_chat_result("call-1", "unknown", duration_seconds=1.0)],
                    }
                }
            ]
        ),
        _catalog(),
    )
    invalid = normalize_chat_tool_timeout_rate(
        _chat_trace(
            [
                {
                    "content": {
                        "tool_call": [_chat_call("call-1", "tool_a")],
                        "tool_result": [_chat_result("call-1", "tool_a", duration_seconds="bad")],
                    }
                }
            ]
        ),
        _catalog(),
    )

    assert unknown["normalization_error"] == "missing_tool_sla"
    assert invalid["normalization_error"] == "invalid_timestamp"


def test_chat_deduplicates_aliases_and_rejects_conflicting_definitions() -> None:
    request = _chat_call("call-1", "chat_tool_a")
    normalized = normalize_chat_tool_timeout_rate(
        _chat_trace(
            [
                {"content": {"tool_call": [request, dict(request)]}},
                {
                    "content": {
                        "tool_result": [
                            _chat_result("call-1", "tool_a", duration_seconds=4.0),
                            _chat_result("call-1", "tool_a", duration_seconds=4.0),
                        ]
                    }
                },
            ]
        ),
        _catalog(),
    )
    assert normalized["normalization_error"] is None
    assert normalized["tool_calls"] == ({"call_id": "call-1", "timeout_reason": None},)
    assert normalized["diagnostics"]["duplicate_state_event_count"] == 2

    conflicting = normalize_chat_tool_timeout_rate(
        _chat_trace(
            [
                {
                    "content": {
                        "tool_call": [
                            _chat_call("call-1", "tool_a"),
                            _chat_call("call-1", "tool_b"),
                        ]
                    }
                }
            ]
        ),
        _catalog(),
    )
    assert conflicting["normalization_error"] == "conflicting_call_definition"


def test_chat_rejects_missing_ids_invalid_containers_and_legacy_queue() -> None:
    missing_id = normalize_chat_tool_timeout_rate(
        _chat_trace([{"content": {"tool_call": [{"toolName": "tool_a"}]}}]), _catalog()
    )
    invalid = normalize_chat_tool_timeout_rate(
        {"canvas": {"meta": {"traceId": "chat-1"}, "agents": "invalid"}}, _catalog()
    )
    legacy = normalize_chat_tool_timeout_rate(
        {"traceId": "legacy", "tool_call_queue": []}, _catalog()
    )

    assert missing_id["normalization_error"] == "missing_call_id"
    assert invalid["normalization_error"] == "invalid_chat_trace_structure"
    assert legacy["normalization_error"] == "invalid_chat_trace_structure"


def test_research_deduplicates_lifecycle_and_uses_earliest_start_and_terminal_end() -> None:
    events = [
        _research_event("call-1", "tool_a", "pending"),
        _research_event("call-1", "tool_a", "running", start=10_000),
        _research_event("call-1", "tool_a", "running", start=11_000),
        _research_event("call-1", "tool_a", "completed", start=10_000, end=16_000),
        _research_event("call-2", "tool_b", "timeout", start=20_000),
    ]

    normalized = normalize_research_tool_timeout_rate(events, _catalog())

    assert [call["timeout_reason"] for call in normalized["tool_calls"]] == [
        "sla_exceeded",
        "explicit_timeout",
    ]
    assert normalized["diagnostics"]["duplicate_state_event_count"] == 1
    assert normalized["diagnostics"]["missing_time_timeout_count"] == 0


def test_research_missing_terminal_or_time_is_counted_as_timeout() -> None:
    normalized = normalize_research_tool_timeout_rate(
        [
            _research_event("call-1", "tool_a", "running", start=10_000),
            _research_event("call-2", "tool_b", "completed", end=12_000),
        ],
        _catalog(),
    )

    assert [call["timeout_reason"] for call in normalized["tool_calls"]] == [
        "missing_timestamps",
        "missing_timestamps",
    ]
    assert normalized["diagnostics"]["missing_time_timeout_count"] == 2


def test_research_can_recover_tool_name_from_later_lifecycle_event() -> None:
    first = _research_event("call-1", "tool_a", "running", start=10_000)
    part = first["part"]
    assert isinstance(part, dict)
    part.pop("tool")

    normalized = normalize_research_tool_timeout_rate(
        [first, _research_event("call-1", "tool_a", "completed", start=10_000, end=10_004)],
        _catalog(),
    )

    assert normalized["normalization_error"] is None
    assert normalized["tool_calls"] == ({"call_id": "call-1", "timeout_reason": None},)


def test_research_missing_call_id_and_conflicting_tool_are_isolated() -> None:
    missing_id = normalize_research_tool_timeout_rate(
        [_research_event(None, "tool_a", "running", start=1_000)], _catalog()
    )
    conflict = normalize_research_tool_timeout_rate(
        [
            _research_event("call-1", "tool_a", "running", start=1_000),
            _research_event("call-1", "tool_b", "completed", start=1_000, end=2_000),
        ],
        _catalog(),
    )

    assert missing_id["normalization_error"] == "missing_call_id"
    assert conflict["normalization_error"] == "conflicting_call_definition"


def _chat_trace(messages: list[dict[str, object]]) -> dict[str, object]:
    midpoint = len(messages) // 2
    return {
        "canvas": {
            "meta": {"traceId": "chat-1"},
            "agents": [
                {"messages": messages[:midpoint]},
                {"messages": messages[midpoint:]},
            ],
        }
    }


def _chat_call(call_id: str, tool_name: str) -> dict[str, object]:
    return {"toolId": call_id, "toolName": tool_name, "arguments": {}}


def _chat_result(
    call_id: str,
    tool_name: str,
    *,
    duration_seconds: object = None,
    result: object = None,
) -> dict[str, object]:
    value: dict[str, object] = {"toolId": call_id, "toolName": tool_name}
    if duration_seconds is not None:
        value["durationSeconds"] = duration_seconds
    if result is not None:
        value["result"] = result
    return value


def _research_event(
    call_id: str | None,
    tool_name: str,
    status: str,
    *,
    start: object = None,
    end: object = None,
) -> dict[str, object]:
    time: dict[str, object] = {}
    if start is not None:
        time["start"] = start
    if end is not None:
        time["end"] = end
    part: dict[str, object] = {
        "type": "tool",
        "tool": tool_name,
        "state": {"status": status, "time": time},
    }
    if call_id is not None:
        part["callID"] = call_id
    return {"type": "message.part.updated", "part": part}
