"""工具参数 Schema 合法率的确定性评测器。"""

from collections.abc import Mapping

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus


class ToolArgumentSchemaValidityEvaluator(BaseEvaluator):
    """在 Schema 已覆盖调用上计算类型、枚举与嵌套结构合法率。"""

    @property
    def metric_name(self) -> str:
        return "tool_argument_schema_validity_rate"

    @property
    def evaluator_version(self) -> str:
        return "1.1.0"

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
            total_count,
            covered_count,
            valid_count,
            invalid_count,
            diagnostics,
            normalization_error,
        ) = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            "manual_version": manual_version,
            "total_call_count": total_count,
            "schema_covered_call_count": covered_count,
            "schema_valid_call_count": valid_count,
            "schema_invalid_call_count": invalid_count,
            **diagnostics,
        }
        if mode not in {"chat", "research"}:
            metadata["reason"] = "unsupported_trace_mode"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if normalization_error is not None:
            metadata["reason"] = normalization_error
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if total_count == 0:
            metadata["reason"] = "no_tool_calls"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if covered_count == 0:
            metadata["reason"] = "no_schema_covered_calls"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        return self._result(
            valid_count / covered_count,
            EvaluationStatus.COMPUTED,
            metadata,
        )

    def _result(
        self,
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
        int,
        dict[str, int],
        str | None,
    ]
    | None
):
    trace_id_value = trace.get("trace_id")
    mode = _non_empty_string(trace.get("mode"))
    manual_version = _non_empty_string(trace.get("manual_version"))
    total_count = _non_negative_int(trace.get("total_call_count"))
    covered_count = _non_negative_int(trace.get("schema_covered_call_count"))
    valid_count = _non_negative_int(trace.get("schema_valid_call_count"))
    invalid_count = _non_negative_int(trace.get("schema_invalid_call_count"))
    diagnostics_value = trace.get("diagnostics")
    normalization_error = trace.get("normalization_error")
    if (
        (trace_id_value is not None and not isinstance(trace_id_value, str))
        or mode is None
        or manual_version is None
        or total_count is None
        or covered_count is None
        or valid_count is None
        or invalid_count is None
        or covered_count > total_count
        or valid_count + invalid_count != covered_count
        or not isinstance(diagnostics_value, Mapping)
        or (normalization_error is not None and _non_empty_string(normalization_error) is None)
    ):
        return None

    diagnostic_names = (
        "unknown_schema_call_count",
        "malformed_arguments_call_count",
        "type_violation_call_count",
        "enum_violation_call_count",
        "missing_nested_required_call_count",
        "undeclared_argument_call_count",
        "duplicate_call_definition_count",
        "malformed_event_count",
    )
    diagnostics: dict[str, int] = {}
    for name in diagnostic_names:
        count = _non_negative_int(diagnostics_value.get(name))
        if count is None:
            return None
        diagnostics[name] = count
    if (
        diagnostics["unknown_schema_call_count"] + covered_count > total_count
        or diagnostics["malformed_arguments_call_count"] > invalid_count
        or diagnostics["type_violation_call_count"] > invalid_count
        or diagnostics["enum_violation_call_count"] > invalid_count
        or diagnostics["missing_nested_required_call_count"] > invalid_count
        or diagnostics["undeclared_argument_call_count"] > covered_count
    ):
        return None

    return (
        trace_id_value.strip() if isinstance(trace_id_value, str) else None,
        mode,
        manual_version,
        total_count,
        covered_count,
        valid_count,
        invalid_count,
        diagnostics,
        _non_empty_string(normalization_error),
    )


def _non_empty_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
