from copy import deepcopy

from collectors.tool_resource_usage import (
    normalize_chat_tool_resource_usage,
    normalize_research_tool_resource_usage,
)


def test_chat_extracts_each_tool_duration_and_marks_tokens_unobservable() -> None:
    trace = {
        "canvas": {
            "meta": {"traceId": "trace-1"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [
                                    {"toolId": "call-1", "toolName": "search"},
                                    {"taskId": "worker-1", "agentName": "worker"},
                                ]
                            }
                        },
                        {
                            "content": {
                                "tool_result": [
                                    {
                                        "toolId": "call-1",
                                        "toolName": "search",
                                        "durationSeconds": 1.25,
                                        "result": "SENSITIVE",
                                    }
                                ]
                            }
                        },
                    ]
                }
            ],
        }
    }
    original = deepcopy(trace)

    normalized = normalize_chat_tool_resource_usage(trace)

    assert normalized["tool_calls"] == (
        {
            "trace_id": "trace-1",
            "mode": "chat",
            "call_id": "call-1",
            "tool_name": "search",
            "duration_ms": 1_250,
            "duration_error": None,
            "token_total": None,
            "token_error": "no_observable_tool_token_usage",
        },
    )
    assert normalized["diagnostics"] == {
        "duration_missing_count": 0,
        "duplicate_observation_count": 0,
        "orphan_result_count": 0,
        "malformed_event_count": 0,
        "excluded_agent_delegation_count": 1,
        "excluded_agent_return_count": 0,
    }
    assert normalized["normalization_error"] is None
    assert "SENSITIVE" not in repr(normalized)
    assert trace == original


def test_chat_isolates_missing_invalid_and_conflicting_duration_observations() -> None:
    trace = _chat_trace(
        calls=[
            {"toolId": "call-1", "toolName": "search"},
            {"toolId": "call-2", "toolName": "quote"},
            {"toolId": "call-3", "toolName": "news"},
        ],
        results=[
            {"toolId": "call-1", "toolName": "search"},
            {"toolId": "call-2", "toolName": "quote", "durationSeconds": "bad"},
            {"toolId": "call-3", "toolName": "news", "durationSeconds": 1},
            {"toolId": "call-3", "toolName": "news", "durationSeconds": 2},
            {"toolId": "orphan", "toolName": "other", "durationSeconds": 99},
        ],
    )

    normalized = normalize_chat_tool_resource_usage(trace)

    assert [call["duration_error"] for call in normalized["tool_calls"]] == [
        "missing_tool_duration",
        "invalid_duration",
        "conflicting_duration_observation",
    ]
    assert normalized["diagnostics"]["duration_missing_count"] == 1
    assert normalized["diagnostics"]["duplicate_observation_count"] == 1
    assert normalized["diagnostics"]["orphan_result_count"] == 1


def test_research_uses_earliest_start_and_latest_terminal_end_per_call() -> None:
    base = 1_700_000_000_000
    events = [
        _research_event("call-1", "search", "running", start=base),
        _research_event("call-1", "search", "running", start=base + 100),
        _research_event("call-1", "search", "completed", start=base, end=base + 1_500),
        _research_event("call-2", "quote", "running", start=base + 3_000),
    ]

    normalized = normalize_research_tool_resource_usage(events)

    assert normalized["tool_calls"] == (
        {
            "trace_id": None,
            "mode": "research",
            "call_id": "call-1",
            "tool_name": "search",
            "duration_ms": 1_500,
            "duration_error": None,
            "token_total": None,
            "token_error": "no_observable_tool_token_usage",
        },
        {
            "trace_id": None,
            "mode": "research",
            "call_id": "call-2",
            "tool_name": "quote",
            "duration_ms": None,
            "duration_error": "missing_tool_duration",
            "token_total": None,
            "token_error": "no_observable_tool_token_usage",
        },
    )
    assert normalized["diagnostics"]["duration_missing_count"] == 1
    assert normalized["diagnostics"]["duplicate_observation_count"] == 1


