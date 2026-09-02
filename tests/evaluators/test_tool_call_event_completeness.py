from copy import deepcopy

import pytest

from evaluators.base import EvaluationStatus
from evaluators.tool_call_event_completeness import ToolCallEventCompletenessEvaluator


def _normalized_trace(
    *,
    calls: tuple[object, ...],
    normalization_error: str | None = None,
) -> dict[str, object]:
    return {
        "trace_id": "trace-001",
        "mode": "research",
        "tool_calls": calls,
        "diagnostics": {
            "duplicate_state_event_count": 1,
            "malformed_event_count": 0,
        },
        "normalization_error": normalization_error,
    }


def _call(call_id: str, *, request: object, terminal: object) -> dict[str, object]:
    return {
        "call_id": call_id,
        "request_observed": request,
        "terminal_observed": terminal,
        "source_event_count": 1,
    }


def test_evaluator_calculates_complete_calls_over_unique_calls() -> None:
    trace = _normalized_trace(
        calls=(
            _call("call-1", request=True, terminal=True),
            _call("call-2", request=True, terminal=False),
            _call("call-3", request=False, terminal=True),
            _call("call-4", request=True, terminal=True),
        )
    )
    original = deepcopy(trace)

    result = ToolCallEventCompletenessEvaluator().evaluate(trace)

    assert result.metric_name == "tool_call_event_completeness_rate"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(0.5)
    assert result.metadata == {
        "trace_id": "trace-001",
        "mode": "research",
        "complete_call_count": 2,
        "unique_call_count": 4,
        "incomplete_call_count": 2,
        "duplicate_state_event_count": 1,
        "malformed_event_count": 0,
    }
    assert trace == original


def test_failure_terminal_is_complete_when_request_was_observed() -> None:
    trace = _normalized_trace(calls=(_call("call-1", request=True, terminal=True),))

    result = ToolCallEventCompletenessEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(1.0)


def test_no_tool_calls_is_not_computable_instead_of_zero_or_one() -> None:
    trace = _normalized_trace(calls=())

    result = ToolCallEventCompletenessEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "no_tool_calls"


def test_missing_research_call_id_is_not_computable_under_strict_policy() -> None:
    trace = _normalized_trace(calls=(), normalization_error="missing_call_id")

    result = ToolCallEventCompletenessEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "missing_call_id"


def test_success_specific_normalization_error_does_not_change_completeness() -> None:
    trace = _normalized_trace(calls=(_call("call-1", request=True, terminal=True),))
    trace["success_normalization_error"] = "tool_name_mismatch"

    result = ToolCallEventCompletenessEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(1.0)


@pytest.mark.parametrize(
    "trace",
    [
        {},
        _normalized_trace(calls=("not-a-mapping",)),
        _normalized_trace(calls=(_call("call-1", request=1, terminal=True),)),
    ],
)
def test_invalid_normalized_trace_is_isolated_as_error(trace: dict[str, object]) -> None:
    result = ToolCallEventCompletenessEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.ERROR
    assert result.value is None
    assert result.metadata["reason"] == "invalid_normalized_trace"
