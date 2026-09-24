from collectors.tool_call_latency import (
    normalize_chat_tool_call_latency,
    normalize_research_tool_call_latency,
)


def test_chat_groups_valid_and_missing_durations_by_tool() -> None:
    trace = {
        "canvas": {
            "meta": {"traceId": "trace-1"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [
                                    {"toolId": "c1", "toolName": "quote"},
                                    {"toolId": "c2", "toolName": "quote"},
                                    {"toolId": "c3", "toolName": "quote"},
                                ]
                            }
                        },
                        {
                            "content": {
                                "tool_result": [
                                    {"toolId": "c1", "durationSeconds": 1},
                                    {"toolId": "c2", "durationSeconds": 2},
                                ]
                            }
                        },
                    ]
                }
            ],
        }
    }

    normalized = normalize_chat_tool_call_latency(trace)

    assert normalized["per_tool_statistics"] == (
        {
            "tool_name": "quote",
            "call_count": 3,
            "latency_observed_count": 2,
            "latency_missing_count": 1,
            "latency_error_count": 0,
            "tool_latency_total_ms": 3000,
            "duration_error_reasons": (),
        },
    )
    assert normalized["normalization_error"] is None


def test_research_uses_earliest_start_and_latest_terminal_end() -> None:
    events = [
        _research_event("c1", "search", "pending", start=1_700_000_001_000),
        _research_event(
            "c1", "search", "completed", start=1_700_000_001_100, end=1_700_000_002_500
        ),
        _research_event(
            "c2", "search", "completed", start=1_700_000_003_000, end=1_700_000_003_500
        ),
    ]

    normalized = normalize_research_tool_call_latency(events)

    assert normalized["per_tool_statistics"] == (
        {
            "tool_name": "search",
            "call_count": 2,
            "latency_observed_count": 2,
            "latency_missing_count": 0,
            "latency_error_count": 0,
            "tool_latency_total_ms": 2000,
            "duration_error_reasons": (),
        },
    )


def test_invalid_duration_is_isolated_to_affected_tool() -> None:
    trace = {
        "canvas": {
            "meta": {"traceId": "trace-1"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [
                                    {"toolId": "c1", "toolName": "bad"},
                                    {"toolId": "c2", "toolName": "good"},
                                ],
                                "tool_result": [
                                    {"toolId": "c1", "durationSeconds": "bad"},
                                    {"toolId": "c2", "durationSeconds": 0.25},
                                ],
                            }
                        }
                    ]
                }
            ],
        }
    }

    normalized = normalize_chat_tool_call_latency(trace)

    assert normalized["per_tool_statistics"] == (
        {
            "tool_name": "bad",
            "call_count": 1,
            "latency_observed_count": 0,
            "latency_missing_count": 0,
            "latency_error_count": 1,
            "tool_latency_total_ms": 0,
            "duration_error_reasons": ("invalid_duration",),
        },
        {
            "tool_name": "good",
            "call_count": 1,
            "latency_observed_count": 1,
            "latency_missing_count": 0,
            "latency_error_count": 0,
            "tool_latency_total_ms": 250,
            "duration_error_reasons": (),
        },
    )


def test_chat_excludes_respond_from_per_tool_statistics() -> None:
    # 回归(2026-09-21): respond 曾泄漏进逐工具时延统计(宽表聊天列 13 处),
    # 与 per_tool_call_tokens 的业务工具范围不一致;排除后不得出现在统计中,
    # 且其耗时缺失不计入 missing 诊断。
    trace = {
        "canvas": {
            "meta": {"traceId": "trace-1"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [
                                    {"toolId": "c1", "toolName": "quote"},
                                    {"toolId": "c2", "toolName": "respond"},
                                ],
                                "tool_result": [
                                    {"toolId": "c1", "durationSeconds": 1},
                                    {"toolId": "c2", "durationSeconds": 5},
                                ],
                            }
                        }
                    ]
                }
            ],
        }
    }

    normalized = normalize_chat_tool_call_latency(trace)

    assert [item["tool_name"] for item in normalized["per_tool_statistics"]] == ["quote"]
    assert normalized["diagnostics"]["excluded_agent_return_count"] == 1
    assert normalized["diagnostics"]["duration_missing_count"] == 0
    assert normalized["normalization_error"] is None


def _research_event(
    call_id: str,
    tool_name: str,
    status: str,
    *,
    start: int,
    end: int | None = None,
) -> dict[str, object]:
    time: dict[str, int] = {"start": start}
    if end is not None:
        time["end"] = end
    return {
        "type": "message.part.updated",
        "part": {
            "type": "tool",
            "callID": call_id,
            "tool": tool_name,
            "state": {"status": status, "time": time},
        },
    }
