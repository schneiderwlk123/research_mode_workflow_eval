"""计算最终回答中的直接召回数值与来源证据的一致率。"""

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

from collectors.evidence_quality import EvidenceUnit
from collectors.numeric_consistency import NumericConsistencyCandidate
from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_DIRECT_REASONS = frozenset({"aligned_evidence", "no_matching_retrieved_value"})
_EXCLUDED_REASONS = frozenset(
    {"user_provided_number", "date_or_identifier", "derived_value", "non_factual_number"}
)
_NOT_ASSESSABLE_REASONS = frozenset(
    {
        "ambiguous_numeric_claim",
        "ambiguous_unit",
        "conflicting_evidence_values",
        "incomplete_evidence",
    }
)


@dataclass(frozen=True, slots=True)
class NumericConsistencyJudgeRequest:
    """最终回答数值与来源证据的单会话 Judge 请求。"""

    trace_id: str | None
    mode: str
    query: str
    answer: str
    evidence_units: tuple[EvidenceUnit, ...]
    schema_version: str
    evidence_scope_version: str


class NumericConsistencyJudge(Protocol):
    judge_name: str
    judge_version: str
    prompt_version: str

    def judge(self, request: NumericConsistencyJudgeRequest) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class _NumericJudgment:
    claim_type: str
    reason_code: str
    answer_value: Decimal | None
    evidence_value: Decimal | None
    answer_scale: Decimal | None
    evidence_scale: Decimal | None
    answer_decimal_places: int | None


