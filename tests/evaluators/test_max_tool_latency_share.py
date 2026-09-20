import json

import pytest

from evaluators.base import EvaluationStatus
from evaluators.max_tool_latency_share import MaxToolLatencyShareEvaluator


def test_computes_share_and_preserves_all_tied_tool_names() -> None:
    result = MaxToolLatencyShareEvaluator().evaluate(
        _trace(2000, (("search", 1000), ("excel", 1000)))
    )

    assert result.metric_name == "max_tool_latency_share"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert json.loads(str(result.value)) == [
        {"tool_name": "excel", "max_tool_latency_share": pytest.approx(0.5)},
        {"tool_name": "search", "max_tool_latency_share": pytest.approx(0.5)},
    ]
    assert result.metadata["max_latency_tool_names"] == ("excel", "search")
    assert result.metadata["max_tool_latency_total_ms"] == 1000
    assert result.metadata["total_tool_latency_ms"] == 2000


@pytest.mark.parametrize(
    "reason",
    ["no_tool_calls", "incomplete_tool_duration_coverage", "zero_tool_duration"],
)
def test_missing_inputs_are_not_computable(reason: str) -> None:
    result = MaxToolLatencyShareEvaluator().evaluate(_trace(None, (), error=reason))

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == reason


def test_duration_error_is_reported_as_error() -> None:
    result = MaxToolLatencyShareEvaluator().evaluate(_trace(None, (), error="invalid_duration"))

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_duration"


def test_rejects_invalid_normalized_trace() -> None:
    result = MaxToolLatencyShareEvaluator().evaluate(_trace(10, (("search", 11),)))

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


def _trace(
    total: int | None,
    tools: tuple[tuple[str, int], ...],
    *,
    error: str | None = None,
) -> dict[str, object]:
    return {
        "trace_id": "trace-1",
        "mode": "chat",
        "total_tool_latency_ms": total,
        "per_tool_latency_totals": tuple(
            {"tool_name": name, "tool_latency_total_ms": value} for name, value in tools
        ),
        "diagnostics": {
            "duration_missing_count": 0,
            "duplicate_observation_count": 0,
            "orphan_result_count": 0,
            "malformed_event_count": 0,
            "excluded_agent_return_count": 0,
        },
        "normalization_error": error,
    }
