import pytest

from evaluators.base import EvaluationStatus
from evaluators.output_token_ratio import OutputTokenRatioEvaluator


def test_computes_output_token_ratio() -> None:
    result = OutputTokenRatioEvaluator().evaluate(_trace(30, 120))

    assert result.metric_name == "output_token_ratio"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(0.25)


def test_zero_output_is_computed_as_zero() -> None:
    result = OutputTokenRatioEvaluator().evaluate(_trace(0, 100))

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0


@pytest.mark.parametrize(
    ("output", "total", "reason"),
    [
        (None, None, "no_observable_token_usage"),
        (20, 100, "incomplete_token_coverage"),
        (0, 0, "zero_total_token_consumption"),
    ],
)
def test_missing_or_zero_denominator_is_not_computable(
    output: int | None, total: int | None, reason: str
) -> None:
    result = OutputTokenRatioEvaluator().evaluate(_trace(output, total, error=reason))

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == reason


def test_rejects_output_greater_than_total() -> None:
    result = OutputTokenRatioEvaluator().evaluate(_trace(101, 100))

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


def _trace(output: int | None, total: int | None, *, error: str | None = None) -> dict[str, object]:
    return {
        "trace_id": "trace-1",
        "mode": "chat",
        "output_token_consumption": output,
        "total_token_consumption": total,
        "diagnostics": {
            "valid_step_count": 1 if output is not None else 0,
            "token_missing_count": 1 if error == "incomplete_token_coverage" else 0,
            "duplicate_token_snapshot_count": 0,
        },
        "normalization_error": error,
    }
