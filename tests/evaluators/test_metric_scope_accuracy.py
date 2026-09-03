import pytest

from collectors.metric_scope_accuracy import MetricScopeCandidate
from collectors.parameter_value_accuracy import ParameterValueCandidate
from evaluators.base import EvaluationStatus
from evaluators.metric_scope_accuracy import (
    MetricRequirementExtractionRequest,
    MetricScopeAccuracyEvaluator,
    MetricScopeAssessmentRequest,
)


class FakeJudge:
    judge_name = "fake"
    judge_version = "model"
    prompt_version = "1.0.0"

    def __init__(self, *, no_metric: bool = False) -> None:
        self.extract_calls = 0
        self.assess_calls = 0
        self.no_metric = no_metric

    def extract_requirements(
        self, request: MetricRequirementExtractionRequest
    ) -> dict[str, object]:
        del request
        self.extract_calls += 1
        if self.no_metric:
            return {
                "verdict": "not_assessable",
                "reason_code": "no_metric_requirement",
                "requirements": [],
                "confidence": 0.8,
            }
        return {
            "verdict": "extracted",
            "reason_code": "metric_requirements_extracted",
            "requirements": [
                {
                    "requirement_id": "m1",
                    "metric_name": "营业收入",
                    "fields": {
                        "unit": "百万元",
                        "currency": "CNY",
                        "statistical_scope": "consolidated",
                    },
                }
            ],
            "confidence": 0.9,
        }

    def assess(self, request: MetricScopeAssessmentRequest) -> dict[str, object]:
        del request
        self.assess_calls += 1
        return {
            "fields": [
                {
                    "requirement_id": "m1",
                    "field_type": "metric_name",
                    "verdict": "correct",
                    "reason_code": "normalized_equivalent",
                    "confidence": 0.9,
                },
                {
                    "requirement_id": "m1",
                    "field_type": "unit",
                    "verdict": "correct",
                    "reason_code": "direct_match",
                    "confidence": 0.9,
                },
                {
                    "requirement_id": "m1",
                    "field_type": "currency",
                    "verdict": "incorrect",
                    "reason_code": "wrong_currency",
                    "confidence": 0.9,
                },
                {
                    "requirement_id": "m1",
                    "field_type": "statistical_scope",
                    "verdict": "correct",
                    "reason_code": "schema_default_equivalent",
                    "confidence": 0.9,
                },
            ],
            "unexpected_field_count": 1,
            "confidence": 0.9,
        }


def _trace(
    mode: str = "chat", calls: tuple[ParameterValueCandidate, ...] = ()
) -> dict[str, object]:
    return {
        "trace_id": "t1",
        "mode": mode,
        "manual_version": "0.1.0",
        "tool_call_count": len(calls),
        "candidate": MetricScopeCandidate("按人民币百万元、合并口径查询营收", calls),
        "diagnostics": {
            "unknown_schema_call_count": 0,
            "malformed_arguments_call_count": 0,
            "duplicate_call_definition_count": 0,
            "malformed_event_count": 0,
        },
        "normalization_error": None,
    }


def _call() -> ParameterValueCandidate:
    return ParameterValueCandidate(
        "c1",
        "按人民币百万元、合并口径查询营收",
        "finance",
        "查询财务指标。",
        {"metrics": "指标列表。"},
        frozenset({"metrics"}),
        {"metrics": ["营业收入"]},
    )


def test_two_stage_score_and_query_cache_are_auditable() -> None:
    judge = FakeJudge()
    evaluator = MetricScopeAccuracyEvaluator(judge)
    first = evaluator.evaluate(_trace(calls=(_call(),)))
    second = evaluator.evaluate(_trace(mode="research", calls=(_call(),)))
    assert first.status is EvaluationStatus.COMPUTED
    assert first.value == pytest.approx(3 / 5)
    assert first.metadata["expected_field_count"] == 4
    assert first.metadata["unexpected_field_count"] == 1
    assert second.value == first.value
    assert judge.extract_calls == 1
    assert judge.assess_calls == 2


def test_not_assessable_requirement_and_errors_are_safe() -> None:
    judge = FakeJudge(no_metric=True)
    result = MetricScopeAccuracyEvaluator(judge).evaluate(_trace())
    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "no_metric_requirement"


def test_no_tool_evidence_is_deterministic_zero() -> None:
    judge = FakeJudge()
    result = MetricScopeAccuracyEvaluator(judge).evaluate(_trace())
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0.0
    assert result.metadata["incorrect_field_count"] == 4
    assert judge.assess_calls == 0


def test_assessment_must_cover_every_frozen_field() -> None:
    judge = FakeJudge()
    original_assess = judge.assess

    def incomplete(request: MetricScopeAssessmentRequest) -> dict[str, object]:
        response = original_assess(request)
        fields = response["fields"]
        assert isinstance(fields, list)
        response["fields"] = fields[:-1]
        return response

    judge.assess = incomplete  # type: ignore[method-assign]
    result = MetricScopeAccuracyEvaluator(judge).evaluate(_trace(calls=(_call(),)))
    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "invalid_assessment_output"


def test_invalid_normalized_trace_is_error() -> None:
    result = MetricScopeAccuracyEvaluator(FakeJudge()).evaluate(_trace() | {"tool_call_count": 1})
    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"
