from collectors.max_tool_latency_share import (
    normalize_chat_max_tool_latency_share,
    normalize_research_max_tool_latency_share,
)


def test_chat_excludes_respond_and_sums_business_tool_durations() -> None:
    normalized = normalize_chat_max_tool_latency_share(
        {
            "canvas": {
                "meta": {"traceId": "trace-1"},
                "agents": [
                    {
                        "messages": [
                            {
                                "content": {
                                    "tool_call": [
                                        {"toolId": "c1", "toolName": "search"},
                                        {"toolId": "c2", "toolName": "search"},
                                        {"toolId": "c3", "toolName": "Respond"},
                                    ],
                                    "tool_result": [
                                        {"toolId": "c1", "durationSeconds": 1},
                                        {"toolId": "c2", "durationSeconds": 2},
                                    ],
                                }
                            }
                        ]
                    }
                ],
            }
        }
    )

    assert normalized["total_tool_latency_ms"] == 3000
    assert normalized["per_tool_latency_totals"] == (
        {"tool_name": "search", "tool_latency_total_ms": 3000},
    )
    assert normalized["diagnostics"]["excluded_agent_return_count"] == 1
    assert normalized["diagnostics"]["duration_missing_count"] == 0
    assert normalized["normalization_error"] is None


def test_research_preserves_all_tied_business_tools() -> None:
    normalized = normalize_research_max_tool_latency_share(
        [
            _event("c1", "excel", 1_700_000_000_000, 1_700_000_001_000),
            _event("c2", "search", 1_700_000_002_000, 1_700_000_003_000),
        ]
    )

    assert normalized["total_tool_latency_ms"] == 2000
    assert normalized["per_tool_latency_totals"] == (
        {"tool_name": "excel", "tool_latency_total_ms": 1000},
        {"tool_name": "search", "tool_latency_total_ms": 1000},
    )


def test_missing_or_zero_business_duration_is_not_computable() -> None:
    missing = normalize_chat_max_tool_latency_share(
        {
            "canvas": {
                "meta": {"traceId": "trace-1"},
                "agents": [
                    {"messages": [{"content": {"tool_call": [{"toolId": "c1", "toolName": "x"}]}}]}
                ],
            }
        }
    )
    zero = normalize_chat_max_tool_latency_share(
        {
            "canvas": {
                "meta": {"traceId": "trace-2"},
                "agents": [
                    {
                        "messages": [
                            {
                                "content": {
                                    "tool_call": [{"toolId": "c1", "toolName": "x"}],
                                    "tool_result": [{"toolId": "c1", "durationSeconds": 0}],
                                }
                            }
                        ]
                    }
                ],
            }
        }
    )

    assert missing["normalization_error"] == "incomplete_tool_duration_coverage"
    assert zero["normalization_error"] == "zero_tool_duration"


def test_invalid_duration_remains_an_error() -> None:
    normalized = normalize_chat_max_tool_latency_share(
        {
            "canvas": {
                "meta": {"traceId": "trace-1"},
                "agents": [
                    {
                        "messages": [
                            {
                                "content": {
                                    "tool_call": [{"toolId": "c1", "toolName": "x"}],
                                    "tool_result": [{"toolId": "c1", "durationSeconds": "bad"}],
                                }
                            }
                        ]
                    }
                ],
            }
        }
    )

    assert normalized["normalization_error"] == "invalid_duration"


def _event(call_id: str, tool_name: str, start: int, end: int) -> dict[str, object]:
    return {
        "type": "message.part.updated",
        "part": {
            "type": "tool",
            "callID": call_id,
            "tool": tool_name,
            "state": {"status": "completed", "time": {"start": start, "end": end}},
        },
    }