def test_research_isolates_negative_duration_and_rejects_missing_call_id() -> None:
    base = 1_700_000_000_000
    negative = normalize_research_tool_resource_usage(
        [_research_event("call-1", "search", "completed", start=base + 1_000, end=base)]
    )
    missing_id = normalize_research_tool_resource_usage(
        [_research_event(None, "search", "running", start=1_000)]
    )

    assert negative["tool_calls"][0]["duration_error"] == "invalid_event_order"
    assert missing_id["tool_calls"] == ()
    assert missing_id["normalization_error"] == "missing_call_id"


def test_both_modes_report_no_tool_calls_without_inventing_zero_values() -> None:
    chat = normalize_chat_tool_resource_usage(_chat_trace(calls=[], results=[]))
    research = normalize_research_tool_resource_usage([])

    assert chat["normalization_error"] == "no_tool_calls"
    assert research["normalization_error"] == "no_tool_calls"


def test_chat_excludes_respond_agent_return_from_business_calls() -> None:
    # 回归(2026-09-21): respond 是 Agent 返回(权威口径 1.1 agent_return),
    # 不得作为业务工具进入逐调用资源视图——曾泄漏进 per_tool_call_latency_ms
    # 聊天列 13 处,与 per_tool_call_tokens/max_tool_latency_share 口径不一致。
    # 大小写变体Respond 同样排除;被排除调用的结果不算孤立结果。
    trace = _chat_trace(
        calls=[
            {"toolId": "call-1", "toolName": "search"},
            {"toolId": "call-2", "toolName": "Respond"},
        ],
        results=[
            {"toolId": "call-1", "toolName": "search", "durationSeconds": 1},
            {"toolId": "call-2", "durationSeconds": 2},
        ],
    )
    original = deepcopy(trace)

    normalized = normalize_chat_tool_resource_usage(trace)

    assert [call["tool_name"] for call in normalized["tool_calls"]] == ["search"]
    assert normalized["diagnostics"]["excluded_agent_return_count"] == 1
    assert normalized["diagnostics"]["orphan_result_count"] == 0
    assert normalized["normalization_error"] is None
    assert trace == original


def test_chat_with_only_respond_returns_no_tool_calls() -> None:
    # 只有 Agent 返回、没有业务工具时不得记 0 值调用,返回 no_tool_calls。
    trace = _chat_trace(
        calls=[{"toolId": "call-1", "toolName": "respond"}],
        results=[{"toolId": "call-1", "durationSeconds": 1}],
    )

    normalized = normalize_chat_tool_resource_usage(trace)

    assert normalized["tool_calls"] == ()
    assert normalized["normalization_error"] == "no_tool_calls"
    assert normalized["diagnostics"]["excluded_agent_return_count"] == 1


def test_chat_counts_excluded_agent_delegation_diagnostic() -> None:
    # agentName 委派此前静默排除无计数;补齐诊断计数,消除静默丢弃。
    trace = _chat_trace(
        calls=[
            {"taskId": "worker-1", "agentName": "worker_search"},
            {"taskId": "worker-2", "agentName": "worker_calc"},
            {"toolId": "call-1", "toolName": "search"},
        ],
        results=[{"toolId": "call-1", "durationSeconds": 1}],
    )

    normalized = normalize_chat_tool_resource_usage(trace)

    assert [call["tool_name"] for call in normalized["tool_calls"]] == ["search"]
    assert normalized["diagnostics"]["excluded_agent_delegation_count"] == 2
    assert normalized["normalization_error"] is None


def test_research_exclusion_diagnostics_are_zero() -> None:
    # 研究模式无 respond/agentName 委派结构,两个排除计数恒为 0。
    normalized = normalize_research_tool_resource_usage(
        [_research_event("call-1", "task", "completed", start=1_000, end=2_000)]
    )

    assert normalized["diagnostics"]["excluded_agent_delegation_count"] == 0
    assert normalized["diagnostics"]["excluded_agent_return_count"] == 0
    # task(研究侧子 Agent 委派)按 2026-09-21 需求方确认保留计入业务工具。
    assert [call["tool_name"] for call in normalized["tool_calls"]] == ["task"]


def _chat_trace(
    *, calls: list[dict[str, object]], results: list[dict[str, object]]
) -> dict[str, object]:
    return {
        "canvas": {
            "meta": {"traceId": "trace-1"},
            "agents": [
                {"messages": [{"content": {"tool_call": calls}}]},
                {"messages": [{"content": {"tool_result": results}}]},
            ],
        }
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
