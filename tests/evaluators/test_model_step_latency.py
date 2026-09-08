from copy import deepcopy

import pytest

from evaluators.base import EvaluationStatus
from evaluators.model_step_latency import AverageModelStepLatencyEvaluator


def _trace(
    *,
    mode: str = "chat",
    durations: object = (1_000, 2_000),
    valid_count: object = 2,
    missing_count: object = 1,
    duplicate_count: object = 0,
    cross_check_count: object = 0,
    max_difference: object = None,
    error: object = None,
) -> dict[str, object]:
    return {
        "trace_id": "trace-1",
        "mode": mode,
        "step_durations_ms": durations,
        "diagnostics": {
            "valid_step_count": valid_count,
            "latency_missing_count": missing_count,
            "duplicate_boundary_event_count": duplicate_count,
            "timestamp_cross_check_count": cross_check_count,
            "max_abs_boundary_difference_ms": max_difference,
        },
        "normalization_error": error,
    }


def test_computes_trace_average_and_aggregation_inputs_without_mutation() -> None:
    trace = _trace()
    original = deepcopy(trace)

    result = AverageModelStepLatencyEvaluator().evaluate(trace)

    assert result.metric_name == "average_model_step_latency_ms"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 1_500
    assert result.metadata["valid_step_count"] == 2
    assert result.metadata["latency_missing_count"] == 1
    assert result.metadata["total_step_duration_ms"] == 3_000
    assert trace == original


def test_research_preserves_timestamp_cross_check_diagnostics() -> None:
    result = AverageModelStepLatencyEvaluator().evaluate(
        _trace(
            mode="research",
            durations=(500,),
            valid_count=1,
            cross_check_count=1,
            max_difference=20,
        )
    )

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 500
    assert result.metadata["timestamp_cross_check_count"] == 1
    assert result.metadata["max_abs_boundary_difference_ms"] == 20


def test_no_observable_steps_is_not_computable() -> None:
    result = AverageModelStepLatencyEvaluator().evaluate(
        _trace(
            durations=(),
            valid_count=0,
            missing_count=2,
            error="no_observable_model_steps",
        )
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "no_observable_model_steps"


@pytest.mark.parametrize(
    "reason",
    [
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "missing_step_id",
        "invalid_duration",
        "invalid_timestamp",
        "invalid_event_order",
        "conflicting_step_boundary",
    ],
)
def test_invalid_observations_are_errors(reason: str) -> None:
    result = AverageModelStepLatencyEvaluator().evaluate(
        _trace(durations=(), valid_count=0, error=reason)
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.value is None
    assert result.metadata["reason"] == reason


@pytest.mark.parametrize(
    "trace",
    [
        {},
        _trace(mode="unsupported"),
        _trace(durations=(1_000, -1)),
        _trace(valid_count=3),
        _trace(missing_count=-1),
        _trace(cross_check_count=1, max_difference=None),
        _trace(cross_check_count=0, max_difference=1),
    ],
)
def test_invalid_or_unsupported_normalized_trace_is_isolated(
    trace: dict[str, object],
) -> None:
    result = AverageModelStepLatencyEvaluator().evaluate(trace)

    if trace.get("mode") == "unsupported":
        assert result.status is EvaluationStatus.NOT_COMPUTABLE
        assert result.metadata["reason"] == "unsupported_trace_mode"
    else:
        assert result.status is EvaluationStatus.ERROR
        assert result.metadata["reason"] == "invalid_normalized_trace"
