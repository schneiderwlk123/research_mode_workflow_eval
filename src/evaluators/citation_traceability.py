"""引用可溯率的确定性评估器。

版本 1.0.0（2026-09-20 口径确认）：最终回答中的 `[n]` 引用编号在中间检索输出暴露的
来源编号注册表内可解析的比例。弱判定边界——编号存在不代表内容真使用了该来源，
内容级对应属 v2/Judge 方向；该边界随 evaluator_version 与 citation_pattern_version
共同声明。
"""

from collections.abc import Mapping
from typing import cast

from collectors.citation_traceability import (
    BODY_CHANNELS,
    CITE_PATTERN_VERSION,
    SOURCE_REGISTRY_VERSION,
)
from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus


class CitationTraceabilityRateEvaluator(BaseEvaluator):
    """使用安全聚合计数计算引用可溯率，不接触或持久化回答与来源正文。"""

    @property
    def metric_name(self) -> str:
        return "citation_traceability_rate"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        metadata, error = parsed
        if cast(str, metadata["mode"]) not in {"chat", "research"}:
            metadata["reason"] = "unsupported_trace_mode"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if error is not None:
            metadata["reason"] = error
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        denominator = cast(int, metadata["extracted_citation_count"])
        if denominator == 0:
            metadata["reason"] = "no_extractable_citations"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        # 有引用而无来源注册表时按 0 计算并保留 fabrication_risk_signal 诊断：
        # 引用编号无从解析本身是可判定事实，对齐数值锚定率的无中生有信号入口。
        return self._result(
            cast(int, metadata["resolved_citation_count"]) / denominator,
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
    extracted = _non_negative_int(trace.get("extracted_citation_count"))
    resolved = _non_negative_int(trace.get("resolved_citation_count"))
    range_exceeded = _non_negative_int(trace.get("range_exceeded_citation_count"))
    nonstandard = _non_negative_int(trace.get("nonstandard_citation_count"))
    source_sequence = _non_negative_int(trace.get("source_sequence_length"))
    source_registry = _non_negative_int(trace.get("source_registry_size"))
    diagnostics = trace.get("diagnostics")
    error = trace.get("normalization_error")
    if (
        not isinstance(mode, str)
        or not mode.strip()
        or (trace_id is not None and not isinstance(trace_id, str))
        or not isinstance(body_channel, str)
        or body_channel not in BODY_CHANNELS
        or extracted is None
        or resolved is None
        or range_exceeded is None
        or nonstandard is None
        or source_sequence is None
        or source_registry is None
        or resolved > extracted
        or range_exceeded != extracted - resolved
        or not isinstance(diagnostics, Mapping)
        or trace.get("citation_pattern_version") != CITE_PATTERN_VERSION
        or trace.get("source_registry_version") != SOURCE_REGISTRY_VERSION
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
        "extracted_citation_count": extracted,
        "resolved_citation_count": resolved,
        "range_exceeded_citation_count": range_exceeded,
        "nonstandard_citation_count": nonstandard,
        "source_sequence_length": source_sequence,
        "source_registry_size": source_registry,
        "citation_pattern_version": CITE_PATTERN_VERSION,
        "source_registry_version": SOURCE_REGISTRY_VERSION,
        "diagnostics": diagnostic_counts,
    }
    return metadata, error.strip() if isinstance(error, str) else None


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
