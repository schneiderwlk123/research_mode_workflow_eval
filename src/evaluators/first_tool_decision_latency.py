"""计算当前请求到首次工具链决策的确定性时延。"""

from collections.abc import Mapping

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_INPUT_ERROR_REASONS = frozenset({"invalid_timestamp", "invalid_tool_call_queue"})


class FirstToolDecisionLatencyEvaluator(BaseEvaluator):
    """以毫秒输出单会话首次工具或子 Agent 决策时延。"""

    @property
    def metric_name(self) -> str:
        return "first_tool_decision_latency_ms"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        trace_id, mode, start_ms, decision_ms, source, diagnostics, error = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            "unit": "ms",
            "decision_source": source,
            **diagnostics,
        }
        if mode not in {"chat", "research"}:
            metadata["reason"] = "unsupported_trace_mode"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if error is not None:
            metadata["reason"] = error
            status = (
                EvaluationStatus.ERROR
                if error in _INPUT_ERROR_REASONS
                else EvaluationStatus.NOT_COMPUTABLE
            )
            return self._result(None, status, metadata)
        if start_ms is None or decision_ms is None or source is None:
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)
        latency_ms = decision_ms - start_ms
        if latency_ms < 0:
            metadata["reason"] = "invalid_event_order"
            return self._result(None, EvaluationStatus.ERROR, metadata)
        return self._result(latency_ms, EvaluationStatus.COMPUTED, metadata)

    def _result(
        self, value: int | None, status: EvaluationStatus, metadata: Mapping[str, object]
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _parse_trace(
    trace: Mapping[str, object],
) -> (
    tuple[
        str | None,
        str,
        int | None,
        int | None,
        str | None,
        dict[str, int],
        str | None,
    ]
    | None
):
    trace_id = trace.get("trace_id")
    mode = _text(trace.get("mode"))
    start_ms = _optional_int(trace.get("request_start_ms"))
    decision_ms = _optional_int(trace.get("first_decision_ms"))
    source_value = trace.get("decision_source")
    source = _text(source_value) if source_value is not None else None
    raw_diagnostics = trace.get("diagnostics")
    error_value = trace.get("normalization_error")
    error = _text(error_value) if error_value is not None else None
    if (
        (trace_id is not None and not isinstance(trace_id, str))
        or mode is None
        or start_ms is _INVALID
        or decision_ms is _INVALID
        or (source_value is not None and source is None)
        or not isinstance(raw_diagnostics, Mapping)
        or (error_value is not None and error is None)
    ):
        return None
    diagnostics: dict[str, int] = {}
    for name in (
        "decision_event_count",
        "malformed_decision_event_count",
        "duplicate_pending_event_count",
    ):
        value = raw_diagnostics.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        diagnostics[name] = value
    return (
        trace_id,
        mode,
        start_ms if isinstance(start_ms, int) else None,
        decision_ms if isinstance(decision_ms, int) else None,
        source,
        diagnostics,
        error,
    )


class _Invalid:
    pass


_INVALID = _Invalid()


def _optional_int(value: object) -> int | None | _Invalid:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return _INVALID


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
