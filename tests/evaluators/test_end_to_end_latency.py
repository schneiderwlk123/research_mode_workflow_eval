from copy import deepcopy

import pytest

from evaluators.base import EvaluationStatus
from evaluators.end_to_end_latency import EndToEndLatencyEvaluator


def _trace(
    *,
    mode: str = "chat",
    start_ms: object = 1_000,
    end_ms: object = 8_000,
    reported_ms: object = None,
    error: object = None,
) -> dict[str, object]:
    return {
        "trace_id": "trace-1",
        "mode": mode,
        "request_start_ms": start_ms,
        "answer_end_ms": end_ms,
        "reported_duration_ms": reported_ms,
        "timestamp_precision": "seconds" if mode == "chat" else "milliseconds",
        "diagnostics": {"duplicate_boundary_event_count": 0},
        "normalization_error": error,
    }


def test_chat_uses_timestamp_delta_without_mutation() -> None:
    trace = _trace()
    original = deepcopy(trace)

    result = EndToEndLatencyEvaluator().evaluate(trace)

    assert result.metric_name == "end_to_end_latency_ms"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 7_000
    assert result.metadata["latency_source"] == "timestamp_delta"
    assert result.metadata["timestamp_precision"] == "seconds"
    assert result.metadata["boundary_difference_ms"] is None
    assert trace == original


def test_research_prefers_reported_duration_and_records_boundary_difference() -> None:
    result = EndToEndLatencyEvaluator().evaluate(
        _trace(mode="research", end_ms=8_002, reported_ms=7_000)
    )

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 7_000
    assert result.metadata["latency_source"] == "turn_end_duration"
    assert result.metadata["boundary_difference_ms"] == 2


def test_research_falls_back_to_timestamp_delta_when_duration_is_missing() -> None:
    result = EndToEndLatencyEvaluator().evaluate(_trace(mode="research"))

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 7_000
    assert result.metadata["latency_source"] == "timestamp_delta"


@pytest.mark.parametrize(
    "reason",
    ["missing_start_timestamp", "missing_end_timestamp", "missing_end_to_end_duration"],
)
def test_missing_boundaries_are_not_computable(reason: str) -> None:
    result = EndToEndLatencyEvaluator().evaluate(_trace(start_ms=None, end_ms=None, error=reason))

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == reason


@pytest.mark.parametrize(
    "reason", ["invalid_chat_trace_structure", "invalid_timestamp", "invalid_duration"]
)
def test_invalid_observations_are_errors(reason: str) -> None:
    result = EndToEndLatencyEvaluator().evaluate(_trace(start_ms=None, end_ms=None, error=reason))

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == reason


def test_negative_timestamp_order_or_reported_duration_is_an_error() -> None:
    negative_order = EndToEndLatencyEvaluator().evaluate(_trace(start_ms=8_000, end_ms=7_000))
    negative_duration = EndToEndLatencyEvaluator().evaluate(_trace(mode="research", reported_ms=-1))

    assert negative_order.status is EvaluationStatus.ERROR
    assert negative_order.metadata["reason"] == "invalid_event_order"
    assert negative_duration.status is EvaluationStatus.ERROR
    assert negative_duration.metadata["reason"] == "invalid_event_order"


@pytest.mark.parametrize(
    "trace",
    [
        {},
        _trace(mode="unsupported"),
        _trace(start_ms=True),
        {**_trace(), "timestamp_precision": "minutes"},
        {**_trace(), "diagnostics": {"duplicate_boundary_event_count": -1}},
    ],
)
def test_invalid_or_unsupported_normalized_trace_is_isolated(trace: dict[str, object]) -> None:
    result = EndToEndLatencyEvaluator().evaluate(trace)

    if trace.get("mode") == "unsupported":
        assert result.status is EvaluationStatus.NOT_COMPUTABLE
        assert result.metadata["reason"] == "unsupported_trace_mode"
    else:
        assert result.status is EvaluationStatus.ERROR
        assert result.metadata["reason"] == "invalid_normalized_trace"
