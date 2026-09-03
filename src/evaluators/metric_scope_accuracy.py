"""两阶段评估指标名称及其口径字段。"""

import hashlib
import math
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, TypeAlias

from collectors.metric_scope_accuracy import MetricScopeCandidate
from collectors.parameter_value_accuracy import ParameterValueCandidate
from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_SCOPE_FIELDS = frozenset({"unit", "currency", "frequency", "statistical_scope", "data_attribute"})
_REASONS = {
    "correct": frozenset({"direct_match", "normalized_equivalent", "schema_default_equivalent"}),
    "incorrect": frozenset(
        {
            "missing_required_metric",
            "wrong_metric",
            "wrong_unit",
            "wrong_currency",
            "wrong_frequency",
            "wrong_statistical_scope",
            "wrong_data_attribute",
        }
    ),
    "not_assessable": frozenset({"insufficient_tool_evidence", "requires_external_context"}),
}


@dataclass(frozen=True, slots=True)
class MetricRequirement:
    requirement_id: str
    metric_name: str
    fields: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))


@dataclass(frozen=True, slots=True)
class MetricRequirementExtractionRequest:
    trace_id: str | None
    mode: str
    query: str


@dataclass(frozen=True, slots=True)
class MetricScopeAssessmentRequest:
    trace_id: str | None
    mode: str
    requirements: tuple[MetricRequirement, ...]
    tool_calls: tuple[ParameterValueCandidate, ...]
    manual_version: str


class MetricScopeJudge(Protocol):
    judge_name: str
    judge_version: str
    prompt_version: str

    def extract_requirements(
        self, request: MetricRequirementExtractionRequest
    ) -> Mapping[str, object]: ...

    def assess(self, request: MetricScopeAssessmentRequest) -> Mapping[str, object]: ...


_ParsedTrace: TypeAlias = tuple[
    str | None,
    str,
    str,
    int,
    MetricScopeCandidate | None,
    dict[str, int],
    str | None,
]
_AssessmentCounts: TypeAlias = tuple[int, int, int, int, float]


