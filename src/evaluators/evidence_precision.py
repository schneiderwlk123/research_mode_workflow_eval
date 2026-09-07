"""计算外部检索证据与用户需求的查准率。"""

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from collectors.evidence_quality import EvidencePrecisionCandidate, EvidenceUnit
from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus
from evaluators.openai_compatible_judge_runtime import judge_error_metadata

_ALLOWED_REASON_CODES = {
    "relevant": frozenset(
        {
            "directly_addresses_requirement",
            "supports_required_fact",
            "refutes_required_assumption",
            "supplies_required_input",
            "constrains_scope",
        }
    ),
    "irrelevant": frozenset(
        {
            "topically_related_only",
            "unrelated_to_requirement",
            "unnecessary_background",
            "non_factual_content",
        }
    ),
    "not_assessable": frozenset(
        {
            "insufficient_unit_content",
            "ambiguous_requirement",
            "unsupported_evidence_format",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class EvidenceRelevanceJudgeRequest:
    """单会话证据相关性请求；正文只允许在 Judge 生命周期内存在。"""

    trace_id: str | None
    mode: str
    query: str
    evidence_units: tuple[EvidenceUnit, ...]
    schema_version: str
    evidence_scope_version: str


class EvidenceRelevanceJudge(Protocol):
    """证据相关性 Judge 的稳定版本化接口。"""

    judge_name: str
    judge_version: str
    prompt_version: str

    def judge(self, request: EvidenceRelevanceJudgeRequest) -> Mapping[str, object]: ...


class EvidencePrecisionEvaluator(BaseEvaluator):
    """按完整证据分母计算相关证据比例，不把不可判定单元静默视为无关。"""

    def __init__(self, judge: EvidenceRelevanceJudge) -> None:
        self._judge = judge
        self._judge_name = _required_string(judge.judge_name)
        self._judge_version = _required_string(judge.judge_version)
        self._prompt_version = _required_string(judge.prompt_version)

    @property
    def metric_name(self) -> str:
        return "evidence_precision_rate"

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
            schema_version,
            scope_version,
            candidate,
            unit_count,
            diagnostics,
            error,
        ) = parsed
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

        request = EvidenceRelevanceJudgeRequest(
            trace_id=trace_id,
            mode=mode,
            query=candidate.query,
            evidence_units=candidate.evidence_units,
            schema_version=schema_version,
            evidence_scope_version=scope_version,
        )
        try:
            response = self._judge.judge(request)
        except Exception as judge_error:
            reason, safe_metadata = judge_error_metadata(judge_error)
            metadata.update(safe_metadata)
            metadata["reason"] = reason
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        judgments = _parse_judgments(response, candidate.evidence_units)
        if judgments is None:
            metadata["reason"] = "invalid_judge_output"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        relevant_count = sum(verdict == "relevant" for verdict in judgments)
        irrelevant_count = sum(verdict == "irrelevant" for verdict in judgments)
        not_assessable_count = sum(verdict == "not_assessable" for verdict in judgments)
        metadata.update(
            relevant_evidence_unit_count=relevant_count,
            irrelevant_evidence_unit_count=irrelevant_count,
            not_assessable_evidence_unit_count=not_assessable_count,
        )
        if not_assessable_count:
            metadata["reason"] = "incomplete_relevance_judgment"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        return self._result(
            relevant_count / unit_count,
            EvaluationStatus.COMPUTED,
            metadata,
        )

    def _result(
        self, value: float | None, status: EvaluationStatus, metadata: Mapping[str, object]
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _parse_judgments(
    response: Mapping[str, object], evidence_units: tuple[EvidenceUnit, ...]
) -> tuple[str, ...] | None:
    if set(response) != {"judgments"}:
        return None
    raw_judgments = response.get("judgments")
    if not isinstance(raw_judgments, list):
        return None
    expected_ids = {unit.unit_id for unit in evidence_units}
    parsed: dict[str, str] = {}
    for raw in raw_judgments:
        if not isinstance(raw, Mapping) or set(raw) != {
            "unit_id",
            "verdict",
            "reason_code",
            "confidence",
        }:
            return None
        unit_id = raw.get("unit_id")
        verdict = raw.get("verdict")
        reason_code = raw.get("reason_code")
        confidence = raw.get("confidence")
        if (
            not isinstance(unit_id, str)
            or unit_id not in expected_ids
            or unit_id in parsed
            or not isinstance(verdict, str)
            or verdict not in _ALLOWED_REASON_CODES
            or not isinstance(reason_code, str)
            or reason_code not in _ALLOWED_REASON_CODES[verdict]
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            return None
        parsed[unit_id] = verdict
    if set(parsed) != expected_ids:
        return None
    return tuple(parsed[unit.unit_id] for unit in evidence_units)


def _parse_trace(
    trace: Mapping[str, object],
) -> (
    tuple[
        str | None,
        str,
        str,
        str,
        EvidencePrecisionCandidate | None,
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
    error_value = trace.get("normalization_error")
    if (
        (trace_id is not None and not isinstance(trace_id, str))
        or mode is None
        or schema_version is None
        or scope_version is None
        or unit_count is None
        or (candidate is not None and not isinstance(candidate, EvidencePrecisionCandidate))
        or not isinstance(raw_diagnostics, Mapping)
        or (error_value is not None and _text(error_value) is None)
    ):
        return None
    if isinstance(candidate, EvidencePrecisionCandidate) and not _valid_candidate(
        candidate, unit_count
    ):
        return None
    diagnostic_names = (
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
    for name in diagnostic_names:
        value = _non_negative_int(raw_diagnostics.get(name))
        if value is None:
            return None
        diagnostics[name] = value
    return (
        trace_id.strip() if isinstance(trace_id, str) else None,
        mode,
        schema_version,
        scope_version,
        candidate if isinstance(candidate, EvidencePrecisionCandidate) else None,
        unit_count,
        diagnostics,
        _text(error_value),
    )


def _required_string(value: object) -> str:
    parsed = _text(value)
    if parsed is None:
        raise ValueError("Judge 版本标识必须是非空字符串")
    return parsed


def _valid_candidate(candidate: EvidencePrecisionCandidate, unit_count: int) -> bool:
    if _text(candidate.query) is None or not candidate.evidence_units:
        return False
    if len(candidate.evidence_units) != unit_count:
        return False
    unit_ids: set[str] = set()
    for unit in candidate.evidence_units:
        if (
            _text(unit.unit_id) is None
            or unit.unit_id in unit_ids
            or _text(unit.tool_name) is None
            or _text(unit.content) is None
            or not _is_json_value(unit.content)
        ):
            return False
        unit_ids.add(unit.unit_id)
    return True


def _is_json_value(content: str) -> bool:
    try:
        json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return False
    return True


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _non_negative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
