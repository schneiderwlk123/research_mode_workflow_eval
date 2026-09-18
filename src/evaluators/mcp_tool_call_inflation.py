"""MCP 工具调用膨胀率的确定性评估器。

版本 1.2.0（2026-09-18 复核修订）：异步任务轮询调用（入参恰为 taskId 单键）不进入
分子与分母，进入 polling_call_count 诊断；新增 empty_argument_call_count 空入参诊断。
仅存在轮询调用而无其他手册内 MCP 时，返回 NOT_COMPUTABLE / no_non_polling_mcp_calls。
"""

from collections.abc import Mapping

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus


class McpToolCallInflationEvaluator(BaseEvaluator):
    """计算同一任务内相同 MCP 与相同入参的后续调用占比（轮询调用已排除）。"""

    @property
    def metric_name(self) -> str:
        return "mcp_tool_call_inflation_rate"

    @property
    def evaluator_version(self) -> str:
        return "1.2.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        """使用规范化阶段产生的安全聚合计数计算指标。"""

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
            manual_version,
            call_count,
            signature_count,
            inflated_count,
            diagnostics,
            error,
        ) = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            "manual_version": manual_version,
            "mcp_call_count": call_count,
            "unique_request_signature_count": signature_count,
            "inflated_call_count": inflated_count,
            "malformed_event_count": diagnostics["malformed_event_count"],
            "out_of_scope_tool_call_count": diagnostics["out_of_scope_tool_call_count"],
            "duplicate_call_observation_count": diagnostics["duplicate_call_observation_count"],
            "polling_call_count": diagnostics["polling_call_count"],
            "empty_argument_call_count": diagnostics["empty_argument_call_count"],
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
        if call_count == 0:
            # 轮询调用已按协议行为排除；仅剩轮询时说明本 Trace 没有可评价的普通 MCP 调用。
            metadata["reason"] = (
                "no_non_polling_mcp_calls"
                if diagnostics["polling_call_count"] > 0
                else "no_registered_mcp_calls"
            )
            return self._result(
                value=None,
                status=EvaluationStatus.NOT_COMPUTABLE,
                metadata=metadata,
            )

        return self._result(
            value=inflated_count / call_count,
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
) -> (
    tuple[
        str | None,
        str,
        str,
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
    manual_version = _non_empty_string(trace.get("manual_version"))
    call_count = _non_negative_int(trace.get("mcp_call_count"))
    signature_count = _non_negative_int(trace.get("unique_request_signature_count"))
    inflated_count = _non_negative_int(trace.get("inflated_call_count"))
    diagnostics_value = trace.get("diagnostics")
    normalization_error = trace.get("normalization_error")

    if (
        (trace_id_value is not None and not isinstance(trace_id_value, str))
        or mode is None
        or manual_version is None
        or call_count is None
        or signature_count is None
        or inflated_count is None
        or signature_count > call_count
        or inflated_count != call_count - signature_count
        or not isinstance(diagnostics_value, Mapping)
        or (normalization_error is not None and _non_empty_string(normalization_error) is None)
    ):
        return None

    malformed_count = _non_negative_int(diagnostics_value.get("malformed_event_count"))
    out_of_scope_count = _non_negative_int(diagnostics_value.get("out_of_scope_tool_call_count"))
    duplicate_observation_count = _non_negative_int(
        diagnostics_value.get("duplicate_call_observation_count")
    )
    polling_count = _non_negative_int(diagnostics_value.get("polling_call_count"))
    empty_argument_count = _non_negative_int(diagnostics_value.get("empty_argument_call_count"))
    if (
        malformed_count is None
        or out_of_scope_count is None
        or duplicate_observation_count is None
        or polling_count is None
        or empty_argument_count is None
    ):
        return None

    return (
        trace_id_value.strip() if isinstance(trace_id_value, str) else None,
        mode.lower(),
        manual_version,
        call_count,
        signature_count,
        inflated_count,
        {
            "malformed_event_count": malformed_count,
            "out_of_scope_tool_call_count": out_of_scope_count,
            "duplicate_call_observation_count": duplicate_observation_count,
            "polling_call_count": polling_count,
            "empty_argument_call_count": empty_argument_count,
        },
        _non_empty_string(normalization_error),
    )


def _non_empty_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
