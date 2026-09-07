from collections.abc import Mapping

import pytest

from collectors.evidence_quality import EvidenceUnit, build_evidence_source_catalog
from collectors.statement_support import StatementSupportCandidate, normalize_chat_statement_support
from evaluators.base import EvaluationStatus
from evaluators.openai_compatible_judge_runtime import JudgeExecutionError
from evaluators.statement_support import StatementSupportEvaluator, StatementSupportJudgeRequest


class FakeJudge:
    judge_name = "fake-statement-support"
    judge_version = "test-model"
    prompt_version = "1.0.0"

    def __init__(self, response: object) -> None:
        self.response = response
        self.requests: list[StatementSupportJudgeRequest] = []

    def judge(self, request: StatementSupportJudgeRequest) -> Mapping[str, object]:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        assert isinstance(self.response, Mapping)
        return self.response


def _trace(
    *, mode: str = "chat", evidence: bool = True, error: str | None = None
) -> dict[str, object]:
    units = (EvidenceUnit("call-1:0", "search", '{"revenue":100}'),) if evidence else ()
    candidate = StatementSupportCandidate(
        query="分析收入",
        answer="收入为 100 亿元，同比增长 10%，建议关注。",
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
            "evidence_call_count": int(evidence),
            "successful_evidence_response_count": int(evidence),
            "unsegmentable_evidence_response_count": 0,
            "duplicate_evidence_unit_count": 0,
            "duplicate_call_definition_count": 0,
            "duplicate_result_observation_count": 0,
            "orphan_result_count": 0,
        },
        "normalization_error": error,
    }


def _claim(
    claim_id: str,
    claim_type: str,
    verdict: str,
    reason_code: str,
    supporting_unit_ids: list[str],
) -> dict[str, object]:
    return {
        "claim_id": claim_id,
        "claim_type": claim_type,
        "verdict": verdict,
        "reason_code": reason_code,
        "supporting_unit_ids": supporting_unit_ids,
        "confidence": 0.95,
    }


@pytest.mark.parametrize("mode", ["chat", "research"])
def test_supported_verifiable_claims_divided_by_all_verifiable_claims(mode: str) -> None:
    judge = FakeJudge(
        {
            "claims": [
                _claim("c1", "verifiable", "supported", "directly_supported", ["call-1:0"]),
                _claim("c2", "verifiable", "unsupported", "no_supporting_evidence", []),
                _claim("c3", "excluded", "excluded", "non_factual_recommendation", []),
            ]
        }
    )

    result = StatementSupportEvaluator(judge).evaluate(_trace(mode=mode))

    assert result.metric_name == "statement_support_rate"
    assert result.evaluator_version == "1.2.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0.5
    assert result.metadata["verifiable_claim_count"] == 2
    assert result.metadata["supported_claim_count"] == 1
    assert result.metadata["excluded_claim_count"] == 1
    assert "收入为 100" not in repr(result.metadata)


def test_canvas_collector_output_is_accepted_by_evaluator() -> None:
    catalog = build_evidence_source_catalog(
        {
            "manual_version": "0.1.0",
            "tools": [{"name": "search", "description": "检索", "parameters": []}],
        },
        evidence_tool_names={"search"},
    )
    normalized = normalize_chat_statement_support(
        {
            "canvas": {
                "meta": {"traceId": "trace-1", "query": "分析收入"},
                "agents": [
                    {
                        "messages": [
                            {
                                "content": {
                                    "tool_call": [
                                        {
                                            "toolId": "call-1",
                                            "toolName": "search",
                                            "arguments": {},
                                        }
                                    ],
                                    "tool_result": [
                                        {
                                            "toolId": "call-1",
                                            "result": {"data": [{"revenue": 100}]},
                                        }
                                    ],
                                }
                            }
                        ]
                    }
                ],
                "answer": {"answer": "公司收入为 100 亿元。"},
            }
        },
        catalog,
    )
    judge = FakeJudge(
        {"claims": [_claim("c1", "verifiable", "supported", "directly_supported", ["call-1:0"])]}
    )

    result = StatementSupportEvaluator(judge).evaluate(normalized)

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 1.0
    assert result.evaluator_version == "1.2.0"


def test_no_evidence_can_produce_zero_and_no_verifiable_claims_is_not_computable() -> None:
    zero = StatementSupportEvaluator(
        FakeJudge({"claims": [_claim("c1", "verifiable", "unsupported", "no_evidence", [])]})
    ).evaluate(_trace(evidence=False))
    no_claims = StatementSupportEvaluator(
        FakeJudge(
            {"claims": [_claim("c1", "excluded", "excluded", "non_factual_recommendation", [])]}
        )
    ).evaluate(_trace(evidence=False))

    assert zero.status is EvaluationStatus.COMPUTED
    assert zero.value == 0
    assert no_claims.status is EvaluationStatus.NOT_COMPUTABLE
    assert no_claims.metadata["reason"] == "no_verifiable_claims"


def test_not_assessable_claim_makes_result_not_computable() -> None:
    result = StatementSupportEvaluator(
        FakeJudge({"claims": [_claim("c1", "verifiable", "not_assessable", "ambiguous_claim", [])]})
    ).evaluate(_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "incomplete_claim_judgment"


@pytest.mark.parametrize(
    "claims",
    [
        [],
        [_claim("c1", "verifiable", "supported", "directly_supported", ["missing:0"])],
        [
            _claim("c1", "verifiable", "supported", "directly_supported", ["call-1:0"]),
            _claim("c1", "verifiable", "unsupported", "no_supporting_evidence", []),
        ],
        [_claim("c1", "verifiable", "unsupported", "no_supporting_evidence", ["call-1:0"])],
    ],
)
def test_invalid_judge_claims_are_rejected(claims: list[dict[str, object]]) -> None:
    result = StatementSupportEvaluator(FakeJudge({"claims": claims})).evaluate(_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "invalid_judge_output"


def test_normalization_and_judge_failures_do_not_persist_sensitive_text() -> None:
    judge = FakeJudge(RuntimeError("供应商返回了敏感正文"))

    failed = StatementSupportEvaluator(judge).evaluate(_trace())
    missing = StatementSupportEvaluator(judge).evaluate(
        _trace(error="incomplete_evidence_observation")
    )

    assert failed.metadata["reason"] == "judge_failure"
    assert "敏感正文" not in repr(failed.metadata)
    assert missing.metadata["reason"] == "incomplete_evidence_observation"
    assert len(judge.requests) == 1


def test_shared_judge_failure_keeps_safe_reason_and_diagnostics() -> None:
    error = JudgeExecutionError(
        "judge_input_too_large",
        {"request_byte_count": 815437, "max_request_bytes": 524288, "judge_attempt_count": 0},
    )

    result = StatementSupportEvaluator(FakeJudge(error)).evaluate(_trace())

    assert result.metadata["reason"] == "judge_input_too_large"
    assert result.metadata["request_byte_count"] == 815437
    assert result.metadata["max_request_bytes"] == 524288
