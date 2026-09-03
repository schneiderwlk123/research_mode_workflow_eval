import math
from collections.abc import Mapping

import pytest

from collectors.evidence_quality import EvidencePrecisionCandidate, EvidenceUnit
from evaluators.base import EvaluationStatus
from evaluators.evidence_precision import (
    EvidencePrecisionEvaluator,
    EvidenceRelevanceJudgeRequest,
)


class FakeJudge:
    judge_name = "fake-evidence-judge"
    judge_version = "test-model"
    prompt_version = "1.0.0"

    def __init__(self, response: object) -> None:
        self.response = response
        self.requests: list[EvidenceRelevanceJudgeRequest] = []

    def judge(self, request: EvidenceRelevanceJudgeRequest) -> Mapping[str, object]:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        assert isinstance(self.response, Mapping)
        return self.response


def _normalized_trace(*, error: str | None = None) -> dict[str, object]:
    units = (
        EvidenceUnit("call-1:0", "search", '{"title":"相关资料"}'),
        EvidenceUnit("call-1:1", "search", '{"title":"无关资料"}'),
    )
    candidate = EvidencePrecisionCandidate("分析公司收入", units)
    return {
        "trace_id": "trace-1",
        "mode": "chat",
        "schema_version": "0.1.0",
        "evidence_scope_version": "evidence-scope-test",
        "evidence_unit_count": 2,
        "candidate": candidate if error is None else None,
        "diagnostics": {
            "malformed_event_count": 0,
            "out_of_scope_tool_call_count": 0,
            "evidence_call_count": 1,
            "successful_evidence_response_count": 1,
            "unsegmentable_evidence_response_count": 0,
            "duplicate_evidence_unit_count": 0,
            "duplicate_call_definition_count": 0,
        },
        "normalization_error": error,
    }


def test_relevant_units_divided_by_all_units() -> None:
    judge = FakeJudge(
        {
            "judgments": [
                {
                    "unit_id": "call-1:0",
                    "verdict": "relevant",
                    "reason_code": "directly_addresses_requirement",
                    "confidence": 0.95,
                },
                {
                    "unit_id": "call-1:1",
                    "verdict": "irrelevant",
                    "reason_code": "unrelated_to_requirement",
                    "confidence": 0.9,
                },
            ]
        }
    )

    result = EvidencePrecisionEvaluator(judge).evaluate(_normalized_trace())

    assert result.metric_name == "evidence_precision_rate"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0.5
    assert result.metadata["relevant_evidence_unit_count"] == 1
    assert result.metadata["irrelevant_evidence_unit_count"] == 1
    assert result.metadata["evidence_unit_count"] == 2
    assert len(judge.requests) == 1
    assert "相关资料" not in repr(result.metadata)


def test_not_assessable_unit_makes_full_denominator_not_computable() -> None:
    judge = FakeJudge(
        {
            "judgments": [
                {
                    "unit_id": "call-1:0",
                    "verdict": "relevant",
                    "reason_code": "supports_required_fact",
                    "confidence": 0.9,
                },
                {
                    "unit_id": "call-1:1",
                    "verdict": "not_assessable",
                    "reason_code": "insufficient_unit_content",
                    "confidence": 0.5,
                },
            ]
        }
    )

    result = EvidencePrecisionEvaluator(judge).evaluate(_normalized_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "incomplete_relevance_judgment"
    assert result.metadata["not_assessable_evidence_unit_count"] == 1


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"judgments": []},
        {
            "judgments": [
                {
                    "unit_id": "call-1:0",
                    "verdict": "relevant",
                    "reason_code": "unrelated_to_requirement",
                    "confidence": 0.9,
                },
                {
                    "unit_id": "call-1:1",
                    "verdict": "irrelevant",
                    "reason_code": "unrelated_to_requirement",
                    "confidence": 0.9,
                },
            ]
        },
        {
            "judgments": [
                {
                    "unit_id": "call-1:0",
                    "verdict": "relevant",
                    "reason_code": "directly_addresses_requirement",
                    "confidence": math.nan,
                },
                {
                    "unit_id": "call-1:1",
                    "verdict": "irrelevant",
                    "reason_code": "unrelated_to_requirement",
                    "confidence": 0.9,
                },
            ]
        },
        {
            "judgments": [
                {
                    "unit_id": "call-1:0",
                    "verdict": "relevant",
                    "reason_code": "directly_addresses_requirement",
                    "confidence": 0.9,
                },
                {
                    "unit_id": "call-1:0",
                    "verdict": "relevant",
                    "reason_code": "directly_addresses_requirement",
                    "confidence": 0.9,
                },
            ]
        },
    ],
)
def test_invalid_or_incomplete_judge_output_is_rejected(response: Mapping[str, object]) -> None:
    result = EvidencePrecisionEvaluator(FakeJudge(response)).evaluate(_normalized_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "invalid_judge_output"


def test_normalization_error_skips_judge_and_invalid_trace_is_isolated() -> None:
    judge = FakeJudge({"judgments": []})

    missing = EvidencePrecisionEvaluator(judge).evaluate(
        _normalized_trace(error="no_stable_evidence_units")
    )
    invalid = EvidencePrecisionEvaluator(judge).evaluate(
        _normalized_trace() | {"evidence_unit_count": 3}
    )

    assert missing.status is EvaluationStatus.NOT_COMPUTABLE
    assert missing.metadata["reason"] == "no_stable_evidence_units"
    assert invalid.status is EvaluationStatus.ERROR
    assert invalid.metadata["reason"] == "invalid_normalized_trace"
    assert judge.requests == []


def test_empty_or_duplicate_unit_ids_are_invalid_normalized_traces() -> None:
    judge = FakeJudge({"judgments": []})
    empty_candidate = EvidencePrecisionCandidate("分析公司收入", ())
    duplicate_candidate = EvidencePrecisionCandidate(
        "分析公司收入",
        (
            EvidenceUnit("same", "search", '{"value":1}'),
            EvidenceUnit("same", "search", '{"value":2}'),
        ),
    )
    invalid_json_candidate = EvidencePrecisionCandidate(
        "分析公司收入", (EvidenceUnit("unit-1", "search", "not-json"),)
    )

    empty = EvidencePrecisionEvaluator(judge).evaluate(
        _normalized_trace() | {"candidate": empty_candidate, "evidence_unit_count": 0}
    )
    duplicate = EvidencePrecisionEvaluator(judge).evaluate(
        _normalized_trace() | {"candidate": duplicate_candidate}
    )
    invalid_json = EvidencePrecisionEvaluator(judge).evaluate(
        _normalized_trace() | {"candidate": invalid_json_candidate, "evidence_unit_count": 1}
    )

    assert empty.status is EvaluationStatus.ERROR
    assert empty.metadata["reason"] == "invalid_normalized_trace"
    assert duplicate.status is EvaluationStatus.ERROR
    assert duplicate.metadata["reason"] == "invalid_normalized_trace"
    assert invalid_json.status is EvaluationStatus.ERROR
    assert invalid_json.metadata["reason"] == "invalid_normalized_trace"
    assert judge.requests == []


def test_judge_failure_does_not_echo_sensitive_content() -> None:
    result = EvidencePrecisionEvaluator(FakeJudge(RuntimeError("供应商泄露正文"))).evaluate(
        _normalized_trace()
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "judge_failure"
    assert "供应商泄露正文" not in repr(result.metadata)
    assert "相关资料" not in repr(result.metadata)
