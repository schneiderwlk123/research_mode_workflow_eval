"""完全重复调用率的确定性评估器。"""

from collections.abc import Mapping

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus


class ExactDuplicateToolCallEvaluator(BaseEvaluator):
    """计算工具名、参数和响应内容均相同的后续调用占比。"""

    @property
    def metric_name(self) -> str:
        return "exact_duplicate_tool_call_rate"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        """使用规范化阶段产生的安全聚合计数计算指标。"""

        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                value=None,
                status=EvaluationStatus.ERROR,
                metadata={"reason": "invalid_normalized_trace"},
            )

        trace_id, mode, unique_count, comparable_count, duplicate_count, diagnostics, error = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            "unique_call_count": unique_count,
            "comparable_call_count": comparable_count,
            "uncomparable_call_count": unique_count - comparable_count,
            "exact_duplicate_call_count": duplicate_count,
            "malformed_event_count": diagnostics["malformed_event_count"],
            "missing_response_payload_count": diagnostics["missing_response_payload_count"],
            "duplicate_state_event_count": diagnostics["duplicate_state_event_count"],
        }
        if mode not in {"chat", "research"}:
            metadata["reason"] = "unsupported_trace_mode"
            return self._result(
                value=None,
                status=EvaluationStatus.NOT_COMPUTABLE,
                metadata=metadata,
            )
        if error is not None:
            metadata["reason"] = error
            return self._result(
                value=None,
                status=EvaluationStatus.NOT_COMPUTABLE,
                metadata=metadata,
            )
        if unique_count == 0:
            metadata["reason"] = "no_tool_calls"
            return self._result(
                value=None,
                status=EvaluationStatus.NOT_COMPUTABLE,
                metadata=metadata,
            )

        return self._result(
            value=duplicate_count / unique_count,
            status=EvaluationStatus.COMPUTED,
            metadata=metadata,
        )

    def _result(
        self,
        *,
        value: float | None,
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
) -> tuple[str | None, str, int, int, int, dict[str, int], str | None] | None:
    trace_id_value = trace.get("trace_id")
    mode = trace.get("mode")
    unique_count = _non_negative_int(trace.get("unique_call_count"))
    comparable_count = _non_negative_int(trace.get("comparable_call_count"))
    duplicate_count = _non_negative_int(trace.get("exact_duplicate_call_count"))
    diagnostics_value = trace.get("diagnostics")
    normalization_error = trace.get("normalization_error")

    if (
        (trace_id_value is not None and not isinstance(trace_id_value, str))
        or not isinstance(mode, str)
        or not mode.strip()
        or unique_count is None
        or comparable_count is None
        or duplicate_count is None
        or comparable_count > unique_count
        or duplicate_count > comparable_count
        or not isinstance(diagnostics_value, Mapping)
        or (
            normalization_error is not None
            and (not isinstance(normalization_error, str) or not normalization_error.strip())
        )
    ):
        return None

    malformed_count = _non_negative_int(diagnostics_value.get("malformed_event_count"))
    missing_payload_count = _non_negative_int(
        diagnostics_value.get("missing_response_payload_count")
    )
    duplicate_state_count = _non_negative_int(diagnostics_value.get("duplicate_state_event_count"))
    if (
        malformed_count is None
        or missing_payload_count is None
        or duplicate_state_count is None
        or missing_payload_count > unique_count
    ):
        return None

    return (
        trace_id_value.strip() if isinstance(trace_id_value, str) else None,
        mode.strip().lower(),
        unique_count,
        comparable_count,
        duplicate_count,
        {
            "malformed_event_count": malformed_count,
            "missing_response_payload_count": missing_payload_count,
            "duplicate_state_event_count": duplicate_state_count,
        },
        normalization_error.strip() if isinstance(normalization_error, str) else None,
    )


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
