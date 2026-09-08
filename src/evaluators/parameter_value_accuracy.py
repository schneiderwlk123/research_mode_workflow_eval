"""使用可注入 Judge 评估工具参数与用户 query 的语义匹配程度。"""

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeGuard

from collectors.parameter_value_accuracy import ParameterValueCandidate
from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus
from evaluators.openai_compatible_judge_runtime import judge_error_metadata

_ALLOWED_REASON_CODES_BY_VERDICT = {
    "correct": frozenset(
        {
            "direct_match",
            "logically_derived",
            "normalized_equivalent",
            "query_consistent",
        }
    ),
    "incorrect": frozenset(
        {
            "contradicts_query",
            "irrelevant_constraint",
            "missing_query_required",
            "wrong_entity",
            "wrong_time_range",
            "wrong_unit",
            "wrong_value",
            "query_inconsistent",
        }
    ),
    "not_assessable": frozenset(
        {
            "operational_default",
            "query_underspecified",
            "requires_external_context",
            "insufficient_query_evidence",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class ParameterValueJudgeRequest:
    """单次 Judge 请求；调用方不得记录其中的 query 和 arguments。"""

    trace_id: str | None
    mode: str
    call_id: str
    query: str
    tool_name: str
    tool_description: str
    parameter_descriptions: Mapping[str, str]
    arguments: Mapping[str, object]


class ParameterValueJudge(Protocol):
    """模型适配器必须实现的稳定接口。"""

    judge_name: str
    judge_version: str
    prompt_version: str

    def judge(self, request: ParameterValueJudgeRequest) -> Mapping[str, object]:
        """返回经过结构化输出约束的逐字段判定。"""


@dataclass(frozen=True, slots=True)
class _CallCounts:
    correct: int
    incorrect: int
    not_assessable: int
    reason_codes: tuple[str, ...]


class ParameterValueAccuracyEvaluator(BaseEvaluator):
    """聚合 Judge 的逐字段三态判定，并隔离单个调用失败。"""

    def __init__(self, judge: ParameterValueJudge) -> None:
        self._judge = judge
        self._judge_name = _required_string(judge.judge_name, "judge_name")
        self._judge_version = _required_string(judge.judge_version, "judge_version")
        self._prompt_version = _required_string(judge.prompt_version, "prompt_version")

    @property
    def metric_name(self) -> str:
        return "parameter_value_accuracy_rate"

    @property
    def evaluator_version(self) -> str:
        return "1.4.0"

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
            total_count,
            eligible_count,
            candidates,
            diagnostics,
            normalization_error,
        ) = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            "manual_version": manual_version,
            "total_call_count": total_count,
            "eligible_call_count": eligible_count,
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
        if total_count == 0:
            metadata["reason"] = "no_tool_calls"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if eligible_count == 0:
            metadata["reason"] = "no_schema_covered_calls"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)

        correct_count = 0
        incorrect_count = 0
        not_assessable_count = 0
        judged_call_count = 0
        judge_failed_call_count = 0
        judge_failure_reasons: Counter[str] = Counter()
        reason_codes: Counter[str] = Counter()
        for candidate in candidates:
            request = ParameterValueJudgeRequest(
                trace_id=trace_id,
                mode=mode,
                call_id=candidate.call_id,
                query=candidate.query,
                tool_name=candidate.tool_name,
                tool_description=candidate.tool_description,
                parameter_descriptions=candidate.parameter_descriptions,
                arguments=candidate.arguments,
            )
            try:
                response = self._judge.judge(request)
            except Exception as judge_error:
                # Judge 是外部边界；单调用失败只进入安全计数，不能泄露响应或参数正文。
                reason, _ = judge_error_metadata(judge_error)
                judge_failure_reasons[reason] += 1
                judge_failed_call_count += 1
                continue
            try:
                counts = _parse_judge_response(response, candidate)
            except ValueError:
                judge_failure_reasons["invalid_judge_output"] += 1
                judge_failed_call_count += 1
                continue
            judged_call_count += 1
            correct_count += counts.correct
            incorrect_count += counts.incorrect
            not_assessable_count += counts.not_assessable
            reason_codes.update(counts.reason_codes)

        assessable_field_count = correct_count + incorrect_count
        metadata.update(
            {
                "judged_call_count": judged_call_count,
                "judge_failed_call_count": judge_failed_call_count,
                "correct_field_count": correct_count,
                "incorrect_field_count": incorrect_count,
                "not_assessable_field_count": not_assessable_count,
                "assessable_field_count": assessable_field_count,
                "judge_reason_code_counts": dict(sorted(reason_codes.items())),
                "judge_failure_reason_counts": dict(sorted(judge_failure_reasons.items())),
            }
        )
        if judge_failed_call_count == eligible_count:
            metadata["reason"] = (
                next(iter(judge_failure_reasons))
                if len(judge_failure_reasons) == 1
                else "judge_failure"
            )
            return self._result(None, EvaluationStatus.ERROR, metadata)
        if assessable_field_count == 0:
            metadata["reason"] = "no_assessable_fields"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        return self._result(
            correct_count / assessable_field_count,
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
            metric_name=self.metric_name,
            evaluator_version=self.evaluator_version,
            value=value,
            status=status,
            metadata=metadata,
        )


def _parse_judge_response(
    response: Mapping[str, object], candidate: ParameterValueCandidate
) -> _CallCounts:
    raw_fields = response.get("fields")
    if not _is_sequence(raw_fields):
        raise ValueError("Judge fields 必须是数组")

    actual_paths = _argument_field_paths(candidate.arguments)
    seen_paths: set[str] = set()
    correct = 0
    incorrect = 0
    not_assessable = 0
    reason_codes: list[str] = []
    for raw_field in raw_fields:
        if not isinstance(raw_field, Mapping):
            raise ValueError("Judge 字段判定必须是对象")
        field_path = _non_empty_string(raw_field.get("field_path"))
        verdict = _non_empty_string(raw_field.get("verdict"))
        reason_code = _non_empty_string(raw_field.get("reason_code"))
        confidence = raw_field.get("confidence")
        if (
            field_path is None
            or verdict not in {"correct", "incorrect", "not_assessable"}
            or reason_code is None
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
            or field_path in seen_paths
        ):
            raise ValueError("Judge 字段判定结构非法")
        if reason_code not in _ALLOWED_REASON_CODES_BY_VERDICT[verdict]:
            raise ValueError("Judge reason_code 不在安全白名单中")
        if field_path not in actual_paths:
            if (
                field_path not in candidate.schema_field_paths
                or verdict != "incorrect"
                or reason_code != "missing_query_required"
            ):
                raise ValueError("Judge 返回了无法定位的字段")
        seen_paths.add(field_path)
        reason_codes.append(reason_code)
        if verdict == "correct":
            correct += 1
        elif verdict == "incorrect":
            incorrect += 1
        else:
            not_assessable += 1
    if not actual_paths.issubset(seen_paths):
        raise ValueError("Judge 未覆盖全部实际参数字段")
    return _CallCounts(correct, incorrect, not_assessable, tuple(reason_codes))


def _argument_field_paths(arguments: Mapping[str, object]) -> frozenset[str]:
    return frozenset(parameter_argument_field_values(arguments))


def parameter_argument_field_values(arguments: Mapping[str, object]) -> dict[str, object]:
    """按统一口径展开对象叶子并保留数组整体，供 evaluator 与 Judge 适配器复用。"""

    fields: dict[str, object] = {}

    def visit(value: object, prefix: str) -> None:
        if isinstance(value, Mapping) and value:
            for key, item in value.items():
                child = f"{prefix}.{key}" if prefix else str(key)
                visit(item, child)
            return
        fields[prefix] = value

    for name, value in arguments.items():
        visit(value, str(name))
    return fields


def _parse_trace(
    trace: Mapping[str, object],
) -> (
    tuple[
        str | None,
        str,
        str,
        int,
        int,
        tuple[ParameterValueCandidate, ...],
        dict[str, int],
        str | None,
    ]
    | None
):
    trace_id_value = trace.get("trace_id")
    mode = _non_empty_string(trace.get("mode"))
    manual_version = _non_empty_string(trace.get("manual_version"))
    total_count = _non_negative_int(trace.get("total_call_count"))
    eligible_count = _non_negative_int(trace.get("eligible_call_count"))
    raw_candidates = trace.get("candidates")
    diagnostics_value = trace.get("diagnostics")
    normalization_error = trace.get("normalization_error")
    if (
        (trace_id_value is not None and not isinstance(trace_id_value, str))
        or mode is None
        or manual_version is None
        or total_count is None
        or eligible_count is None
        or eligible_count > total_count
        or not _is_sequence(raw_candidates)
        or not isinstance(diagnostics_value, Mapping)
        or (normalization_error is not None and _non_empty_string(normalization_error) is None)
    ):
        return None
    if len(raw_candidates) != eligible_count or not all(
        isinstance(candidate, ParameterValueCandidate) for candidate in raw_candidates
    ):
        return None
    candidates = tuple(
        candidate for candidate in raw_candidates if isinstance(candidate, ParameterValueCandidate)
    )

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
    if diagnostics["unknown_schema_call_count"] + eligible_count > total_count:
        return None
    return (
        trace_id_value.strip() if isinstance(trace_id_value, str) else None,
        mode,
        manual_version,
        total_count,
        eligible_count,
        candidates,
        diagnostics,
        _non_empty_string(normalization_error),
    )


def _required_string(value: object, name: str) -> str:
    parsed = _non_empty_string(value)
    if parsed is None:
        raise ValueError(f"{name} 必须是非空字符串")
    return parsed


def _non_empty_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
