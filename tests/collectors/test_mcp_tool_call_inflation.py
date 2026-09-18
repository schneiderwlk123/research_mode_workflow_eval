from copy import deepcopy

import pytest

from collectors.mcp_tool_call_inflation import (
    normalize_chat_mcp_tool_call_inflation,
    normalize_research_mcp_tool_call_inflation,
)
from collectors.routing import CapabilityCatalog, build_capability_catalog


def _catalog() -> CapabilityCatalog:
    return build_capability_catalog(
        {
            "manual_version": "0.1.0",
            "tools": [
                {"name": "quote", "description": "行情", "parameters": []},
                {"name": "news", "description": "新闻", "parameters": []},
                {
                    "name": "stock_analyze_result",
                    "description": "异步任务轮询",
                    "parameters": [],
                },
            ],
        }
    )


def _chat_trace(
    *agents: dict[str, object], trace_id: str | None = "trace-001"
) -> dict[str, object]:
    return {
        "canvas": {
            "meta": {"traceId": trace_id},
            "agents": list(agents),
        },
    }


def _chat_message(*calls: dict[str, object]) -> dict[str, object]:
    return {
        "messages": [
            {
                "role": "assistant",
                "content": {"tool_call": list(calls)},
            }
        ]
    }


def _chat_call(call_id: str, tool_name: str, arguments: object) -> dict[str, object]:
    return {"toolId": call_id, "toolName": tool_name, "arguments": arguments}


def test_chat_counts_same_mcp_and_canonical_arguments_after_first_call_as_inflated() -> None:
    trace = _chat_trace(
        _chat_message(
            _chat_call("call-1", "quote", {"symbol": "AAA", "window": 1}),
            _chat_call("call-2", "mcp__finance__quote", {"window": 1, "symbol": "AAA"}),
        ),
        _chat_message(
            _chat_call("call-3", "quote", {"symbol": "BBB", "window": 1}),
            _chat_call("call-4", "respond", {"text": "sensitive"}),
            {"taskId": "worker-1", "agentName": "worker_search", "arguments": {}},
        ),
    )
    original = deepcopy(trace)

    normalized = normalize_chat_mcp_tool_call_inflation(trace, _catalog())

    assert normalized == {
        "trace_id": "trace-001",
        "mode": "chat",
        "manual_version": "0.1.0",
        "mcp_call_count": 3,
        "unique_request_signature_count": 2,
        "inflated_call_count": 1,
        "diagnostics": {
            "malformed_event_count": 0,
            "out_of_scope_tool_call_count": 1,
            "duplicate_call_observation_count": 0,
            "polling_call_count": 0,
            "empty_argument_call_count": 0,
        },
        "normalization_error": None,
    }
    assert "AAA" not in repr(normalized)
    assert trace == original


def test_chat_deduplicates_same_call_id_before_counting_inflation() -> None:
    call = _chat_call("call-1", "quote", '{"symbol":"AAA"}')

    normalized = normalize_chat_mcp_tool_call_inflation(
        _chat_trace(_chat_message(call), _chat_message(deepcopy(call))), _catalog()
    )

    assert normalized["mcp_call_count"] == 1
    assert normalized["unique_request_signature_count"] == 1
    assert normalized["inflated_call_count"] == 0
    assert normalized["diagnostics"]["duplicate_call_observation_count"] == 1


@pytest.mark.parametrize(
    ("request_payload", "reason"),
    [
        ({"toolName": "quote", "arguments": {}}, "missing_call_id"),
        ({"toolId": "call-1", "arguments": {}}, "missing_tool_name"),
        ({"toolId": "call-1", "toolName": "quote"}, "missing_call_arguments"),
    ],
)
def test_chat_requires_stable_id_and_arguments(
    request_payload: dict[str, object], reason: str
) -> None:
    normalized = normalize_chat_mcp_tool_call_inflation(
        _chat_trace(_chat_message(request_payload)),
        _catalog(),
    )

    assert normalized["normalization_error"] == reason


def test_chat_rejects_conflicting_definition_for_same_call_id() -> None:
    normalized = normalize_chat_mcp_tool_call_inflation(
        _chat_trace(
            _chat_message(
                _chat_call("call-1", "quote", {"symbol": "AAA"}),
                _chat_call("call-1", "quote", {"symbol": "BBB"}),
            )
        ),
        _catalog(),
    )

    assert normalized["normalization_error"] == "conflicting_call_definition"


