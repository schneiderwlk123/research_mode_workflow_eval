from copy import deepcopy

import pytest

from evaluators.base import EvaluationStatus
from evaluators.required_parameter_completeness import RequiredParameterCompletenessEvaluator


def _trace(
    *,
    total: object = 5,
    covered: object = 4,
    eligible: object = 3,
    required: object = 6,
    filled: object = 4,
    normalization_error: str | None = None,
) -> dict[str, object]:
    unknown_count = total - covered if isinstance(total, int) and isinstance(covered, int) else 0
    zero_required_count = (
        covered - eligible if isinstance(covered, int) and isinstance(eligible, int) else 0
    )
    missing_count = (
        required - filled if isinstance(required, int) and isinstance(filled, int) else 0
    )
    return {
        "trace_id": "trace-001",
        "mode": "chat",
        "manual_version": "0.1.0",
        "total_call_count": total,
        "schema_covered_call_count": covered,
        "eligible_call_count": eligible,
        "required_parameter_count": required,
        "filled_required_parameter_count": filled,
        "diagnostics": {
            "unknown_schema_call_count": unknown_count,
            "zero_required_parameter_call_count": zero_required_count,
            "malformed_arguments_call_count": 0,
            "missing_required_parameter_count": missing_count,
            "duplicate_call_definition_count": 0,
            "malformed_event_count": 0,
        },
        "normalization_error": normalization_error,
    }


def test_evaluator_calculates_only_micro_completeness_metric() -> None:
    trace = _trace()
    original = deepcopy(trace)

    result = RequiredParameterCompletenessEvaluator().evaluate(trace)

    assert result.metric_name == "required_parameter_completeness_rate"
    assert result.evaluator_version == "1.1.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(4 / 6)
    assert "schema_coverage_rate" not in result.metadata
    assert result.metadata["manual_version"] == "0.1.0"
    assert result.metadata["missing_required_parameter_count"] == 2
    assert trace == original


@pytest.mark.parametrize(
    ("trace", "reason"),
    [
        (_trace(total=0, covered=0, eligible=0, required=0, filled=0), "no_tool_calls"),
        (
            _trace(total=2, covered=0, eligible=0, required=0, filled=0),
            "no_schema_covered_calls",
        ),
        (
            _trace(total=2, covered=2, eligible=0, required=0, filled=0),
            "no_required_parameters_in_scope",
        ),
        (_trace(normalization_error="missing_call_id"), "missing_call_id"),
    ],
)
def test_missing_inputs_are_not_computable(trace: dict[str, object], reason: str) -> None:
    result = RequiredParameterCompletenessEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == reason


@pytest.mark.parametrize(
    "trace",
    [
        {},
        _trace(total=True),
        _trace(total=2, covered=3),
        _trace(required=2, filled=3),
        _trace(required=6, filled=4)
        | {
            "diagnostics": {
                "unknown_schema_call_count": 3,
                "zero_required_parameter_call_count": 1,
                "malformed_arguments_call_count": 0,
                "missing_required_parameter_count": 2,
                "duplicate_call_definition_count": 0,
                "malformed_event_count": 0,
            }
        },
    ],
)
def test_invalid_normalized_trace_is_isolated_as_error(trace: dict[str, object]) -> None:
    result = RequiredParameterCompletenessEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.ERROR
    assert result.value is None
    assert result.metadata["reason"] == "invalid_normalized_trace"
