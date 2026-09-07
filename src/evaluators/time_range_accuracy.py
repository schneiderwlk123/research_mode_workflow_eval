"""使用结构化 Judge 评估时间范围字段是否被正确传递。"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from collectors.parameter_value_accuracy import ParameterValueCandidate
from collectors.time_range_accuracy import TimeRangeCandidate
from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus
from evaluators.openai_compatible_judge_runtime import judge_error_metadata

_ASSESSED_REASON = "time_fields_compared"
_NOT_ASSESSABLE_REASONS = frozenset(
    {
        "no_temporal_requirement",
        "missing_reference_time",
        "ambiguous_temporal_requirement",
        "insufficient_time_evidence",
    }
)


@dataclass(frozen=True, slots=True)
class TimeRangeJudgeRequest:
    """单会话 Judge 请求；时间原文和工具参数不得持久化。"""

    trace_id: str | None
    mode: str
    query: str
    reference_time: str | None
    tool_calls: tuple[ParameterValueCandidate, ...]
    manual_version: str


class TimeRangeJudge(Protocol):
    """时间范围 Judge 适配器的稳定接口。"""

    judge_name: str
    judge_version: str
    prompt_version: str

    def judge(self, request: TimeRangeJudgeRequest) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class _TimeRangeJudgment:
    verdict: str
    reason_code: str
    correct: int
    incorrect: int
    not_assessable: int
    unexpected: int
    confidence: float


class TimeRangeAccuracyEvaluator(BaseEvaluator):
    """以正确字段数除以可评估预期字段数，不把缺失输入补零。"""

    def __init__(self, judge: TimeRangeJudge) -> None:
        self._judge = judge
        self._judge_name = _required_string(judge.judge_name)
        self._judge_version = _required_string(judge.judge_version)
        self._prompt_version = _required_string(judge.prompt_version)

    @property
    def metric_name(self) -> str:
        return "time_range_extraction_accuracy"

    @property
    def evaluator_version(self) -> str:
        return "1.2.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None,
                EvaluationStatus.ERROR,
                {"reason": "invalid_normalized_trace"},
            )
        (
            trace_id,
            mode,
            manual_version,
            tool_call_count,
            reference_time_source,
            candidate,
            diagnostics,
            normalization_error,
        ) = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            "manual_version": manual_version,
            "tool_call_count": tool_call_count,
            "reference_time_source": reference_time_source,
            "reference_time_present": candidate is not None
            and candidate.reference_time is not None,
            "judge_name": self._judge_name,
            "judge_version": self._judge_version,
            "prompt_version": self._prompt_version,
            **diagnostics,
        }
        if mode not in {"chat", "research"}:
            metadata["reason"] = "unsupported_trace_mode"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if normalization_error is not None:
            metadata["reason"] = normalization_error
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if candidate is None:
            return self._result(
                None,
                EvaluationStatus.ERROR,
                {**metadata, "reason": "invalid_normalized_trace"},
            )

        request = TimeRangeJudgeRequest(
            trace_id=trace_id,
            mode=mode,
            query=candidate.query,
            reference_time=candidate.reference_time,
            tool_calls=candidate.tool_calls,
            manual_version=manual_version,
        )
        try:
            response = self._judge.judge(request)
        except Exception as judge_error:
            reason, safe_metadata = judge_error_metadata(judge_error)
            metadata.update(safe_metadata)
            metadata["reason"] = reason
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        judgment = _parse_judge_response(response)
        if judgment is None:
            metadata["reason"] = "invalid_judge_output"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)

        metadata.update(
            {
                "judge_reason_code": judgment.reason_code,
                "judge_confidence": judgment.confidence,
                "correct_field_count": judgment.correct,
                "incorrect_field_count": judgment.incorrect,
                "not_assessable_field_count": judgment.not_assessable,
                "unexpected_constraint_count": judgment.unexpected,
                "assessable_field_count": judgment.correct + judgment.incorrect,
            }
        )
        if judgment.verdict == "not_assessable":
            metadata["reason"] = judgment.reason_code
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        denominator = judgment.correct + judgment.incorrect
        return self._result(
            judgment.correct / denominator,
            EvaluationStatus.COMPUTED,
            metadata,
        )

    def _result(
        self,
        value: float | None,
        status: EvaluationStatus,
        metadata: Mapping[str, object],
    ) -> EvaluationResult:
        return EvaluationResult(
            self.metric_name,
            self.evaluator_version,
            value,
            status,
            metadata,
        )


def _parse_judge_response(response: Mapping[str, object]) -> _TimeRangeJudgment | None:
    required_keys = {
        "verdict",
        "reason_code",
        "correct_field_count",
        "incorrect_field_count",
        "not_assessable_field_count",
        "unexpected_constraint_count",
        "confidence",
    }
    if set(response) != required_keys:
        return None
    verdict = _text(response.get("verdict"))
    reason_code = _text(response.get("reason_code"))
    correct = _non_negative_int(response.get("correct_field_count"))
    incorrect = _non_negative_int(response.get("incorrect_field_count"))
    not_assessable = _non_negative_int(response.get("not_assessable_field_count"))
    unexpected = _non_negative_int(response.get("unexpected_constraint_count"))
    confidence_value = response.get("confidence")
    if (
        verdict not in {"assessed", "not_assessable"}
        or reason_code is None
        or correct is None
        or incorrect is None
        or not_assessable is None
        or unexpected is None
        or isinstance(confidence_value, bool)
        or not isinstance(confidence_value, (int, float))
        or not math.isfinite(float(confidence_value))
        or not 0 <= float(confidence_value) <= 1
    ):
        return None
    if verdict == "assessed":
        if reason_code != _ASSESSED_REASON or correct + incorrect == 0:
            return None
    elif (
        reason_code not in _NOT_ASSESSABLE_REASONS
        or correct != 0
        or incorrect != 0
        or unexpected != 0
    ):
        return None
    return _TimeRangeJudgment(
        verdict,
        reason_code,
        correct,
        incorrect,
        not_assessable,
        unexpected,
        float(confidence_value),
    )


def _parse_trace(
    trace: Mapping[str, object],
) -> (
    tuple[
        str | None,
        str,
        str,
        int,
        str,
        TimeRangeCandidate | None,
        dict[str, int],
        str | None,
    ]
    | None
):
    trace_id_value = trace.get("trace_id")
    mode = _text(trace.get("mode"))
    manual_version = _text(trace.get("manual_version"))
    tool_call_count = _non_negative_int(trace.get("tool_call_count"))
    reference_time_source = _text(trace.get("reference_time_source"))
    candidate_value = trace.get("candidate")
    diagnostics_value = trace.get("diagnostics")
    error_value = trace.get("normalization_error")
    if (
        (trace_id_value is not None and not isinstance(trace_id_value, str))
        or mode is None
        or manual_version is None
        or tool_call_count is None
        or reference_time_source not in {"provided", "canvas_meta", "event", "missing"}
        or (candidate_value is not None and not isinstance(candidate_value, TimeRangeCandidate))
        or not isinstance(diagnostics_value, Mapping)
        or (error_value is not None and _text(error_value) is None)
    ):
        return None
    candidate = candidate_value if isinstance(candidate_value, TimeRangeCandidate) else None
    diagnostic_names = (
        "unknown_schema_call_count",
        "malformed_arguments_call_count",
        "duplicate_call_definition_count",
        "malformed_event_count",
    )
    diagnostics: dict[str, int] = {}
    for name in diagnostic_names:
        count = _non_negative_int(diagnostics_value.get(name))
        if count is None:
            return None
        diagnostics[name] = count
    if candidate is not None and (
        len(candidate.tool_calls) + diagnostics["unknown_schema_call_count"] != tool_call_count
    ):
        return None
    return (
        trace_id_value.strip() if isinstance(trace_id_value, str) else None,
        mode,
        manual_version,
        tool_call_count,
        reference_time_source,
        candidate,
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