class MetricScopeAccuracyEvaluator(BaseEvaluator):
    def __init__(self, judge: MetricScopeJudge) -> None:
        self._judge = judge
        self._cache: dict[str, tuple[MetricRequirement, ...] | str] = {}
        self._versions = tuple(
            _required(x) for x in (judge.judge_name, judge.judge_version, judge.prompt_version)
        )

    @property
    def metric_name(self) -> str:
        return "metric_scope_extraction_accuracy"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        trace_id, mode, manual, total, candidate, diagnostics, error = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            "manual_version": manual,
            "tool_call_count": total,
            "judge_name": self._versions[0],
            "judge_version": self._versions[1],
            "prompt_version": self._versions[2],
            **diagnostics,
        }
        if mode not in {"chat", "research"}:
            metadata["reason"] = "unsupported_trace_mode"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if error:
            metadata["reason"] = error
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if candidate is None:
            return self._result(
                None, EvaluationStatus.ERROR, {**metadata, "reason": "invalid_normalized_trace"}
            )
        key = hashlib.sha256(
            " ".join(unicodedata.normalize("NFKC", candidate.query).split()).encode()
        ).hexdigest()
        cached = key in self._cache
        requirement_result = self._cache.get(key)
        if requirement_result is None:
            try:
                raw = self._judge.extract_requirements(
                    MetricRequirementExtractionRequest(trace_id, mode, candidate.query)
                )
            except Exception:
                metadata["reason"] = "judge_failure"
                return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
            requirement_result = _parse_requirements(raw)
            if requirement_result is None:
                metadata["reason"] = "invalid_requirement_output"
                return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
            self._cache[key] = requirement_result
        metadata["requirement_cache_hit"] = cached
        if isinstance(requirement_result, str):
            metadata["reason"] = requirement_result
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        expected = sum(1 + len(r.fields) for r in requirement_result)
        if not candidate.tool_calls:
            metadata.update(
                expected_field_count=expected,
                correct_field_count=0,
                incorrect_field_count=expected,
                not_assessable_field_count=0,
                unexpected_field_count=0,
                assessment_judge_called=False,
            )
            return self._result(0.0, EvaluationStatus.COMPUTED, metadata)
        try:
            raw_assessment = self._judge.assess(
                MetricScopeAssessmentRequest(
                    trace_id, mode, requirement_result, candidate.tool_calls, manual
                )
            )
        except Exception:
            metadata["reason"] = "judge_failure"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        counts = _parse_assessment(raw_assessment, requirement_result)
        if counts is None:
            metadata["reason"] = "invalid_assessment_output"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        correct, incorrect, not_assessable, unexpected, confidence = counts
        denominator = correct + incorrect + unexpected
        metadata.update(
            expected_field_count=expected,
            correct_field_count=correct,
            incorrect_field_count=incorrect,
            not_assessable_field_count=not_assessable,
            unexpected_field_count=unexpected,
            assessment_judge_called=True,
            judge_confidence=confidence,
        )
        if denominator == 0:
            metadata["reason"] = "insufficient_metric_evidence"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        return self._result(correct / denominator, EvaluationStatus.COMPUTED, metadata)

    def _result(
        self,
        value: float | None,
        status: EvaluationStatus,
        metadata: Mapping[str, object],
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _parse_requirements(raw: Mapping[str, object]) -> tuple[MetricRequirement, ...] | str | None:
    if set(raw) != {"verdict", "reason_code", "requirements", "confidence"} or not _confidence(
        raw.get("confidence")
    ):
        return None
    verdict = _text(raw.get("verdict"))
    reason = _text(raw.get("reason_code"))
    items = raw.get("requirements")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        return None
    if verdict == "not_assessable":
        return (
            reason
            if reason in {"no_metric_requirement", "ambiguous_metric_requirement"} and not items
            else None
        )
    if verdict != "extracted" or reason != "metric_requirements_extracted" or not items:
        return None
    result = []
    ids = set()
    for item in items:
        if not isinstance(item, Mapping) or set(item) != {
            "requirement_id",
            "metric_name",
            "fields",
        }:
            return None
        rid = _text(item.get("requirement_id"))
        name = _text(item.get("metric_name"))
        fields = item.get("fields")
        if rid is None or name is None or rid in ids or not isinstance(fields, Mapping):
            return None
        parsed = {k: _text(v) for k, v in fields.items()}
        if any(k not in _SCOPE_FIELDS or v is None for k, v in parsed.items()):
            return None
        ids.add(rid)
        result.append(
            MetricRequirement(rid, name, {k: v for k, v in parsed.items() if v is not None})
        )
    return tuple(result)


def _parse_assessment(
    raw: Mapping[str, object], requirements: tuple[MetricRequirement, ...]
) -> _AssessmentCounts | None:
    if set(raw) != {"fields", "unexpected_field_count", "confidence"} or not _confidence(
        raw.get("confidence")
    ):
        return None
    fields = raw.get("fields")
    unexpected = _nonneg(raw.get("unexpected_field_count"))
    if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes)) or unexpected is None:
        return None
    expected = {(r.requirement_id, "metric_name") for r in requirements} | {
        (r.requirement_id, k) for r in requirements for k in r.fields
    }
    seen = set()
    counts = {"correct": 0, "incorrect": 0, "not_assessable": 0}
    for field in fields:
        if not isinstance(field, Mapping) or set(field) != {
            "requirement_id",
            "field_type",
            "verdict",
            "reason_code",
            "confidence",
        }:
            return None
        key = (_text(field.get("requirement_id")), _text(field.get("field_type")))
        verdict = _text(field.get("verdict"))
        reason = _text(field.get("reason_code"))
        if (
            key not in expected
            or key in seen
            or verdict not in _REASONS
            or reason not in _REASONS[verdict]
            or not _confidence(field.get("confidence"))
        ):
            return None
        seen.add(key)
        counts[verdict] += 1
    if seen != expected:
        return None
    confidence_value = raw["confidence"]
    assert isinstance(confidence_value, (int, float)) and not isinstance(confidence_value, bool)
    return (
        counts["correct"],
        counts["incorrect"],
        counts["not_assessable"],
        unexpected,
        float(confidence_value),
    )


def _parse_trace(trace: Mapping[str, object]) -> _ParsedTrace | None:
    candidate = trace.get("candidate")
    diagnostics = trace.get("diagnostics")
    total = _nonneg(trace.get("tool_call_count"))
    if (
        not isinstance(diagnostics, Mapping)
        or total is None
        or (candidate is not None and not isinstance(candidate, MetricScopeCandidate))
    ):
        return None
    mode = _text(trace.get("mode"))
    manual = _text(trace.get("manual_version"))
    error = trace.get("normalization_error")
    trace_id = trace.get("trace_id")
    names = (
        "unknown_schema_call_count",
        "malformed_arguments_call_count",
        "duplicate_call_definition_count",
        "malformed_event_count",
    )
    parsed = {n: _nonneg(diagnostics.get(n)) for n in names}
    if (
        mode is None
        or manual is None
        or any(v is None for v in parsed.values())
        or (error is not None and _text(error) is None)
    ):
        return None
    if (
        isinstance(candidate, MetricScopeCandidate)
        and len(candidate.tool_calls) + int(parsed["unknown_schema_call_count"] or 0) != total
    ):
        return None
    return (
        trace_id if isinstance(trace_id, str) else None,
        mode,
        manual,
        total,
        candidate,
        {k: int(v or 0) for k, v in parsed.items()},
        _text(error),
    )


def _text(v: object) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _nonneg(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _confidence(v: object) -> bool:
    return (
        not isinstance(v, bool)
        and isinstance(v, (int, float))
        and math.isfinite(float(v))
        and 0 <= float(v) <= 1
    )


def _required(v: object) -> str:
    result = _text(v)
    if result is None:
        raise ValueError("Judge 版本标识不能为空")
    return result
