from copy import deepcopy

import pytest

from evaluators.base import EvaluationStatus
from evaluators.tool_calls_count import ToolCallsCountEvaluator


def _trace(
    *,
    mode: str = "chat",
    statistics: object = (
        {"tool_name": "quote", "call_count": 1, "repeat_call_count": 0},
        {"tool_name": "search", "call_count": 2, "repeat_call_count": 1},
    ),
    error: object = None,
) -> dict[str, object]:
    return {
        "trace_id": "trace-1",
        "mode": mode,
        "per_tool_statistics": statistics,
        "diagnostics": {
            "duplicate_call_observation_count": 0,
            "excluded_agent_delegation_count": 0,
            "excluded_agent_return_count": 0,
            "malformed_event_count": 0,
        },
        "normalization_error": error,
    }


def test_computes_total_unique_calls_and_preserves_grouped_statistics() -> None:
    trace = _trace()
    original = deepcopy(trace)

    result = ToolCallsCountEvaluator().evaluate(trace)

    assert result.metric_name == "tool_calls_count"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 3
    assert result.metadata["unique_tool_name_count"] == 2
    assert result.metadata["total_repeat_call_count"] == 1
    assert result.metadata["per_tool_statistics"] == (
        {"tool_name": "quote", "call_count": 1, "repeat_call_count": 0},
        {"tool_name": "search", "call_count": 2, "repeat_call_count": 1},
    )
    assert trace == original


def test_no_business_tools_is_computed_as_zero() -> None:
    result = ToolCallsCountEvaluator().evaluate(_trace(statistics=()))

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0
    assert result.metadata["unique_tool_name_count"] == 0
    assert result.metadata["total_repeat_call_count"] == 0


@pytest.mark.parametrize(
    "reason",
    [
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "missing_trace_id",
        "missing_call_id",
        "missing_tool_name",
        "conflicting_call_definition",
    ],
)
def test_normalization_failures_are_errors(reason: str) -> None:
    result = ToolCallsCountEvaluator().evaluate(_trace(statistics=(), error=reason))

    assert result.status is EvaluationStatus.ERROR
    assert result.value is None
    assert result.metadata["reason"] == reason


def test_unsupported_mode_is_not_computable() -> None:
    result = ToolCallsCountEvaluator().evaluate(_trace(mode="other"))

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "unsupported_trace_mode"


@pytest.mark.parametrize(
    "trace",
    [
        {},
        _trace(statistics=({"tool_name": "search", "call_count": -1, "repeat_call_count": 0},)),
        _trace(statistics=({"tool_name": "search", "call_count": 1, "repeat_call_count": 1},)),
        _trace(
            statistics=(
                {"tool_name": "search", "call_count": 1, "repeat_call_count": 0},
                {"tool_name": "search", "call_count": 1, "repeat_call_count": 0},
            )
        ),
    ],
)
def test_invalid_normalized_trace_is_isolated(trace: dict[str, object]) -> None:
    result = ToolCallsCountEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.ERROR
    assert result.value is None
    assert result.metadata["reason"] == "invalid_normalized_trace"
