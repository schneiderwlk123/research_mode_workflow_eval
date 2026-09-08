"""计算单条 Trace 的平均模型步骤时延。"""

from collections.abc import Mapping, Sequence
from typing import TypeGuard

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_ERROR_REASONS = frozenset(
    {
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "missing_step_id",
        "invalid_duration",
        "invalid_timestamp",
        "invalid_event_order",
        "conflicting_step_boundary",
    }
)


class AverageModelStepLatencyEvaluator(BaseEvaluator):
    """计算有效模型步骤的算术平均，并保留全量微平均所需的总和与计数。"""

    @property
    def metric_name(self) -> str:
        return "average_model_step_latency_ms"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        trace_id, mode, durations, diagnostics, error = parsed
        total_duration = sum(durations)
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            **diagnostics,
            "total_step_duration_ms": total_duration,
        }
        if mode not in {"chat", "research"}:
            metadata["reason"] = "unsupported_trace_mode"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if error is not None:
            metadata["reason"] = error
            status = (
                EvaluationStatus.NOT_COMPUTABLE
                if error == "no_observable_model_steps"
                else EvaluationStatus.ERROR
            )
            return self._result(None, status, metadata)
        if not durations:
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)
        return self._result(total_duration / len(durations), EvaluationStatus.COMPUTED, metadata)

    def _result(
        self,
        value: float | None,
        status: EvaluationStatus,
        metadata: Mapping[str, object],
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _parse_trace(
    trace: Mapping[str, object],
) -> tuple[str | None, str, tuple[int, ...], dict[str, int | None], str | None] | None:
    trace_id_value = trace.get("trace_id")
    mode = _text(trace.get("mode"))
    raw_durations = trace.get("step_durations_ms")
    raw_diagnostics = trace.get("diagnostics")
    error_value = trace.get("normalization_error")
    error = _text(error_value) if error_value is not None else None
    if (
        (trace_id_value is not None and _text(trace_id_value) is None)
        or mode is None
        or not _is_sequence(raw_durations)
        or not isinstance(raw_diagnostics, Mapping)
        or (error_value is not None and error is None)
    ):
        return None

    durations: list[int] = []
    for value in raw_durations:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        durations.append(value)

    diagnostics: dict[str, int | None] = {}
    for name in (
        "valid_step_count",
        "latency_missing_count",
        "duplicate_boundary_event_count",
        "timestamp_cross_check_count",
    ):
        value = raw_diagnostics.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        diagnostics[name] = value
    max_difference = raw_diagnostics.get("max_abs_boundary_difference_ms")
    if max_difference is not None and (
        not isinstance(max_difference, int)
        or isinstance(max_difference, bool)
        or max_difference < 0
    ):
        return None
    diagnostics["max_abs_boundary_difference_ms"] = max_difference

    valid_count = diagnostics["valid_step_count"]
    cross_check_count = diagnostics["timestamp_cross_check_count"]
    if valid_count != len(durations):
        return None
    if (cross_check_count == 0) != (max_difference is None):
        return None
    if cross_check_count is not None and cross_check_count > len(durations):
        return None
    if error is not None and durations:
        return None
    if error is not None and error not in _ERROR_REASONS | {"no_observable_model_steps"}:
        return None
    return _text(trace_id_value), mode, tuple(durations), diagnostics, error


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
