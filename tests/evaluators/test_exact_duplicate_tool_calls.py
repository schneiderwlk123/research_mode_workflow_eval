from copy import deepcopy

import pytest

from evaluators.base import EvaluationStatus
from evaluators.exact_duplicate_tool_calls import ExactDuplicateToolCallEvaluator


def _trace(
    *,
    unique: object,
    comparable: object,
    duplicates: object,
    missing_payloads: object = 1,
    normalization_error: str | None = None,
) -> dict[str, object]:
    return {
        "trace_id": "trace-001",
        "mode": "chat",
        "unique_call_count": unique,
        "comparable_call_count": comparable,
        "exact_duplicate_call_count": duplicates,
        "diagnostics": {
            "malformed_event_count": 0,
            "missing_response_payload_count": missing_payloads,
            "duplicate_state_event_count": 0,
        },
        "normalization_error": normalization_error,
    }


def test_evaluator_calculates_exact_duplicates_over_all_unique_calls() -> None:
    trace = _trace(unique=4, comparable=3, duplicates=1)
    original = deepcopy(trace)

    result = ExactDuplicateToolCallEvaluator().evaluate(trace)

    assert result.metric_name == "exact_duplicate_tool_call_rate"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(0.25)
    assert result.metadata == {
        "trace_id": "trace-001",
        "mode": "chat",
        "unique_call_count": 4,
        "comparable_call_count": 3,
        "uncomparable_call_count": 1,
        "exact_duplicate_call_count": 1,
        "malformed_event_count": 0,
        "missing_response_payload_count": 1,
        "duplicate_state_event_count": 0,
    }
    assert trace == original


def test_calls_without_exact_duplicates_return_zero() -> None:
    result = ExactDuplicateToolCallEvaluator().evaluate(
        _trace(unique=3, comparable=2, duplicates=0)
    )

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(0.0)


def test_no_tool_calls_is_not_computable() -> None:
    result = ExactDuplicateToolCallEvaluator().evaluate(
        _trace(unique=0, comparable=0, duplicates=0, missing_payloads=0)
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "no_tool_calls"


def test_missing_duplicate_key_is_not_computable() -> None:
    result = ExactDuplicateToolCallEvaluator().evaluate(
        _trace(
            unique=1,
            comparable=0,
            duplicates=0,
            normalization_error="missing_duplicate_key",
        )
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "missing_duplicate_key"


@pytest.mark.parametrize(
    "trace",
    [
        {},
        _trace(unique=True, comparable=1, duplicates=0),
        _trace(unique=1, comparable=2, duplicates=0),
        _trace(unique=1, comparable=1, duplicates=2),
    ],
)
def test_invalid_normalized_trace_is_isolated_as_error(trace: dict[str, object]) -> None:
    result = ExactDuplicateToolCallEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.ERROR
    assert result.value is None
    assert result.metadata["reason"] == "invalid_normalized_trace"
