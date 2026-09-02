from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus


class StubEvaluator(BaseEvaluator):
    """用于验证统一接口的最小具体评测器。"""

    @property
    def metric_name(self) -> str:
        return "stub_metric"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        return EvaluationResult(
            metric_name=self.metric_name,
            evaluator_version=self.evaluator_version,
            value=cast(float, trace["value"]),
            status=EvaluationStatus.COMPUTED,
            metadata={"trace_id": trace.get("trace_id")},
        )


def test_base_evaluator_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        BaseEvaluator()  # type: ignore[abstract]


def test_concrete_evaluator_returns_traceable_result() -> None:
    result = StubEvaluator().evaluate({"trace_id": "trace-001", "value": 0.75})

    assert result.metric_name == "stub_metric"
    assert result.evaluator_version == "1.0.0"
    assert result.value == pytest.approx(0.75)
    assert result.status is EvaluationStatus.COMPUTED
    assert result.metadata == {"trace_id": "trace-001"}


def test_result_is_immutable_including_metadata() -> None:
    result = EvaluationResult(
        metric_name="stub_metric",
        evaluator_version="1.0.0",
        value=1.0,
        status=EvaluationStatus.COMPUTED,
        metadata={"sample_count": 1},
    )

    with pytest.raises(FrozenInstanceError):
        result.value = 0.0  # type: ignore[misc]
    with pytest.raises(TypeError):
        result.metadata["sample_count"] = 2  # type: ignore[index]


def test_not_computable_result_represents_missing_value_without_using_zero() -> None:
    result = EvaluationResult(
        metric_name="stub_metric",
        evaluator_version="1.0.0",
        value=None,
        status=EvaluationStatus.NOT_COMPUTABLE,
        metadata={"reason": "missing_required_field"},
    )

    assert result.value is None
    assert result.status is EvaluationStatus.NOT_COMPUTABLE


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), True])
def test_computed_result_rejects_missing_or_non_finite_numeric_value(value: object) -> None:
    with pytest.raises(ValueError):
        EvaluationResult(
            metric_name="stub_metric",
            evaluator_version="1.0.0",
            value=value,  # type: ignore[arg-type]
            status=EvaluationStatus.COMPUTED,
        )


def test_non_computed_result_rejects_numeric_value() -> None:
    with pytest.raises(ValueError):
        EvaluationResult(
            metric_name="stub_metric",
            evaluator_version="1.0.0",
            value=0.0,
            status=EvaluationStatus.ERROR,
        )


@pytest.mark.parametrize("field", ["metric_name", "evaluator_version"])
def test_result_rejects_blank_identity_fields(field: str) -> None:
    metric_name = "  " if field == "metric_name" else "stub_metric"
    evaluator_version = "  " if field == "evaluator_version" else "1.0.0"

    with pytest.raises(ValueError):
        EvaluationResult(
            metric_name=metric_name,
            evaluator_version=evaluator_version,
            value=None,
            status=EvaluationStatus.NOT_COMPUTABLE,
        )
