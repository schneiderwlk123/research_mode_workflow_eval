"""计算最终产物对上游应保留原子事实的信息保留率。"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from collectors.evidence_quality import EvidenceUnit
from collectors.information_retention import InformationRetentionCandidate
from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_REASON_CODES = {
    "retained": frozenset(
        {"explicitly_retained", "faithfully_paraphrased", "retained_by_simple_derivation"}
    ),
    "not_retained": frozenset(
        {
            "omitted_from_output",
            "materially_incomplete",
            "contradicted_in_output",
            "empty_final_output",
        }
    ),
    "not_assessable": frozenset({"ambiguous_fact", "ambiguous_output", "unsupported_fact_format"}),
    "excluded": frozenset(
        {"not_required_by_query", "duplicate_fact", "non_factual_or_instructional"}
    ),
}


@dataclass(frozen=True, slots=True)
class InformationRetentionJudgeRequest:
    """单次上游事实原子化与最终产物保留判断请求。"""

    trace_id: str | None
    mode: str
    query: str
    answer: str
    evidence_units: tuple[EvidenceUnit, ...]
    schema_version: str
    evidence_scope_version: str


class InformationRetentionJudge(Protocol):
    judge_name: str
    judge_version: str
    prompt_version: str

    def judge(self, request: InformationRetentionJudgeRequest) -> Mapping[str, object]: ...


class InformationRetentionEvaluator(BaseEvaluator):
    """严格校验原子事实来源与完整判定后计算 retained / required。"""

    def __init__(self, judge: InformationRetentionJudge) -> None:
        self._judge = judge
        self._judge_name = _required_string(judge.judge_name)
        self._judge_version = _required_string(judge.judge_version)
        self._prompt_version = _required_string(judge.prompt_version)

    @property
    def metric_name(self) -> str:
        return "information_retention_rate"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

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
        request = InformationRetentionJudgeRequest(
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
        facts = _parse_facts(response, candidate.evidence_units)
        if facts is None:
            metadata["reason"] = "invalid_judge_output"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        retained = sum(
            verdict == "retained" for fact_type, verdict in facts if fact_type == "required"
        )
        not_retained = sum(
            verdict == "not_retained" for fact_type, verdict in facts if fact_type == "required"
        )
        not_assessable = sum(
            verdict == "not_assessable" for fact_type, verdict in facts if fact_type == "required"
        )
        excluded = sum(fact_type == "excluded" for fact_type, _ in facts)
        required = retained + not_retained + not_assessable
        metadata.update(
            required_fact_count=required,
            retained_fact_count=retained,
            not_retained_fact_count=not_retained,
            not_assessable_fact_count=not_assessable,
            excluded_fact_count=excluded,
        )
        if not_assessable:
            metadata["reason"] = "incomplete_retention_judgment"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if required == 0:
            metadata["reason"] = "no_required_facts"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        return self._result(retained / required, EvaluationStatus.COMPUTED, metadata)

    def _result(
        self, value: float | None, status: EvaluationStatus, metadata: Mapping[str, object]
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _parse_facts(
    response: Mapping[str, object], evidence_units: tuple[EvidenceUnit, ...]
) -> tuple[tuple[str, str], ...] | None:
    raw_facts = response.get("facts")
    if set(response) != {"facts"} or not isinstance(raw_facts, list):
        return None
    unit_ids = {unit.unit_id for unit in evidence_units}
    fact_ids: set[str] = set()
    parsed: list[tuple[str, str]] = []
    for raw in raw_facts:
        if not isinstance(raw, Mapping) or set(raw) != {
            "fact_id",
            "fact_type",
            "verdict",
            "reason_code",
            "source_unit_ids",
            "confidence",
        }:
            return None
        fact_id = _text(raw.get("fact_id"))
        fact_type = raw.get("fact_type")
        verdict = raw.get("verdict")
        reason_code = raw.get("reason_code")
        source_ids = raw.get("source_unit_ids")
        confidence = raw.get("confidence")
        if (
            fact_id is None
            or fact_id in fact_ids
            or fact_type not in {"required", "excluded"}
            or not isinstance(verdict, str)
            or verdict not in _REASON_CODES
            or not isinstance(reason_code, str)
            or reason_code not in _REASON_CODES[verdict]
            or not isinstance(source_ids, list)
            or not source_ids
            or any(not isinstance(unit_id, str) for unit_id in source_ids)
            or len(set(source_ids)) != len(source_ids)
            or not set(source_ids).issubset(unit_ids)
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            return None
        if fact_type == "excluded" and verdict != "excluded":
            return None
        if fact_type == "required" and verdict == "excluded":
            return None
        fact_ids.add(fact_id)
        parsed.append((fact_type, verdict))
    return tuple(parsed)


def _parse_trace(
    trace: Mapping[str, object],
) -> (
    tuple[
        str | None,
        str,
        str,
        str,
        InformationRetentionCandidate | None,
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
    candidate = trace.get("candidate")
    raw_diagnostics = trace.get("diagnostics")
    error = trace.get("normalization_error")
    if (
        (trace_id is not None and not isinstance(trace_id, str))
        or mode is None
        or schema_version is None
        or scope_version is None
        or unit_count is None
        or (candidate is not None and not isinstance(candidate, InformationRetentionCandidate))
        or not isinstance(raw_diagnostics, Mapping)
        or (error is not None and _text(error) is None)
    ):
        return None
    if isinstance(candidate, InformationRetentionCandidate) and not _valid_candidate(
        candidate, unit_count
    ):
        return None
    names = (
        "malformed_event_count",
        "out_of_scope_tool_call_count",
        "evidence_call_count",
        "successful_evidence_response_count",
        "unsegmentable_evidence_response_count",
        "duplicate_evidence_unit_count",
        "duplicate_call_definition_count",
    )
    diagnostics: dict[str, int] = {}
    for name in names:
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


def _valid_candidate(candidate: InformationRetentionCandidate, unit_count: int) -> bool:
    return (
        _text(candidate.query) is not None
        and isinstance(candidate.answer, str)
        and bool(candidate.evidence_units)
        and len(candidate.evidence_units) == unit_count
        and all(isinstance(unit, EvidenceUnit) for unit in candidate.evidence_units)
    )


def _required_string(value: object) -> str:
    normalized = _text(value)
    if normalized is None:
        raise ValueError("Judge 名称、版本和 prompt 版本必须是非空字符串")
    return normalized


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _non_negative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
