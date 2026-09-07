from collectors.exact_duplicate_calls import (
    normalize_chat_exact_duplicates,
    normalize_research_exact_duplicates,
)


def test_canvas_chat_counts_only_repeated_request_and_response_content() -> None:
    trace = {
        "canvas": {
            "meta": {"traceId": "trace-001"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [
                                    _canvas_call(
                                        "call-1",
                                        "quote",
                                        {"symbol": "AAA", "window": 1},
                                    ),
                                    _canvas_call(
                                        "call-2",
                                        "quote",
                                        {"window": 1, "symbol": "AAA"},
                                    ),
                                    _canvas_call(
                                        "call-3",
                                        "quote",
                                        {"symbol": "AAA", "window": 1},
                                    ),
                                    _canvas_call(
                                        "call-4",
                                        "quote",
                                        {"symbol": "BBB", "window": 1},
                                    ),
                                    {
                                        "taskId": "worker-1",
                                        "agentName": "worker_search",
                                        "arguments": {"query": "不得保留"},
                                    },
                                ]
                            }
                        },
                        {
                            "content": {
                                "tool_result": [
                                    _canvas_result("call-1", result={"data": [1, 2]}),
                                    _canvas_result("call-2", raw_result='{"data": [1, 2]}'),
                                    _canvas_result("call-3", result={"data": [1, 3]}),
                                    _canvas_result("call-4", result={"data": [1, 2]}),
                                ]
                            }
                        },
                    ]
                }
            ],
        }
    }

    normalized = normalize_chat_exact_duplicates(trace)

    assert normalized == {
        "trace_id": "trace-001",
        "mode": "chat",
        "unique_call_count": 4,
        "comparable_call_count": 4,
        "exact_duplicate_call_count": 1,
        "diagnostics": {
            "malformed_event_count": 0,
            "missing_response_payload_count": 0,
            "duplicate_state_event_count": 0,
            "duplicate_request_observation_count": 0,
            "duplicate_result_observation_count": 0,
            "orphan_result_count": 0,
        },
        "normalization_error": None,
    }
    assert "AAA" not in repr(normalized)


def test_canvas_chat_missing_response_and_orphan_result_are_diagnosed() -> None:
    trace = {
        "canvas": {
            "meta": {"traceId": "trace-002"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [
                                    _canvas_call("call-1", "quote", {"symbol": "AAA"}),
                                    _canvas_call("call-2", "quote", {"symbol": "AAA"}),
                                ],
                                "tool_result": [
                                    {"toolId": "call-1", "result": {}},
                                    {"toolId": "worker-result", "toolName": "direct_answer"},
                                ],
                            }
                        }
                    ]
                }
            ],
        }
    }

    normalized = normalize_chat_exact_duplicates(trace)

    assert normalized["unique_call_count"] == 2
    assert normalized["comparable_call_count"] == 0
    assert normalized["exact_duplicate_call_count"] == 0
    assert normalized["diagnostics"]["missing_response_payload_count"] == 2
    assert normalized["diagnostics"]["orphan_result_count"] == 1


def test_canvas_chat_duplicate_observations_are_merged() -> None:
    request = _canvas_call("call-1", "quote", {"symbol": "AAA"})
    trace = {
        "canvas": {
            "meta": {"traceId": "trace-003"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [request, dict(request)],
                                "tool_result": [
                                    _canvas_result("call-1", result={"value": 1}),
                                    _canvas_result("call-1", result={"value": 1}),
                                ],
                            }
                        }
                    ]
                }
            ],
        }
    }

    normalized = normalize_chat_exact_duplicates(trace)

    assert normalized["unique_call_count"] == 1
    assert normalized["comparable_call_count"] == 1
    assert normalized["diagnostics"]["duplicate_request_observation_count"] == 1
    assert normalized["diagnostics"]["duplicate_result_observation_count"] == 1


def test_canvas_chat_conflicting_request_or_result_is_not_computable() -> None:
    conflicting_request = {
        "canvas": {
            "meta": {"traceId": "trace-004"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [
                                    _canvas_call("call-1", "quote", {"symbol": "AAA"}),
                                    _canvas_call("call-1", "quote", {"symbol": "BBB"}),
                                ]
                            }
                        }
                    ]
                }
            ],
        }
    }
    conflicting_result = {
        "canvas": {
            "meta": {"traceId": "trace-005"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [_canvas_call("call-1", "quote", {})],
                                "tool_result": [
                                    _canvas_result("call-1", result={"value": 1}),
                                    _canvas_result("call-1", result={"value": 2}),
                                ],
                            }
                        }
                    ]
                }
            ],
        }
    }

    assert normalize_chat_exact_duplicates(conflicting_request)["normalization_error"] == (
        "conflicting_call_definition"
    )
    assert normalize_chat_exact_duplicates(conflicting_result)["normalization_error"] == (
        "conflicting_call_result"
    )


