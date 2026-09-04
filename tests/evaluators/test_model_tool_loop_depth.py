from copy import deepcopy

import pytest

from evaluators.base import EvaluationStatus
from evaluators.model_tool_loop_depth import ModelToolLoopDepthEvaluator


def _trace(
    *,
    rounds: object = 3,
    tool_rounds: object = 2,
    completed_loops: object = 2,
    tool_calls: object = 3,
    mode: str = "research",
    normalization_error: str | None = None,
) -> dict[str, object]:
    return {
        "trace_id": "session-1:message-1",
        "mode": mode,
        "model_round_count": rounds,
        "tool_calling_round_count": tool_rounds,
        "completed_loop_count": completed_loops,
        "unique_tool_call_count": tool_calls,
        "diagnostics": {
            "malformed_event_count": 0,
            "duplicate_loop_observation_count": 1,
            "duplicate_tool_observation_count": 1,
        },
        "normalization_error": normalization_error,
    }


def test_evaluator_returns_completed_loop_depth() -> None:
    trace = _trace()
    original = deepcopy(trace)

    result = ModelToolLoopDepthEvaluator().evaluate(trace)

    assert result.metric_name == "model_tool_loop_depth"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 2
    assert result.metadata == {
        "trace_id": "session-1:message-1",
        "mode": "research",
        "model_round_count": 3,
        "tool_calling_round_count": 2,
        "completed_loop_count": 2,
        "unique_tool_call_count": 3,
        "malformed_event_count": 0,
        "duplicate_loop_observation_count": 1,
        "duplicate_tool_observation_count": 1,
    }
    assert trace == original


def test_normal_completion_without_tools_returns_zero() -> None:
    result = ModelToolLoopDepthEvaluator().evaluate(
        _trace(rounds=1, tool_rounds=0, completed_loops=0, tool_calls=0)
    )

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0


def test_chat_mode_returns_explicit_not_computable_reason() -> None:
    result = ModelToolLoopDepthEvaluator().evaluate(
        _trace(
            rounds=0,
            tool_rounds=0,
            completed_loops=0,
            tool_calls=0,
            mode="chat",
            normalization_error="missing_model_iteration_boundary",
        )
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "missing_model_iteration_boundary"


def test_research_normalization_failure_is_not_computable() -> None:
    result = ModelToolLoopDepthEvaluator().evaluate(
        _trace(normalization_error="missing_tool_terminal_state")
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "missing_tool_terminal_state"


@pytest.mark.parametrize(
    "trace",
    [
        {},
        _trace(rounds=True),
        _trace(rounds=1, tool_rounds=2),
        _trace(rounds=2, tool_rounds=1, completed_loops=2),
        _trace(rounds=2, tool_rounds=1, completed_loops=0),
        _trace(rounds=2, tool_rounds=1, completed_loops=1, tool_calls=0),
        _trace(mode="unsupported"),
    ],
)
def test_invalid_normalized_trace_is_safely_isolated(trace: dict[str, object]) -> None:
    result = ModelToolLoopDepthEvaluator().evaluate(trace)

    if trace.get("mode") == "unsupported":
        assert result.status is EvaluationStatus.NOT_COMPUTABLE
        assert result.metadata["reason"] == "unsupported_trace_mode"
    else:
        assert result.status is EvaluationStatus.ERROR
        assert result.metadata["reason"] == "invalid_normalized_trace"
