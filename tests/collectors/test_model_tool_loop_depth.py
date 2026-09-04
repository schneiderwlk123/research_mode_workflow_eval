from copy import deepcopy

import pytest

from collectors.model_tool_loop_depth import (
    normalize_chat_model_tool_loop_depth,
    normalize_research_model_tool_loop_depth,
)


def _turn_start() -> dict[str, object]:
    return {
        "type": "turn.start",
        "sessionID": "session-1",
        "userMessageID": "message-1",
    }


def _turn_end() -> dict[str, object]:
    return {
        "type": "turn.end",
        "sessionID": "session-1",
        "userMessageID": "message-1",
    }


def _llm_response(loop: int, finish_reason: str, *call_ids: str) -> dict[str, object]:
    return {
        "type": "llm.response",
        "loop": loop,
        "sessionID": "session-1",
        "userMessageID": "message-1",
        "result": {
            "finishReason": finish_reason,
            "toolCalls": [{"toolCallId": call_id} for call_id in call_ids],
        },
    }


def _tool_state(call_id: str, status: str) -> dict[str, object]:
    return {
        "type": "message.part.updated",
        "sessionID": "session-1",
        "part": {
            "type": "tool",
            "callID": call_id,
            "state": {"status": status},
        },
    }


def test_chat_mode_is_not_computable_without_model_iteration_boundaries() -> None:
    trace = {
        "traceId": "trace-1",
        "tool_call_queue": [{"event_type": "tool_call", "tool_request_args": {"toolId": "call-1"}}],
    }
    original = deepcopy(trace)

    normalized = normalize_chat_model_tool_loop_depth(trace)

    assert normalized == {
        "trace_id": "trace-1",
        "mode": "chat",
        "model_round_count": 0,
        "tool_calling_round_count": 0,
        "completed_loop_count": 0,
        "unique_tool_call_count": 0,
        "diagnostics": {
            "malformed_event_count": 0,
            "duplicate_loop_observation_count": 0,
            "duplicate_tool_observation_count": 0,
        },
        "normalization_error": "missing_model_iteration_boundary",
    }
    assert trace == original


def test_research_counts_completed_tool_rounds_not_parallel_calls() -> None:
    events = [
        _turn_start(),
        _llm_response(1, "tool-calls", "call-1", "call-2"),
        _llm_response(1, "tool-calls", "call-1", "call-2"),
        _tool_state("call-1", "running"),
        _tool_state("call-1", "completed"),
        _tool_state("call-2", "completed"),
        _llm_response(2, "tool-calls", "call-3"),
        _tool_state("call-3", "completed"),
        _llm_response(3, "stop"),
        _turn_end(),
    ]
    original = deepcopy(events)

    normalized = normalize_research_model_tool_loop_depth(events)

    assert normalized == {
        "trace_id": "session-1:message-1",
        "mode": "research",
        "model_round_count": 3,
        "tool_calling_round_count": 2,
        "completed_loop_count": 2,
        "unique_tool_call_count": 3,
        "diagnostics": {
            "malformed_event_count": 0,
            "duplicate_loop_observation_count": 1,
            "duplicate_tool_observation_count": 1,
        },
        "normalization_error": None,
    }
    assert events == original


def test_research_without_tool_calls_has_zero_depth() -> None:
    normalized = normalize_research_model_tool_loop_depth(
        [_turn_start(), _llm_response(1, "stop"), _turn_end()]
    )

    assert normalized["completed_loop_count"] == 0
    assert normalized["normalization_error"] is None


@pytest.mark.parametrize(
    ("events", "reason"),
    [
        (
            [_turn_start(), {"type": "llm.response", "result": {}}, _turn_end()],
            "missing_model_iteration_boundary",
        ),
        (
            [
                _turn_start(),
                _llm_response(1, "tool-calls", "call-1"),
                _llm_response(3, "stop"),
                _tool_state("call-1", "completed"),
                _turn_end(),
            ],
            "non_contiguous_loop_index",
        ),
        (
            [
                _turn_start(),
                _llm_response(1, "tool-calls", "call-1"),
                _llm_response(1, "tool-calls", "call-2"),
                _llm_response(2, "stop"),
                _tool_state("call-1", "completed"),
                _tool_state("call-2", "completed"),
                _turn_end(),
            ],
            "conflicting_model_loop_definition",
        ),
        (
            [
                _turn_start(),
                _llm_response(1, "tool-calls", "call-1"),
                _llm_response(2, "stop"),
                _turn_end(),
            ],
            "missing_tool_terminal_state",
        ),
        (
            [
                _turn_start(),
                _llm_response(1, "tool-calls", "call-1"),
                _tool_state("call-1", "completed"),
                _turn_end(),
            ],
            "incomplete_model_tool_cycle",
        ),
        (
            [_turn_start(), _llm_response(1, "stop")],
            "missing_turn_terminal",
        ),
    ],
)
def test_research_incomplete_or_conflicting_structures_are_not_estimated(
    events: list[object], reason: str
) -> None:
    normalized = normalize_research_model_tool_loop_depth(events)

    assert normalized["normalization_error"] == reason


def test_research_tool_call_without_stable_id_is_rejected() -> None:
    events = [
        _turn_start(),
        {
            "type": "llm.response",
            "loop": 1,
            "result": {"finishReason": "tool-calls", "toolCalls": [{}]},
        },
        _llm_response(2, "stop"),
        _turn_end(),
    ]

    normalized = normalize_research_model_tool_loop_depth(events)

    assert normalized["normalization_error"] == "missing_tool_call_id"


def test_normalized_result_does_not_retain_model_or_tool_content() -> None:
    model_event = _llm_response(1, "tool-calls", "call-1")
    model_event["result"]["text"] = "sensitive-model-output"  # type: ignore[index]
    model_event["result"]["toolCalls"][0]["input"] = {  # type: ignore[index]
        "secret": "sensitive-tool-input"
    }
    tool_event = _tool_state("call-1", "completed")
    tool_event["part"]["state"]["output"] = "sensitive-tool-output"  # type: ignore[index]

    normalized = normalize_research_model_tool_loop_depth(
        [_turn_start(), model_event, tool_event, _llm_response(2, "stop"), _turn_end()]
    )

    serialized = repr(normalized)
    assert "sensitive-model-output" not in serialized
    assert "sensitive-tool-input" not in serialized
    assert "sensitive-tool-output" not in serialized
