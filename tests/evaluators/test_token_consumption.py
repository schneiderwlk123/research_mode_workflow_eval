from copy import deepcopy

import pytest

from evaluators.base import EvaluationStatus
from evaluators.token_consumption import TotalTokenConsumptionEvaluator


def _trace(
    *,
    mode: str = "chat",
    totals: object = (120, 50),
    valid_count: object = 2,
    missing_count: object = 0,
    duplicate_count: object = 0,
    error: object = None,
) -> dict[str, object]:
    return {
        "trace_id": "trace-1",
        "mode": mode,
        "step_token_totals": totals,
        "diagnostics": {
            "valid_step_count": valid_count,
            "token_missing_count": missing_count,
            "duplicate_token_snapshot_count": duplicate_count,
        },
        "normalization_error": error,
    }


def test_computes_total_token_consumption_without_mutation() -> None:
    trace = _trace()
    original = deepcopy(trace)

    result = TotalTokenConsumptionEvaluator().evaluate(trace)

    assert result.metric_name == "total_token_consumption"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 170
    assert result.metadata["valid_step_count"] == 2
    assert result.metadata["token_missing_count"] == 0
    assert trace == original


@pytest.mark.parametrize(
    "reason",
    ["no_observable_token_usage", "incomplete_token_coverage"],
)
def test_missing_token_coverage_is_not_computable(reason: str) -> None:
    totals = () if reason == "no_observable_token_usage" else (100,)
    valid_count = len(totals)
    result = TotalTokenConsumptionEvaluator().evaluate(
        _trace(
            totals=totals,
            valid_count=valid_count,
            missing_count=1,
            error=reason,
        )
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == reason


@pytest.mark.parametrize(
    "reason",
    [
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "missing_step_id",
        "invalid_token_count",
        "conflicting_token_snapshot",
    ],
)
def test_invalid_observations_are_errors(reason: str) -> None:
    result = TotalTokenConsumptionEvaluator().evaluate(
        _trace(totals=(), valid_count=0, error=reason)
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.value is None
    assert result.metadata["reason"] == reason


@pytest.mark.parametrize(
    "trace",
    [
        {},
        _trace(mode="unsupported"),
        _trace(totals=(100, -1)),
        _trace(valid_count=3),
        _trace(missing_count=-1),
        _trace(duplicate_count=True),
        _trace(totals=(), valid_count=0),
    ],
)
def test_invalid_or_unsupported_normalized_trace_is_isolated(
    trace: dict[str, object],
) -> None:
    result = TotalTokenConsumptionEvaluator().evaluate(trace)

    if trace.get("mode") == "unsupported":
        assert result.status is EvaluationStatus.NOT_COMPUTABLE
        assert result.metadata["reason"] == "unsupported_trace_mode"
    else:
        assert result.status is EvaluationStatus.ERROR
        assert result.metadata["reason"] == "invalid_normalized_trace"
