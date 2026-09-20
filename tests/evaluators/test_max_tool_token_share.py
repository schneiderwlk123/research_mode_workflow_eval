import json

import pytest

from evaluators.base import EvaluationStatus
from evaluators.max_tool_token_share import MaxToolTokenShareEvaluator


def test_computes_share_and_preserves_all_tied_tool_names() -> None:
    result = MaxToolTokenShareEvaluator().evaluate(_trace(100, (("b", 60), ("a", 60), ("c", 20))))

    assert result.metric_name == "max_tool_token_share"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert isinstance(result.value, str)
    assert json.loads(result.value) == [
        {"tool_name": "a", "max_tool_token_share": pytest.approx(0.6)},
        {"tool_name": "b", "max_tool_token_share": pytest.approx(0.6)},
    ]
    assert result.metadata["max_token_tool_names"] == ("a", "b")
    assert result.metadata["max_tool_associated_tokens"] == 60


def test_zero_tool_associated_tokens_is_computed_as_zero() -> None:
    result = MaxToolTokenShareEvaluator().evaluate(_trace(100, (("search", 0),)))

    assert result.status is EvaluationStatus.COMPUTED
    assert isinstance(result.value, str)
    assert json.loads(result.value) == [{"tool_name": "search", "max_tool_token_share": 0.0}]


@pytest.mark.parametrize(
    "reason",
    [
        "no_tool_calls",
        "incomplete_token_coverage",
        "missing_tool_step_association",
        "zero_total_token_consumption",
    ],
)
def test_missing_inputs_are_not_computable(reason: str) -> None:
    result = MaxToolTokenShareEvaluator().evaluate(_trace(None, (), error=reason))

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == reason


def test_rejects_attribution_greater_than_trace_total() -> None:
    result = MaxToolTokenShareEvaluator().evaluate(_trace(10, (("search", 11),)))

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


def test_rejects_unknown_normalization_reason() -> None:
    result = MaxToolTokenShareEvaluator().evaluate(_trace(None, (), error="unknown_reason"))

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


def _trace(
    total: int | None,
    tools: tuple[tuple[str, int], ...],
    *,
    error: str | None = None,
) -> dict[str, object]:
    return {
        "trace_id": "trace-1",
        "mode": "chat",
        "total_token_consumption": total,
        "per_tool_token_totals": tuple(
            {"tool_name": name, "tool_associated_token_total": value} for name, value in tools
        ),
        "diagnostics": {"shared_token_step_count": 0},
        "normalization_error": error,
    }
