"""以两阶段结构化 Judge 评估时间范围字段是否被正确传递。"""

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from collectors.parameter_value_accuracy import ParameterValueCandidate
from collectors.time_range_accuracy import TimeRangeCandidate
from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus
from evaluators.openai_compatible_judge_runtime import judge_error_metadata

_EXTRACTED_REASON = "temporal_requirements_extracted"
_NOT_ASSESSABLE_REASONS = frozenset(
    {
        "no_temporal_requirement",
        "missing_reference_time",
        "ambiguous_temporal_requirement",
        "insufficient_time_evidence",
    }
)
_FIELD_TYPES = frozenset(
    {
        "start_time",
        "end_time",
        "frequency",
        "value_type",
        "freshness",
        "reporting_period",
    }
)
_FIELD_REASON_CODES = {
    "correct": frozenset({"matched"}),
    "incorrect": frozenset(
        {
            "missing_expected_field",
            "contradicts_requirement",
            "incompatible_temporal_role",
            "unexpected_constraint",
        }
    ),
    "not_assessable": frozenset({"insufficient_time_evidence"}),
}
_FREQUENCY_SIGNAL = re.compile(
    r"日频|每日|按日|周频|每周|按周|月频|每月|按月|月度|季频|每季|按季|季度|年频|每年|按年|年度"
)
_VALUE_TYPE_SIGNAL = re.compile(r"历史|实际|预测|预期|未来|\d{2,4}[AE](?![A-Za-z])", re.I)
_REPORTING_PERIOD_SIGNAL = re.compile(r"年报|半年报|中报|一季报|三季报|季度报告")
_FRESHNESS_SIGNAL = re.compile(r"最新|最近一次|近期最新")
_ROLLING_SIGNAL = re.compile(r"最近半年|近半年|半年内|近\s*\d+\s*(?:年|个月|月|周|日)")
_RANGE_SIGNAL = re.compile(
    r"20\d{2}\s*年|(?<!\d)\d{2}\s*年|20\d{2}[-/.]\d{1,2}|以来|截至|从.+到|至|最近半年|近半年|半年内|近\s*\d+\s*(?:年|个月|月|周|日)"
)
_FOUR_DIGIT_YEAR = re.compile(r"(?<!\d)(20\d{2})\s*年")
_TWO_DIGIT_YEAR = re.compile(r"(?<!\d)(\d{2})\s*年")


@dataclass(frozen=True, slots=True)
class TimeRangeRequirementRequest:
    """仅包含用户时间需求；工具证据不得进入第一阶段以免反向污染分母。"""

    trace_id: str | None
    mode: str
    query: str
    reference_time: str | None


@dataclass(frozen=True, slots=True)
class TimeRangeExpectedField:
    """由程序分配稳定 ID 的内存预期字段，字段值不得持久化。"""

    field_id: str
    range_index: int
    field_type: str
    expected_value: str


@dataclass(frozen=True, slots=True)
class TimeRangeFieldMatchRequest:
    """将已冻结的用户预期字段与实际工具时间证据逐项比较。"""

    trace_id: str | None
    mode: str
    reference_time: str | None
    expected_fields: tuple[TimeRangeExpectedField, ...]
    tool_calls: tuple[ParameterValueCandidate, ...]
    manual_version: str


class TimeRangeJudge(Protocol):
    """时间范围两阶段 Judge 适配器的稳定接口。"""

    judge_name: str
    judge_version: str
    prompt_version: str

    def extract_requirements(
        self, request: TimeRangeRequirementRequest
    ) -> Mapping[str, object]: ...

    def match_fields(self, request: TimeRangeFieldMatchRequest) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class _RequirementExtraction:
    assessment: str
    reason_code: str
    fields: tuple[TimeRangeExpectedField, ...]
    confidence: float


@dataclass(frozen=True, slots=True)
class _FieldResult:
    field_id: str
    verdict: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class _FieldMatching:
    fields: tuple[_FieldResult, ...]
    unexpected_field_ids: tuple[str, ...]


