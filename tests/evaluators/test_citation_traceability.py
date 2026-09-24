"""引用可溯率评测器行为测试：规范化输入校验、比率计算与不可计算边界。"""

from copy import deepcopy

import pytest

from collectors.citation_traceability import (
    CITE_PATTERN_VERSION,
    SOURCE_REGISTRY_VERSION,
)
from evaluators.base import EvaluationStatus
from evaluators.citation_traceability import CitationTraceabilityRateEvaluator


def _trace(
    *,
    extracted: object = 3,
    resolved: object = 2,
    range_exceeded: object = 1,
    nonstandard: object = 0,
    source_sequence: object = 10,
    source_registry: object = 10,
    body_channel: object = "chat_answer",
    mode: object = "chat",
    normalization_error: str | None = None,
    diagnostics: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "trace_id": "trace-001",
        "mode": mode,
        "body_channel": body_channel,
        "citation_pattern_version": CITE_PATTERN_VERSION,
        "source_registry_version": SOURCE_REGISTRY_VERSION,
        "extracted_citation_count": extracted,
        "resolved_citation_count": resolved,
        "range_exceeded_citation_count": range_exceeded,
        "nonstandard_citation_count": nonstandard,
        "source_sequence_length": source_sequence,
        "source_registry_size": source_registry,
        "diagnostics": {
            "malformed_event_count": 0,
            "orphan_result_count": 0,
            "failure_signal_payload_count": 0,
            "conflicting_result_count": 0,
            "nonstandard_citation_count": 0,
            "markdown_link_citation_count": 0,
            "range_exceeded_citation_count": 0,
            "source_list_missing": 0,
            "numbering_scheme_mixed_suspected": 0,
            "fabrication_risk_signal": 0,
            **(diagnostics or {}),
        },
        "normalization_error": normalization_error,
    }


def test_evaluator_calculates_resolved_citations_over_extracted() -> None:
    trace = _trace()
    original = deepcopy(trace)

    result = CitationTraceabilityRateEvaluator().evaluate(trace)

    assert result.metric_name == "citation_traceability_rate"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(2 / 3)
    assert result.metadata["extracted_citation_count"] == 3
    assert result.metadata["resolved_citation_count"] == 2
    assert result.metadata["range_exceeded_citation_count"] == 1
    assert trace == original


def test_all_resolved_returns_one() -> None:
    result = CitationTraceabilityRateEvaluator().evaluate(
        _trace(extracted=2, resolved=2, range_exceeded=0)
    )

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 1.0


def test_no_extractable_citations_is_not_computable() -> None:
    result = CitationTraceabilityRateEvaluator().evaluate(
        _trace(extracted=0, resolved=0, range_exceeded=0)
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "no_extractable_citations"


def test_fabrication_signal_zero_with_metadata() -> None:
    result = CitationTraceabilityRateEvaluator().evaluate(
        _trace(
            extracted=2,
            resolved=0,
            range_exceeded=2,
            source_sequence=0,
            source_registry=0,
            diagnostics={"fabrication_risk_signal": 1, "source_list_missing": 1},
        )
    )

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0.0
    diagnostics_metadata = result.metadata["diagnostics"]
    assert isinstance(diagnostics_metadata, dict)
    assert diagnostics_metadata["fabrication_risk_signal"] == 1


def test_normalization_failure_is_not_computable() -> None:
    result = CitationTraceabilityRateEvaluator().evaluate(
        _trace(normalization_error="missing_final_answer")
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "missing_final_answer"


@pytest.mark.parametrize(
    "trace",
    [
        {},
        _trace(extracted=True),
        _trace(extracted=2, resolved=3),
        _trace(range_exceeded=5),
        _trace(source_sequence="10"),
        _trace(body_channel="unsupported"),
        _trace(mode="unsupported"),
        _trace(diagnostics={"fabrication_risk_signal": "1"}),
        {**_trace(), "citation_pattern_version": "cite_v0"},
        {**_trace(), "source_registry_version": "source_registry_v0"},
    ],
)
def test_invalid_normalized_trace_is_isolated_as_error(trace: dict[str, object]) -> None:
    result = CitationTraceabilityRateEvaluator().evaluate(trace)

    if trace.get("mode") == "unsupported":
        assert result.status is EvaluationStatus.NOT_COMPUTABLE
        assert result.metadata["reason"] == "unsupported_trace_mode"
    else:
        assert result.status is EvaluationStatus.ERROR
        assert result.metadata["reason"] == "invalid_normalized_trace"
