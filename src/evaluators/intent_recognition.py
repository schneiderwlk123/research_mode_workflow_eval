"""使用结构化 Judge 评估用户意图与代理行为证据是否对齐。"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from collectors.intent_recognition import IntentEvidenceFragment, IntentRecognitionCandidate
from collectors.routing import CapabilityDefinition
from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_ALLOWED_REASON_CODES = {
    "correct": frozenset({"material_intents_aligned", "normalized_equivalent"}),
    "incorrect": frozenset(
        {"wrong_primary_intent", "material_intent_omitted", "unrelated_agent_behavior"}
    ),
    "not_assessable": frozenset({"insufficient_agent_evidence", "ambiguous_user_intent"}),
}


@dataclass(frozen=True, slots=True)
class IntentRecognitionJudgeRequest:
    """单会话 Judge 请求；所有正文仅允许在调用生命周期内存在。"""

    trace_id: str | None
    mode: str
    query: str
    evidence_fragments: tuple[IntentEvidenceFragment, ...]
    capabilities: tuple[CapabilityDefinition, ...]
    schema_version: str


class IntentRecognitionJudge(Protocol):
    """意图识别 Judge 适配器的稳定接口。"""

    judge_name: str
    judge_version: str
    prompt_version: str

    def judge(self, request: IntentRecognitionJudgeRequest) -> Mapping[str, object]: ...


class IntentRecognitionAccuracyEvaluator(BaseEvaluator):
    """将 Judge 三态判定转换为二元会话准确率，无法判定时不补零。"""

    def __init__(self, judge: IntentRecognitionJudge) -> None:
        self._judge = judge
        self._judge_name = _required_string(judge.judge_name)
        self._judge_version = _required_string(judge.judge_version)
        self._prompt_version = _required_string(judge.prompt_version)

    @property
    def metric_name(self) -> str:
        return "intent_recognition_accuracy"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None,
                EvaluationStatus.ERROR,
                {"reason": "invalid_normalized_trace"},
            )
        trace_id, mode, schema_version, candidate, counts, diagnostics, error = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            "schema_version": schema_version,
            "evidence_fragment_count": counts[0],
            "actual_capability_count": counts[1],
            "judge_name": self._judge_name,
            "judge_version": self._judge_version,
            "prompt_version": self._prompt_version,
            **diagnostics,
        }
        if mode not in {"chat", "research"}:
            metadata["reason"] = "unsupported_trace_mode"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if error is not None:
            metadata["reason"] = error
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if candidate is None:
            return self._result(
                None,
                EvaluationStatus.ERROR,
                {**metadata, "reason": "invalid_normalized_trace"},
            )

        request = IntentRecognitionJudgeRequest(
            trace_id=trace_id,
            mode=mode,
            query=candidate.query,
            evidence_fragments=candidate.evidence_fragments,
            capabilities=candidate.capabilities,
            schema_version=schema_version,
        )
        try:
            response = self._judge.judge(request)
        except Exception:
            metadata["reason"] = "judge_failure"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        verdict = _parse_judge_response(response)
        if verdict is None:
            metadata["reason"] = "invalid_judge_output"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        judgment, reason_code, confidence = verdict
        metadata["judge_reason_code"] = reason_code
        metadata["judge_confidence"] = confidence
        if judgment == "not_assessable":
            metadata["reason"] = "insufficient_intent_evidence"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        return self._result(
            1.0 if judgment == "correct" else 0.0,
            EvaluationStatus.COMPUTED,
            metadata,
        )

    def _result(
        self, value: float | None, status: EvaluationStatus, metadata: Mapping[str, object]
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _parse_judge_response(response: Mapping[str, object]) -> tuple[str, str, float] | None:
    if set(response) != {"verdict", "reason_code", "confidence"}:
        return None
    verdict = response.get("verdict")
    reason_code = response.get("reason_code")
    confidence = response.get("confidence")
    if (
        not isinstance(verdict, str)
        or verdict not in _ALLOWED_REASON_CODES
        or not isinstance(reason_code, str)
        or reason_code not in _ALLOWED_REASON_CODES[verdict]
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or not 0 <= float(confidence) <= 1
    ):
        return None
    return verdict, reason_code, float(confidence)


def _parse_trace(
    trace: Mapping[str, object],
) -> (
    tuple[
        str | None,
        str,
        str,
        IntentRecognitionCandidate | None,
        tuple[int, int],
        dict[str, int],
        str | None,
    ]
    | None
):
    trace_id = trace.get("trace_id")
    mode = _text(trace.get("mode"))
    schema_version = _text(trace.get("schema_version"))
    evidence_count = _non_negative_int(trace.get("evidence_fragment_count"))
    capability_count = _non_negative_int(trace.get("actual_capability_count"))
    candidate = trace.get("candidate")
    raw_diagnostics = trace.get("diagnostics")
    error_value = trace.get("normalization_error")
    if (
        (trace_id is not None and not isinstance(trace_id, str))
        or mode is None
        or schema_version is None
        or evidence_count is None
        or capability_count is None
        or (candidate is not None and not isinstance(candidate, IntentRecognitionCandidate))
        or not isinstance(raw_diagnostics, Mapping)
        or (error_value is not None and _text(error_value) is None)
    ):
        return None
    if isinstance(candidate, IntentRecognitionCandidate) and (
        len(candidate.evidence_fragments) != evidence_count
        or len(candidate.capabilities) != capability_count
    ):
        return None
    diagnostic_names = (
        "malformed_event_count",
        "out_of_scope_tool_call_count",
        "duplicate_call_definition_count",
        "duplicate_evidence_count",
    )
    diagnostics: dict[str, int] = {}
    for name in diagnostic_names:
        value = _non_negative_int(raw_diagnostics.get(name))
        if value is None:
            return None
        diagnostics[name] = value
    return (
        trace_id.strip() if isinstance(trace_id, str) else None,
        mode,
        schema_version,
        candidate if isinstance(candidate, IntentRecognitionCandidate) else None,
        (evidence_count, capability_count),
        diagnostics,
        _text(error_value),
    )


def _required_string(value: object) -> str:
    parsed = _text(value)
    if parsed is None:
        raise ValueError("Judge 版本标识必须是非空字符串")
    return parsed


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _non_negative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