def test_chat_rejects_same_call_id_reused_across_mcp_scope_boundary() -> None:
    normalized = normalize_chat_mcp_tool_call_inflation(
        _chat_trace(
            _chat_message(
                _chat_call("call-1", "respond", {"text": "sensitive"}),
                _chat_call("call-1", "quote", {"symbol": "AAA"}),
            )
        ),
        _catalog(),
    )

    assert normalized["normalization_error"] == "conflicting_call_definition"


def test_chat_rejects_unserializable_arguments() -> None:
    normalized = normalize_chat_mcp_tool_call_inflation(
        _chat_trace(_chat_message(_chat_call("call-1", "quote", {"symbols": {"AAA"}}))),
        _catalog(),
    )

    assert normalized["normalization_error"] == "unserializable_call_arguments"


def test_chat_polling_calls_are_excluded_from_numerator_and_denominator() -> None:
    normalized = normalize_chat_mcp_tool_call_inflation(
        _chat_trace(
            _chat_message(
                _chat_call("call-1", "stock_analyze_result", {"taskId": "task-1"}),
                _chat_call("call-2", "stock_analyze_result", {"taskId": "task-1"}),
                _chat_call("call-3", "quote", {"symbol": "AAA"}),
                _chat_call("call-4", "quote", {"symbol": "AAA"}),
            )
        ),
        _catalog(),
    )

    assert normalized["mcp_call_count"] == 2
    assert normalized["unique_request_signature_count"] == 1
    assert normalized["inflated_call_count"] == 1
    assert normalized["diagnostics"]["polling_call_count"] == 2
    assert normalized["normalization_error"] is None


def test_chat_only_polling_calls_leave_zero_denominator() -> None:
    normalized = normalize_chat_mcp_tool_call_inflation(
        _chat_trace(
            _chat_message(
                _chat_call("call-1", "stock_analyze_result", {"taskId": "task-1"}),
                _chat_call("call-2", "stock_analyze_result", {"taskId": "task-1"}),
            )
        ),
        _catalog(),
    )

    assert normalized["mcp_call_count"] == 0
    assert normalized["inflated_call_count"] == 0
    assert normalized["diagnostics"]["polling_call_count"] == 2
    assert normalized["normalization_error"] is None


def test_chat_empty_argument_calls_are_counted() -> None:
    normalized = normalize_chat_mcp_tool_call_inflation(
        _chat_trace(
            _chat_message(
                _chat_call("call-1", "quote", {}),
                _chat_call("call-2", "quote", {}),
            )
        ),
        _catalog(),
    )

    assert normalized["inflated_call_count"] == 1
    assert normalized["diagnostics"]["empty_argument_call_count"] == 2


def test_chat_polling_rule_is_conservative_on_extra_arguments() -> None:
    normalized = normalize_chat_mcp_tool_call_inflation(
        _chat_trace(
            _chat_message(
                _chat_call("call-1", "quote", {"taskId": "task-1", "symbol": "AAA"}),
                _chat_call("call-2", "quote", {"taskId": "task-1", "symbol": "AAA"}),
            )
        ),
        _catalog(),
    )

    assert normalized["mcp_call_count"] == 2
    assert normalized["inflated_call_count"] == 1
    assert normalized["diagnostics"]["polling_call_count"] == 0


@pytest.mark.parametrize(
    ("trace", "reason"),
    [
        ({"traceId": "legacy", "tool_call_queue": []}, "invalid_chat_trace_structure"),
        (
            {"canvas": {"meta": {"traceId": "trace-001"}, "agents": "invalid"}},
            "invalid_chat_trace_structure",
        ),
        (_chat_trace({"messages": "invalid"}), "invalid_chat_trace_structure"),
        (
            _chat_trace({"messages": [{"role": "assistant", "content": "invalid"}]}),
            "invalid_chat_trace_structure",
        ),
        (_chat_trace(_chat_message(), trace_id=None), "missing_trace_id"),
    ],
)
def test_invalid_or_legacy_chat_structure_is_isolated(
    trace: dict[str, object], reason: str
) -> None:
    normalized = normalize_chat_mcp_tool_call_inflation(trace, _catalog())

    assert normalized["normalization_error"] == reason
    assert normalized["diagnostics"]["malformed_event_count"] >= 1