class RetrievedNumericConsistencyEvaluator(BaseEvaluator):
    """Judge 负责语义对齐，确定性逻辑负责单位倍率、舍入和数值比较。"""

    def __init__(self, judge: NumericConsistencyJudge) -> None:
        self._judge = judge
        self._judge_name = _required_string(judge.judge_name)
        self._judge_version = _required_string(judge.judge_version)
        self._prompt_version = _required_string(judge.prompt_version)

    @property
    def metric_name(self) -> str:
        return "retrieved_numeric_consistency_rate"

    @property
    def evaluator_version(self) -> str:
        return "1.1.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        trace_id, mode, schema_version, scope_version, candidate, unit_count, diagnostics, error = (
            parsed
        )
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            "schema_version": schema_version,
            "evidence_scope_version": scope_version,
            "evidence_unit_count": unit_count,
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
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)

        request = NumericConsistencyJudgeRequest(
            trace_id,
            mode,
            candidate.query,
            candidate.answer,
            candidate.evidence_units,
            schema_version,
            scope_version,
        )
        try:
            response = self._judge.judge(request)
        except Exception:
            metadata["reason"] = "judge_failure"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        judgments = _parse_claims(response, candidate.evidence_units)
        if judgments is None:
            metadata["reason"] = "invalid_judge_output"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)

        consistent = 0
        inconsistent = 0
        excluded = 0
        not_assessable = 0
        for judgment in judgments:
            if judgment.claim_type == "excluded":
                excluded += 1
            elif judgment.claim_type == "not_assessable":
                not_assessable += 1
            elif judgment.reason_code == "no_matching_retrieved_value":
                inconsistent += 1
            elif _values_match(judgment):
                consistent += 1
            else:
                inconsistent += 1

        direct = consistent + inconsistent
        metadata.update(
            direct_numeric_claim_count=direct,
            consistent_numeric_claim_count=consistent,
            inconsistent_numeric_claim_count=inconsistent,
            not_assessable_numeric_claim_count=not_assessable,
            excluded_numeric_claim_count=excluded,
        )
        if not_assessable:
            metadata["reason"] = "incomplete_numeric_judgment"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if direct == 0:
            metadata["reason"] = "no_retrieved_numeric_claims"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        return self._result(consistent / direct, EvaluationStatus.COMPUTED, metadata)

    def _result(
        self, value: float | None, status: EvaluationStatus, metadata: Mapping[str, object]
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _values_match(judgment: _NumericJudgment) -> bool:
    if (
        judgment.answer_value is None
        or judgment.evidence_value is None
        or judgment.answer_scale is None
        or judgment.evidence_scale is None
        or judgment.answer_decimal_places is None
    ):
        return False
    answer = judgment.answer_value * judgment.answer_scale
    evidence = judgment.evidence_value * judgment.evidence_scale
    tolerance = Decimal("0.5") * (Decimal(10) ** -judgment.answer_decimal_places)
    tolerance *= judgment.answer_scale
    return abs(answer - evidence) <= tolerance


def _parse_claims(
    response: Mapping[str, object], evidence_units: tuple[EvidenceUnit, ...]
) -> tuple[_NumericJudgment, ...] | None:
    raw_claims = response.get("claims")
    if set(response) != {"claims"} or not isinstance(raw_claims, list):
        return None
    unit_ids = {unit.unit_id for unit in evidence_units}
    claim_ids: set[str] = set()
    parsed: list[_NumericJudgment] = []
    expected_keys = {
        "claim_id",
        "claim_type",
        "reason_code",
        "answer_value",
        "evidence_value",
        "canonical_unit",
        "answer_scale",
        "evidence_scale",
        "answer_decimal_places",
        "evidence_unit_ids",
        "confidence",
    }
    for raw in raw_claims:
        if not isinstance(raw, Mapping) or set(raw) != expected_keys:
            return None
        claim_id = _text(raw.get("claim_id"))
        claim_type = raw.get("claim_type")
        reason_code = raw.get("reason_code")
        evidence_ids = raw.get("evidence_unit_ids")
        confidence = raw.get("confidence")
        if (
            claim_id is None
            or claim_id in claim_ids
            or claim_type not in {"direct_retrieval", "excluded", "not_assessable"}
            or not isinstance(reason_code, str)
            or not isinstance(evidence_ids, list)
            or any(not isinstance(unit_id, str) for unit_id in evidence_ids)
            or len(set(evidence_ids)) != len(evidence_ids)
            or not set(evidence_ids).issubset(unit_ids)
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            return None
        judgment = _parse_claim(raw, str(claim_type), reason_code, evidence_ids)
        if judgment is None:
            return None
        claim_ids.add(claim_id)
        parsed.append(judgment)
    return tuple(parsed)


def _parse_claim(
    raw: Mapping[str, object], claim_type: str, reason_code: str, evidence_ids: list[object]
) -> _NumericJudgment | None:
    if claim_type == "excluded":
        if reason_code not in _EXCLUDED_REASONS or not _empty_numeric_fields(raw, evidence_ids):
            return None
        return _NumericJudgment(claim_type, reason_code, None, None, None, None, None)
    if claim_type == "not_assessable":
        if reason_code not in _NOT_ASSESSABLE_REASONS or not _empty_numeric_fields(
            raw, evidence_ids
        ):
            return None
        return _NumericJudgment(claim_type, reason_code, None, None, None, None, None)
    if reason_code not in _DIRECT_REASONS:
        return None
    answer_value_text = raw.get("answer_value")
    answer_value = _decimal(answer_value_text)
    answer_scale = _positive_decimal(raw.get("answer_scale"))
    decimal_places = _non_negative_int(raw.get("answer_decimal_places"))
    canonical_unit = _text(raw.get("canonical_unit"))
    if (
        answer_value is None
        or answer_scale is None
        or decimal_places is None
        or decimal_places > 18
        or canonical_unit is None
        or not isinstance(answer_value_text, str)
        or _display_decimal_places(answer_value_text) != decimal_places
    ):
        return None
    if reason_code == "no_matching_retrieved_value":
        if (
            raw.get("evidence_value") is not None
            or raw.get("evidence_scale") is not None
            or evidence_ids
        ):
            return None
        return _NumericJudgment(
            claim_type, reason_code, answer_value, None, answer_scale, None, decimal_places
        )
    evidence_value = _decimal(raw.get("evidence_value"))
    evidence_scale = _positive_decimal(raw.get("evidence_scale"))
    if evidence_value is None or evidence_scale is None or not evidence_ids:
        return None
    return _NumericJudgment(
        claim_type,
        reason_code,
        answer_value,
        evidence_value,
        answer_scale,
        evidence_scale,
        decimal_places,
    )


def _empty_numeric_fields(raw: Mapping[str, object], evidence_ids: list[object]) -> bool:
    return not evidence_ids and all(
        raw.get(name) is None
        for name in (
            "answer_value",
            "evidence_value",
            "canonical_unit",
            "answer_scale",
            "evidence_scale",
            "answer_decimal_places",
        )
    )


def _parse_trace(
    trace: Mapping[str, object],
) -> (
    tuple[
        str | None,
        str,
        str,
        str,
        NumericConsistencyCandidate | None,
        int,
        dict[str, int],
        str | None,
    ]
    | None
):
    trace_id = trace.get("trace_id")
    mode = _text(trace.get("mode"))
    schema_version = _text(trace.get("schema_version"))
    scope_version = _text(trace.get("evidence_scope_version"))
    unit_count = _non_negative_int(trace.get("evidence_unit_count"))
    candidate_value = trace.get("candidate")
    raw_diagnostics = trace.get("diagnostics")
    error = trace.get("normalization_error")
    if (
        (trace_id is not None and not isinstance(trace_id, str))
        or mode is None
        or schema_version is None
        or scope_version is None
        or unit_count is None
        or (
            candidate_value is not None
            and not isinstance(candidate_value, NumericConsistencyCandidate)
        )
        or not isinstance(raw_diagnostics, Mapping)
        or (error is not None and _text(error) is None)
    ):
        return None
    candidate = (
        candidate_value if isinstance(candidate_value, NumericConsistencyCandidate) else None
    )
    if candidate is not None and not _valid_candidate(candidate, unit_count):
        return None
    diagnostics: dict[str, int] = {}
    for name in (
        "malformed_event_count",
        "out_of_scope_tool_call_count",
        "evidence_call_count",
        "successful_evidence_response_count",
        "unsegmentable_evidence_response_count",
        "duplicate_evidence_unit_count",
        "duplicate_call_definition_count",
        "duplicate_result_observation_count",
        "orphan_result_count",
    ):
        value = _non_negative_int(raw_diagnostics.get(name))
        if value is None:
            return None
        diagnostics[name] = value
    return (
        trace_id,
        mode,
        schema_version,
        scope_version,
        candidate,
        unit_count,
        diagnostics,
        _text(error),
    )


def _valid_candidate(candidate: NumericConsistencyCandidate, unit_count: int) -> bool:
    if _text(candidate.query) is None or _text(candidate.answer) is None:
        return False
    if len(candidate.evidence_units) != unit_count:
        return False
    seen: set[str] = set()
    for unit in candidate.evidence_units:
        if _text(unit.unit_id) is None or unit.unit_id in seen or _text(unit.tool_name) is None:
            return False
        try:
            json.loads(unit.content)
        except (json.JSONDecodeError, TypeError):
            return False
        seen.add(unit.unit_id)
    return True


def _decimal(value: object) -> Decimal | None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > 64
        or "e" in value.lower()
    ):
        return None
    try:
        parsed = Decimal(value.strip())
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _display_decimal_places(value: str) -> int:
    normalized = value.strip().lstrip("+-")
    return len(normalized.rsplit(".", 1)[1]) if "." in normalized else 0


def _positive_decimal(value: object) -> Decimal | None:
    parsed = _decimal(value)
    return parsed if parsed is not None and parsed > 0 else None


def _required_string(value: object) -> str:
    parsed = _text(value)
    if parsed is None:
        raise ValueError("Judge 版本标识必须是非空字符串")
    return parsed


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _non_negative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
