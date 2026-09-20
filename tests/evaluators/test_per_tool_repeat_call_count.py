import json
from copy import deepcopy

import pytest

from evaluators.base import EvaluationStatus
from evaluators.per_tool_repeat_call_count import PerToolRepeatCallCountEvaluator


def _trace(
    *,
    mode: str = "chat",
    statistics: object = (
        {"tool_name": "single", "call_count": 1, "repeat_call_count": 0},
        {"tool_name": "tool_b", "call_count": 2, "repeat_call_count": 1},
        {"tool_name": "tool_a", "call_count": 4, "repeat_call_count": 3},
        {"tool_name": "tool_c", "call_count": 2, "repeat_call_count": 1},
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


def test_outputs_stable_compact_json_for_only_repeated_tools() -> None:
    trace = _trace()
    original = deepcopy(trace)

    result = PerToolRepeatCallCountEvaluator().evaluate(trace)

    assert result.metric_name == "per_tool_repeat_call_count"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == (
        '[{"tool_name":"tool_a","call_count":4,"repeat_call_count":3},'
        '{"tool_name":"tool_b","call_count":2,"repeat_call_count":1},'
        '{"tool_name":"tool_c","call_count":2,"repeat_call_count":1}]'
    )
    assert json.loads(result.value) == [
        {"tool_name": "tool_a", "call_count": 4, "repeat_call_count": 3},
        {"tool_name": "tool_b", "call_count": 2, "repeat_call_count": 1},
        {"tool_name": "tool_c", "call_count": 2, "repeat_call_count": 1},
    ]
    assert result.metadata["repeated_tool_name_count"] == 3
    assert result.metadata["total_repeat_call_count"] == 5
    assert trace == original


def test_no_repeated_tools_is_computed_as_empty_json_array() -> None:
    result = PerToolRepeatCallCountEvaluator().evaluate(
        _trace(statistics=({"tool_name": "search", "call_count": 1, "repeat_call_count": 0},))
    )

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == "[]"
    assert result.metadata["repeated_tool_name_count"] == 0
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
    result = PerToolRepeatCallCountEvaluator().evaluate(_trace(statistics=(), error=reason))

    assert result.status is EvaluationStatus.ERROR
    assert result.value is None
    assert result.metadata["reason"] == reason


def test_unsupported_mode_is_not_computable() -> None:
    result = PerToolRepeatCallCountEvaluator().evaluate(_trace(mode="other"))

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "unsupported_trace_mode"


@pytest.mark.parametrize(
    "trace",
    [
        {},
        _trace(statistics=({"tool_name": "search", "call_count": 1, "repeat_call_count": 1},)),
        _trace(
            statistics=(
                {"tool_name": "search", "call_count": 2, "repeat_call_count": 1},
                {"tool_name": "search", "call_count": 2, "repeat_call_count": 1},
            )
        ),
    ],
)
def test_invalid_normalized_trace_is_isolated(trace: dict[str, object]) -> None:
    result = PerToolRepeatCallCountEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.ERROR
    assert result.value is None
    assert result.metadata["reason"] == "invalid_normalized_trace"