def test_research_deduplicates_definitions_and_ignores_lifecycle_repetition() -> None:
    request_event = _research_requests(
        ("call-1", "quote", {"symbol": "AAA", "window": 1}),
        ("call-2", "quote", {"window": 1, "symbol": "AAA"}),
        ("call-3", "quote", {"symbol": "BBB", "window": 1}),
        ("call-4", "excel", {"range": "A1"}),
    )
    events = [
        request_event,
        deepcopy(request_event),
        _research_state("call-1", "quote", "pending"),
        _research_state("call-1", "quote", "running"),
        _research_state("call-1", "quote", "completed"),
    ]
    original = deepcopy(events)

    normalized = normalize_research_mcp_tool_call_inflation(
        events,
        _catalog(),
        session_id="session-1",
        user_message_id="message-1",
    )

    assert normalized["trace_id"] == "session-1:message-1"
    assert normalized["mcp_call_count"] == 3
    assert normalized["unique_request_signature_count"] == 2
    assert normalized["inflated_call_count"] == 1
    assert normalized["diagnostics"]["out_of_scope_tool_call_count"] == 1
    assert normalized["diagnostics"]["duplicate_call_observation_count"] == 3
    assert normalized["normalization_error"] is None
    assert "AAA" not in repr(normalized)
    assert events == original


def test_research_mcp_lifecycle_without_arguments_is_not_computable() -> None:
    normalized = normalize_research_mcp_tool_call_inflation(
        [_research_state("call-1", "quote", "completed")], _catalog()
    )

    assert normalized["normalization_error"] == "missing_call_arguments"


def test_research_polling_calls_are_excluded_and_exempt_from_argument_requirement() -> None:
    events = [
        _research_requests(("call-1", "stock_analyze_result", {"taskId": "task-1"})),
        _research_state("call-1", "stock_analyze_result", "pending"),
        _research_state("call-1", "stock_analyze_result", "completed"),
        # 仅有生命周期入参定义的轮询调用同样按入参形态识别并排除,不触发 missing_call_arguments。
        {
            "type": "message.part.updated",
            "part": {
                "type": "tool",
                "callID": "call-2",
                "tool": "stock_analyze_result",
                "state": {"status": "completed", "input": {"taskId": "task-1"}},
            },
        },
    ]

    normalized = normalize_research_mcp_tool_call_inflation(events, _catalog())

    assert normalized["mcp_call_count"] == 0
    assert normalized["unique_request_signature_count"] == 0
    assert normalized["inflated_call_count"] == 0
    assert normalized["diagnostics"]["polling_call_count"] == 2
    assert normalized["normalization_error"] is None


def test_research_empty_argument_calls_are_counted() -> None:
    normalized = normalize_research_mcp_tool_call_inflation(
        [_research_requests(("call-1", "quote", {}), ("call-2", "quote", {}))], _catalog()
    )

    assert normalized["inflated_call_count"] == 1
    assert normalized["diagnostics"]["empty_argument_call_count"] == 2


def test_research_request_definition_replaces_pending_placeholder_input() -> None:
    events = [
        {
            "type": "message.part.updated",
            "part": {
                "type": "tool",
                "callID": "call-1",
                "tool": "quote",
                "state": {"status": "pending", "input": {}},
            },
        },
        _research_requests(("call-1", "quote", {"symbol": "AAA"})),
    ]

    normalized = normalize_research_mcp_tool_call_inflation(events, _catalog())

    assert normalized["mcp_call_count"] == 1
    assert normalized["unique_request_signature_count"] == 1
    assert normalized["normalization_error"] is None


def test_research_rejects_conflicting_definition_for_same_call_id() -> None:
    normalized = normalize_research_mcp_tool_call_inflation(
        [
            _research_requests(("call-1", "quote", {"symbol": "AAA"})),
            _research_requests(("call-1", "quote", {"symbol": "BBB"})),
        ],
        _catalog(),
    )

    assert normalized["normalization_error"] == "conflicting_call_definition"


def test_research_rejects_same_call_id_reused_across_mcp_scope_boundary() -> None:
    normalized = normalize_research_mcp_tool_call_inflation(
        [
            _research_requests(("call-1", "excel", {"range": "A1"})),
            _research_requests(("call-1", "quote", {"symbol": "AAA"})),
        ],
        _catalog(),
    )

    assert normalized["normalization_error"] == "conflicting_call_definition"


def _research_requests(*calls: tuple[str, str, object]) -> dict[str, object]:
    return {
        "type": "llm.response",
        "result": {
            "toolCalls": [
                {"toolCallId": call_id, "toolName": tool_name, "input": arguments}
                for call_id, tool_name, arguments in calls
            ]
        },
    }


def _research_state(call_id: str, tool_name: str, status: str) -> dict[str, object]:
    return {
        "type": "message.part.updated",
        "part": {
            "type": "tool",
            "callID": call_id,
            "tool": tool_name,
            "state": {"status": status},
        },
    }
