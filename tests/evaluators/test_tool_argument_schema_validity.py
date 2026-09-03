from copy import deepcopy

import pytest

from evaluators.base import EvaluationStatus
from evaluators.tool_argument_schema_validity import ToolArgumentSchemaValidityEvaluator


def _trace(
    *,
    total: object = 7,
    covered: object = 6,
    valid: object = 4,
    invalid: object = 2,
    normalization_error: str | None = None,
) -> dict[str, object]:
    unknown = total - covered if type(total) is int and type(covered) is int else 0
    type_violations = 1 if type(invalid) is int and invalid > 0 else 0
    enum_violations = 1 if type(invalid) is int and invalid > 1 else 0
    undeclared = 1 if type(covered) is int and covered > 0 else 0
    return {
        "trace_id": "trace-001",
        "mode": "chat",
        "manual_version": "0.1.0",
        "total_call_count": total,
        "schema_covered_call_count": covered,
        "schema_valid_call_count": valid,
        "schema_invalid_call_count": invalid,
        "diagnostics": {
            "unknown_schema_call_count": unknown,
            "malformed_arguments_call_count": 0,
            "type_violation_call_count": type_violations,
            "enum_violation_call_count": enum_violations,
            "missing_nested_required_call_count": 0,
            "undeclared_argument_call_count": undeclared,
            "duplicate_call_definition_count": 0,
            "malformed_event_count": 0,
        },
        "normalization_error": normalization_error,
    }


def test_evaluator_calculates_only_schema_validity_metric() -> None:
    trace = _trace()
    original = deepcopy(trace)

    result = ToolArgumentSchemaValidityEvaluator().evaluate(trace)

    assert result.metric_name == "tool_argument_schema_validity_rate"
    assert result.evaluator_version == "1.1.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(4 / 6)
    assert "schema_coverage_rate" not in result.metadata
    assert result.metadata["manual_version"] == "0.1.0"
    assert trace == original


@pytest.mark.parametrize(
    ("trace", "reason"),
    [
        (_trace(total=0, covered=0, valid=0, invalid=0), "no_tool_calls"),
        (
            _trace(total=2, covered=0, valid=0, invalid=0),
            "no_schema_covered_calls",
        ),
        (_trace(normalization_error="missing_call_id"), "missing_call_id"),
    ],
)
def test_missing_inputs_are_not_computable(trace: dict[str, object], reason: str) -> None:
    result = ToolArgumentSchemaValidityEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == reason


@pytest.mark.parametrize(
    "trace",
    [
        {},
        _trace(total=True),
        _trace(total=2, covered=3),
        _trace(covered=4, valid=3, invalid=2),
        _trace()
        | {
            "diagnostics": {
                "unknown_schema_call_count": 3,
                "malformed_arguments_call_count": 0,
                "type_violation_call_count": 1,
                "enum_violation_call_count": 1,
                "missing_nested_required_call_count": 0,
                "undeclared_argument_call_count": 1,
                "duplicate_call_definition_count": 0,
                "malformed_event_count": 0,
            }
        },
    ],
)
def test_invalid_normalized_trace_is_isolated_as_error(trace: dict[str, object]) -> None:
    result = ToolArgumentSchemaValidityEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.ERROR
    assert result.value is None
    assert result.metadata["reason"] == "invalid_normalized_trace"