def test_canvas_chat_requires_identifiers_and_arguments() -> None:
    cases: tuple[tuple[dict[str, object], str], ...] = (
        ({"canvas": {"meta": {}, "agents": []}}, "missing_trace_id"),
        (_canvas_trace({"toolName": "quote", "arguments": {}}), "missing_call_id"),
        (_canvas_trace({"toolId": "call-1", "arguments": {}}), "missing_tool_name"),
        (_canvas_trace({"toolId": "call-1", "toolName": "quote"}), "missing_call_arguments"),
    )

    for trace, expected_error in cases:
        assert normalize_chat_exact_duplicates(trace)["normalization_error"] == expected_error


def test_legacy_chat_exact_duplicate_queue_is_not_supported() -> None:
    normalized = normalize_chat_exact_duplicates({"tool_call_queue": []})

    assert normalized["unique_call_count"] == 0
    assert normalized["normalization_error"] == "invalid_chat_trace_structure"


def test_research_deduplicates_lifecycle_events_before_comparing_calls() -> None:
    events = [
        _research_requests(
            ("call-1", "excel", {"range": "A1"}),
            ("call-2", "excel", {"range": "A1"}),
            ("call-3", "excel", {"range": "A1"}),
        ),
        _research_state("call-1", "pending"),
        _research_state("call-1", "completed", output={"value": 1}),
        _research_state("call-1", "completed", output={"value": 1}),
        _research_state("call-2", "running"),
        _research_state("call-2", "completed", output='{"value": 1}'),
        _research_state("call-3", "completed", output={"value": 2}),
    ]

    normalized = normalize_research_exact_duplicates(events)

    assert normalized == {
        "trace_id": None,
        "mode": "research",
        "unique_call_count": 3,
        "comparable_call_count": 3,
        "exact_duplicate_call_count": 1,
        "diagnostics": {
            "malformed_event_count": 0,
            "missing_response_payload_count": 0,
            "duplicate_state_event_count": 1,
            "duplicate_request_observation_count": 0,
            "duplicate_result_observation_count": 0,
            "orphan_result_count": 0,
        },
        "normalization_error": None,
    }
    assert "A1" not in repr(normalized)


def test_research_tool_event_without_request_mapping_is_not_computable() -> None:
    events = [_research_state("call-1", "completed", output={"value": 1})]

    normalized = normalize_research_exact_duplicates(events)

    assert normalized["normalization_error"] == "missing_duplicate_key"


def test_research_tool_event_without_call_id_is_not_computable() -> None:
    events = [
        {
            "type": "message.part.updated",
            "part": {"type": "tool", "state": {"status": "completed", "output": {}}},
        }
    ]

    normalized = normalize_research_exact_duplicates(events)

    assert normalized["normalization_error"] == "missing_call_id"


def _canvas_call(call_id: str, tool_name: str, arguments: object) -> dict[str, object]:
    return {"toolId": call_id, "toolName": tool_name, "arguments": arguments}


def _canvas_result(
    call_id: str,
    *,
    result: object | None = None,
    raw_result: object | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {"toolId": call_id}
    if result is not None:
        value["result"] = result
    if raw_result is not None:
        value["rawResult"] = raw_result
    return value


def _canvas_trace(call: dict[str, object]) -> dict[str, object]:
    return {
        "canvas": {
            "meta": {"traceId": "trace-required-fields"},
            "agents": [{"messages": [{"content": {"tool_call": [call]}}]}],
        }
    }


def _research_requests(*calls: tuple[str, str, object]) -> dict[str, object]:
    return {
        "type": "model.completed",
        "result": {
            "toolCalls": [
                {"toolCallId": call_id, "toolName": tool_name, "input": arguments}
                for call_id, tool_name, arguments in calls
            ]
        },
    }


def _research_state(
    call_id: str,
    status: str,
    *,
    output: object | None = None,
) -> dict[str, object]:
    state: dict[str, object] = {"status": status}
    if output is not None:
        state["output"] = output
    return {
        "type": "message.part.updated",
        "part": {"type": "tool", "callID": call_id, "state": state},
    }
