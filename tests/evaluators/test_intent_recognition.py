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

    def __init__(self, response: object) -> None:
        self.response = response
        self.requests: list[IntentRecognitionJudgeRequest] = []

    def judge(self, request: IntentRecognitionJudgeRequest) -> Mapping[str, object]:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        assert isinstance(self.response, Mapping)
        return self.response


def _normalized_trace(*, error: str | None = None) -> dict[str, object]:
    candidate = IntentRecognitionCandidate(
        query="分析公司行情",
        evidence_fragments=(IntentEvidenceFragment("subtask_title", "查询公司行情"),),
        capabilities=(CapabilityDefinition("quote", "查询行情。", ()),),
    )
    return {
        "trace_id": "trace-1",
        "mode": "chat",
        "schema_version": "0.1.0",
        "evidence_fragment_count": 1,
        "actual_capability_count": 1,
        "candidate": candidate,
        "diagnostics": {
            "malformed_event_count": 0,
            "out_of_scope_tool_call_count": 0,
            "duplicate_call_definition_count": 0,
            "duplicate_evidence_count": 0,
        },
        "normalization_error": error,
    }


@pytest.mark.parametrize(
    ("verdict", "reason_code", "expected"),
    [
        ("correct", "material_intents_aligned", 1.0),
        ("incorrect", "material_intent_omitted", 0.0),
    ],
)
def test_judge_verdict_produces_binary_session_score(
    verdict: str, reason_code: str, expected: float
) -> None:
    judge = FakeJudge({"verdict": verdict, "reason_code": reason_code, "confidence": 0.9})

    result = IntentRecognitionAccuracyEvaluator(judge).evaluate(_normalized_trace())

    assert result.metric_name == "intent_recognition_accuracy"
    assert result.evaluator_version == "1.2.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == expected
    assert result.metadata["judge_reason_code"] == reason_code
    assert result.metadata["judge_confidence"] == 0.9
    assert len(judge.requests) == 1


def test_not_assessable_verdict_is_not_computable() -> None:
    judge = FakeJudge(
        {
            "verdict": "not_assessable",
            "reason_code": "insufficient_agent_evidence",
            "confidence": 0.7,
        }
    )

    result = IntentRecognitionAccuracyEvaluator(judge).evaluate(_normalized_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "insufficient_intent_evidence"


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"verdict": "correct", "reason_code": "wrong_primary_intent", "confidence": 0.9},
        {"verdict": "unknown", "reason_code": "material_intents_aligned", "confidence": 0.9},
        {"verdict": "correct", "reason_code": "material_intents_aligned", "confidence": True},
        {"verdict": "correct", "reason_code": "material_intents_aligned", "confidence": math.nan},
        {
            "verdict": "correct",
            "reason_code": "material_intents_aligned",
            "confidence": 0.9,
            "explanation": "不得持久化",
        },
    ],
)
def test_invalid_judge_output_is_not_computable(response: Mapping[str, object]) -> None:
    result = IntentRecognitionAccuracyEvaluator(FakeJudge(response)).evaluate(_normalized_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "invalid_judge_output"
    assert "不得持久化" not in repr(result.metadata)


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
    judge = FakeJudge(
        {"verdict": "correct", "reason_code": "material_intents_aligned", "confidence": 1.0}
    )

    result = IntentRecognitionAccuracyEvaluator(judge).evaluate(
        _normalized_trace(error="missing_query") | {"candidate": None}
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "missing_query"
    assert judge.requests == []


def test_invalid_normalized_trace_is_isolated_as_error() -> None:
    result = IntentRecognitionAccuracyEvaluator(
        FakeJudge(
            {"verdict": "correct", "reason_code": "material_intents_aligned", "confidence": 1.0}
        )
    ).evaluate(_normalized_trace() | {"evidence_fragment_count": 2})

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"
