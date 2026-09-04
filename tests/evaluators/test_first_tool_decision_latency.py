from copy import deepcopy

import pytest

from evaluators.base import EvaluationStatus
from evaluators.first_tool_decision_latency import FirstToolDecisionLatencyEvaluator


def _trace(
    *,
    mode: str = "chat",
    start_ms: object = 1_000,
    decision_ms: object = 7_250,
    error: object = None,
) -> dict[str, object]:
    return {
        "trace_id": "trace-1",
        "mode": mode,
        "request_start_ms": start_ms,
        "first_decision_ms": decision_ms,
        "decision_source": "sub_agent" if mode == "chat" else "tool_pending",
        "diagnostics": {
            "decision_event_count": 2,
            "malformed_decision_event_count": 0,
            "duplicate_pending_event_count": 0,
        },
        "normalization_error": error,
    }


@pytest.mark.parametrize("mode", ["chat", "research"])
def test_evaluator_returns_elapsed_milliseconds_without_mutation(mode: str) -> None:
    trace = _trace(mode=mode)
    original = deepcopy(trace)

    result = FirstToolDecisionLatencyEvaluator().evaluate(trace)

    assert result.metric_name == "first_tool_decision_latency_ms"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 6250
    assert result.metadata == {
        "trace_id": "trace-1",
        "mode": mode,
        "unit": "ms",
        "decision_source": "sub_agent" if mode == "chat" else "tool_pending",
        "decision_event_count": 2,
        "malformed_decision_event_count": 0,
        "duplicate_pending_event_count": 0,
    }
    assert trace == original


@pytest.mark.parametrize(
    "reason",
    [
        "no_tool_decision",
        "missing_turn_start_time",
        "missing_tool_decision_time",
        "incomplete_tool_decision_observation",
        "missing_call_id",
        "conflicting_turn_start",
    ],
)
def test_missing_observations_are_not_computable(reason: str) -> None:
    trace = _trace(start_ms=None, decision_ms=None, error=reason)

    result = FirstToolDecisionLatencyEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == reason


def test_invalid_timestamp_and_negative_order_are_errors() -> None:
    invalid = FirstToolDecisionLatencyEvaluator().evaluate(
        _trace(start_ms=None, decision_ms=None, error="invalid_timestamp")
    )
    negative = FirstToolDecisionLatencyEvaluator().evaluate(
        _trace(start_ms=8_000, decision_ms=7_000)
    )

    assert invalid.status is EvaluationStatus.ERROR
    assert invalid.metadata["reason"] == "invalid_timestamp"
    assert negative.status is EvaluationStatus.ERROR
    assert negative.metadata["reason"] == "invalid_event_order"


@pytest.mark.parametrize(
    "trace",
    [
        {},
        _trace(mode="unsupported"),
        _trace(start_ms=True),
        _trace(error=3),
    ],
)
def test_invalid_or_unsupported_normalized_trace_is_isolated(trace: dict[str, object]) -> None:
    result = FirstToolDecisionLatencyEvaluator().evaluate(trace)

    if trace.get("mode") == "unsupported":
        assert result.status is EvaluationStatus.NOT_COMPUTABLE
        assert result.metadata["reason"] == "unsupported_trace_mode"
    else:
        assert result.status is EvaluationStatus.ERROR
        assert result.metadata["reason"] == "invalid_normalized_trace"