class TimeRangeAccuracyEvaluator(BaseEvaluator):
    """先冻结 query 预期字段，再逐字段匹配工具证据并计算准确率。"""

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
        return "2.0.0"

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
        judge_validation_retry_count = 0
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

        extraction_request = TimeRangeRequirementRequest(
            trace_id=trace_id,
            mode=mode,
            query=candidate.query,
            reference_time=candidate.reference_time,
        )
        extraction: _RequirementExtraction | None = None
        for attempt in range(2):
            if attempt:
                judge_validation_retry_count += 1
                metadata["judge_validation_retry_count"] = judge_validation_retry_count
            try:
                extraction_response = self._judge.extract_requirements(extraction_request)
            except Exception as judge_error:
                return self._judge_failure(judge_error, metadata)
            extraction = _parse_requirement_extraction(extraction_response)
            if extraction is not None:
                break
        if extraction is None:
            metadata["reason"] = "invalid_judge_output"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        extraction = _reconcile_requirement_extraction(candidate.query, extraction)

        metadata.update(
            {
                "extraction_reason_code": extraction.reason_code,
                "extraction_confidence": extraction.confidence,
                "expected_field_count": len(extraction.fields),
                "expected_field_types": tuple(field.field_type for field in extraction.fields),
            }
        )
        if extraction.assessment == "not_assessable":
            metadata["reason"] = extraction.reason_code
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)

        if tool_call_count == 0:
            field_results = tuple(
                _FieldResult(field.field_id, "incorrect", "missing_expected_field")
                for field in extraction.fields
            )
            return self._computed_result(metadata, extraction.fields, field_results, ())
        if not candidate.tool_calls:
            metadata.update(
                {
                    "reason": "insufficient_time_evidence",
                    "not_assessable_field_count": len(extraction.fields),
                    "assessable_field_count": 0,
                }
            )
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)

        match_request = TimeRangeFieldMatchRequest(
            trace_id=trace_id,
            mode=mode,
            reference_time=candidate.reference_time,
            expected_fields=extraction.fields,
            tool_calls=candidate.tool_calls,
            manual_version=manual_version,
        )
        matching: _FieldMatching | None = None
        for attempt in range(2):
            if attempt:
                judge_validation_retry_count += 1
                metadata["judge_validation_retry_count"] = judge_validation_retry_count
            try:
                match_response = self._judge.match_fields(match_request)
            except Exception as judge_error:
                return self._judge_failure(judge_error, metadata)
            matching = _parse_field_matching(match_response, extraction.fields)
            if matching is not None:
                break
        if matching is None:
            metadata["reason"] = "invalid_judge_output"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        return self._computed_result(
            metadata,
            extraction.fields,
            matching.fields,
            matching.unexpected_field_ids,
        )

    def _computed_result(
        self,
        metadata: dict[str, object],
        expected_fields: tuple[TimeRangeExpectedField, ...],
        field_results: tuple[_FieldResult, ...],
        unexpected_field_ids: tuple[str, ...],
    ) -> EvaluationResult:
        correct = sum(field.verdict == "correct" for field in field_results)
        incorrect = sum(field.verdict == "incorrect" for field in field_results)
        not_assessable = sum(field.verdict == "not_assessable" for field in field_results)
        metadata.update(
            {
                "correct_field_count": correct,
                "incorrect_field_count": incorrect,
                "not_assessable_field_count": not_assessable,
                "unexpected_constraint_count": len(unexpected_field_ids),
                "assessable_field_count": correct + incorrect,
                "field_verdicts": tuple(
                    (field.field_id, field.verdict, field.reason_code)
                    for field in field_results
                ),
            }
        )
        if len(field_results) != len(expected_fields):
            metadata["reason"] = "invalid_judge_output"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if not_assessable:
            metadata["reason"] = "insufficient_time_evidence"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        return self._result(
            correct / (correct + incorrect),
            EvaluationStatus.COMPUTED,
            metadata,
        )

    def _judge_failure(
        self, judge_error: Exception, metadata: dict[str, object]
    ) -> EvaluationResult:
        reason, safe_metadata = judge_error_metadata(judge_error)
        metadata.update(safe_metadata)
        metadata["reason"] = reason
        return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)

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


