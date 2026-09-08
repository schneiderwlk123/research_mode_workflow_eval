import math
from collections.abc import Mapping

import pytest

from collectors.intent_recognition import IntentEvidenceFragment, IntentRecognitionCandidate
from collectors.routing import CapabilityDefinition
from evaluators.base import EvaluationStatus
from evaluators.intent_recognition import (
    IntentRecognitionAccuracyEvaluator,
    IntentRecognitionJudgeRequest,
)
from evaluators.openai_compatible_judge_runtime import JudgeExecutionError


class FakeJudge:
    judge_name = "fake-intent-judge"
    judge_version = "test-model"
    prompt_version = "1.0.0"

    def __init__(self, response: object | list[object]) -> None:
        self.responses = response if isinstance(response, list) else [response]
        self.requests: list[IntentRecognitionJudgeRequest] = []

    def judge(self, request: IntentRecognitionJudgeRequest) -> Mapping[str, object]:
        self.requests.append(request)
        response = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        assert isinstance(response, Mapping)
        return response


def _judgment(
    *,
    assessment: str = "assessed",
    primary_intent: str = "aligned",
    intent_coverage: str = "complete",
    planning_alignment: str = "aligned",
    mcp_tool_rationality: str = "reasonable",
    final_answer_alignment: str = "aligned",
    reason_code: str = "all_material_intents_covered",
    confidence: float = 0.9,
) -> dict[str, object]:
    return {
        "assessment": assessment,
        "primary_intent": primary_intent,
        "intent_coverage": intent_coverage,
        "planning_alignment": planning_alignment,
        "mcp_tool_rationality": mcp_tool_rationality,
        "final_answer_alignment": final_answer_alignment,
        "reason_code": reason_code,
        "confidence": confidence,
    }


def _normalized_trace(*, error: str | None = None) -> dict[str, object]:
    candidate = IntentRecognitionCandidate(
        query="分析公司行情",
        evidence_fragments=(
            IntentEvidenceFragment("subtask_query", "查询公司行情"),
            IntentEvidenceFragment("final_answer", "已完成行情分析"),
        ),
        capabilities=(CapabilityDefinition("quote", "查询行情。", ()),),
    )
    return {
        "trace_id": "trace-1",
        "mode": "chat",
        "schema_version": "0.1.0",
        "evidence_fragment_count": 2,
        "actual_capability_count": 1,
        "candidate": candidate,
        "diagnostics": {
            "malformed_event_count": 0,
            "out_of_scope_tool_call_count": 0,
            "unknown_mcp_tool_call_count": 0,
            "duplicate_call_definition_count": 0,
            "duplicate_evidence_count": 0,
        },
        "normalization_error": error,
    }


def test_component_judgments_are_weighted_and_summed_by_code() -> None:
    judge = FakeJudge(
        _judgment(
            intent_coverage="partial",
            planning_alignment="aligned",
            mcp_tool_rationality="partially_reasonable",
            final_answer_alignment="misaligned",
            reason_code="material_intent_partially_covered",
        )
    )

    result = IntentRecognitionAccuracyEvaluator(judge).evaluate(_normalized_trace())

    assert result.metric_name == "intent_recognition_accuracy"
    assert result.evaluator_version == "1.3.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(0.5)
    assert result.metadata["component_scores"] == {
        "intent_coverage": 0.5,
        "planning_alignment": 1.0,
        "mcp_tool_rationality": 0.5,
        "final_answer_alignment": 0.0,
    }
    assert result.metadata["component_judgments"] == {
        "intent_coverage": "partial",
        "planning_alignment": "aligned",
        "mcp_tool_rationality": "partially_reasonable",
        "final_answer_alignment": "misaligned",
    }
    assert result.metadata["judge_reason_code"] == "material_intent_partially_covered"
    assert result.metadata["judge_confidence"] == 0.9
    assert len(judge.requests) == 1


