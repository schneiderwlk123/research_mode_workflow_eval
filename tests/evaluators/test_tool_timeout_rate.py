from copy import deepcopy

import pytest

from evaluators.base import EvaluationStatus
from evaluators.tool_timeout_rate import ToolTimeoutRateEvaluator


def _trace(*, mode: str = "chat", error: object = None) -> dict[str, object]:
    return {
        "trace_id": "trace-1",
        "mode": mode,
        "policy_version": "2026-09-04",
        "tool_calls": (
            {"call_id": "call-1", "timeout_reason": None},
            {"call_id": "call-2", "timeout_reason": "sla_exceeded"},
            {"call_id": "call-3", "timeout_reason": "missing_timestamps"},
            {"call_id": "call-4", "timeout_reason": "explicit_timeout"},
        ),
        "diagnostics": {
            "explicit_timeout_count": 1,
            "sla_exceeded_count": 1,
            "missing_time_timeout_count": 1,
            "duplicate_state_event_count": 0,
            "malformed_event_count": 0,
        },
        "normalization_error": error,
    }


@pytest.mark.parametrize("mode", ["chat", "research"])
def test_evaluator_computes_timeout_rate_without_mutation(mode: str) -> None:
    trace = _trace(mode=mode)
    original = deepcopy(trace)

    result = ToolTimeoutRateEvaluator().evaluate(trace)

    assert result.metric_name == "tool_timeout_rate"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0.75
    assert result.metadata == {
        "trace_id": "trace-1",
        "mode": mode,
        "policy_version": "2026-09-04",
        "timeout_call_count": 3,
        "total_call_count": 4,
        "explicit_timeout_count": 1,
        "sla_exceeded_count": 1,
        "missing_time_timeout_count": 1,
        "duplicate_state_event_count": 0,
        "malformed_event_count": 0,
    }
    assert trace == original


@pytest.mark.parametrize(
    "reason",
    ["no_tool_calls", "missing_call_id", "missing_tool_sla"],
)
def test_missing_required_observation_is_not_computable(reason: str) -> None:
    result = ToolTimeoutRateEvaluator().evaluate(_trace(error=reason))

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == reason


@pytest.mark.parametrize(
    "reason", ["invalid_timestamp", "invalid_event_order", "conflicting_call_definition"]
)
def test_invalid_observation_is_an_error(reason: str) -> None:
    result = ToolTimeoutRateEvaluator().evaluate(_trace(error=reason))

    assert result.status is EvaluationStatus.ERROR
    assert result.value is None
    assert result.metadata["reason"] == reason


@pytest.mark.parametrize(
    "trace",
    [
        {},
        _trace(mode="unsupported"),
        {**_trace(), "policy_version": ""},
        {**_trace(), "tool_calls": ({"call_id": "call-1", "timeout_reason": "other"},)},
    ],
)
def test_invalid_normalized_trace_is_isolated(trace: dict[str, object]) -> None:
    result = ToolTimeoutRateEvaluator().evaluate(trace)

    if trace.get("mode") == "unsupported":
        assert result.status is EvaluationStatus.NOT_COMPUTABLE
        assert result.metadata["reason"] == "unsupported_trace_mode"
    else:
        assert result.status is EvaluationStatus.ERROR
        assert result.metadata["reason"] == "invalid_normalized_trace"
