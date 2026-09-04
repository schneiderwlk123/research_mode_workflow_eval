"""模型工具循环深度的确定性评测器。"""

from collections.abc import Mapping

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus


class ModelToolLoopDepthEvaluator(BaseEvaluator):
    """计算完成的模型→工具→模型轮次数，禁止按工具调用数估算。"""

    @property
    def metric_name(self) -> str:
        return "model_tool_loop_depth"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                value=None,
                status=EvaluationStatus.ERROR,
                metadata={"reason": "invalid_normalized_trace"},
            )

        (
            trace_id,
            mode,
            model_round_count,
            tool_calling_round_count,
            completed_loop_count,
            unique_tool_call_count,
            diagnostics,
            normalization_error,
        ) = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            "model_round_count": model_round_count,
            "tool_calling_round_count": tool_calling_round_count,
            "completed_loop_count": completed_loop_count,
            "unique_tool_call_count": unique_tool_call_count,
            "malformed_event_count": diagnostics["malformed_event_count"],
            "duplicate_loop_observation_count": diagnostics["duplicate_loop_observation_count"],
            "duplicate_tool_observation_count": diagnostics["duplicate_tool_observation_count"],
        }
        if mode not in {"chat", "research"}:
            metadata["reason"] = "unsupported_trace_mode"
            return self._result(
                value=None,
                status=EvaluationStatus.NOT_COMPUTABLE,
                metadata=metadata,
            )
        if mode == "chat":
            metadata["reason"] = normalization_error or "missing_model_iteration_boundary"
            return self._result(
                value=None,
                status=EvaluationStatus.NOT_COMPUTABLE,
                metadata=metadata,
            )
        if normalization_error is not None:
            metadata["reason"] = normalization_error
            return self._result(
                value=None,
                status=EvaluationStatus.NOT_COMPUTABLE,
                metadata=metadata,
            )
        if model_round_count == 0:
            metadata["reason"] = "missing_model_iteration_boundary"
            return self._result(
                value=None,
                status=EvaluationStatus.NOT_COMPUTABLE,
                metadata=metadata,
            )

        return self._result(
            value=completed_loop_count,
            status=EvaluationStatus.COMPUTED,
            metadata=metadata,
        )

    def _result(
        self,
        *,
        value: int | None,
        status: EvaluationStatus,
        metadata: Mapping[str, object],
    ) -> EvaluationResult:
        return EvaluationResult(
            metric_name=self.metric_name,
            evaluator_version=self.evaluator_version,
            value=value,
            status=status,
            metadata=metadata,
        )


def _parse_trace(
    trace: Mapping[str, object],
) -> (
    tuple[
        str | None,
        str,
        int,
        int,
        int,
        int,
        dict[str, int],
        str | None,
    ]
    | None
):
    trace_id_value = trace.get("trace_id")
    mode = _non_empty_string(trace.get("mode"))
    model_round_count = _non_negative_int(trace.get("model_round_count"))
    tool_calling_round_count = _non_negative_int(trace.get("tool_calling_round_count"))
    completed_loop_count = _non_negative_int(trace.get("completed_loop_count"))
    unique_tool_call_count = _non_negative_int(trace.get("unique_tool_call_count"))
    diagnostics_value = trace.get("diagnostics")
    normalization_error = trace.get("normalization_error")

    if (
        (trace_id_value is not None and not isinstance(trace_id_value, str))
        or mode is None
        or model_round_count is None
        or tool_calling_round_count is None
        or completed_loop_count is None
        or unique_tool_call_count is None
        or tool_calling_round_count > model_round_count
        or completed_loop_count > tool_calling_round_count
        or (normalization_error is None and completed_loop_count != tool_calling_round_count)
        or (completed_loop_count > 0 and unique_tool_call_count < completed_loop_count)
        or not isinstance(diagnostics_value, Mapping)
        or (normalization_error is not None and _non_empty_string(normalization_error) is None)
    ):
        return None

    malformed_event_count = _non_negative_int(diagnostics_value.get("malformed_event_count"))
    duplicate_loop_observation_count = _non_negative_int(
        diagnostics_value.get("duplicate_loop_observation_count")
    )
    duplicate_tool_observation_count = _non_negative_int(
        diagnostics_value.get("duplicate_tool_observation_count")
    )
    if (
        malformed_event_count is None
        or duplicate_loop_observation_count is None
        or duplicate_tool_observation_count is None
    ):
        return None

    return (
        trace_id_value.strip() if isinstance(trace_id_value, str) else None,
        mode.lower(),
        model_round_count,
        tool_calling_round_count,
        completed_loop_count,
        unique_tool_call_count,
        {
            "malformed_event_count": malformed_event_count,
            "duplicate_loop_observation_count": duplicate_loop_observation_count,
            "duplicate_tool_observation_count": duplicate_tool_observation_count,
        },
        _non_empty_string(normalization_error),
    )


def _non_empty_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
