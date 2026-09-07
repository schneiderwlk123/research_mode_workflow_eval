from copy import deepcopy

import pytest

from evaluators.base import EvaluationStatus
from evaluators.mcp_tool_call_inflation import McpToolCallInflationEvaluator


def _trace(
    *,
    calls: object,
    signatures: object,
    inflated: object,
    mode: str = "chat",
    normalization_error: str | None = None,
) -> dict[str, object]:
    return {
        "trace_id": "trace-001",
        "mode": mode,
        "manual_version": "0.1.0",
        "mcp_call_count": calls,
        "unique_request_signature_count": signatures,
        "inflated_call_count": inflated,
        "diagnostics": {
            "malformed_event_count": 0,
            "out_of_scope_tool_call_count": 2,
            "duplicate_call_observation_count": 1,
        },
        "normalization_error": normalization_error,
    }


def test_evaluator_calculates_inflated_mcp_calls_over_all_mcp_calls() -> None:
    trace = _trace(calls=4, signatures=3, inflated=1)
    original = deepcopy(trace)

    result = McpToolCallInflationEvaluator().evaluate(trace)

    assert result.metric_name == "mcp_tool_call_inflation_rate"
    assert result.evaluator_version == "1.1.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(0.25)
    assert result.metadata == {
        "trace_id": "trace-001",
        "mode": "chat",
        "manual_version": "0.1.0",
        "mcp_call_count": 4,
        "unique_request_signature_count": 3,
        "inflated_call_count": 1,
        "malformed_event_count": 0,
        "out_of_scope_tool_call_count": 2,
        "duplicate_call_observation_count": 1,
    }
    assert trace == original


def test_different_arguments_return_zero_inflation() -> None:
    result = McpToolCallInflationEvaluator().evaluate(
        _trace(calls=3, signatures=3, inflated=0, mode="research")
    )

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0.0


def test_no_registered_mcp_calls_is_not_computable() -> None:
    result = McpToolCallInflationEvaluator().evaluate(_trace(calls=0, signatures=0, inflated=0))

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "no_registered_mcp_calls"


def test_normalization_failure_is_not_computable() -> None:
    result = McpToolCallInflationEvaluator().evaluate(
        _trace(
            calls=1,
            signatures=1,
            inflated=0,
            normalization_error="missing_call_arguments",
        )
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "missing_call_arguments"


@pytest.mark.parametrize(
    "trace",
    [
        {},
        _trace(calls=True, signatures=1, inflated=0),
        _trace(calls=1, signatures=2, inflated=0),
        _trace(calls=2, signatures=1, inflated=0),
        _trace(calls=2, signatures=1, inflated=2),
        _trace(calls=1, signatures=1, inflated=0, mode="unsupported"),
    ],
)
def test_invalid_normalized_trace_is_isolated_as_error(trace: dict[str, object]) -> None:
    result = McpToolCallInflationEvaluator().evaluate(trace)

    if trace.get("mode") == "unsupported":
        assert result.status is EvaluationStatus.NOT_COMPUTABLE
        assert result.metadata["reason"] == "unsupported_trace_mode"
    else:
        assert result.status is EvaluationStatus.ERROR
        assert result.metadata["reason"] == "invalid_normalized_trace"