def test_absent_optional_behavior_is_excluded_and_weights_are_renormalized() -> None:
    judge = FakeJudge(
        _judgment(
            planning_alignment="not_present",
            mcp_tool_rationality="not_present",
            final_answer_alignment="aligned",
        )
    )

    normalized = _normalized_trace()
    candidate = normalized["candidate"]
    assert isinstance(candidate, IntentRecognitionCandidate)
    normalized["candidate"] = IntentRecognitionCandidate(
        candidate.query,
        (IntentEvidenceFragment("final_answer", "已完成行情分析"),),
        (),
    )
    normalized["evidence_fragment_count"] = 1
    normalized["actual_capability_count"] = 0

    result = IntentRecognitionAccuracyEvaluator(judge).evaluate(normalized)

    assert result.value == 1.0
    assert result.metadata["assessed_weight"] == pytest.approx(0.65)


@pytest.mark.parametrize("coverage", ["missing", "contradicted"])
def test_wrong_primary_intent_or_contradiction_is_a_hard_zero(coverage: str) -> None:
    response = _judgment(
        primary_intent="misaligned" if coverage == "missing" else "aligned",
        intent_coverage=coverage,
        reason_code="wrong_primary_intent" if coverage == "missing" else "contradicted_user_intent",
    )

    result = IntentRecognitionAccuracyEvaluator(FakeJudge(response)).evaluate(_normalized_trace())

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0.0
    assert result.metadata["hard_rule_applied"] is True


def test_not_assessable_verdict_is_not_computable() -> None:
    judge = FakeJudge(
        _judgment(
            assessment="not_assessable",
            primary_intent="ambiguous",
            intent_coverage="not_assessable",
            planning_alignment="aligned",
            mcp_tool_rationality="reasonable",
            final_answer_alignment="aligned",
            reason_code="insufficient_agent_evidence",
            confidence=0.7,
        )
    )

    result = IntentRecognitionAccuracyEvaluator(judge).evaluate(_normalized_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "insufficient_intent_evidence"


@pytest.mark.parametrize(
    "response",
    [
        {},
        _judgment(reason_code="wrong_primary_intent"),
        _judgment(assessment="unknown"),
        _judgment(confidence=True),
        _judgment(confidence=math.nan),
        _judgment() | {"explanation": "不得持久化"},
        _judgment(planning_alignment="not_present"),
        _judgment(primary_intent="misaligned"),
    ],
)
def test_invalid_judge_output_is_retried_once_then_not_computable(
    response: Mapping[str, object],
) -> None:
    judge = FakeJudge([response, response])
    result = IntentRecognitionAccuracyEvaluator(judge).evaluate(_normalized_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "invalid_judge_output"
    assert result.metadata["judge_validation_retry_count"] == 1
    assert judge.requests[1].validation_feedback == "invalid_intent_judgment_contract"
    assert "不得持久化" not in repr(result.metadata)


def test_invalid_first_output_can_recover_on_validation_retry() -> None:
    judge = FakeJudge([{}, _judgment()])

    result = IntentRecognitionAccuracyEvaluator(judge).evaluate(_normalized_trace())

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 1.0
    assert result.metadata["judge_validation_retry_count"] == 1


def test_judge_failure_does_not_echo_sensitive_input() -> None:
    judge = FakeJudge(RuntimeError("敏感供应商错误"))

    result = IntentRecognitionAccuracyEvaluator(judge).evaluate(_normalized_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "judge_failure"
    assert "敏感供应商错误" not in repr(result.metadata)
    assert "分析公司行情" not in repr(result.metadata)


def test_shared_judge_failure_reason_is_preserved() -> None:
    result = IntentRecognitionAccuracyEvaluator(
        FakeJudge(JudgeExecutionError("judge_rate_limited", {"http_status": 429}))
    ).evaluate(_normalized_trace())

    assert result.metadata["reason"] == "judge_rate_limited"
    assert result.metadata["http_status"] == 429


def test_normalization_error_skips_judge() -> None:
    judge = FakeJudge(_judgment(confidence=1.0))

    result = IntentRecognitionAccuracyEvaluator(judge).evaluate(
        _normalized_trace(error="missing_query") | {"candidate": None}
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "missing_query"
    assert judge.requests == []


def test_invalid_normalized_trace_is_isolated_as_error() -> None:
    result = IntentRecognitionAccuracyEvaluator(FakeJudge(_judgment(confidence=1.0))).evaluate(
        _normalized_trace() | {"evidence_fragment_count": 3}
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"