def _parse_requirement_extraction(
    response: Mapping[str, object],
) -> _RequirementExtraction | None:
    if set(response) != {"assessment", "reason_code", "fields", "confidence"}:
        return None
    assessment = _text(response.get("assessment"))
    reason_code = _text(response.get("reason_code"))
    fields_value = response.get("fields")
    confidence = _confidence(response.get("confidence"))
    if (
        assessment not in {"assessable", "not_assessable"}
        or reason_code is None
        or not isinstance(fields_value, list)
        or confidence is None
    ):
        return None

    fields: list[TimeRangeExpectedField] = []
    seen_slots: set[tuple[int, str]] = set()
    for index, raw_field in enumerate(fields_value, start=1):
        if not isinstance(raw_field, Mapping) or set(raw_field) != {
            "range_index",
            "field_type",
            "expected_value",
        }:
            return None
        range_index = _non_negative_int(raw_field.get("range_index"))
        field_type = _text(raw_field.get("field_type"))
        expected_value = _text(raw_field.get("expected_value"))
        if (
            range_index is None
            or field_type not in _FIELD_TYPES
            or expected_value is None
            or (range_index, field_type) in seen_slots
        ):
            return None
        seen_slots.add((range_index, field_type))
        fields.append(
            TimeRangeExpectedField(
                field_id=f"time_field_{index:04d}",
                range_index=range_index,
                field_type=field_type,
                expected_value=expected_value,
            )
        )

    if assessment == "assessable":
        if reason_code != _EXTRACTED_REASON or not fields:
            return None
    elif reason_code not in _NOT_ASSESSABLE_REASONS or fields:
        return None
    return _RequirementExtraction(assessment, reason_code, tuple(fields), confidence)


def _reconcile_requirement_extraction(
    query: str, extraction: _RequirementExtraction
) -> _RequirementExtraction:
    """用高置信度词法约束修正 Judge 的明显漏提取和字段过度扩张。"""

    supports_range = _RANGE_SIGNAL.search(query) is not None
    supports_frequency = _FREQUENCY_SIGNAL.search(query) is not None
    supports_value_type = _VALUE_TYPE_SIGNAL.search(query) is not None
    supports_reporting_period = _REPORTING_PERIOD_SIGNAL.search(query) is not None
    supports_freshness = _FRESHNESS_SIGNAL.search(query) is not None
    has_rolling_range = _ROLLING_SIGNAL.search(query) is not None

    filtered = [
        field
        for field in extraction.fields
        if (
            (field.field_type not in {"start_time", "end_time"} or supports_range)
            and (field.field_type != "frequency" or supports_frequency)
            and (field.field_type != "value_type" or supports_value_type)
            and (field.field_type != "reporting_period" or supports_reporting_period)
            and (
                field.field_type != "freshness"
                or (supports_freshness and not has_rolling_range)
            )
        )
    ]
    present_types = {field.field_type for field in filtered}
    deterministic: list[tuple[int, str, str]] = []
    year = _single_explicit_year(query)
    if year is not None:
        if "start_time" not in present_types:
            deterministic.append((1, "start_time", f"{year:04d}-01-01"))
        if "end_time" not in present_types:
            deterministic.append((1, "end_time", f"{year:04d}-12-31"))
    if supports_freshness and not has_rolling_range and "freshness" not in present_types:
        freshness = "exclude_latest" if "最新一次的不算" in query else "latest"
        deterministic.append((0, "freshness", freshness))
    if supports_value_type and re.search(r"预测|预期|未来|\d{2,4}E(?![A-Za-z])", query, re.I):
        if "value_type" not in present_types:
            deterministic.append((0, "value_type", "forecast"))

    combined = [
        (field.range_index, field.field_type, field.expected_value) for field in filtered
    ]
    occupied = {(range_index, field_type) for range_index, field_type, _ in combined}
    combined.extend(
        field for field in deterministic if (field[0], field[1]) not in occupied
    )
    fields = tuple(
        TimeRangeExpectedField(
            field_id=f"time_field_{index:04d}",
            range_index=range_index,
            field_type=field_type,
            expected_value=expected_value,
        )
        for index, (range_index, field_type, expected_value) in enumerate(combined, start=1)
    )
    if fields:
        return _RequirementExtraction(
            "assessable",
            _EXTRACTED_REASON,
            fields,
            extraction.confidence,
        )
    reason = (
        extraction.reason_code
        if extraction.assessment == "not_assessable"
        else "no_temporal_requirement"
    )
    return _RequirementExtraction("not_assessable", reason, (), extraction.confidence)


