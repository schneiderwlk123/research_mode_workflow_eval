"""计算最终回答中可验证原子陈述的证据支持率。"""

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from collectors.evidence_quality import EvidenceUnit
from collectors.statement_support import StatementSupportCandidate
from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus
from evaluators.openai_compatible_judge_runtime import judge_error_metadata

_REASON_CODES = {
    "supported": frozenset(
        {"directly_supported", "entailed_by_combination", "supported_by_simple_derivation"}
    ),
    "unsupported": frozenset(
        {
            "no_evidence",
            "no_supporting_evidence",
            "contradicted_by_evidence",
            "insufficient_support",
        }
    ),
    "not_assessable": frozenset(
        {"ambiguous_claim", "incomplete_evidence", "unsupported_claim_format"}
    ),
    "excluded": frozenset(
        {
            "non_factual_recommendation",
            "value_judgment",
            "heading_or_transition",
            "instruction_or_disclaimer",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class StatementSupportJudgeRequest:
    """单次最终回答陈述拆分与证据支持判断请求。"""

    trace_id: str | None
    mode: str
    query: str
    answer: str
    evidence_units: tuple[EvidenceUnit, ...]
    schema_version: str
    evidence_scope_version: str


class StatementSupportJudge(Protocol):
    judge_name: str
    judge_version: str
    prompt_version: str

    def judge(self, request: StatementSupportJudgeRequest) -> Mapping[str, object]: ...


class StatementSupportEvaluator(BaseEvaluator):
    """严格校验原子陈述覆盖后，计算 supported / verifiable。"""

    def __init__(self, judge: StatementSupportJudge | None = None) -> None:
        if judge is None:
            from evaluators.deterministic_statement_support import (
                DeterministicStatementSupportJudge,
            )

            judge = DeterministicStatementSupportJudge()
        self._judge = judge
        self._judge_name = _required_string(judge.judge_name)
        self._judge_version = _required_string(judge.judge_version)
        self._prompt_version = _required_string(judge.prompt_version)

    @property
    def metric_name(self) -> str:
        return "statement_support_rate"

    @property
    def evaluator_version(self) -> str:
        return "3.0.0"

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
        request = StatementSupportJudgeRequest(
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
        except Exception as judge_error:
            reason, safe_metadata = judge_error_metadata(judge_error)
            metadata.update(safe_metadata)
            metadata["reason"] = reason
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        claims = _parse_claims(response, candidate.evidence_units)
        if claims is None:
            metadata["reason"] = "invalid_judge_output"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        supported = sum(
            verdict == "supported" for claim_type, verdict in claims if claim_type == "verifiable"
        )
        unsupported = sum(
            verdict == "unsupported" for claim_type, verdict in claims if claim_type == "verifiable"
        )
        not_assessable = sum(
            verdict == "not_assessable"
            for claim_type, verdict in claims
            if claim_type == "verifiable"
        )
        excluded = sum(claim_type == "excluded" for claim_type, _ in claims)
        verifiable = supported + unsupported + not_assessable
        metadata.update(
            verifiable_claim_count=verifiable,
            supported_claim_count=supported,
            unsupported_claim_count=unsupported,
            not_assessable_claim_count=not_assessable,
            excluded_claim_count=excluded,
            automatic_assessment_coverage=(
                (supported + unsupported) / verifiable if verifiable else None
            ),
        )
        if verifiable == 0:
            metadata["reason"] = "no_verifiable_claims"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        return self._result(supported / verifiable, EvaluationStatus.COMPUTED, metadata)

    def _result(
        self, value: float | None, status: EvaluationStatus, metadata: Mapping[str, object]
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _parse_claims(
    response: Mapping[str, object], evidence_units: tuple[EvidenceUnit, ...]
) -> tuple[tuple[str, str], ...] | None:
    raw_claims = response.get("claims")
    if set(response) != {"claims"} or not isinstance(raw_claims, list):
        return None
    if not raw_claims:
        return None
    unit_ids = {unit.unit_id for unit in evidence_units}
    claim_ids: set[str] = set()
    parsed: list[tuple[str, str]] = []
    for raw in raw_claims:
        if not isinstance(raw, Mapping) or set(raw) != {
            "claim_id",
            "claim_type",
            "verdict",
            "reason_code",
            "supporting_unit_ids",
            "confidence",
        }:
            return None
        claim_id = _text(raw.get("claim_id"))
        claim_type = raw.get("claim_type")
        verdict = raw.get("verdict")
        reason_code = raw.get("reason_code")
        supporting_ids = raw.get("supporting_unit_ids")
        confidence = raw.get("confidence")
        if (
            claim_id is None
            or claim_id in claim_ids
            or claim_type not in {"verifiable", "excluded"}
            or not isinstance(verdict, str)
            or verdict not in _REASON_CODES
            or not isinstance(reason_code, str)
            or reason_code not in _REASON_CODES[verdict]
            or not isinstance(supporting_ids, list)
            or any(not isinstance(unit_id, str) for unit_id in supporting_ids)
            or len(set(supporting_ids)) != len(supporting_ids)
            or not set(supporting_ids).issubset(unit_ids)
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            return None
        if claim_type == "excluded" and verdict != "excluded":
            return None
        if claim_type == "verifiable" and verdict == "excluded":
            return None
        if verdict == "supported" and not supporting_ids:
            return None
        if verdict != "supported" and supporting_ids:
            return None
        claim_ids.add(claim_id)
        parsed.append((claim_type, verdict))
    return tuple(parsed)


def _parse_trace(
    trace: Mapping[str, object],
) -> (
    tuple[
        str | None, str, str, str, StatementSupportCandidate | None, int, dict[str, int], str | None
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
        or (candidate is not None and not isinstance(candidate, StatementSupportCandidate))
        or not isinstance(raw_diagnostics, Mapping)
        or (error is not None and _text(error) is None)
    ):
        return None
    if isinstance(candidate, StatementSupportCandidate) and not _valid_candidate(
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
        "duplicate_result_observation_count",
        "orphan_result_count",
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


def _valid_candidate(candidate: StatementSupportCandidate, unit_count: int) -> bool:
    if _text(candidate.query) is None or _text(candidate.answer) is None:
        return False
    if len(candidate.evidence_units) != unit_count:
        return False
    seen: set[str] = set()
    for unit in candidate.evidence_units:
        if (
            _text(unit.unit_id) is None
            or unit.unit_id in seen
            or _text(unit.tool_name) is None
            or _text(unit.content) is None
        ):
            return False
        try:
            json.loads(unit.content)
        except (json.JSONDecodeError, TypeError):
            return False
        seen.add(unit.unit_id)
    return True


def _required_string(value: object) -> str:
    parsed = _text(value)
    if parsed is None:
        raise ValueError("Judge 版本标识必须是非空字符串")
    return parsed


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _non_negative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
