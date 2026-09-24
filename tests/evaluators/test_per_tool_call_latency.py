import json

from evaluators.base import EvaluationStatus
from evaluators.per_tool_call_latency import PerToolCallLatencyEvaluator


def test_outputs_computed_partial_missing_and_local_error_items() -> None:
    result = PerToolCallLatencyEvaluator().evaluate(
        _trace(
            (
                _stat("quote", observed=2, missing=1, total=3000),
                _stat("search", observed=0, missing=1, total=0),
                _stat(
                    "weather",
                    observed=0,
                    missing=0,
                    total=0,
                    errors=("conflicting_duration_observation",),
                ),
            )
        )
    )

    assert result.metric_name == "per_tool_call_latency_ms"
    assert result.evaluator_version == "1.1.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert json.loads(str(result.value)) == [
        {
            "tool_name": "quote",
            "per_tool_call_latency_ms": 1500.0,
        },
        {
            "tool_name": "search",
            "per_tool_call_latency_ms": None,
        },
        {
            "tool_name": "weather",
            "per_tool_call_latency_ms": None,
        },
    ]
    assert result.metadata["partial_issues"] == (
        {"status": "not_computable", "reason": "no_observable_tool_duration"},
        {"status": "error", "reason": "conflicting_duration_observation"},
    )


def test_no_tools_is_not_computable() -> None:
    result = PerToolCallLatencyEvaluator().evaluate(_trace((), error="no_tool_calls"))

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "no_tool_calls"


def test_rejects_invalid_normalized_trace() -> None:
    traces = [
        _trace(()),
        _trace(
            (
                {
                    **_stat("x", observed=1, missing=0, total=1),
                    "call_count": 0,
                },
            )
        ),
        _trace((_stat("x", observed=1, missing=0, total=1, errors=("unknown",)),)),
        _trace((), error="unknown"),
    ]

    for trace in traces:
        result = PerToolCallLatencyEvaluator().evaluate(trace)

        assert result.status is EvaluationStatus.ERROR
        assert result.metadata["reason"] == "invalid_normalized_trace"


def _trace(statistics: object, *, error: object = None) -> dict[str, object]:
    return {
        "trace_id": "trace-1",
        "mode": "chat",
        "per_tool_statistics": statistics,
        "diagnostics": {
            "duration_missing_count": 0,
            "duplicate_observation_count": 0,
            "orphan_result_count": 0,
            "malformed_event_count": 0,
            "excluded_agent_delegation_count": 0,
            "excluded_agent_return_count": 0,
        },
        "normalization_error": error,
    }


def _stat(
    tool_name: str,
    *,
    observed: int,
    missing: int,
    total: int,
    errors: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "tool_name": tool_name,
        "call_count": observed + missing + len(errors),
        "latency_observed_count": observed,
        "latency_missing_count": missing,
        "latency_error_count": len(errors),
        "tool_latency_total_ms": total,
        "duration_error_reasons": errors,
    }
