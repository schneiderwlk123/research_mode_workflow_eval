from collections.abc import Mapping

import pytest

from collectors.evidence_quality import EvidenceUnit
from collectors.information_retention import InformationRetentionCandidate
from evaluators.base import EvaluationStatus
from evaluators.information_retention import (
    InformationRetentionEvaluator,
    InformationRetentionJudgeRequest,
)


class FakeJudge:
    judge_name = "fake-information-retention"
    judge_version = "test-model"
    prompt_version = "1.0.0"

    def __init__(self, response: object) -> None:
        self.response = response
        self.requests: list[InformationRetentionJudgeRequest] = []

    def judge(self, request: InformationRetentionJudgeRequest) -> Mapping[str, object]:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        assert isinstance(self.response, Mapping)
        return self.response


def _trace(*, mode: str = "chat", error: str | None = None) -> dict[str, object]:
    units = (
        EvidenceUnit("call-1:0", "search", '{"revenue":100,"growth":10}'),
        EvidenceUnit("call-1:1", "search", '{"as_of":"2025-12-31"}'),
    )
    candidate = InformationRetentionCandidate(
        query="分析收入",
        answer="收入为 100 亿元，同比增长 10%。",
        evidence_units=units,
    )
    return {
        "trace_id": "trace-1",
        "mode": mode,
        "schema_version": "0.1.0",
        "evidence_scope_version": "evidence-scope-test",
        "evidence_unit_count": len(units),
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


def _fact(
    fact_id: str,
    fact_type: str,
    verdict: str,
    reason_code: str,
    source_unit_ids: list[str],
) -> dict[str, object]:
    return {
        "fact_id": fact_id,
        "fact_type": fact_type,
        "verdict": verdict,
        "reason_code": reason_code,
        "source_unit_ids": source_unit_ids,
        "confidence": 0.95,
    }


@pytest.mark.parametrize("mode", ["chat", "research"])
def test_retained_required_facts_divided_by_all_required_facts(mode: str) -> None:
    judge = FakeJudge(
        {
            "facts": [
                _fact("f1", "required", "retained", "explicitly_retained", ["call-1:0"]),
                _fact("f2", "required", "not_retained", "omitted_from_output", ["call-1:1"]),
                _fact("f3", "excluded", "excluded", "not_required_by_query", ["call-1:0"]),
            ]
        }
    )

    result = InformationRetentionEvaluator(judge).evaluate(_trace(mode=mode))

    assert result.metric_name == "information_retention_rate"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0.5
    assert result.metadata["required_fact_count"] == 2
    assert result.metadata["retained_fact_count"] == 1
    assert result.metadata["excluded_fact_count"] == 1
    assert "收入为 100" not in repr(result.metadata)


def test_empty_answer_can_produce_zero_and_no_required_facts_is_not_computable() -> None:
    trace = _trace(mode="research")
    candidate = trace["candidate"]
    assert isinstance(candidate, InformationRetentionCandidate)
    trace["candidate"] = InformationRetentionCandidate(
        candidate.query, "", candidate.evidence_units
    )
    zero = InformationRetentionEvaluator(
        FakeJudge(
            {"facts": [_fact("f1", "required", "not_retained", "empty_final_output", ["call-1:0"])]}
        )
    ).evaluate(trace)
    no_required = InformationRetentionEvaluator(
        FakeJudge(
            {"facts": [_fact("f1", "excluded", "excluded", "not_required_by_query", ["call-1:0"])]}
        )
    ).evaluate(_trace())

    assert zero.status is EvaluationStatus.COMPUTED
    assert zero.value == 0
    assert no_required.status is EvaluationStatus.NOT_COMPUTABLE
    assert no_required.metadata["reason"] == "no_required_facts"


def test_not_assessable_fact_makes_result_not_computable() -> None:
    result = InformationRetentionEvaluator(
        FakeJudge(
            {"facts": [_fact("f1", "required", "not_assessable", "ambiguous_fact", ["call-1:0"])]}
        )
    ).evaluate(_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "incomplete_retention_judgment"


@pytest.mark.parametrize(
    "facts",
    [
        [_fact("f1", "required", "retained", "explicitly_retained", ["missing:0"])],
        [
            _fact("f1", "required", "retained", "explicitly_retained", ["call-1:0"]),
            _fact("f1", "required", "not_retained", "omitted_from_output", ["call-1:1"]),
        ],
        [_fact("f1", "excluded", "retained", "explicitly_retained", ["call-1:0"])],
        [_fact("f1", "required", "retained", "omitted_from_output", ["call-1:0"])],
        [_fact("f1", "required", "retained", "explicitly_retained", [])],
    ],
)
def test_invalid_judge_facts_are_rejected(facts: list[dict[str, object]]) -> None:
    result = InformationRetentionEvaluator(FakeJudge({"facts": facts})).evaluate(_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "invalid_judge_output"


def test_normalization_and_judge_failures_do_not_persist_sensitive_text() -> None:
    judge = FakeJudge(RuntimeError("供应商返回了敏感正文"))

    failed = InformationRetentionEvaluator(judge).evaluate(_trace())
    missing = InformationRetentionEvaluator(judge).evaluate(
        _trace(error="incomplete_fact_segmentation")
    )

    assert failed.metadata["reason"] == "judge_failure"
    assert "敏感正文" not in repr(failed.metadata)
    assert missing.metadata["reason"] == "incomplete_fact_segmentation"
    assert len(judge.requests) == 1
