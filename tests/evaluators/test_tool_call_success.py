from copy import deepcopy

import pytest

from evaluators.base import EvaluationStatus
from evaluators.tool_call_success import ToolCallSuccessEvaluator


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
        "success_normalization_error": None,
    }


def _call(
    call_id: str,
    *,
    success: object,
    rule_id: object,
) -> dict[str, object]:
    return {
        "call_id": call_id,
        "tool_name": "tool-a",
        "request_observed": True,
        "terminal_observed": True,
        "success_observed": success,
        "success_rule_id": rule_id,
        "source_event_count": 1,
    }


def test_evaluator_calculates_successes_over_all_unique_calls() -> None:
    trace = _normalized_trace(
        calls=(
            _call("call-1", success=True, rule_id="rule-v1"),
            _call("call-2", success=False, rule_id="rule-v1"),
            _call("call-3", success=False, rule_id=None),
            _call("call-4", success=True, rule_id="rule-v1"),
        )
    )
    original = deepcopy(trace)

    result = ToolCallSuccessEvaluator().evaluate(trace)

    assert result.metric_name == "tool_call_success_rate"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(0.5)
    assert result.metadata == {
        "trace_id": "trace-001",
        "mode": "research",
        "successful_call_count": 2,
        "unsuccessful_call_count": 2,
        "undetermined_call_count": 0,
        "unique_call_count": 4,
        "success_rule_ids": ("rule-v1",),
        "duplicate_state_event_count": 1,
        "malformed_event_count": 0,
    }
    assert trace == original


def test_any_undetermined_outcome_makes_strict_metric_not_computable() -> None:
    trace = _normalized_trace(
        calls=(
            _call("call-1", success=True, rule_id="rule-v1"),
            _call("call-2", success=None, rule_id=None),
        )
    )

    result = ToolCallSuccessEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "undetermined_tool_outcome"
    assert result.metadata["undetermined_call_count"] == 1


def test_no_tool_calls_is_not_computable() -> None:
    result = ToolCallSuccessEvaluator().evaluate(_normalized_trace(calls=()))

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "no_tool_calls"


def test_normalization_error_is_preserved_as_not_computable() -> None:
    result = ToolCallSuccessEvaluator().evaluate(
        _normalized_trace(calls=(), normalization_error="tool_name_mismatch")
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "tool_name_mismatch"


def test_success_specific_normalization_error_is_not_computable() -> None:
    trace = _normalized_trace(calls=(_call("call-1", success=None, rule_id=None),))
    trace["success_normalization_error"] = "tool_name_mismatch"

    result = ToolCallSuccessEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "tool_name_mismatch"


@pytest.mark.parametrize(
    "trace",
    [
        {},
        _normalized_trace(calls=("not-a-mapping",)),
        _normalized_trace(calls=(_call("call-1", success="yes", rule_id="rule-v1"),)),
        _normalized_trace(calls=(_call("call-1", success=True, rule_id=None),)),
    ],
)
def test_invalid_normalized_trace_is_isolated_as_error(trace: dict[str, object]) -> None:
    result = ToolCallSuccessEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.ERROR
    assert result.value is None
    assert result.metadata["reason"] == "invalid_normalized_trace"
