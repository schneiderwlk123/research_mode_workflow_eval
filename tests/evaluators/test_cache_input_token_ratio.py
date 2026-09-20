import pytest

from evaluators.base import EvaluationStatus
from evaluators.cache_input_token_ratio import CacheInputTokenRatioEvaluator


def test_computes_cache_share_of_effective_input() -> None:
    result = CacheInputTokenRatioEvaluator().evaluate(_trace(90, 150))

    assert result.metric_name == "cache_input_token_ratio"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(0.6)


def test_zero_cache_is_computed_as_zero() -> None:
    result = CacheInputTokenRatioEvaluator().evaluate(_trace(0, 100))

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0


@pytest.mark.parametrize(
    ("cache", "effective_input", "reason", "missing_count"),
    [
        (None, None, "no_observable_cache_usage", 1),
        (60, 100, "incomplete_cache_token_coverage", 1),
        (0, 0, "zero_effective_input_token_consumption", 0),
    ],
)
def test_missing_or_zero_denominator_is_not_computable(
    cache: int | None,
    effective_input: int | None,
    reason: str,
    missing_count: int,
) -> None:
    result = CacheInputTokenRatioEvaluator().evaluate(
        _trace(cache, effective_input, error=reason, missing_count=missing_count)
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == reason


def test_rejects_cache_greater_than_effective_input() -> None:
    result = CacheInputTokenRatioEvaluator().evaluate(_trace(101, 100))

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


def _trace(
    cache: int | None,
    effective_input: int | None,
    *,
    error: str | None = None,
    missing_count: int = 0,
) -> dict[str, object]:
    return {
        "trace_id": "trace-1",
        "mode": "chat",
        "cache_token_consumption": cache,
        "effective_input_token_consumption": effective_input,
        "diagnostics": {
            "valid_step_count": 1 if cache is not None else 0,
            "token_missing_count": missing_count,
            "duplicate_token_snapshot_count": 0,
        },
        "normalization_error": error,
    }
