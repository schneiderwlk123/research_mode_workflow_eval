from copy import deepcopy

import pytest

from evaluators.base import EvaluationStatus
from evaluators.subtask_redundancy import SubtaskRedundancyEvaluator


def _normalized_trace(
    *,
    total: int,
    redundant: int,
    exact: int = 0,
    similar: int = 0,
    error: str | None = None,
) -> dict[str, object]:
    return {
        "trace_id": "trace-1",
        "mode": "chat",
        "total_subtask_count": total,
        "redundant_subtask_count": redundant,
        "algorithm_version": "character_bigram_jaccard_v1",
        "similarity_threshold": 0.9,
        "max_novelty_ratio": 0.1,
        "diagnostics": {
            "exact_duplicate_subtask_count": exact,
            "similar_redundant_subtask_count": similar,
            "malformed_event_count": 0,
        },
        "normalization_error": error,
    }


def test_evaluator_computes_redundancy_rate_without_mutating_input() -> None:
    trace = _normalized_trace(total=4, redundant=2, exact=1, similar=1)
    original = deepcopy(trace)

    result = SubtaskRedundancyEvaluator().evaluate(trace)

    assert result.metric_name == "subtask_redundancy_rate"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(0.5)
    assert result.metadata["redundant_subtask_count"] == 2
    assert trace == original


def test_single_subtask_has_zero_redundancy() -> None:
    result = SubtaskRedundancyEvaluator().evaluate(_normalized_trace(total=1, redundant=0))

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0.0


def test_no_subtasks_is_not_computable() -> None:
    result = SubtaskRedundancyEvaluator().evaluate(_normalized_trace(total=0, redundant=0))

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "no_subtasks"


def test_normalization_failure_is_not_computable() -> None:
    result = SubtaskRedundancyEvaluator().evaluate(
        _normalized_trace(total=0, redundant=0, error="missing_structured_subtasks")
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "missing_structured_subtasks"


@pytest.mark.parametrize(
    "trace",
    [
        _normalized_trace(total=1, redundant=2, exact=2),
        _normalized_trace(total=3, redundant=1, exact=1, similar=1),
        _normalized_trace(total=3, redundant=1) | {"similarity_threshold": 0.8},
        _normalized_trace(total=3, redundant=1) | {"mode": "unknown"},
        _normalized_trace(total=3, redundant=1) | {"mode": []},
    ],
)
def test_invalid_aggregate_is_isolated_as_error(trace: dict[str, object]) -> None:
    result = SubtaskRedundancyEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"
