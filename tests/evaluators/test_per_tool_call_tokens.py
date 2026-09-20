import json

import pytest

from evaluators.base import EvaluationStatus
from evaluators.per_tool_call_tokens import PerToolCallTokensEvaluator


def _trace(statistics: object, *, error: object = None) -> dict[str, object]:
    return {
        "trace_id": "trace-1",
        "mode": "chat",
        "per_tool_statistics": statistics,
        "diagnostics": {
            "shared_token_step_count": 0,
            "duplicate_call_observation_count": 0,
            "duplicate_token_snapshot_count": 0,
            "excluded_agent_delegation_count": 0,
            "excluded_agent_return_count": 0,
            "malformed_event_count": 0,
        },
        "normalization_error": error,
    }


def _stat(
    tool_name: str, *, steps: int, observed: int, missing: int, total: int
) -> dict[str, object]:
    return {
        "tool_name": tool_name,
        "call_count": steps,
        "tool_step_count": steps,
        "single_tool_step_count": steps,
        "multi_tool_step_count": 0,
        "token_observed_count": observed,
        "token_missing_count": missing,
        "tool_associated_token_total": total,
    }


def test_outputs_computed_and_partial_tools_in_one_json_value() -> None:
    result = PerToolCallTokensEvaluator().evaluate(
        _trace(
            (
                _stat("quote", steps=1, observed=1, missing=0, total=250),
                _stat("search", steps=2, observed=1, missing=1, total=250),
            )
        )
    )

    assert result.metric_name == "per_tool_call_tokens"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert isinstance(result.value, str)
    assert json.loads(result.value) == [
        {"tool_name": "quote", "per_tool_call_tokens": 250.0},
        {
            "tool_name": "search",
            "per_tool_call_tokens": None,
            "reason": "incomplete_token_coverage",
        },
    ]
    assert result.metadata["partial_issue_reasons"] == ("incomplete_token_coverage",)


def test_no_tools_is_not_computable_instead_of_empty_array() -> None:
    result = PerToolCallTokensEvaluator().evaluate(_trace((), error="no_tool_calls"))

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "no_tool_calls"


def test_normalization_error_isolated() -> None:
    result = PerToolCallTokensEvaluator().evaluate(_trace((), error="conflicting_token_snapshot"))

    assert result.status is EvaluationStatus.ERROR
    assert result.value is None
    assert result.metadata["reason"] == "conflicting_token_snapshot"


def test_unsupported_mode_is_not_computable() -> None:
    trace = _trace((_stat("search", steps=1, observed=1, missing=0, total=10),))
    trace["mode"] = "other"

    result = PerToolCallTokensEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "unsupported_trace_mode"


@pytest.mark.parametrize(
    "trace",
    [
        _trace(()),
        _trace(
            (
                {
                    **_stat("search", steps=2, observed=2, missing=0, total=10),
                    "call_count": 1,
                },
            )
        ),
        _trace((), error="unknown_reason"),
        {**_trace((_stat("search", steps=1, observed=1, missing=0, total=10),)), "trace_id": 1},
    ],
)
def test_invalid_normalized_trace_is_rejected(trace: dict[str, object]) -> None:
    result = PerToolCallTokensEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"
