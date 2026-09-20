"""计算最终回答数值 span 的证据锚定率。"""

from collections.abc import Mapping
from typing import cast

from collectors.answer_evidence_grounding import (
    BODY_CHANNELS,
    FAILURE_SIGNAL_RULES_VERSION,
    NORMALIZATION_VERSION,
    RESEARCH_EXCLUDED_TOOLS_VERSION,
    UNIT_CATALOG_VERSION,
)
from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus


class AnswerEvidenceGroundingRateEvaluator(BaseEvaluator):
    """使用安全聚合计数计算数值锚定率，不接触或持久化回答与证据正文。"""

    @property
    def metric_name(self) -> str:
        return "answer_evidence_grounding_rate"

    @property
    def evaluator_version(self) -> str:
        return "1.1.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        metadata, error = parsed
        if error is not None:
            metadata["reason"] = error
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        denominator = cast(int, metadata["extracted_span_count"])
        if denominator == 0:
            metadata["reason"] = "no_extractable_spans"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        # 证据侧为空且回答含数值 span 时计算为 0 并携带无中生有信号：无证据却给出
        # 具体数值本身就是可判定事实，对齐陈述支持率的空证据零分入口。
        return self._result(
            cast(int, metadata["grounded_span_count"]) / denominator,
            EvaluationStatus.COMPUTED,
            metadata,
        )

    def _result(
        self,
        value: float | None,
        status: EvaluationStatus,
        metadata: Mapping[str, object],
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _parse_trace(
    trace: Mapping[str, object],
) -> tuple[dict[str, object], str | None] | None:
    mode = trace.get("mode")
    trace_id = trace.get("trace_id")
    body_channel = trace.get("body_channel")
    extracted = _non_negative_int(trace.get("extracted_span_count"))
    grounded = _non_negative_int(trace.get("grounded_span_count"))
    evidence_count = _non_negative_int(trace.get("evidence_text_count"))
    diagnostics = trace.get("diagnostics")
    error = trace.get("normalization_error")
    if (
        not isinstance(mode, str)
        or mode not in {"chat", "research"}
        or (trace_id is not None and not isinstance(trace_id, str))
        or not isinstance(body_channel, str)
        or body_channel not in BODY_CHANNELS
        or extracted is None
        or grounded is None
        or evidence_count is None
        or grounded > extracted
        or not isinstance(diagnostics, Mapping)
        or trace.get("unit_catalog_version") != UNIT_CATALOG_VERSION
        or trace.get("normalization_version") != NORMALIZATION_VERSION
        or trace.get("failure_signal_rules_version") != FAILURE_SIGNAL_RULES_VERSION
        or trace.get("research_excluded_tools_version") != RESEARCH_EXCLUDED_TOOLS_VERSION
        or (error is not None and (not isinstance(error, str) or not error.strip()))
    ):
        return None
    diagnostic_counts: dict[str, int] = {}
    for key, value in diagnostics.items():
        count = _non_negative_int(value)
        if count is None:
            return None
        diagnostic_counts[str(key)] = count
    metadata: dict[str, object] = {
        "trace_id": trace_id,
        "mode": mode,
        "body_channel": body_channel,
        "extracted_span_count": extracted,
        "grounded_span_count": grounded,
        "evidence_text_count": evidence_count,
        "unit_catalog_version": UNIT_CATALOG_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "failure_signal_rules_version": FAILURE_SIGNAL_RULES_VERSION,
        "research_excluded_tools_version": RESEARCH_EXCLUDED_TOOLS_VERSION,
        "diagnostics": diagnostic_counts,
    }
    return metadata, error.strip() if isinstance(error, str) else None


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
