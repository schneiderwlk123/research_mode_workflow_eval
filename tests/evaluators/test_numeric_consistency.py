from collections.abc import Mapping

import pytest

from collectors.evidence_quality import EvidenceUnit
from collectors.numeric_consistency import NumericConsistencyCandidate
from evaluators.base import EvaluationStatus
from evaluators.numeric_consistency import (
    NumericConsistencyJudgeRequest,
    RetrievedNumericConsistencyEvaluator,
)


class FakeJudge:
    judge_name = "fake-numeric-consistency"
    judge_version = "test-model"
    prompt_version = "1.0.0"

    def __init__(self, response: object) -> None:
        self.response = response
        self.requests: list[NumericConsistencyJudgeRequest] = []

    def judge(self, request: NumericConsistencyJudgeRequest) -> Mapping[str, object]:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        assert isinstance(self.response, Mapping)
        return self.response


def _trace(*, mode: str = "chat", error: str | None = None) -> dict[str, object]:
    units = (EvidenceUnit("call-1:0", "search", '{"revenue":123.46}'),)
    return {
        "trace_id": "trace-1",
        "mode": mode,
        "schema_version": "0.1.0",
        "evidence_scope_version": "evidence-scope-test",
        "evidence_unit_count": 1,
        "candidate": NumericConsistencyCandidate("分析收入", "收入为 123.5 亿元。", units)
        if error is None
        else None,
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


def _claim(
    claim_id: str,
    claim_type: str,
    reason_code: str,
    *,
    answer_value: str | None = None,
    evidence_value: str | None = None,
    canonical_unit: str | None = None,
    answer_scale: str | None = None,
    evidence_scale: str | None = None,
    answer_decimal_places: int | None = None,
    evidence_unit_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "claim_id": claim_id,
        "claim_type": claim_type,
        "reason_code": reason_code,
        "answer_value": answer_value,
        "evidence_value": evidence_value,
        "canonical_unit": canonical_unit,
        "answer_scale": answer_scale,
        "evidence_scale": evidence_scale,
        "answer_decimal_places": answer_decimal_places,
        "evidence_unit_ids": evidence_unit_ids or [],
        "confidence": 0.95,
    }


@pytest.mark.parametrize("mode", ["chat", "research"])
def test_deterministic_comparison_handles_scaling_rounding_and_mismatch(mode: str) -> None:
    judge = FakeJudge(
        {
            "claims": [
                _claim(
                    "c1",
                    "direct_retrieval",
                    "aligned_evidence",
                    answer_value="123.5",
                    evidence_value="123.46",
                    canonical_unit="CNY",
                    answer_scale="100000000",
                    evidence_scale="100000000",
                    answer_decimal_places=1,
                    evidence_unit_ids=["call-1:0"],
                ),
                _claim(
                    "c2",
                    "direct_retrieval",
                    "aligned_evidence",
                    answer_value="12.2",
                    evidence_value="10.2",
                    canonical_unit="CNY",
                    answer_scale="100000000",
                    evidence_scale="100000000",
                    answer_decimal_places=1,
                    evidence_unit_ids=["call-1:0"],
                ),
                _claim("c3", "excluded", "derived_value"),
            ]
        }
    )

    result = RetrievedNumericConsistencyEvaluator(judge).evaluate(_trace(mode=mode))

    assert result.metric_name == "retrieved_numeric_consistency_rate"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(0.5)
    assert result.metadata["consistent_numeric_claim_count"] == 1
    assert result.metadata["inconsistent_numeric_claim_count"] == 1
    assert result.metadata["excluded_numeric_claim_count"] == 1
    assert "123.5" not in repr(result.metadata)


def test_equivalent_scales_compare_in_canonical_units() -> None:
    judge = FakeJudge(
        {
            "claims": [
                _claim(
                    "c1",
                    "direct_retrieval",
                    "aligned_evidence",
                    answer_value="1.23",
                    evidence_value="12300",
                    canonical_unit="CNY",
                    answer_scale="100000000",
                    evidence_scale="10000",
                    answer_decimal_places=2,
                    evidence_unit_ids=["call-1:0"],
                )
            ]
        }
    )

    result = RetrievedNumericConsistencyEvaluator(judge).evaluate(_trace())

    assert result.value == 1.0


def test_missing_value_in_complete_evidence_counts_as_inconsistent() -> None:
    judge = FakeJudge(
        {
            "claims": [
                _claim(
                    "c1",
                    "direct_retrieval",
                    "no_matching_retrieved_value",
                    answer_value="100",
                    canonical_unit="CNY",
                    answer_scale="100000000",
                    answer_decimal_places=0,
                )
            ]
        }
    )

    result = RetrievedNumericConsistencyEvaluator(judge).evaluate(_trace())

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0.0


@pytest.mark.parametrize(
    ("claim_type", "reason", "expected_reason"),
    [
        ("not_assessable", "conflicting_evidence_values", "incomplete_numeric_judgment"),
        ("excluded", "derived_value", "no_retrieved_numeric_claims"),
    ],
)
def test_unassessable_or_absent_direct_claims_do_not_shrink_denominator(
    claim_type: str, reason: str, expected_reason: str
) -> None:
    result = RetrievedNumericConsistencyEvaluator(
        FakeJudge({"claims": [_claim("c1", claim_type, reason)]})
    ).evaluate(_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == expected_reason


def test_empty_claim_list_means_no_retrieved_numeric_claims() -> None:
    result = RetrievedNumericConsistencyEvaluator(FakeJudge({"claims": []})).evaluate(_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "no_retrieved_numeric_claims"


def test_normalization_and_judge_failures_are_safely_isolated() -> None:
    judge = FakeJudge(RuntimeError("sensitive provider response"))

    failed = RetrievedNumericConsistencyEvaluator(judge).evaluate(_trace())
    missing = RetrievedNumericConsistencyEvaluator(judge).evaluate(
        _trace(error="incomplete_evidence_observation")
    )

    assert failed.metadata["reason"] == "judge_failure"
    assert "sensitive provider response" not in repr(failed.metadata)
    assert missing.metadata["reason"] == "incomplete_evidence_observation"
    assert len(judge.requests) == 1


def test_unsupported_mode_is_not_computable() -> None:
    trace = _trace()
    trace["mode"] = "unsupported"

    result = RetrievedNumericConsistencyEvaluator(FakeJudge({"claims": []})).evaluate(trace)

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "unsupported_trace_mode"


def test_invalid_normalized_trace_is_isolated_as_error() -> None:
    trace = _trace()
    trace["evidence_unit_count"] = 2

    result = RetrievedNumericConsistencyEvaluator(FakeJudge({"claims": []})).evaluate(trace)

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"claims": [_claim("c1", "direct_retrieval", "aligned_evidence")]},
        {
            "claims": [
                _claim(
                    "c1",
                    "direct_retrieval",
                    "aligned_evidence",
                    answer_value="NaN",
                    evidence_value="1",
                    canonical_unit="CNY",
                    answer_scale="1",
                    evidence_scale="1",
                    answer_decimal_places=0,
                    evidence_unit_ids=["call-1:0"],
                )
            ]
        },
        {
            "claims": [
                _claim(
                    "c1",
                    "direct_retrieval",
                    "aligned_evidence",
                    answer_value="123.50",
                    evidence_value="123.50",
                    canonical_unit="CNY",
                    answer_scale="1",
                    evidence_scale="1",
                    answer_decimal_places=1,
                    evidence_unit_ids=["call-1:0"],
                )
            ]
        },
    ],
)
def test_invalid_judge_output_is_not_computable(response: Mapping[str, object]) -> None:
    result = RetrievedNumericConsistencyEvaluator(FakeJudge(response)).evaluate(_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "invalid_judge_output"
