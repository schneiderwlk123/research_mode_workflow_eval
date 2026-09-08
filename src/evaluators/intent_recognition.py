"""使用结构化 Judge 评估用户意图与代理行为证据是否对齐。"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from collectors.intent_recognition import IntentEvidenceFragment, IntentRecognitionCandidate
from collectors.routing import CapabilityDefinition
from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus
from evaluators.openai_compatible_judge_runtime import judge_error_metadata

_RESPONSE_KEYS = frozenset(
    {
        "assessment",
        "primary_intent",
        "intent_coverage",
        "planning_alignment",
        "mcp_tool_rationality",
        "final_answer_alignment",
        "reason_code",
        "confidence",
    }
)
_ASSESSMENTS = frozenset({"assessed", "not_assessable"})
_PRIMARY_INTENTS = frozenset({"aligned", "misaligned", "ambiguous"})
_INTENT_COVERAGE_SCORES = {"complete": 1.0, "partial": 0.5, "missing": 0.0, "contradicted": 0.0}
_ALIGNMENT_SCORES = {"aligned": 1.0, "partially_aligned": 0.5, "misaligned": 0.0}
_MCP_SCORES = {"reasonable": 1.0, "partially_reasonable": 0.5, "unreasonable": 0.0}
_WEIGHTS = {
    "intent_coverage": 0.45,
    "planning_alignment": 0.20,
    "mcp_tool_rationality": 0.15,
    "final_answer_alignment": 0.20,
}
_ASSESSED_REASON_CODES = frozenset(
    {
        "all_material_intents_covered",
        "material_intent_partially_covered",
        "material_intent_omitted",
        "wrong_primary_intent",
        "contradicted_user_intent",
        "unrelated_agent_behavior",
    }
)
_NOT_ASSESSABLE_REASON_CODES = frozenset({"insufficient_agent_evidence", "ambiguous_user_intent"})


@dataclass(frozen=True, slots=True)
class IntentRecognitionJudgeRequest:
    """单会话 Judge 请求；所有正文仅允许在调用生命周期内存在。"""

    trace_id: str | None
    mode: str
    query: str
    evidence_fragments: tuple[IntentEvidenceFragment, ...]
    capabilities: tuple[CapabilityDefinition, ...]
    schema_version: str
    validation_feedback: str | None = None


class IntentRecognitionJudge(Protocol):
    """意图识别 Judge 适配器的稳定接口。"""

    judge_name: str
    judge_version: str
    prompt_version: str

    def judge(self, request: IntentRecognitionJudgeRequest) -> Mapping[str, object]: ...


class IntentRecognitionAccuracyEvaluator(BaseEvaluator):
    """按固定权重汇总分项语义判定；LLM 不参与算术。"""

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
        return "1.3.0"

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
            "judge_validation_retry_count": 0,
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
        judgment = self._call_and_parse(request, metadata)
        if judgment is None and "reason" not in metadata:
            metadata["judge_validation_retry_count"] = 1
            judgment = self._call_and_parse(
                IntentRecognitionJudgeRequest(
                    trace_id=request.trace_id,
                    mode=request.mode,
                    query=request.query,
                    evidence_fragments=request.evidence_fragments,
                    capabilities=request.capabilities,
                    schema_version=request.schema_version,
                    validation_feedback="invalid_intent_judgment_contract",
                ),
                metadata,
            )
        if judgment is None:
            if "reason" in metadata:
                return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
            metadata["reason"] = "invalid_judge_output"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        assessment, primary_intent, components, reason_code, confidence = judgment
        metadata["judge_reason_code"] = reason_code
        metadata["judge_confidence"] = confidence
        if assessment == "not_assessable":
            metadata["reason"] = "insufficient_intent_evidence"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        component_scores, assessed_weight = _component_scores(components)
        metadata["component_judgments"] = dict(components)
        metadata["component_scores"] = component_scores
        metadata["assessed_weight"] = assessed_weight
        hard_rule = (
            primary_intent == "misaligned" or components["intent_coverage"] == "contradicted"
        )
        metadata["hard_rule_applied"] = hard_rule
        value = 0.0 if hard_rule else _weighted_score(component_scores, assessed_weight)
        return self._result(
            value,
            EvaluationStatus.COMPUTED,
            metadata,
        )

    def _call_and_parse(
        self, request: IntentRecognitionJudgeRequest, metadata: dict[str, object]
    ) -> tuple[str, str, dict[str, str], str, float] | None:
        try:
            response = self._judge.judge(request)
        except Exception as judge_error:
            reason, safe_metadata = judge_error_metadata(judge_error)
            metadata.update(safe_metadata)
            metadata["reason"] = reason
            return None
        return _parse_judge_response(response, request)

    def _result(
        self, value: float | None, status: EvaluationStatus, metadata: Mapping[str, object]
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _parse_judge_response(
    response: Mapping[str, object], request: IntentRecognitionJudgeRequest
) -> tuple[str, str, dict[str, str], str, float] | None:
    if set(response) != _RESPONSE_KEYS:
        return None
    assessment = response.get("assessment")
    primary_intent = response.get("primary_intent")
    intent_coverage = response.get("intent_coverage")
    planning_alignment = response.get("planning_alignment")
    mcp_tool_rationality = response.get("mcp_tool_rationality")
    final_answer_alignment = response.get("final_answer_alignment")
    reason_code = response.get("reason_code")
    confidence = response.get("confidence")
    if (
        not isinstance(assessment, str)
        or assessment not in _ASSESSMENTS
        or not isinstance(primary_intent, str)
        or primary_intent not in _PRIMARY_INTENTS
        or not isinstance(intent_coverage, str)
        or intent_coverage not in {*_INTENT_COVERAGE_SCORES, "not_assessable"}
        or not isinstance(planning_alignment, str)
        or planning_alignment not in {*_ALIGNMENT_SCORES, "not_present"}
        or not isinstance(mcp_tool_rationality, str)
        or mcp_tool_rationality not in {*_MCP_SCORES, "not_present"}
        or not isinstance(final_answer_alignment, str)
        or final_answer_alignment not in {*_ALIGNMENT_SCORES, "not_present"}
        or not isinstance(reason_code, str)
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or not 0 <= float(confidence) <= 1
        or not _component_presence_is_valid(
            request,
            planning_alignment=planning_alignment,
            mcp_tool_rationality=mcp_tool_rationality,
            final_answer_alignment=final_answer_alignment,
        )
    ):
        return None
    if assessment == "not_assessable":
        if (
            primary_intent != "ambiguous"
            or intent_coverage != "not_assessable"
            or reason_code not in _NOT_ASSESSABLE_REASON_CODES
        ):
            return None
    elif (
        primary_intent == "ambiguous"
        or intent_coverage == "not_assessable"
        or reason_code not in _ASSESSED_REASON_CODES
        or (
            reason_code == "wrong_primary_intent"
            and (primary_intent != "misaligned" or intent_coverage not in {"partial", "missing"})
        )
        or (reason_code == "contradicted_user_intent" and intent_coverage != "contradicted")
        or (
            reason_code == "all_material_intents_covered"
            and (primary_intent != "aligned" or intent_coverage != "complete")
        )
        or (
            reason_code == "material_intent_partially_covered"
            and (primary_intent != "aligned" or intent_coverage != "partial")
        )
        or (
            reason_code == "material_intent_omitted"
            and (primary_intent != "aligned" or intent_coverage != "missing")
        )
        or (
            reason_code == "unrelated_agent_behavior"
            and (primary_intent != "misaligned" or intent_coverage != "missing")
        )
    ):
        return None
    return (
        assessment,
        primary_intent,
        {
            "intent_coverage": intent_coverage,
            "planning_alignment": planning_alignment,
            "mcp_tool_rationality": mcp_tool_rationality,
            "final_answer_alignment": final_answer_alignment,
        },
        reason_code,
        float(confidence),
    )


def _component_presence_is_valid(
    request: IntentRecognitionJudgeRequest,
    *,
    planning_alignment: str,
    mcp_tool_rationality: str,
    final_answer_alignment: str,
) -> bool:
    """由代码确认各类证据是否存在，Judge 不得自行改变评分分母。"""

    planning_present = any(
        fragment.kind in {"subtask_query", "plan_task", "plan_description"}
        for fragment in request.evidence_fragments
    )
    final_answer_present = any(
        fragment.kind == "final_answer" for fragment in request.evidence_fragments
    )
    return (
        (planning_alignment != "not_present") == planning_present
        and (mcp_tool_rationality != "not_present") == bool(request.capabilities)
        and (final_answer_alignment != "not_present") == final_answer_present
    )


def _component_scores(components: Mapping[str, str]) -> tuple[dict[str, float], float]:
    scores: dict[str, float] = {
        "intent_coverage": _INTENT_COVERAGE_SCORES[components["intent_coverage"]]
    }
    assessed_weight = _WEIGHTS["intent_coverage"]
    for name in ("planning_alignment", "final_answer_alignment"):
        label = components[name]
        if label != "not_present":
            scores[name] = _ALIGNMENT_SCORES[label]
            assessed_weight += _WEIGHTS[name]
    mcp_label = components["mcp_tool_rationality"]
    if mcp_label != "not_present":
        scores["mcp_tool_rationality"] = _MCP_SCORES[mcp_label]
        assessed_weight += _WEIGHTS["mcp_tool_rationality"]
    return scores, assessed_weight


def _weighted_score(component_scores: Mapping[str, float], assessed_weight: float) -> float:
    weighted_sum = sum(_WEIGHTS[name] * score for name, score in component_scores.items())
    return weighted_sum / assessed_weight


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
        "unknown_mcp_tool_call_count",
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
