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


def test_chat_normalization_marks_sla_explicit_and_missing_time_timeouts() -> None:
    trace = {
        "traceId": "chat-1",
        "tool_call_queue": [
            _chat_call("chat_tool_a", "2026-09-04 10:00:00", "2026-09-04 10:00:04"),
            _chat_call("tool_b", "2026-09-04 10:00:00", "2026-09-04 10:00:03"),
            _chat_call(
                "tool_a",
                "2026-09-04 10:00:00",
                "2026-09-04 10:00:01",
                status="timed_out",
            ),
            _chat_call("tool_a", "2026-09-04 10:00:00", None),
        ],
    }

    normalized = normalize_chat_tool_timeout_rate(trace, _catalog())

    assert [call["timeout_reason"] for call in normalized["tool_calls"]] == [
        None,
        "sla_exceeded",
        "explicit_timeout",
        "missing_timestamps",
    ]
    assert normalized["diagnostics"] == {
        "explicit_timeout_count": 1,
        "sla_exceeded_count": 1,
        "missing_time_timeout_count": 1,
        "duplicate_state_event_count": 0,
        "malformed_event_count": 0,
    }
    assert "tool_response_result" not in repr(normalized)


def test_chat_unknown_tool_or_invalid_time_is_not_silently_scored() -> None:
    unknown = normalize_chat_tool_timeout_rate(
        {"tool_call_queue": [_chat_call("unknown", 1_000, 2_000)]}, _catalog()
    )
    invalid = normalize_chat_tool_timeout_rate(
        {"tool_call_queue": [_chat_call("tool_a", "bad", 2_000)]}, _catalog()
    )

    assert unknown["normalization_error"] == "missing_tool_sla"
    assert invalid["normalization_error"] == "invalid_timestamp"


def test_chat_aliases_can_differ_but_conflicting_call_ids_are_rejected() -> None:
    aliased_call = {
        "event_type": "tool_call",
        "tool_request_args": {
            "toolName": "chat_tool_a",
            "toolId": "call-1",
            "create_time": 1_000,
        },
        "tool_response_result": {
            "toolName": "tool_a",
            "toolId": "call-1",
            "create_time": 1_004,
        },
    }
    normalized = normalize_chat_tool_timeout_rate({"tool_call_queue": [aliased_call]}, _catalog())
    assert normalized["normalization_error"] is None

    conflicting_call = {
        **aliased_call,
        "tool_response_result": {
            "toolName": "tool_a",
            "toolId": "call-2",
            "create_time": 1_004,
        },
    }
    conflicting = normalize_chat_tool_timeout_rate(
        {"tool_call_queue": [conflicting_call]}, _catalog()
    )
    assert conflicting["normalization_error"] == "conflicting_call_definition"


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


def _chat_call(
    tool_name: str, start: object, end: object, *, status: str | None = None
) -> dict[str, object]:
    response: dict[str, object] = {"create_time": end}
    if status is not None:
        response["status"] = status
    return {
        "event_type": "tool_call",
        "tool_request_args": {"toolName": tool_name, "create_time": start},
        "tool_response_result": response,
    }


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