def _single_explicit_year(query: str) -> int | None:
    years = {int(value) for value in _FOUR_DIGIT_YEAR.findall(query)}
    years.update(2000 + int(value) for value in _TWO_DIGIT_YEAR.findall(query))
    return next(iter(years)) if len(years) == 1 else None


def _parse_field_matching(
    response: Mapping[str, object],
    expected_fields: tuple[TimeRangeExpectedField, ...],
) -> _FieldMatching | None:
    expected_ids = {field.field_id for field in expected_fields}
    diagnostic_keys = {
        "missing_expected_field_ids",
        "unexpected_constraint_field_ids",
        "incompatible_temporal_role_field_ids",
    }
    response_keys = set(response)
    if not expected_ids.issubset(response_keys) or not response_keys.issubset(
        expected_ids | diagnostic_keys
    ):
        return None
    # 部分兼容端点在 JSON-object 回退模式下会省略空诊断数组；字段 verdict 完整时按空数组处理。
    raw_missing = response.get("missing_expected_field_ids", [])
    raw_unexpected = response.get("unexpected_constraint_field_ids", [])
    raw_incompatible = response.get("incompatible_temporal_role_field_ids", [])
    if (
        not isinstance(raw_missing, list)
        or not isinstance(raw_unexpected, list)
        or not isinstance(raw_incompatible, list)
    ):
        return None

    missing_ids = _field_id_list(raw_missing, expected_ids)
    unexpected_ids = _field_id_list(raw_unexpected, expected_ids)
    incompatible_ids = _field_id_list(raw_incompatible, expected_ids)
    if missing_ids is None or unexpected_ids is None or incompatible_ids is None:
        return None
    if (
        set(missing_ids) & set(unexpected_ids)
        or set(missing_ids) & set(incompatible_ids)
        or set(unexpected_ids) & set(incompatible_ids)
    ):
        return None

    results: list[_FieldResult] = []
    for field in expected_fields:
        verdict = _text(response.get(field.field_id))
        if verdict not in _FIELD_REASON_CODES:
            return None
        if field.field_id in missing_ids:
            reason_code = "missing_expected_field"
        elif field.field_id in unexpected_ids:
            reason_code = "unexpected_constraint"
        elif field.field_id in incompatible_ids:
            reason_code = "incompatible_temporal_role"
        elif verdict == "correct":
            reason_code = "matched"
        elif verdict == "incorrect":
            reason_code = "contradicts_requirement"
        else:
            reason_code = "insufficient_time_evidence"
        if reason_code not in _FIELD_REASON_CODES[verdict]:
            return None
        results.append(_FieldResult(field.field_id, verdict, reason_code))
    return _FieldMatching(tuple(results), unexpected_ids)


def _field_id_list(value: list[object], expected_ids: set[str]) -> tuple[str, ...] | None:
    result: list[str] = []
    for raw_id in value:
        field_id = _text(raw_id)
        if field_id not in expected_ids or field_id in result:
            return None
        result.append(field_id)
    return tuple(result)


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


def _confidence(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) and 0 <= parsed <= 1 else None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _non_negative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
