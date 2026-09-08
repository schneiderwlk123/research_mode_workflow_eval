"""计算聊天与研究模式统一的端到端时延。"""

from collections.abc import Mapping
from typing import TypeGuard

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_MISSING_REASONS = frozenset(
    {"missing_start_timestamp", "missing_end_timestamp", "missing_end_to_end_duration"}
)
_ERROR_REASONS = frozenset(
    {
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "invalid_timestamp",
        "invalid_duration",
        "conflicting_turn_boundary",
    }
)


class EndToEndLatencyEvaluator(BaseEvaluator):
    """优先使用研究日志报告耗时，否则使用请求到回答边界的墙钟差值。"""

    @property
    def metric_name(self) -> str:
        return "end_to_end_latency_ms"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        trace_id, mode, start_ms, end_ms, reported_ms, precision, duplicate_count, error = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            "latency_source": None,
            "timestamp_precision": precision,
            "boundary_difference_ms": None,
            "duplicate_boundary_event_count": duplicate_count,
        }
        if mode not in {"chat", "research"}:
            metadata["reason"] = "unsupported_trace_mode"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if error is not None:
            metadata["reason"] = error
            if error in _MISSING_REASONS:
                status = EvaluationStatus.NOT_COMPUTABLE
            elif error in _ERROR_REASONS:
                status = EvaluationStatus.ERROR
            else:
                status = EvaluationStatus.ERROR
                metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, status, metadata)
        if start_ms is None or end_ms is None:
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)

        timestamp_delta = end_ms - start_ms
        if timestamp_delta < 0 or (reported_ms is not None and reported_ms < 0):
            metadata["reason"] = "invalid_event_order"
            return self._result(None, EvaluationStatus.ERROR, metadata)

        if mode == "research" and reported_ms is not None:
            metadata["latency_source"] = "turn_end_duration"
            metadata["boundary_difference_ms"] = timestamp_delta - reported_ms
            return self._result(reported_ms, EvaluationStatus.COMPUTED, metadata)

        metadata["latency_source"] = "timestamp_delta"
        return self._result(timestamp_delta, EvaluationStatus.COMPUTED, metadata)

    def _result(
        self, value: int | None, status: EvaluationStatus, metadata: Mapping[str, object]
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _parse_trace(
    trace: Mapping[str, object],
) -> tuple[str | None, str, int | None, int | None, int | None, str, int, str | None] | None:
    trace_id_value = trace.get("trace_id")
    mode = _text(trace.get("mode"))
    start_value = trace.get("request_start_ms")
    end_value = trace.get("answer_end_ms")
    reported_value = trace.get("reported_duration_ms")
    precision = _text(trace.get("timestamp_precision"))
    diagnostics = trace.get("diagnostics")
    error_value = trace.get("normalization_error")
    error = _text(error_value) if error_value is not None else None
    if (
        (trace_id_value is not None and _text(trace_id_value) is None)
        or mode is None
        or not _is_optional_int(start_value)
        or not _is_optional_int(end_value)
        or not _is_optional_int(reported_value)
        or precision not in {"seconds", "milliseconds"}
        or not isinstance(diagnostics, Mapping)
        or (error_value is not None and error is None)
    ):
        return None
    duplicate_count = diagnostics.get("duplicate_boundary_event_count")
    if (
        not isinstance(duplicate_count, int)
        or isinstance(duplicate_count, bool)
        or duplicate_count < 0
    ):
        return None
    if mode == "chat" and reported_value is not None:
        return None
    return (
        _text(trace_id_value),
        mode,
        start_value,
        end_value,
        reported_value,
        precision,
        duplicate_count,
        error,
    )


def _is_optional_int(value: object) -> TypeGuard[int | None]:
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
