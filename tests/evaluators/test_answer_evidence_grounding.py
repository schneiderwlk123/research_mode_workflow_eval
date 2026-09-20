from copy import deepcopy
from types import MappingProxyType
from typing import cast

import pytest

from evaluators.answer_evidence_grounding import AnswerEvidenceGroundingRateEvaluator
from evaluators.base import EvaluationStatus


def _normalized_trace(
    *,
    mode: str = "chat",
    body_channel: str = "chat_answer",
    extracted: int = 4,
    grounded: int = 3,
    evidence_count: int = 2,
    fabrication: int = 0,
    error: str | None = None,
) -> dict[str, object]:
    return {
        "trace_id": "trace-1",
        "mode": mode,
        "body_channel": body_channel,
        "extracted_span_count": extracted,
        "grounded_span_count": grounded,
        "evidence_text_count": evidence_count,
        "unit_catalog_version": "grounding_units_v2",
        "normalization_version": "grounding_norm_v2",
        "failure_signal_rules_version": "chat_failure_signals_v1",
        "research_excluded_tools_version": "research_excluded_tools_v1",
        "diagnostics": {
            "orphan_result_count": 0,
            "failure_signal_payload_count": 0,
            "conflicting_result_count": 0,
            "evidence_chunk_count": 166,
            "excluded_tool_output_count": 0,
            "non_terminal_tool_part_count": 0,
            "turn_end_present": 1,
            "fabrication_risk_signal": fabrication,
            "rounded_match_count": 0,
        },
        "normalization_error": error,
    }


def test_evaluator_computes_grounding_rate_without_mutating_input() -> None:
    trace = _normalized_trace()
    original = deepcopy(trace)

    result = AnswerEvidenceGroundingRateEvaluator().evaluate(trace)

    assert result.metric_name == "answer_evidence_grounding_rate"
    assert result.evaluator_version == "1.1.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(0.75)
    assert result.metadata["extracted_span_count"] == 4
    assert result.metadata["grounded_span_count"] == 3
    diagnostics = cast(MappingProxyType[str, object], result.metadata["diagnostics"])
    assert diagnostics["evidence_chunk_count"] == 166
    assert trace == original


def test_evaluator_computes_zero_when_evidence_is_empty() -> None:
    trace = _normalized_trace(extracted=2, grounded=0, evidence_count=0, fabrication=1)

    result = AnswerEvidenceGroundingRateEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0.0
    diagnostics = cast(MappingProxyType[str, object], result.metadata["diagnostics"])
    assert diagnostics["fabrication_risk_signal"] == 1


def test_no_extractable_spans_is_not_computable() -> None:
    result = AnswerEvidenceGroundingRateEvaluator().evaluate(
        _normalized_trace(extracted=0, grounded=0)
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "no_extractable_spans"


@pytest.mark.parametrize(
    "error",
    [
        "missing_final_answer",
        "missing_turn_boundary",
        "invalid_chat_trace_structure",
        "conflicting_call_definition",
    ],
)
def test_normalization_errors_are_not_computable(error: str) -> None:
    result = AnswerEvidenceGroundingRateEvaluator().evaluate(_normalized_trace(error=error))

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == error


@pytest.mark.parametrize(
    "trace",
    [
        _normalized_trace(grounded=5, extracted=4),
        _normalized_trace() | {"mode": "unknown"},
        _normalized_trace() | {"body_channel": "unknown"},
        _normalized_trace() | {"unit_catalog_version": "units_v0"},
        _normalized_trace() | {"normalization_version": "norm_v0"},
        _normalized_trace() | {"failure_signal_rules_version": "v0"},
        _normalized_trace() | {"research_excluded_tools_version": "v0"},
        _normalized_trace() | {"diagnostics": {"evidence_chunk_count": -1}},
        _normalized_trace() | {"diagnostics": {"unknown_key": "not_int"}},
        _normalized_trace() | {"extracted_span_count": -1},
    ],
)
def test_invalid_aggregate_is_isolated_as_error(trace: dict[str, object]) -> None:
    result = AnswerEvidenceGroundingRateEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"
